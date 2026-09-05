"""QML front end: engine load, settings round trip, controller binding.

Runs headless via QT_QPA_PLATFORM=offscreen + QT_QUICK_BACKEND=software;
skipped entirely without PyQt6.QtQml. The controller under test is the real
PanelController with the adb boundary stubbed (test_controller's pattern:
stub monitor never threads, install check reported synchronously), so the
QML bindings are exercised against the production data flow.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

pytest.importorskip("PyQt6.QtQml")

from PyQt6.QtCore import QEventLoop, QObject, QTimer, QUrl  # noqa: E402
from PyQt6.QtQml import (  # noqa: E402
        QQmlApplicationEngine,
        QQmlComponent,
        QQmlEngine,
)
from PyQt6.QtWidgets import QApplication  # noqa: E402

import duo.ui.controller as controller_mod  # noqa: E402
from duo.ui.app import QML_MAIN, SettingsApi  # noqa: E402
from duo.ui.controller import MIRROR_KEY, PanelController  # noqa: E402

SETTINGS_QML = QML_MAIN.with_name("SettingsPage.qml")


class _StubMonitor:
        """DeviceMonitor stand-in: no threads, no adb, deterministic state."""

        instances: list[_StubMonitor] = []

        def __init__(self, on_change, query=None, adb_binary=None,
                     poll_interval_s: float = 2.0) -> None:
                self.on_change = on_change
                self.started = False
                self.online: list[str] = []
                self._states: dict[str, str] = {}
                _StubMonitor.instances.append(self)

        def set_states(self, states: dict[str, str]) -> None:
                self._states = dict(states)
                self.online = [s for s, st in states.items() if st == "device"]

        @property
        def states(self) -> dict[str, str]:
                return dict(self._states)

        def poll_now(self) -> None:
                self.on_change(self.states)

        def start(self) -> None:
                self.started = True

        def stop(self) -> None:
                pass


@pytest.fixture()
def qapp():
        """Ensure exactly one QApplication exists (offscreen)."""
        app = QApplication.instance() or QApplication([])
        yield app


@pytest.fixture()
def settings_file(tmp_path, monkeypatch):
        """settings.json under a Chinese + space path (round trip friendly)."""
        import duo.core.settings as settings_mod

        path = tmp_path / "设 置" / "settings.json"
        monkeypatch.setattr(settings_mod, "settings_path", lambda: path)
        return path


@pytest.fixture()
def prefs_stub(monkeypatch):
        """Portrait prefs never touch the real data dir."""

        class _StubPrefs:
                payload: str | None = None
                parent = Path(".")

                def read_text(self, encoding: str = "utf-8") -> str:
                        raise OSError("missing")

                def write_text(self, text: str, encoding: str = "utf-8") -> None:
                        self.payload = text

                def mkdir(self, parents: bool = True, exist_ok: bool = True) -> None:
                        pass

        stub = _StubPrefs()
        monkeypatch.setattr(controller_mod, "_prefs_path", lambda: stub)
        return stub


@pytest.fixture()
def no_adb(monkeypatch):
        """Stub the device monitor; the install check reports synchronously."""
        _StubMonitor.instances = []
        monkeypatch.setattr(controller_mod, "DeviceMonitor", _StubMonitor)

        def fake_resolve_installed(adb_binary, done):
                done({"cn.com.langeasy.LangEasyLexis", "tv.danmaku.bili"})

        monkeypatch.setattr(controller_mod, "_resolve_installed", fake_resolve_installed)


def _pump(ms: int) -> None:
        """Spin the event loop so bindings, transitions and timers settle."""
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()


def _make_engine(controller: PanelController, api: SettingsApi) -> QQmlApplicationEngine:
        """Load Main.qml against the real controller (no errors tolerated)."""
        engine = QQmlApplicationEngine()
        warnings: list[object] = []
        engine.warnings.connect(warnings.append)
        context = engine.rootContext()
        context.setContextProperty("ctrl", controller)
        context.setContextProperty("settingsApi", api)
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        assert engine.rootObjects(), "Main.qml 加载失败"
        assert warnings == [], [str(w) for w in warnings]
        return engine


# ----------------------------------------------------------------- ① engine


def test_main_qml_loads_with_root_object(qapp, no_adb, prefs_stub, settings_file):
        """Main.qml loads without errors and the root window exists."""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                # The window title comes from QML; the toast/markers exist.
                assert root.property("title") == "Duo"
                assert root.findChild(QObject, "statusToast") is not None
                assert root.findChild(QObject, "gearButton") is not None
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


# ---------------------------------------------------- ② settings round trip


def test_settings_api_roundtrip(settings_file, qapp):
        """save() persists and load() reads back (Chinese/space path too)."""
        api = SettingsApi()
        values = {
                "scrcpy_path": r"C:\bin\scrcpy 4.1\scrcpy.exe",
                "adb_path": r"C:\工具\platform-tools\adb.exe",
                "fps": 120,
                "bitrate_mbps": 8,
                "dpi": 400,
                "corner_mode": "g2",
                "corner_size_dip": 64,
                "glass_enabled": False,
        }
        assert api.save(dict(values)) == []
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["corner_mode"] == "g2"
        assert api.load() == values


def test_settings_api_save_reports_problems(settings_file, qapp):
        """Invalid values come back as a problem list; nothing is persisted."""
        api = SettingsApi()
        problems = api.save({
                "scrcpy_path": r"C:\x\scrcpy.exe",
                "adb_path": "",
                "fps": 9999,
                "bitrate_mbps": 30,
                "dpi": None,
                "corner_mode": "round",
                "corner_size_dip": 48,
                "glass_enabled": True,
        })
        assert any("fps" in p for p in problems)
        assert any("corner_mode" in p for p in problems)
        assert not Path(settings_file).exists()


def test_settings_api_folds_whole_js_doubles(settings_file, qapp):
        """QML numbers are always JS doubles; _number folds whole ones to int.

        Without the fold, json writes ``120.0`` and validate() would reject
        every int field coming back from the QML page.
        """
        api = SettingsApi()
        problems = api.save({
                "scrcpy_path": "",
                "adb_path": "",
                "fps": 120.0,
                "bitrate_mbps": 8.0,
                "dpi": 400.0,
                "corner_mode": "system",
                "corner_size_dip": 64.0,
                "glass_enabled": True,
        })
        assert problems == []
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["fps"] == 120 and isinstance(raw["fps"], int)
        assert raw["corner_size_dip"] == 64
        loaded = api.load()
        assert loaded["fps"] == 120 and isinstance(loaded["fps"], int)
        assert loaded["dpi"] == 400 and isinstance(loaded["dpi"], int)


def test_settings_api_load_problems(settings_file, qapp):
        """Corrupt settings.json surfaces via loadProblems() (the red bar)."""
        api = SettingsApi()
        assert api.loadProblems() == []          # missing file: no problems
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text("{not json", encoding="utf-8")
        problems = api.loadProblems()
        assert problems and "settings.json" in problems[0]


# --------------------------------------------------- ④ controller → QML bind


def test_controller_bindings_drive_qml(qapp, no_adb, prefs_stub, settings_file):
        """statusText and engineLocked flow from the controller into QML."""
        controller = PanelController("/nonexistent/adb-for-tests")
        try:
                _StubMonitor.instances[-1].set_states({"S1": "device"})
                controller._devicesPolled.emit({"S1": "device"})
                engine = _make_engine(controller, SettingsApi())
                root = engine.rootObjects()[0]
                _pump(250)   # first frame + toast fade-in (140ms)

                toast = root.findChild(QObject, "statusToast")
                label = root.findChild(QObject, "statusToastLabel")
                assert toast is not None and label is not None
                assert label.property("text") == "就绪"
                assert toast.property("visible") is True

                # A controller status event re-renders the toast text.
                controller._set_status("已启动 微信 · 横屏")
                _pump(50)
                assert label.property("text") == "已启动 微信 · 横屏"

                # The engine lock flips the mirror button both ways.
                mirror = root.findChild(QObject, "mirrorButton")
                assert mirror is not None
                assert mirror.property("enabled") is True
                exit_code: list[int | None] = [None]
                proc = SimpleNamespace(poll=lambda: exit_code[0], terminate=lambda: None)
                controller._sessions[MIRROR_KEY] = proc  # type: ignore[assignment]
                controller._emit_sessions()
                _pump(50)
                assert controller.engineLocked is True
                assert mirror.property("enabled") is False
                exit_code[0] = 0          # the mirror process exits...
                controller.reapSessions() # ...the reaper drops it and notifies
                _pump(50)
                assert controller.engineLocked is False
                assert mirror.property("enabled") is True
        finally:
                controller.shutdown()


# ----------------------------------------------- ⑤ settings page + g2 slider


@pytest.fixture()
def settings_page(qapp, settings_file):
        """A SettingsPage instance over the real SettingsApi (tmp settings)."""
        api = SettingsApi()
        engine = QQmlEngine()
        context = engine.rootContext()
        context.setContextProperty("settingsApi", api)
        component = QQmlComponent(engine, str(SETTINGS_QML))
        assert not component.isError(), [str(e) for e in component.errors()]
        page = component.create()
        assert page is not None
        yield page
        page.deleteLater()
        engine.deleteLater()
        _pump(20)


def test_settings_page_loads_defaults_and_g2_slider(settings_page):
        """Defaults fill the page; the corner slider lives only in g2 mode."""
        slider = settings_page.findChild(QObject, "cornerSlider")
        dpi_auto = settings_page.findChild(QObject, "dpiAutoSwitch")
        adb_field = settings_page.findChild(QObject, "adbPathField")
        assert slider is not None and dpi_auto is not None and adb_field is not None
        # Defaults: system rounding (slider idle), dpi auto, empty adb path.
        assert slider.property("enabled") is False
        assert dpi_auto.property("checked") is True
        assert adb_field.property("text") == ""

        settings_page.setProperty("cornerMode", "g2")
        assert slider.property("enabled") is True
        slider.setProperty("value", 72)
        _pump(30)
        assert slider.property("value") == 72
        settings_page.setProperty("cornerMode", "none")
        assert slider.property("enabled") is False


def test_settings_page_save_accepts_with_real_api(settings_page):
        """saveChanges() over valid values emits accepted() (Main pops on it)."""
        accepted: list[bool] = []
        settings_page.accepted.connect(lambda: accepted.append(True))
        adb_field = settings_page.findChild(QObject, "adbPathField")
        assert adb_field is not None
        adb_field.setProperty("text", r"C:\工具\adb.exe")
        settings_page.setProperty("cornerMode", "g2")
        meta = settings_page.metaObject()
        assert meta.indexOfMethod("saveChanges()") >= 0
        meta.invokeMethod(settings_page, "saveChanges")
        assert accepted == [True]


def test_settings_page_cancel_discards_changes(settings_page, settings_file):
        """取消 = 回填 + cancelled()：改动既不保留也不落盘（旧 widgets 行为）。"""
        cancelled: list[bool] = []
        settings_page.cancelled.connect(lambda: cancelled.append(True))
        adb_field = settings_page.findChild(QObject, "adbPathField")
        assert adb_field is not None
        adb_field.setProperty("text", r"C:\临时\adb.exe")
        meta = settings_page.metaObject()
        assert meta.indexOfMethod("cancelChanges()") >= 0
        meta.invokeMethod(settings_page, "cancelChanges")
        assert adb_field.property("text") == ""
        assert cancelled == [True]
        assert not Path(settings_file).exists()


def test_settings_page_dpi_auto_disables_number_box(settings_page):
        """DPI 自动开 = 数字框禁用（自动跟随显示），关 = 可编辑。"""
        dpi_auto = settings_page.findChild(QObject, "dpiAutoSwitch")
        dpi_box = settings_page.findChild(QObject, "dpiBox")
        assert dpi_auto is not None and dpi_box is not None
        assert dpi_auto.property("checked") is True
        assert dpi_box.property("enabled") is False
        dpi_auto.setProperty("checked", False)
        assert dpi_box.property("enabled") is True
        dpi_auto.setProperty("checked", True)
        assert dpi_box.property("enabled") is False


def test_settings_page_engine_locked_disables_engine_rows(settings_page):
        """engineLocked（会话运行中）禁用引擎路径行（QML 页同 widgets 版）。"""
        adb_field = settings_page.findChild(QObject, "adbPathField")
        assert adb_field is not None
        assert adb_field.property("enabled") is True
        settings_page.setProperty("engineLocked", True)
        _pump(30)
        assert adb_field.property("enabled") is False
        settings_page.setProperty("engineLocked", False)
        _pump(30)
        assert adb_field.property("enabled") is True

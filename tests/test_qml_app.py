"""QML front end: engine load, settings round trip, controller binding.

Runs headless via QT_QPA_PLATFORM=offscreen + QT_QUICK_BACKEND=software;
skipped entirely without PyQt6.QtQml. The controller under test is the real
PanelController with the adb boundary stubbed (test_controller's pattern:
stub monitor never threads, install check reported synchronously), so the
QML bindings are exercised against the production data flow.

Main-panel coverage beyond load/bindings: search filtering (label + pinyin
initials), the pinned row card, the shared right-click context menu (incl.
the aspect-ratio section over duo.core.aspects' frozen table), the top
capsule navigating the StackView between 首页/设置, the running-session card
with hover-revealed close buttons, the fallback letter squircle palette and
the panel's layout order. Mouse and key interactions are delivered as real
QMouse/QKey events through the QQuickWindow (offscreen delivers them), so
MouseArea-driven pieces (right-click menu, pinned-icon click, chip hover)
are exercised like production.
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

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import (  # noqa: E402
        QEvent,
        QEventLoop,
        QObject,
        QPointF,
        Qt,
        QTimer,
        QUrl,
        pyqtSlot,
)
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent  # noqa: E402
from PyQt6.QtQml import (  # noqa: E402
        QQmlApplicationEngine,
        QQmlComponent,
        QQmlEngine,
)
from PyQt6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import duo.ui.controller as controller_mod  # noqa: E402
from duo.ui.app import QML_MAIN, SettingsApi  # noqa: E402
from duo.ui.controller import APP_CATALOG, MIRROR_KEY, PanelController  # noqa: E402

SETTINGS_QML = QML_MAIN.with_name("SettingsPage.qml")

#: DESIGN.md §3.1 fallback palette (mirrored by Style.qml fallbackPalette).
FALLBACK_PALETTE = [
        "#5F7292", "#6F8468", "#96725D", "#7D6B94", "#628C87", "#946363",
        "#6C7FA3", "#829462", "#936280", "#628192", "#8C7163", "#746A92",
]


def expected_fallback_color(package: str) -> str:
        """The QML fallbackColor contract, mirrored in Python for asserts."""
        return FALLBACK_PALETTE[sum(ord(ch) for ch in package) % 12].lower()


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
        # 上下文属性只存指针：不钉住 Python 引用会被 GC，push 设置页时
        # settingsApi 变 null（同 test_settings_qml.make_page 的注记）
        engine._api_ref = api
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        assert engine.rootObjects(), "Main.qml 加载失败"
        assert warnings == [], [str(w) for w in warnings]
        return engine


def _walk(item):
        """Yield item and its visual descendants (fresh wrappers each call)."""
        yield item
        for child in item.childItems():
                yield from _walk(child)


def _grid(root) -> QObject:
        grid = root.findChild(QObject, "appsGrid")
        assert grid is not None, "应用网格未找到"
        return grid


def _grid_model(root) -> list[dict]:
        """The grid's (filtered) model as a plain list of entry dicts."""
        model = _grid(root).property("model")
        assert isinstance(model, list)
        return model


def _find_delegate(root, parent: QObject, package: str) -> QQuickItem:
        """Locate a delegate (tile / pinned icon) under ``parent`` by package."""
        target = sip.cast(parent, QQuickItem)
        for item in _walk(target):
                data = item.property("modelData")
                if data is not None and data["package"] == package:
                        return sip.cast(item, QQuickItem)
        raise AssertionError(f"未找到 {package} 的委托")


def _scene_items(root, name: str) -> list[QQuickItem]:
        """Visual-scene items carrying objectName == name.

        Repeater 委托只挂在视觉树（QObject findChild 找不到——固定卡/网格测试
        同款做法），从窗口 contentItem 走 childItems。"""
        scene = sip.cast(root, QQuickWindow).contentItem()
        return [i for i in _walk(scene) if i.objectName() == name]


def _mouse(qapp, root, item: QQuickItem, button) -> None:
        """Deliver a real mouse click at the item's centre (scene coords).

        QQuickItem.width/height 是 Q_INVOKABLE 方法而非属性，必须走
        property()（直接 .width 会拿到 builtin method）。
        """
        window = sip.cast(root, QQuickWindow)
        center = item.mapToScene(QPointF(
                float(item.property("width")) / 2,
                float(item.property("height")) / 2,
        ))
        qapp.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseButtonPress, center, center, button, button,
                Qt.KeyboardModifier.NoModifier))
        qapp.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseButtonRelease, center, center, button,
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _key(qapp, root, key) -> None:
        """Deliver a key press + release to the window's focus item."""
        window = sip.cast(root, QQuickWindow)
        for typ in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                qapp.sendEvent(window, QKeyEvent(
                        typ, key, Qt.KeyboardModifier.NoModifier))


def _move(qapp, root, item: QQuickItem) -> None:
        """Deliver a real button-less mouse move at the item's centre (hover)."""
        window = sip.cast(root, QQuickWindow)
        center = item.mapToScene(QPointF(
                float(item.property("width")) / 2,
                float(item.property("height")) / 2,
        ))
        qapp.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseMove, center, center,
                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))


def _bring_online(controller: PanelController) -> None:
        """One online device, delivered through the stubbed poll hop."""
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        controller._devicesPolled.emit({"S1": "device"})


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
                for name in ("deviceCard", "searchField", "appsGrid",
                             "appContextMenu"):
                        assert root.findChild(QObject, name) is not None, name
                # 顶栏胶囊（两页常驻）与两枚分段；设置段 objectName 沿用
                # gearButton（scripts/qml_shots.py 以该名驱动设置页出图）
                assert root.findChild(QObject, "topCapsule") is not None
                assert root.findChild(QObject, "capsuleHome") is not None
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
                "audio_policy": "all",
                "video_codec": "h265",
                "turn_screen_off": True,
                "top_bar_mode": "native",
                "bottom_bar_mode": "immersive",
        }
        assert api.save(dict(values)) == []
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["corner_mode"] == "g2"
        assert raw["audio_policy"] == "all"
        assert raw["video_codec"] == "h265"
        assert raw["turn_screen_off"] is True
        assert "flex_resolution" not in raw
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
                engine.deleteLater()   # 别泄漏活引擎：残留 toast 计时会漂进后续测试
                _pump(20)


# ----------------------------------------------- ⑤ tiles + pin dock


def test_app_tile_held_guard(qapp, no_adb, prefs_stub, settings_file):
        """App tiles toggle portrait on long-press exactly once.

        The long-press regression: pressAndHold (portrait toggle) plus the
        platform's echoed click (session launch) hit the same press, and
        the delegate died in the rebuild storm. The tile MouseArea must
        carry the ``held`` flag, swallow the echoed click and reset it on
        the next press; the loaded object exposes the flag.
        """
        text = QML_MAIN.read_text(encoding="utf-8")
        assert "property bool held: false" in text
        assert "if (held) { held = false; return }" in text   # swallow echoed click
        assert "onPressed: function () { held = false }" in text   # reset per press
        assert "held = true" in text                          # set by pressAndHold

        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)   # first frame: grid layout + model settle
                # 网格只显示已装应用（2026-09-08 语义）：播种全部目录包
                controller._installedResolved.emit(
                        {p.package for p in APP_CATALOG}
                )
                _pump(120)
                grid = _grid(root)
                grid.forceLayout()   # materialize delegates (no exposed
                _pump(50)            # window offscreen, so force them)

                tile_areas = [
                        item for item in _walk(sip.cast(grid, QQuickItem))
                        if "MouseArea" in item.metaObject().className()
                        and item.property("held") is not None
                ]
                assert len(tile_areas) == len(APP_CATALOG), (
                        "expected one MouseArea per catalog tile",
                        len(tile_areas),
                )
                for ma in tile_areas:
                        assert ma.property("held") is False
                        # left + right accepted (start vs context menu)
                        assert ma.property("acceptedButtons") == (
                                Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton
                        )
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_app_tile_pin_button_and_dock_move(qapp, no_adb, prefs_stub, settings_file):
        """Pin badge: one per tile, ★ tracks the pinned state, and a pinned
        tile LEAVES the grid for the pinned-row card (pinnedApps). The grid
        model is checked via its (filtered) model property — no delegate
        walking after the rebuild storm."""
        text = QML_MAIN.read_text(encoding="utf-8")
        assert 'objectName: "pinButton"' in text
        assert "ctrl.togglePin(tile.modelData.package)" in text   # badge wired to slot

        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                # 网格只显示已装应用（2026-09-08 语义）：播种最小集，
                # 微信排在第 3 格（非首格且在首行内——GridView 懒实例化）
                controller._installedResolved.emit({
                        "cn.com.langeasy.LangEasyLexis",
                        "tv.danmaku.bili",
                        "com.tencent.mm",
                })
                _pump(120)
                grid = _grid(root)
                card = root.findChild(QObject, "pinnedCard")
                assert card is not None
                assert card.property("visible") is False   # 无置顶不出现

                grid.forceLayout()   # materialize delegates offscreen
                _pump(50)
                items = list(_walk(sip.cast(grid, QQuickItem)))
                assert sum(1 for i in items if i.objectName() == "pinButton") == 3
                # 未置顶：微信在网格里（非首格），角标 ☆
                assert "com.tencent.mm" in {e["package"] for e in _grid_model(root)}
                mm = next(
                        i for i in items if i.property("modelData") is not None
                        and i.property("modelData")["package"] == "com.tencent.mm"
                )
                assert mm.property("x") > 0
                badge = next(
                        c for c in mm.childItems() if c.objectName() == "pinButton"
                )
                star = next(
                        c for c in badge.childItems()
                        if c.metaObject().className() == "QQuickText"
                )
                assert star.property("text") == "☆"

                # 置顶：搬去固定栏模型 → 网格不再含它，固定卡出现
                controller.togglePin("com.tencent.mm")
                _pump(250)   # 卡片 140ms 淡入 + 模型重建
                assert "com.tencent.mm" not in {e["package"] for e in _grid_model(root)}
                pinned = [str(e["package"]) for e in controller.pinnedApps]
                assert pinned == ["com.tencent.mm"]
                assert all(bool(e["pinned"]) for e in controller.pinnedApps)
                assert card.property("visible") is True

                # 取消置顶：回到网格，回到拼音序位置，固定卡淡出
                controller.togglePin("com.tencent.mm")
                _pump(300)
                assert "com.tencent.mm" in {e["package"] for e in _grid_model(root)}
                assert controller.pinnedApps == []
                assert card.property("visible") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_pinned_card_click_starts_session(qapp, no_adb, prefs_stub,
                                          settings_file, monkeypatch):
        """固定卡小图标点击 = startSession（与磁贴同语义）。

    真实点击走 QMouseEvent → MouseArea onClicked → ctrl.startSession；
    _spawn 被换成必然抛错的桩，会话启动路径到 spawn 为止可全验证。
        """
        controller = PanelController("/nonexistent/adb-for-tests")

        def no_spawn(argv):
                raise OSError("no-spawn-in-tests")

        monkeypatch.setattr(controller, "_spawn", no_spawn)
        _bring_online(controller)
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                # 网格只显示已装应用（2026-09-08 语义）：先播种 installed
                controller._installedResolved.emit({
                        p.package for p in APP_CATALOG
                } | {"com.foo.extra"})
                _pump(120)
                card = root.findChild(QObject, "pinnedCard")
                assert card.property("visible") is False

                controller.togglePin("tv.danmaku.bili")   # 已装应用（可点）
                _pump(250)
                assert card.property("visible") is True

                icon = _find_delegate(root, card, "tv.danmaku.bili")
                assert icon.property("width") == 44
                assert icon.property("opacity") == 1.0   # installed → 可点
                _mouse(qapp, root, icon, Qt.MouseButton.LeftButton)
                _pump(80)
                assert controller.statusText.startswith("启动失败")
                assert "哔哩哔哩" in controller.statusText   # startSession 全路径
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


# ------------------------------------------------ ⑥ 搜索（纯前端过滤）


def test_search_filters_by_label_pinyin_and_restores(qapp, no_adb, prefs_stub,
                                                     settings_file):
        """搜索命中 = 标签原文包含 或 拼音首字母前缀；清空即全量恢复。"""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                # 网格只显示已装应用（2026-09-08 语义）：先播种 installed
                controller._installedResolved.emit({
                        p.package for p in APP_CATALOG
                } | {"com.foo.extra"})
                _pump(120)
                grid = _grid(root)
                grid.forceLayout()
                _pump(50)
                search = root.findChild(QObject, "searchField")
                no_match = root.findChild(QObject, "noMatchLabel")
                assert {e["package"] for e in _grid_model(root)} \
                        == {str(p.package) for p in APP_CATALOG}

                # 拼音首字母：wx 命中微信(wx)与微信读书(wxds)——前缀语义
                search.setProperty("text", "wx")
                _pump(80)
                assert {e["package"] for e in _grid_model(root)} \
                        == {"com.tencent.mm", "com.tencent.weread"}
                # 拼音串更深的前缀：gddt 只命中高德地图
                search.setProperty("text", "gdd")
                _pump(80)
                assert {e["package"] for e in _grid_model(root)} \
                        == {"com.autonavi.minimap"}
                # 标签原文包含：哔 → 哔哩哔哩
                search.setProperty("text", "哔")
                _pump(80)
                assert {e["package"] for e in _grid_model(root)} \
                        == {"tv.danmaku.bili"}
                assert no_match.property("visible") is False

                # 无匹配：网格空 + 一行辅助文字
                search.setProperty("text", "zzz")
                _pump(80)
                assert grid.property("count") == 0
                assert no_match.property("visible") is True

                # 清空恢复全量
                search.setProperty("text", "")
                _pump(80)
                assert grid.property("count") == len(APP_CATALOG)
                assert no_match.property("visible") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_search_clear_button_and_esc(qapp, no_adb, prefs_stub, settings_file):
        """清空钮仅在有文字时露出并工作；Esc 清空并失焦；聚焦变浮层色。"""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                # 网格只显示已装应用（2026-09-08 语义）：先播种 installed
                controller._installedResolved.emit({
                        p.package for p in APP_CATALOG
                } | {"com.foo.extra"})
                _pump(120)
                search = root.findChild(QObject, "searchField")
                clear = root.findChild(QObject, "searchClearButton")
                capsule = root.findChild(QObject, "searchCapsule")

                assert clear.property("visible") is False
                search.setProperty("text", "wx")
                _pump(200)   # 140ms 淡入
                assert clear.property("visible") is True
                clear.click()
                _pump(200)   # 140ms 淡出
                assert search.property("text") == ""
                assert clear.property("visible") is False

                # 聚焦 = 亚克力浮层感（flyoutFill 令牌；QColor.name 默认丢 alpha，
                # 用 HexArgb 比对；TextField 初始即持焦点，从聚焦态起测）
                def capsule_hex() -> str:
                        return capsule.property("color").name(
                                QColor.NameFormat.HexArgb)

                search.forceActiveFocus()
                _pump(200)
                assert search.property("activeFocus") is True
                assert capsule_hex() == "#99ffffff"   # 聚焦 = flyoutFill
                search.setProperty("focus", False)
                _pump(200)   # 140ms 颜色过渡
                assert search.property("activeFocus") is False
                assert capsule_hex() == "#b8ffffff"   # 常态 = cardFill

                # Esc 清空并失焦（输入中场景：焦点在搜索框）
                search.setProperty("text", "wx")
                search.forceActiveFocus()
                _pump(30)
                assert search.property("activeFocus") is True
                _key(qapp, root, Qt.Key.Key_Escape)
                _pump(80)
                assert search.property("text") == ""
                assert search.property("activeFocus") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


class _DisplaySpyController(PanelController):
        """Records display-mode setter invocations.

        QML 经元对象调用槽：实例属性补丁不会被分发，必须重新声明为
        pyqtSlot，子类的槽向量才会接管（同 _MirrorSpyController 注记）。
        """

        def __init__(self, adb_binary: str) -> None:
                super().__init__(adb_binary)
                self.fixed_calls: list[tuple[str, str]] = []
                self.flex_calls: list[str] = []

        @pyqtSlot(str, str)
        def setDisplayFixed(self, package: str, aspect_id: str) -> None:
                self.fixed_calls.append((package, aspect_id))

        @pyqtSlot(str)
        def setDisplayFlex(self, package: str) -> None:
                self.flex_calls.append(package)


class _MirrorSpyController(PanelController):
        """Records startMirror invocations (same metaobject rule as above)."""

        def __init__(self, adb_binary: str) -> None:
                super().__init__(adb_binary)
                self.mirror_calls: list[bool] = []

        @pyqtSlot()
        def startMirror(self) -> None:
                self.mirror_calls.append(True)


class _BarSpyController(PanelController):
        """Records setAppBar invocations (same metaobject rule as above)."""

        def __init__(self, adb_binary: str) -> None:
                super().__init__(adb_binary)
                self.bar_calls: list[tuple[str, str, str]] = []

        @pyqtSlot(str, str, str)
        def setAppBar(self, package: str, which: str, mode: str) -> None:
                self.bar_calls.append((package, which, mode))


class _VolumeSpyController(PanelController):
        """Records setMediaVolume invocations (same metaobject rule)."""

        def __init__(self, adb_binary: str) -> None:
                super().__init__(adb_binary)
                self.volume_calls: list[int] = []

        @pyqtSlot(int)
        def setMediaVolume(self, index: int) -> None:
                self.volume_calls.append(index)


# --------------------------------------- ⑦ 右键上下文菜单（磁贴/固定卡共用）


def test_context_menu_right_click_pin_and_dismiss(qapp, no_adb, prefs_stub,
                                                  settings_file):
        """右键弹菜单；打开/置顶语义与 Esc/点外部关闭路径全覆盖（比例区另测）。"""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                grid = _grid(root)
                grid.forceLayout()
                _pump(50)
                menu = root.findChild(QObject, "appContextMenu")
                menu_pin = root.findChild(QObject, "menuPin")
                card = root.findChild(QObject, "pinnedCard")
                assert menu.property("visible") is False

                tile = _find_delegate(root, grid, "tv.danmaku.bili")
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)   # 140ms 淡入
                assert menu.property("openState") is True
                assert menu.property("visible") is True
                assert menu.property("entry")["package"] == "tv.danmaku.bili"
                assert menu_pin.property("text") == "置顶到固定栏"   # 未置顶

                # Esc 关闭（菜单聚焦）
                _key(qapp, root, Qt.Key.Key_Escape)
                _pump(250)
                assert menu.property("openState") is False
                assert menu.property("visible") is False

                # 重开 → 置顶条目：点击即关 + togglePin 生效 + 固定卡出现
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)
                assert menu.property("openState") is True
                menu_pin.click()
                _pump(250)
                assert menu.property("openState") is False   # 选择后关闭
                assert [str(e["package"]) for e in controller.pinnedApps] \
                        == ["tv.danmaku.bili"]
                assert card.property("visible") is True

                # 固定卡小图标右键同款菜单；置顶态文案翻转
                icon = _find_delegate(root, card, "tv.danmaku.bili")
                _mouse(qapp, root, icon, Qt.MouseButton.RightButton)
                _pump(200)
                assert menu.property("openState") is True
                assert menu_pin.property("text") == "取消置顶"   # 已置顶

                # 点外部（设备卡处）关闭
                _mouse(qapp, root, sip.cast(
                        root.findChild(QObject, "deviceCard"), QQuickItem),
                        Qt.MouseButton.LeftButton)
                _pump(250)
                assert menu.property("openState") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_context_menu_display_mode_two_level(qapp, no_adb, prefs_stub,
                                              settings_file):
        """显示模式区二级菜单（DESIGN §3.7）：一级 = 自适应窗口（flex 圆点）+
        固定比例 ▸；点击展开二级（一级右侧、错位 4、不出面板）；二级 = 2 小节
        × 5（含机身，冻结表与 duo/core/aspects.py 一致），示意矩形按真实比例；
        点二级项 → setDisplayFixed + 关两级；点自适应 → setDisplayFlex + 关菜单；
        菜单贴右缘时二级向左展开防溢出。"""
        controller = _DisplaySpyController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                grid = _grid(root)
                grid.forceLayout()
                _pump(50)
                panel = sip.cast(grid.property("parent"), QQuickItem)
                menu = root.findChild(QObject, "appContextMenu")
                sub = root.findChild(QObject, "aspectSubmenu")
                flex_row = root.findChild(QObject, "menuDisplayFlex")
                fixed_row = root.findChild(QObject, "menuDisplayFixed")
                assert sub is not None and flex_row is not None \
                        and fixed_row is not None
                assert flex_row.property("text") == "自适应窗口"
                assert fixed_row.property("text") == "固定比例"

                tile = _find_delegate(root, grid, "tv.danmaku.bili")
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)   # 140ms 淡入
                assert menu.property("openState") is True
                assert sub.property("openState") is False   # 二级默认收起

                # 缺省 flex：自适应行圆点亮（左侧 4px 强调色）
                flex_dot = next(c for c in _walk(sip.cast(flex_row, QQuickItem))
                                if c.objectName() == "menuCheckDot")
                assert (flex_dot.property("width"), flex_dot.property("height")) \
                        == (4, 4)
                assert flex_dot.property("visible") is True

                # 点击「固定比例 ▸」展开二级：一级右侧留 4、垂直错位 4（高菜单
                # 钳制在面板内）；右侧放不下时向左展开——两分支均不出面板右缘
                fixed_row.click()
                _pump(200)
                assert sub.property("openState") is True
                assert sub.property("visible") is True

                def expected_sub_x(menu_x: float) -> float:
                        right = menu_x + float(menu.property("width")) + 4
                        if right + float(sub.property("width")) \
                                <= float(panel.property("width")) - 4:
                                return right
                        return max(4.0, menu_x - float(sub.property("width")) - 4)

                assert float(sub.property("x")) == pytest.approx(
                        expected_sub_x(float(menu.property("x"))))
                assert float(sub.property("y")) == pytest.approx(min(
                        float(menu.property("y")) + 4,
                        float(panel.property("height"))
                        - float(sub.property("height")) - 4))
                assert float(sub.property("x")) >= 4
                assert float(sub.property("x")) + float(sub.property("width")) \
                        <= float(panel.property("width")) - 4

                # 条目 = 2 小节 ×（4 冻结比例 + 1 机身）
                rows = _scene_items(root, "aspectRow")
                assert {str(r.property("aspectId")) for r in rows} == {
                        "21:9", "16:9", "4:3", "1:1", "body-l",
                        "3:4", "2:3", "5:7", "9:16", "body-p",
                }
                assert root.findChild(QObject, "menuOpen") is not None
                assert root.findChild(QObject, "menuPin") is not None
                assert {str(h.property("text")) for h in
                        _scene_items(root, "aspectSectionHeaderText")} \
                        == {"横屏", "竖屏"}

                # 示意矩形存在且按真实比例缩放（横屏最大边宽 16 / 竖屏最大边高 14）
                def glyph_of(row) -> QQuickItem:
                        return next(c for c in _walk(row)
                                    if c.objectName() == "aspectGlyph")

                row_169 = next(r for r in rows if r.property("aspectId") == "16:9")
                row_916 = next(r for r in rows if r.property("aspectId") == "9:16")
                row_body = next(r for r in rows if r.property("aspectId") == "body-l")
                assert row_body.property("text") == "机身"
                assert (float(glyph_of(row_169).property("width")),
                        float(glyph_of(row_169).property("height"))) == (16, 9)
                assert (float(glyph_of(row_916).property("width")),
                        float(glyph_of(row_916).property("height"))) == (7.9, 14)
                assert float(glyph_of(row_body).property("width")) == 16
                assert "svg" in str(glyph_of(row_169).property("source"))

                # 点 16:9 → setDisplayFixed + 两级全关（真鼠标事件走委托）
                _mouse(qapp, root, row_169, Qt.MouseButton.LeftButton)
                _pump(200)
                assert controller.fixed_calls == [("tv.danmaku.bili", "16:9")]
                assert menu.property("openState") is False
                assert sub.property("openState") is False

                # 「自适应窗口」→ setDisplayFlex + 关菜单
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)
                flex_row.click()
                _pump(200)
                assert controller.flex_calls == ["tv.danmaku.bili"]
                assert menu.property("openState") is False

                # 菜单靠左缘：二级在一级右侧展开（+4）
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)
                menu.setProperty("x", 8.0)
                fixed_row.click()
                _pump(200)
                assert sub.property("openState") is True
                assert float(sub.property("x")) == pytest.approx(
                        8.0 + float(menu.property("width")) + 4)

                # 菜单贴右缘：二级向左展开防溢出（仍留 4 边距）
                _key(qapp, root, Qt.Key.Key_Escape)
                _pump(250)
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)
                menu.setProperty("x", float(panel.property("width")) - 132.0)
                fixed_row.click()
                _pump(200)
                assert sub.property("openState") is True
                assert float(sub.property("x")) == pytest.approx(
                        float(menu.property("x")) - float(sub.property("width")) - 4)
                assert float(sub.property("x")) >= 4
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_context_menu_display_dots_follow_memory(qapp, no_adb, prefs_stub,
                                                 settings_file):
        """选中圆点按记忆：fixed 记忆 → 打开菜单即亮该比例项、自适应圆点灭；
        hover 也展开二级；菜单开着时 displayModeChanged 即时刷新（圆点跟着
        新记忆走），改回自适应则自适应圆点复活。"""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                grid = _grid(root)
                grid.forceLayout()
                _pump(50)
                assert root.findChild(QObject, "appContextMenu") is not None
                sub = root.findChild(QObject, "aspectSubmenu")
                flex_row = root.findChild(QObject, "menuDisplayFlex")
                fixed_row = sip.cast(root.findChild(QObject, "menuDisplayFixed"),
                                     QQuickItem)

                # 冻结表 id 无需设备即可记忆
                controller.setDisplayFixed("tv.danmaku.bili", "16:9")

                tile = _find_delegate(root, grid, "tv.danmaku.bili")
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)

                def pick_dot(aspect_id: str):
                        row = next(r for r in _scene_items(root, "aspectRow")
                                   if r.property("aspectId") == aspect_id)
                        return next(c for c in _walk(row)
                                    if c.objectName() == "aspectPickDot")

                # 打开菜单即按记忆选中：16:9 亮、21:9 灭、自适应圆点灭
                flex_dot = next(c for c in _walk(sip.cast(flex_row, QQuickItem))
                                if c.objectName() == "menuCheckDot")
                assert flex_dot.property("visible") is False

                # hover「固定比例 ▸」也展开二级
                _move(qapp, root, fixed_row)
                _pump(200)
                assert sub.property("openState") is True
                assert pick_dot("16:9").property("visible") is True
                assert pick_dot("21:9").property("visible") is False

                # 菜单开着换记忆（displayModeChanged 带包名）→ 圆点即时跟走
                controller.setDisplayFixed("tv.danmaku.bili", "9:16")
                _pump(80)
                assert pick_dot("16:9").property("visible") is False
                assert pick_dot("9:16").property("visible") is True
                assert flex_dot.property("visible") is False

                # 改回自适应：自适应圆点复活，比例圆点全灭
                controller.setDisplayFlex("tv.danmaku.bili")
                _pump(80)
                assert flex_dot.property("visible") is True
                assert pick_dot("9:16").property("visible") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def _bar_pick_dot(row) -> QQuickItem:
        """The selection Dot inside one 窗口栏 row."""
        return next(c for c in _walk(row) if c.objectName() == "barPickDot")


def test_context_menu_window_bar_two_level(qapp, no_adb, prefs_stub,
                                            settings_file):
        """窗口栏二级菜单（每应用设置）：一级「窗口栏 ▸」居「固定比例 ▸」之后；
    二级 = 上巴 3 项（跟随默认/沉浸/系统）+ 下巴 4 项（跟随默认/沉浸/系统/
    不显示——2026-09-09 第三态 none）；无 explicit 时圆点在「跟随默认」；
    点选 = setAppBar(pkg, which, mode)（跟随默认传空串）+ 关两级；与比例
    二级互斥（同一时刻只有一条展开链）。
        """
        controller = _BarSpyController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                grid = _grid(root)
                grid.forceLayout()
                _pump(50)
                menu = root.findChild(QObject, "appContextMenu")
                sub = root.findChild(QObject, "barSubmenu")
                aspect_sub = root.findChild(QObject, "aspectSubmenu")
                fixed_row = root.findChild(QObject, "menuDisplayFixed")
                bar_row = root.findChild(QObject, "menuBarModes")
                assert sub is not None and bar_row is not None
                assert bar_row.property("text") == "窗口栏"
                # 窗口栏 ▸ 在固定比例 ▸ 之后（菜单列内序，按 objectName 比对——
                # PyQt 包装器相等性不可靠）
                menu_col = sip.cast(bar_row.property("parent"), QQuickItem)
                order = [str(k.objectName()) for k in menu_col.childItems()]
                assert order.index("menuDisplayFixed") < order.index("menuBarModes")

                tile = _find_delegate(root, grid, "tv.danmaku.bili")
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)   # 140ms 淡入
                assert menu.property("openState") is True
                assert sub.property("openState") is False   # 二级默认收起

                # 先开比例二级再开窗口栏：互斥（比例收起，窗口栏展开）
                fixed_row.click()
                _pump(200)
                assert aspect_sub.property("openState") is True
                bar_row.click()
                _pump(200)
                assert aspect_sub.property("openState") is False
                assert sub.property("openState") is True
                assert sub.property("visible") is True

                # 条目 = 上巴 3 项 + 下巴 4 项，文字/模式齐全（小节头文字同步核对）
                rows = _scene_items(root, "barRow")
                assert len(rows) == 7
                sections = (
                        ("top", "上巴",
                         {"", "immersive", "native"},
                         {"跟随默认", "沉浸", "系统"}),
                        ("bottom", "下巴",
                         {"", "immersive", "native", "none"},
                         {"跟随默认", "沉浸", "系统", "不显示"}),
                )
                for which, label, modes, texts in sections:
                        part = [r for r in rows if r.property("barWhich") == which]
                        assert len(part) == len(modes)
                        assert {str(r.property("barMode")) for r in part} == modes
                        assert {str(r.property("text")) for r in part} == texts
                        assert label in {str(h.property("text")) for h in
                                         _scene_items(root, "barSectionHeaderText")}
                # 窗口栏小节头不混入比例菜单的断言集
                assert {str(h.property("text")) for h in
                        _scene_items(root, "aspectSectionHeaderText")} \
                        == {"横屏", "竖屏"}

                # 无 explicit：圆点全在两枚「跟随默认」上
                for r in rows:
                        assert _bar_pick_dot(r).property("visible") \
                                is (r.property("barMode") == "")

                # 点上巴·系统 → setAppBar(pkg, "top", "native") + 两级全关
                top_native = next(
                        r for r in rows
                        if r.property("barWhich") == "top"
                        and r.property("barMode") == "native")
                top_native.click()
                _pump(200)
                assert controller.bar_calls == [("tv.danmaku.bili", "top", "native")]
                assert menu.property("openState") is False
                assert sub.property("openState") is False

                # 重开 → 点下巴·跟随默认 → 空串清除 override，同样关两级
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)
                bar_row.click()
                _pump(200)
                bottom_follow = next(
                        r for r in _scene_items(root, "barRow")
                        if r.property("barWhich") == "bottom"
                        and r.property("barMode") == "")
                bottom_follow.click()
                _pump(200)
                assert controller.bar_calls[-1] == ("tv.danmaku.bili", "bottom", "")
                assert menu.property("openState") is False

                # 再重开 → 点下巴·不显示 → none（第三态）同样下发并关两级
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)
                bar_row.click()
                _pump(200)
                bottom_none = next(
                        r for r in _scene_items(root, "barRow")
                        if r.property("barWhich") == "bottom"
                        and r.property("barMode") == "none")
                bottom_none.click()
                _pump(200)
                assert controller.bar_calls[-1] == ("tv.danmaku.bili", "bottom", "none")
                assert menu.property("openState") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_context_menu_window_bar_dots_follow_memory(qapp, no_adb, prefs_stub,
                                                     settings_file):
        """窗口栏圆点按 explicit 记忆：打开菜单即亮 override 项（另一侧不受
    牵连）；菜单开着时 barPrefsChanged 即时刷新；清除后回「跟随默认」。"""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                grid = _grid(root)
                grid.forceLayout()
                _pump(50)
                bar_row = root.findChild(QObject, "menuBarModes")

                controller.setAppBar("tv.danmaku.bili", "top", "native")
                controller.setAppBar("tv.danmaku.bili", "bottom", "immersive")

                tile = _find_delegate(root, grid, "tv.danmaku.bili")
                _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
                _pump(200)
                bar_row.click()
                _pump(200)

                def row(which: str, mode: str) -> QQuickItem:
                        return next(
                                r for r in _scene_items(root, "barRow")
                                if r.property("barWhich") == which
                                and r.property("barMode") == mode)

                # 打开即按记忆选中：top native 亮（跟随默认灭），
                # bottom immersive 亮（跟随默认灭）——两侧互不串
                assert _bar_pick_dot(row("top", "native")).property("visible") is True
                assert _bar_pick_dot(row("top", "")).property("visible") is False
                assert _bar_pick_dot(row("bottom", "immersive")).property("visible") is True
                assert _bar_pick_dot(row("bottom", "")).property("visible") is False

                # 菜单开着换记忆（barPrefsChanged 带包名）→ 圆点即时跟走
                controller.setAppBar("tv.danmaku.bili", "top", "")
                _pump(80)
                assert _bar_pick_dot(row("top", "")).property("visible") is True
                assert _bar_pick_dot(row("top", "native")).property("visible") is False
                assert _bar_pick_dot(row("bottom", "immersive")).property("visible") is True

                # 真点选（真控制器）：setAppBar 生效、状态消息、两级全关
                _mouse(qapp, root, row("bottom", "native"),
                        Qt.MouseButton.LeftButton)
                _pump(200)
                assert controller.barModeFor("tv.danmaku.bili", "bottom") \
                        == {"explicit": True, "mode": "native"}
                assert controller.statusText == "哔哩哔哩 下巴将使用系统栏"
                assert root.findChild(QObject, "appContextMenu") \
                        .property("openState") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_running_card_chip_hover_reveals_close(qapp, no_adb, prefs_stub,
                                               settings_file):
        """运行卡（DESIGN §3.7）：仅会话期出现的玻璃卡；芯片绿点 = Dot
        （8px 绿核 + 1px 白环，与设备卡同构）；hover 露出 ✕（140ms），
        ✕ 点击 = stopSession；移出后 ✕ 再隐。"""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                card = root.findChild(QObject, "runningCard")
                assert card.property("visible") is False   # 无会话不出现

                terminated: list[str] = []
                controller._sessions["tv.danmaku.bili"] = SimpleNamespace(
                        poll=lambda: None,
                        terminate=lambda: terminated.append("bili"))
                controller._emit_sessions()
                _pump(250)   # 卡片 140ms 淡入
                assert card.property("visible") is True
                chip = _scene_items(root, "sessionChip")[0]
                stop = _scene_items(root, "chipStopButton")[0]

                # 绿点 = Dot：外层 10px 白环圆 + 内层 8px 绿核，芯片与设备卡
                # 同构（§3.7，绿核同尺寸；零 border 原生渲染）
                dot = next(c for c in _walk(chip) if c.objectName() == "chipDot")
                assert dot.property("width") == 10
                dev_dot = sip.cast(root.findChild(QObject, "deviceDot"), QQuickItem)
                assert dev_dot.property("width") == 10
                core = sip.cast(dot, QQuickItem).childItems()[0]
                assert core.property("width") == 8

                # ✕ 未 hover 隐藏（位宽预留不换行）→ hover 淡入 → 点击停止
                assert stop.property("opacity") == 0.0
                _move(qapp, root, chip)
                _pump(250)   # 140ms 淡入
                assert stop.property("opacity") == 1.0
                _mouse(qapp, root, stop, Qt.MouseButton.LeftButton)
                _pump(80)
                assert terminated == ["bili"]
                assert controller.statusText.startswith("已关闭 哔哩哔哩")

                # 移出芯片：✕ 再隐
                _move(qapp, root, sip.cast(
                        root.findChild(QObject, "deviceCard"), QQuickItem))
                _pump(250)
                assert stop.property("opacity") == 0.0
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_top_capsule_segments_navigate_stack(qapp, no_adb, prefs_stub,
                                             settings_file):
        """顶栏胶囊（DESIGN §3.2）：两段选中随 stack.depth 翻转，点击 =
        push/pop；设置页常驻覆盖；Esc（设置页快捷键）= pop。"""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(250)
                stack = root.findChild(QObject, "pageStack")
                capsule = root.findChild(QObject, "topCapsule")
                home = root.findChild(QObject, "capsuleHome")
                settings_seg = root.findChild(QObject, "gearButton")
                assert stack is not None and capsule is not None
                assert capsule.property("y") == 16
                assert capsule.property("height") == 32
                assert stack.property("depth") == 1
                assert home.property("selected") is True
                assert settings_seg.property("selected") is False

                settings_seg.click()
                _pump(300)   # push 过渡
                assert stack.property("depth") == 2
                assert root.findChild(QObject, "settingsPageQml") is not None
                assert capsule.property("visible") is True   # 两页常驻
                assert home.property("selected") is False
                assert settings_seg.property("selected") is True

                # Esc = 设置页取消返回（页面无返回钮，快捷键保留；此刻栈上
                # 只有这一页，残留页的快捷键不会造成歧义冲突）
                _key(qapp, root, Qt.Key.Key_Escape)
                _pump(300)
                assert stack.property("depth") == 1

                # 再推一次 → 首页段 = pop；设置页随 pop 隐藏（销毁异步）
                settings_seg.click()
                _pump(300)
                assert stack.property("depth") == 2
                home.click()
                _pump(300)
                assert stack.property("depth") == 1
                page = root.findChild(QObject, "settingsPageQml")
                assert page is None or page.property("visible") is False
                assert home.property("selected") is True
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_capsule_segments_split_half_width(qapp, no_adb, prefs_stub,
                                           settings_file):
        """段落均分（DESIGN.md §3.2）：两段各占通栏一半——宽 (通栏-4)/2、
        x 2 与 通栏/2+2、高 28、y 2——文字居中（文本元素铺满半宽段 +
        CapsuleSegment 源码声明的水平居中对齐）。"""
        qml_src = QML_MAIN.read_text(encoding="utf-8")
        assert "horizontalAlignment: Text.AlignHCenter" in qml_src
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                capsule = sip.cast(root.findChild(QObject, "topCapsule"), QQuickItem)
                home = sip.cast(root.findChild(QObject, "capsuleHome"), QQuickItem)
                gear = sip.cast(root.findChild(QObject, "gearButton"), QQuickItem)
                w = float(capsule.property("width"))

                for seg in (home, gear):
                        assert float(seg.property("width")) == pytest.approx(w / 2 - 4)
                        assert float(seg.property("height")) == \
                                float(capsule.property("height")) - 4
                        assert float(seg.property("y")) == 2
                        # 文字居中：文本元素铺满整段（宽 = 段宽、x 0），配合
                        # CapsuleSegment 声明的 horizontalAlignment:
                        # Text.AlignHCenter → 字形在半宽段内居中（inline
                        # component 派生类名带 _QML 后缀，前缀匹配）
                        text = next(c for c in _walk(seg)
                                    if c.metaObject().className().startswith("QQuickText"))
                        assert float(text.property("x")) == 0
                        assert float(text.property("width")) == pytest.approx(
                                float(seg.property("width")))
                        assert float(text.property("implicitWidth")) \
                                < float(seg.property("width"))   # 靠对齐而非天然满宽
                assert float(home.property("x")) == 2
                assert float(gear.property("x")) == pytest.approx(w / 2 + 2)
                # 两段相邻不重叠：首段右缘到中线留 2，次段从中线 +2 起
                assert float(home.property("x")) + float(home.property("width")) \
                        == pytest.approx(w / 2 - 2)
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


# --------------------------------------------- ⑦′ 镜像卡（DESIGN.md §3.5）


def test_mirror_card_button_and_context_menu(qapp, no_adb, prefs_stub,
                                             settings_file):
        """镜像卡：卡在固定卡与搜索之间（位置另见布局链测试）；「投屏」
        按钮是唯一启动路径（设备卡不再带按钮）；右键卡弹菜单——打开投屏
        调 startMirror、勾选项切 turn_screen_off 并落盘、Esc/点外部关闭。"""
        controller = _MirrorSpyController("/nonexistent/adb-for-tests")
        _bring_online(controller)
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                device = sip.cast(root.findChild(QObject, "deviceCard"), QQuickItem)

                # 设备卡纯状态展示：树内无投屏按钮
                assert not any(i.objectName() == "mirrorButton" for i in _walk(device))
                card = sip.cast(root.findChild(QObject, "mirrorCard"), QQuickItem)
                btn = sip.cast(root.findChild(QObject, "mirrorButton"), QQuickItem)
                assert card is not None and btn is not None
                names: list[str] = []
                node = btn.parentItem()
                while node is not None:
                        names.append(node.objectName())
                        node = node.parentItem()
                assert "mirrorCard" in names and "deviceCard" not in names
                assert (float(btn.property("width")), float(btn.property("height"))) \
                        == (68.0, 32.0)
                assert btn.property("enabled") is True   # 在线 + 未锁

                # 按钮点击 → startMirror（按钮是唯一启动路径）
                btn.click()
                _pump(80)
                assert controller.mirror_calls == [True]

                # 右键卡 → 镜像菜单；Esc 关闭
                menu = root.findChild(QObject, "mirrorContextMenu")
                assert menu is not None
                assert menu.property("openState") is False
                _mouse(qapp, root, card, Qt.MouseButton.RightButton)
                _pump(200)   # 140ms 淡入
                assert menu.property("openState") is True
                assert menu.property("visible") is True
                _key(qapp, root, Qt.Key.Key_Escape)
                _pump(250)
                assert menu.property("openState") is False

                # 「打开投屏」：调 startMirror 并关菜单
                _mouse(qapp, root, card, Qt.MouseButton.RightButton)
                _pump(200)
                root.findChild(QObject, "mirrorMenuOpen").click()
                _pump(200)
                assert menu.property("openState") is False
                assert controller.mirror_calls == [True, True]

                # 勾选项：切换 turn_screen_off（写盘 + 圆点显隐），菜单保持开
                _mouse(qapp, root, card, Qt.MouseButton.RightButton)
                _pump(200)
                row = sip.cast(root.findChild(QObject, "mirrorMenuScreenOff"),
                               QQuickItem)
                assert row.property("text") == "镜像时关闭设备屏幕"
                dot = next(i for i in _walk(row) if i.objectName() == "menuCheckDot")
                assert (dot.property("width"), dot.property("height")) == (4, 4)
                assert dot.property("visible") is False   # 默认关
                row.click()
                _pump(80)
                assert controller.turnScreenOff is True
                raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
                assert raw["turn_screen_off"] is True
                assert controller.statusText == "镜像时将关闭设备屏幕"
                assert dot.property("visible") is True   # 勾选态即时可见
                assert menu.property("openState") is True   # 勾选不收菜单

                # 点外部（设备卡处）关闭
                _mouse(qapp, root, device, Qt.MouseButton.LeftButton)
                _pump(250)
                assert menu.property("openState") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


# ------------------------------------------- ⑧ 未知应用 fallback 色板


def test_fallback_palette_deterministic(qapp, no_adb, prefs_stub, settings_file):
        """icon 空串 → 首字 squircle；颜色 = 色板[包名码和 % 12]，稳定复现。"""
        # Style.qml 与 DESIGN.md §3.1 的 12 色板同步（spec 锁定）
        style_text = (QML_MAIN.parent / "Style.qml").read_text(encoding="utf-8")
        for hex_color in FALLBACK_PALETTE:
                assert hex_color in style_text, hex_color

        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                grid = _grid(root)

                # 图标工人送达空值等价：条目 icon 置空 + 单次批量通知
                pkg = "tv.danmaku.bili"
                next(e for e in controller._apps
                     if str(e["package"]) == pkg)["icon"] = ""
                controller.appsChanged.emit()
                _pump(120)
                grid.forceLayout()
                _pump(50)

                tile = _find_delegate(root, grid, pkg)
                fb = next(i for i in _walk(tile) if i.objectName() == "fallbackIcon")
                expected = expected_fallback_color(pkg)
                assert fb.property("visible") is True
                assert fb.property("color").name() == expected
                assert fb.property("radius") == 14   # 60px squircle 23%
                letter = next(i for i in _walk(fb)
                              if i.metaObject().className() == "QQuickText")
                assert letter.property("text") == "哔"
                assert letter.property("color").name() == "#ffffff"
                assert letter.property("font").pixelSize() == 19

                # 确定性：同包名重发通知，颜色不跳
                controller.appsChanged.emit()
                _pump(120)
                grid.forceLayout()
                _pump(50)
                fb2 = next(i for i in _walk(_find_delegate(root, grid, pkg))
                           if i.objectName() == "fallbackIcon")
                assert fb2.property("color").name() == expected

                # 固定卡 44px 版本同色（另一尺寸的同一取色函数）
                controller.togglePin(pkg)
                _pump(250)
                card = root.findChild(QObject, "pinnedCard")
                icon = _find_delegate(root, card, pkg)
                fb_small = next(i for i in _walk(icon)
                                if i.objectName() == "fallbackIcon")
                assert fb_small.property("color").name() == expected
                assert fb_small.property("radius") == 10
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


# --------------------------------------------- ⑨ 删除项与布局顺序（规范锁定）


def test_removed_decorations_and_accessible_names(qapp, no_adb, prefs_stub,
                                                  settings_file):
        """磁贴 ToolTip 与 Accessible 尾巴已删；运行卡无会话不出现；芯片方向
        小标签与页面大标题行均已删（DESIGN §3.2/§3.7）。"""
        text = QML_MAIN.read_text(encoding="utf-8")
        assert "右键切换横竖屏" not in text          # ToolTip 文案 + Accessible 尾巴
        assert "ToolTip.visible: tileMa.containsMouse" not in text
        assert "置顶到最前" not in text              # 角标 ToolTip（菜单已覆盖）
        assert 'text: "运行中"' not in text           # 运行卡标题 Text 已删
        assert "点击切换下次启动方向" not in text    # 芯片方向小标签 ToolTip
        assert "ctrl.togglePortrait" not in text    # 方向偏好入口已由比例菜单取代
        assert "portraitFor" not in text
        assert 'text: "Duo"' not in text             # 页面大标题行已删（胶囊接手）
        # Accessible.name 即标签本身（无尾巴）
        assert "Accessible.name: tile.modelData.label" in text
        assert "Accessible.name: pic.modelData.label" in text   # 固定卡小图标同規

        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                # 网格只显示已装：播种含微信的小集（懒实例化只物化视口内行）
                controller._installedResolved.emit({
                        "cn.com.langeasy.LangEasyLexis",
                        "tv.danmaku.bili",
                        "com.tencent.mm",
                })
                _pump(120)
                grid = _grid(root)
                grid.forceLayout()
                _pump(50)
                # 磁贴仍可定位（删装饰不破结构）；运行卡无会话不出现
                tile = _find_delegate(root, grid, "com.tencent.mm")
                assert tile is not None
                assert root.findChild(QObject, "runningCard").property("visible") is False
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_menu_glass_blur_is_masked_and_dual_path(qapp, no_adb, prefs_stub,
                                                 settings_file):
        """右键菜单毛玻璃（真机 GL 主路径）规范锁定。

    历史：早期方案是浮层内嵌 clip:true 的方形采样层（bgSource）对画布
    快照模糊——软件后端不执行着色器、开发出图正常，Windows GL 真机却把
    圆角卡盖成直角（方形容器无遮罩，真机复现）；两次回退（88% 白 →
    不透明）都被否。现方案（MenuGlassPlate 三明治）：
      ① ShaderEffectSource 内容快照（sourceItem = canvasRoot，画布
         bgLayer + 色斑 + StackView 页面的完整合成——2026-09-09 Opus 审计
         方案 A 核心修复，原 stack 只含半透明卡片、模糊对透明底凭空合成
         白雾；live:false + open() 按菜单位置设 sourceRect（钳制在源内）
         + scheduleUpdate() 按需抓帧）；菜单浮层在 zoomLayer、不在
         canvasRoot 子树内 → 无自采样环。
      ② MultiEffect 高斯模糊（gemini 终审档 blur 0.75/blurMax 32 = 等效
         24px，彻底雾化背景文字）+ saturation 0.15 补偿染色漂白 +
         **maskEnabled 圆角 alpha 蒙版**——蒙版把模糊输出硬裁成圆角，
         根治直角 bug。**过采样 1:1 是铁律**（同日晚间真机回归：旧实现
         128px 显示项装 160px 采样区 → 缩放 ≈0.8 且贴边钳制后继续变，
         玻璃内容与真实背景错位漂移，即"背景会移动位置"；菜单边缘模糊
         核心采到纹理界外像素 + maskSpreadAtMin 0.5 软坡 → 四角毛刺）：
         快照层/sourceRect/模糊层/蒙版同尺寸（菜单 + 2×blurMargin 28），
         蒙版白块与菜单同位，无 maskSpreadAtMin（硬裁）。
      ③ 染色层：GL = menuTint 72% 画布色（#B8F5F5F7，无亮度跳变）+
         menuBorder 25% 白亮边；软件后端/WSL（!glassBlur）下 ①②隐藏、
         染色换不透明 menuFill + 深色 hairline 描边（可读优先）。
    本测试在软件后端上运行（glassBlur=false 路径），源码级 + 运行时
    双锁定；卡片仍零采样（分层靠材质对比，另见 test_shadow_policy_*）。
        """
        panel_src = QML_MAIN.read_text(encoding="utf-8")

        # ① 整窗单纹理架构（2026-09-09 v3）：源 = canvasRoot 自身 layer，
        #    模糊层/蒙版整窗大小、与源同原点 → 1:1 由构造保证（真机实测
        #    0/14300 位置锁定 + 溢出 0）；旧的多级快照几何不得回流
        assert "source: canvasRoot" in panel_src
        assert "layer.enabled: true" in panel_src
        assert "width: canvasRoot.width" in panel_src
        assert "sourceItem: stack" not in panel_src
        assert "sourceRect" not in panel_src
        assert "snapW" not in panel_src
        assert "plateBackdrop" not in panel_src

        # ② 高斯模糊 + 圆角 alpha 蒙版（直角 bug 根治的关鍵）+ 审计 §3.2/3.5
        #    参数（方案 A：核直径 48px ≈ 菜单宽 30–37%，保折射辨识度；
        #    saturation 补偿染色漂白；maskSpreadAtMin 柔化蒙版边缘）
        assert "import QtQuick.Effects" in panel_src
        assert "MultiEffect" in panel_src
        assert "maskEnabled: true" in panel_src
        # 裁切开关（2026-09-09 晚二诊：默认阈值窗 0..1 = 恒等映射，蒙版
        # 从不裁切 → 过采样模糊层整块透出 = 真机"两层玻璃"）
        assert "maskThresholdMin: 0.5" in panel_src
        assert "blurEnabled: true" in panel_src
        assert "blurMax: 32" in panel_src
        assert "blur: 0.75" in panel_src
        assert "saturation: 0.15" in panel_src
        assert "maskSpreadAtMin: 0.4" in panel_src
        # autoPadding 必须关（真机 1.25× DPR 实测：默认会拉伸蒙版到扩大
        # 边界 → 裁切比菜单大一圈 = "两层玻璃"；见 glass-recipe.md §2-5）
        assert "autoPaddingEnabled: false" in panel_src

        # ③ 双路径：GL 染色 menuTint / 软件回退不透明 menuFill；模糊层
        #    随 glassBlur 门控（软件后端隐藏即免采样）；亮边 GL = 25% 白
        #    menuBorder / 软件回退 = 深色 hairline
        assert "visible: Style.glassBlur" in panel_src
        assert "Style.menuTint" in panel_src
        assert "Style.menuFill" in panel_src
        # 双路径 + elevated 阶梯（gemini 终审）：染色块按 glassBlur/elevated
        # 分派；描边 GL = 8% 黑 hairline / 软件回退 = 深色 hairline
        assert "return Style.menuFill" in panel_src
        assert "plate.elevated ? Style.menuTintHi : Style.menuTint" in panel_src
        assert "return Style.menuFillBorder" in panel_src
        assert "plate.elevated ? Style.menuBorderHi : Style.menuBorder" in panel_src
        assert "MenuGlassPlate { id: aspectPlate; elevated: true }" in panel_src
        assert "MenuGlassPlate { id: barPlate; elevated: true }" in panel_src
        # 设备像素网格吸附（菜单位置落网格，文字光栅化清晰）+ 整窗单纹理
        # 架构（v3：对齐由构造保证，见 glass-recipe.md §2-6）
        assert "function snapGrid(v)" in panel_src
        assert "x = snapGrid(Math.max(4, Math.min(px" in panel_src
        assert "x: -plate.menuX" in panel_src

        # 旧方形采样层遗迹仍不得回流（卡片/胶囊永不做采样模糊）
        for gone in ("bgSource", "GlassFill"):
                assert gone not in panel_src, gone
        # 卡片视觉：半透明填充 + 亮边 + 卡圆角仍在（分层靠材质对比）
        assert "color: Style.cardFill" in panel_src
        assert "border.color: Style.cardBorder" in panel_src
        assert "radius: Style.cardRadius" in panel_src

        # Style 令牌：软件回退色不透明 #F7F7F9（可读性锁定，审计 §3.7）+
        # GL 霜面 72% 画布色 #B8F5F5F7（无亮度跳变）+ 亮边/回退描边令牌；
        # glassBlur 门控接 app.py 注入的 shadersUsable（WSL/软件后端 false）
        style_src = (QML_MAIN.parent / "Style.qml").read_text(encoding="utf-8")
        assert 'readonly property color menuFill: "#FFF7F7F9"' in style_src
        assert 'readonly property color menuTint: "#B8F5F5F7"' in style_src
        assert 'readonly property color menuBorder: "#14000000"' in style_src
        assert 'readonly property color menuTintHi: "#D0FFFFFF"' in style_src
        assert 'readonly property color menuBorderHi: "#24000000"' in style_src
        assert 'readonly property color menuFillBorder: "#1A000000"' in style_src
        assert "readonly property bool glassBlur" in style_src
        assert "shadersUsable" in style_src
        assert "MultiEffect" not in style_src

        # ---- 运行时（软件后端 = 回退路径在跑）----
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                scene = sip.cast(root, QQuickWindow).contentItem()

                def walk(item):
                        yield item
                        for child in item.childItems():
                                yield from walk(child)

                # 四枚菜单浮层（一级/两枚二级/镜像）同款毛玻璃底板
                for name in ("appContextMenu", "aspectSubmenu",
                             "barSubmenu", "mirrorContextMenu"):
                        menu = next(i for i in walk(scene)
                                    if i.objectName() == name)
                        multis = [i for i in walk(menu)
                                  if i.metaObject().className() == "QQuickMultiEffect"]
                        assert len(multis) == 1, name
                        me = multis[0]
                        assert me.property("maskEnabled") is True
                        assert float(me.property("maskThresholdMin")) == 0.5
                        assert me.property("blurEnabled") is True
                        assert me.property("blurMax") == 32
                        assert float(me.property("blur")) == 0.75
                        assert float(me.property("saturation")) == 0.15
                        assert float(me.property("maskSpreadAtMin")) == 0.4
                        assert me.property("autoPaddingEnabled") is False
                        # 软件路径：模糊层隐藏（不采样），染色 = 不透明 menuFill
                        assert me.property("visible") is False
                        tint = next(
                                i for i in walk(menu)
                                if i.metaObject().className() == "QQuickRectangle"
                                and i.property("color").name(
                                        QColor.NameFormat.HexArgb) == "#fff7f7f9")
                        assert float(tint.property("opacity")) == 1.0
                        # 模糊层源 = 画布包装层（整窗单纹理架构）
                        me_sources = me.property("source")
                        assert me_sources is not None
                        assert me_sources.objectName() == "canvasRoot"
                        chain = []
                        node = menu.parentItem()
                        while node is not None:
                                chain.append(node.objectName())
                                node = node.parentItem()
                        assert "canvasRoot" not in chain
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_menu_glass_snapshot_source_is_canvas_wrapper(qapp, no_adb, prefs_stub,
                                                      settings_file):
        """毛玻璃快照源 = 画布包装层 canvasRoot（2026-09-09 Opus 审计 方案 A
    核心修复）：原 sourceItem = stack 采到的是半透明白卡 + 透明黑底——高斯
    模糊对它凭空合成灰白雾（真机"莫名其妙的白光"）。canvasRoot 把不透明
    画布 bgLayer 与 stack 打包成快照源；canvasRoot 与 zoomLayer 同原点同
    尺寸（菜单 x/y 与 sourceRect 数学不变）；四枚菜单的快照源全部指向它、
    菜单自身不在其子树内（无自采样环）；sourceRect 钳制在源范围内（贴窗口
    边缘不越界采样——GL clamp-to-edge 条纹白带的防护）。源码级 + 运行时
    双锁定（与既有毛玻璃测试同风格）。
        """
        panel_src = QML_MAIN.read_text(encoding="utf-8")

        # ① 源 = canvasRoot 自身 layer（整窗单纹理，v3 架构）
        assert "source: canvasRoot" in panel_src
        assert "layer.enabled: true" in panel_src
        assert "sourceItem: stack" not in panel_src
        # ② canvasRoot 内：画布垫底（bgLayer 先声明）再铺页面（stack）
        assert panel_src.index("id: canvasRoot") \
                < panel_src.index("id: bgLayer") \
                < panel_src.index("id: stack")
        # ③ 整窗几何：模糊层/蒙版与源同大小同原点（x = -menuX 对位）
        assert "x: -plate.menuX" in panel_src
        assert "width: canvasRoot.width" in panel_src
        assert "sourceRect" not in panel_src

        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                scene = sip.cast(root, QQuickWindow).contentItem()

                # 画布根在视觉树里，铺满缩放层（同原点同尺寸——菜单坐标
                # 数学不变的前提）；stack（pageStack）在 canvasRoot 之内
                canvas = next(i for i in _walk(scene)
                              if i.objectName() == "canvasRoot")
                zoom = canvas.parentItem()
                assert (float(canvas.property("width")),
                        float(canvas.property("height"))) \
                        == (float(zoom.property("width")),
                            float(zoom.property("height")))
                stack = next(i for i in _walk(scene)
                             if i.objectName() == "pageStack")
                chain = []
                node = stack.parentItem()
                while node is not None:
                        chain.append(node.objectName())
                        node = node.parentItem()
                assert "canvasRoot" in chain

                # 四枚菜单的快照源全指向 canvasRoot；菜单不在 canvasRoot
                # 子树内（无自采样环）
                for name in ("appContextMenu", "aspectSubmenu",
                             "barSubmenu", "mirrorContextMenu"):
                        menu = next(i for i in _walk(scene)
                                    if i.objectName() == name)
                        me = next(
                                i for i in _walk(menu)
                                if "MultiEffect" in i.metaObject().className())
                        me_src = me.property("source")
                        assert me_src is not None and me_src.objectName() == "canvasRoot"
                        assert me.property("autoPaddingEnabled") is False
                        mchain = []
                        node = menu.parentItem()
                        while node is not None:
                                mchain.append(node.objectName())
                                node = node.parentItem()
                        assert "canvasRoot" not in mchain, name
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_shadow_policy_all_zero_no_shadow_rects():
        """铁律 8（定稿 2026-09-08）：全界面零阴影——QML 里矩形模拟的阴影
    在 GL 后端下就是贴上去的深色矩形（L1 多层 → L3 单层 #1A000000 均被
    真机否决），一律删净。主面板（含顶栏胶囊与右键/二级/镜像三个菜单
    浮层）不再出现任何阴影矩形/阴影色：分层只靠材质对比（cardFill 72% /
    flyoutFill 60% vs 画布）+ cardBorder 亮边；Toast 为深底胶囊，无阴影
    不加。Style.qml 是令牌单例，不得出现任何 Rectangle/阴影矩形（遗留
    cardShadow 色令牌仅供冻结的设置页旧投影引用）。源码级锁定（与采样
    模糊回归测试同风格）。
        """
        panel_src = QML_MAIN.read_text(encoding="utf-8")

        # 多层叠影组件与旧分层阴影色全部删净
        assert "SoftShadow" not in panel_src
        for gone in ("#05000000", "#06000000", "#07000000", "#08000000",
                     "#09000000", "#0A000000", "#1A000000"):
                assert gone not in panel_src, gone

        # 卡片分层仍靠材质对比：cardFill + 亮边 + 卡圆角（非阴影）
        assert "color: Style.cardFill" in panel_src
        assert "border.color: Style.cardBorder" in panel_src
        assert "radius: Style.cardRadius" in panel_src
        # 浮层同语言：flyoutFill + 亮边（胶囊/菜单板仍在，只是无阴影）
        assert "color: Style.flyoutFill" in panel_src

        # 令牌单例不得有阴影矩形（亦不应有任何 Rectangle 元素）
        style_src = (QML_MAIN.parent / "Style.qml").read_text(encoding="utf-8")
        assert "Rectangle" not in style_src


def test_small_dots_native_concentric_zero_border(qapp, no_adb, prefs_stub,
                                                  settings_file):
        """小圆点原生化（Windows 真机反馈②）：`Rectangle{radius:w/2;
    border.width:1}` 在小尺寸非整数像素下 border 会错（半像素跨边界/
    锯齿，偶发错误渲染）——直径 ≤8 的圆点一律同心两个原生实心 Rectangle
    叠加（外层 = 环色实心圆，内层 = 点色实心圆，内径 = 外径 − 2×环宽），
    零 border、全整数尺寸、anchors.centerIn 居中。源码级 + 渲染级双锁定。
        """
        panel_src = QML_MAIN.read_text(encoding="utf-8")

        # Dot 内联组件存在，且自身与内层都不设 border（原生实心圆）
        assert "component Dot: Rectangle" in panel_src
        dot_body = panel_src.split("component Dot: Rectangle", 1)[1] \
                .split("\n    component ", 1)[0]
        assert "border" not in dot_body
        # 四处小圆点全部走 Dot，不再有裸 Rectangle 圆点写法
        for name in ("menuCheckDot", "aspectPickDot", "chipDot", "deviceDot"):
                assert f'objectName: "{name}"' in panel_src, name
        for bare in ("width: 4; height: 4; radius: 2",
                     "width: 8; height: 8; radius: 4"):
                assert bare not in panel_src, bare

        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)

                # 设备状态点（绿点白环）：外层 10px 实心圆（radius 5）+
                # 内层 8px 点核（radius 4），同心居中 ((10−8)/2 = 1)，全整数
                dev = sip.cast(root.findChild(QObject, "deviceDot"), QQuickItem)
                assert (dev.property("width"), dev.property("height")) == (10, 10)
                assert dev.property("radius") == 5
                core = dev.childItems()[0]
                assert (core.property("width"), core.property("height")) == (8, 8)
                assert core.property("radius") == 4
                assert (core.x(), core.y()) == (1, 1)
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_layout_order_capsule_device_pinned_search_grid(qapp, no_adb, prefs_stub,
                                                        settings_file):
        """层级顺序（DESIGN.md §3，无标题行版）与 4px 节奏：设备卡自胶囊
        下方起排，镜像卡居中，固定卡出现把镜像/搜索/网格下推。"""
        controller = PanelController("/nonexistent/adb-for-tests")
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                grid_item = sip.cast(_grid(root), QQuickItem)
                panel = sip.cast(grid_item.property("parent"), QQuickItem)

                def top_of(name) -> float:
                        item = sip.cast(root.findChild(QObject, name), QQuickItem)
                        return item.mapToItem(panel, QPointF(0, 0)).y()

                # 顶栏胶囊：16 下悬，不与内容重叠；无标题行，设备卡直接在
                # 胶囊（16+32）下方留 16 间距起排
                assert top_of("topCapsule") == 16
                assert top_of("deviceCard") == 64
                # 无置顶：固定卡折叠，镜像卡直接贴设备卡（12px 间距），
                # 搜索再贴镜像卡（64+12）
                assert top_of("mirrorCard") == 64 + 76 + 12   # 152
                assert root.findChild(QObject, "mirrorCard").property("height") == 64
                assert top_of("searchCapsule") == 152 + 64 + 12   # 228
                search_top = top_of("searchCapsule")
                grid_top = top_of("appsGrid")
                assert grid_top == search_top + 36 + 16
                # 全宽：搜索与设备卡同宽（页面左右留白 20）
                device = sip.cast(root.findChild(QObject, "deviceCard"), QQuickItem)
                search = sip.cast(root.findChild(QObject, "searchCapsule"), QQuickItem)
                mirror = sip.cast(root.findChild(QObject, "mirrorCard"), QQuickItem)
                assert search.property("width") == device.property("width") \
                        == mirror.property("width") \
                        == panel.property("width") - 40

                # 置顶后：设备卡 → 固定卡(152) → 镜像卡 → 搜索 → 网格，
                # 间距全为 4 的倍数
                controller.togglePin("tv.danmaku.bili")
                _pump(250)
                stops = [top_of("deviceCard"), top_of("pinnedCard"),
                         top_of("mirrorCard"), top_of("searchCapsule"),
                         top_of("appsGrid")]
                assert stops == sorted(stops)
                assert stops == [64, 152, 232, 308, 360]
                assert all(v % 4 == 0 for v in stops)
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


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


def test_settings_page_loads_defaults_and_dropped_controls_gone(settings_page):
        """默认值回填；被删控件（DPI 数字框 / 圆角三选一 + 滑块）不再出现
        （DESIGN.md §3.8：隐形透传另测）。"""
        adb_field = settings_page.findChild(QObject, "adbPathField")
        glass = settings_page.findChild(QObject, "glassSwitch")
        assert adb_field is not None and glass is not None
        assert adb_field.property("text") == ""
        assert glass.property("checked") is True
        for gone in ("cornerSlider", "dpiAutoSwitch", "dpiBox"):
                assert settings_page.findChild(QObject, gone) is None, gone


def test_settings_page_save_accepts_with_real_api(settings_page):
        """saveChanges() over valid values emits accepted() (Main pops on it)."""
        accepted: list[bool] = []
        settings_page.accepted.connect(lambda: accepted.append(True))
        adb_field = settings_page.findChild(QObject, "adbPathField")
        assert adb_field is not None
        adb_field.setProperty("text", r"C:\工具\adb.exe")
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


def test_settings_page_passes_through_dropped_settings(settings_page,
                                                      settings_file):
        """隐形透传：dpi / corner_mode / corner_size_dip 读入 → collect 原样带回
        （SettingsApi.save 按整表构造，丢键即重置）。"""
        api = SettingsApi()
        assert api.save({
                "scrcpy_path": "", "adb_path": "", "fps": 60, "bitrate_mbps": 30,
                "dpi": 400, "corner_mode": "g2", "corner_size_dip": 72,
                "glass_enabled": True,
        }) == []
        meta = settings_page.metaObject()
        assert meta.indexOfMethod("reloadFromApi()") >= 0
        meta.invokeMethod(settings_page, "reloadFromApi")
        _pump(30)
        assert settings_page.property("dpiPass") == 400
        assert settings_page.property("cornerModePass") == "g2"
        assert settings_page.property("cornerSizePass") == 72

        accepted: list[bool] = []
        settings_page.accepted.connect(lambda: accepted.append(True))
        meta.invokeMethod(settings_page, "saveChanges")
        assert accepted == [True]
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["dpi"] == 400
        assert raw["corner_mode"] == "g2"
        assert raw["corner_size_dip"] == 72


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


# --------------------------------------------- ⑩ 投屏质量设置项（新）


def test_settings_page_quality_defaults_load(settings_page):
        """Defaults: codec auto, audio latest, screen-off switch off."""
        switch = settings_page.findChild(QObject, "turnScreenOffSwitch")
        assert switch is not None
        assert settings_page.property("videoCodec") == "auto"
        assert settings_page.property("audioPolicy") == "latest"
        assert settings_page.property("turnScreenOff") is False
        assert switch.property("checked") is False


def test_settings_page_quality_roundtrip_via_real_api(settings_page, settings_file):
        """Setting the three properties and saving persists each field."""
        accepted: list[bool] = []
        settings_page.accepted.connect(lambda: accepted.append(True))
        settings_page.setProperty("videoCodec", "h265")
        settings_page.setProperty("audioPolicy", "off")
        settings_page.setProperty("turnScreenOff", True)
        meta = settings_page.metaObject()
        meta.invokeMethod(settings_page, "saveChanges")
        assert accepted == [True]
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["video_codec"] == "h265"
        assert raw["audio_policy"] == "off"
        assert raw["turn_screen_off"] is True

        # A fresh page reloads the saved values (reloadFromApi on completion).
        api = SettingsApi()
        loaded = api.load()
        assert loaded["video_codec"] == "h265"
        assert loaded["audio_policy"] == "off"
        assert loaded["turn_screen_off"] is True


def test_settings_page_quality_switch_toggles_state(settings_page):
        """The screen-off switch mirrors the page property both ways."""
        switch = settings_page.findChild(QObject, "turnScreenOffSwitch")
        settings_page.setProperty("turnScreenOff", True)
        _pump(20)
        assert switch.property("checked") is True
        settings_page.setProperty("turnScreenOff", False)
        _pump(20)
        assert switch.property("checked") is False


# --------------------------------------------- ⑪ 窗口栏（上巴/下巴模式）


def test_settings_page_window_bar_defaults_load(settings_page):
        """窗口栏组控件齐备（下巴含不显示三段）；页面属性随新默认：上巴
        immersive、下巴 none（2026-09-09：scrcpy 右键已是返回）。"""
        assert settings_page.findChild(QObject, "windowBarCard") is not None
        for name in ("topBarImmersive", "topBarNative",
                     "bottomBarImmersive", "bottomBarNative", "bottomBarNone"):
                assert settings_page.findChild(QObject, name) is not None, name
        assert settings_page.property("topBarMode") == "immersive"
        assert settings_page.property("bottomBarMode") == "none"


def test_settings_page_window_bar_roundtrip_via_real_api(
        settings_page, settings_file
):
        """点切两行（上巴系统/下巴不显示）→ 保存 → settings.json 落盘两字段；
    重开页面回读。"""
        accepted: list[bool] = []
        settings_page.accepted.connect(lambda: accepted.append(True))
        settings_page.findChild(QObject, "topBarNative").click()
        settings_page.findChild(QObject, "bottomBarNone").click()
        _pump(20)
        meta = settings_page.metaObject()
        meta.invokeMethod(settings_page, "saveChanges")
        assert accepted == [True]
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["top_bar_mode"] == "native"
        assert raw["bottom_bar_mode"] == "none"

        api = SettingsApi()
        loaded = api.load()
        assert loaded["top_bar_mode"] == "native"
        assert loaded["bottom_bar_mode"] == "none"


def test_settings_page_window_bar_buttons_toggle_state(settings_page):
        """分段按钮镜像页面属性：三枚举各自独立可切（含下巴·不显示）。"""
        settings_page.findChild(QObject, "topBarNative").click()
        settings_page.findChild(QObject, "bottomBarNative").click()
        _pump(20)
        assert settings_page.property("topBarMode") == "native"
        assert settings_page.property("bottomBarMode") == "native"
        settings_page.findChild(QObject, "bottomBarNone").click()
        _pump(20)
        assert settings_page.property("bottomBarMode") == "none"
        settings_page.findChild(QObject, "topBarImmersive").click()
        _pump(20)
        assert settings_page.property("topBarMode") == "immersive"
        assert settings_page.property("bottomBarMode") == "none"


# --------------------------------------------- ⑫ 音频独占（右键菜单勾选）


def test_context_menu_audio_exclusive_checkable(qapp, no_adb, prefs_stub,
                                                settings_file):
    """「音频独占」一级勾选行居「窗口栏 ▸」之后：点击 = setAudioExclusive
    （写 audio 节）+ 勾选圆点即时刷新（audioPrefsChanged），菜单保持开；
    再点一次取消（空壳节退场）。"""
    controller = PanelController("/nonexistent/adb-for-tests")
    engine = _make_engine(controller, SettingsApi())
    try:
        root = engine.rootObjects()[0]
        _pump(150)
        grid = _grid(root)
        grid.forceLayout()
        _pump(50)
        menu = root.findChild(QObject, "appContextMenu")
        row = sip.cast(root.findChild(QObject, "menuAudioExclusive"), QQuickItem)
        assert row is not None
        assert row.property("text") == "音频独占"

        # 一级菜单列内序：窗口栏 ▸ 之后（同一 Column 的 y 序）
        bar_row = sip.cast(root.findChild(QObject, "menuBarModes"), QQuickItem)
        assert float(row.property("y")) > float(bar_row.property("y"))

        tile = _find_delegate(root, grid, "tv.danmaku.bili")
        _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
        _pump(200)
        assert menu.property("openState") is True
        dot = next(i for i in _walk(row) if i.objectName() == "menuCheckDot")
        assert dot.property("visible") is False   # 默认不独占

        # 勾选：写 prefs audio 节 + 圆点即时可见 + 菜单不收
        row.click()
        _pump(120)
        assert menu.property("openState") is True
        assert dot.property("visible") is True
        assert json.loads(prefs_stub.payload)["audio"] \
            == {"tv.danmaku.bili": {"exclusive": True}}
        assert "独占" in controller.statusText

        # 再点：取消独占，圆点灭，包条目退场（不存空壳节）
        row.click()
        _pump(120)
        assert menu.property("openState") is True
        assert dot.property("visible") is False
        assert json.loads(prefs_stub.payload)["audio"] == {}

        # 重新打开菜单：勾选态按持久化记忆拉取（audioExclusiveFor）
        controller.setAudioExclusive("tv.danmaku.bili", True)
        _pump(80)
        _key(qapp, root, Qt.Key.Key_Escape)
        _pump(250)
        _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
        _pump(200)
        assert dot.property("visible") is True
    finally:
        controller.shutdown()
        engine.deleteLater()
        _pump(20)


def test_context_menu_keep_vd_checkable(qapp, no_adb, prefs_stub,
                                         settings_file):
    """「断开保留画面」一级勾选行居「音频独占」之后：点击 = setKeepVd
    （写 behavior 节）+ 勾选圆点即时刷新（behaviorPrefsChanged），菜单
    保持开；再点一次取消（空壳节退场）。"""
    controller = PanelController("/nonexistent/adb-for-tests")
    engine = _make_engine(controller, SettingsApi())
    try:
        root = engine.rootObjects()[0]
        _pump(150)
        grid = _grid(root)
        grid.forceLayout()
        _pump(50)
        menu = root.findChild(QObject, "appContextMenu")
        row = sip.cast(root.findChild(QObject, "menuKeepVd"), QQuickItem)
        assert row is not None
        assert row.property("text") == "断开保留画面"

        # 一级菜单列内序：音频独占之后（同一 Column 的 y 序）
        audio_row = sip.cast(
            root.findChild(QObject, "menuAudioExclusive"), QQuickItem)
        assert float(row.property("y")) > float(audio_row.property("y"))

        tile = _find_delegate(root, grid, "tv.danmaku.bili")
        _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
        _pump(200)
        assert menu.property("openState") is True
        dot = next(i for i in _walk(row) if i.objectName() == "menuCheckDot")
        assert dot.property("visible") is False   # 默认断开即退回主屏

        # 勾选：写 prefs behavior 节 + 圆点即时可见 + 菜单不收
        row.click()
        _pump(120)
        assert menu.property("openState") is True
        assert dot.property("visible") is True
        assert json.loads(prefs_stub.payload)["behavior"] \
            == {"tv.danmaku.bili": {"keep_vd": True}}
        assert "保留在虚拟屏" in controller.statusText

        # 再点：取消保留，圆点灭，包条目退场（不存空壳节）
        row.click()
        _pump(120)
        assert menu.property("openState") is True
        assert dot.property("visible") is False
        assert json.loads(prefs_stub.payload)["behavior"] == {}

        # 重新打开菜单：勾选态按持久化记忆拉取（keepVdFor）
        controller.setKeepVd("tv.danmaku.bili", True)
        _pump(80)
        _key(qapp, root, Qt.Key.Key_Escape)
        _pump(250)
        _mouse(qapp, root, tile, Qt.MouseButton.RightButton)
        _pump(200)
        assert dot.property("visible") is True
    finally:
        controller.shutdown()
        engine.deleteLater()
        _pump(20)


# --------------------------------------------- ⑬ 镜像卡媒体音量条


def _mouse_at(qapp, root, item: QQuickItem, fraction: float) -> None:
    """Deliver a real mouse click at ``fraction`` of the item's width."""
    window = sip.cast(root, QQuickWindow)
    point = item.mapToScene(QPointF(
        float(item.property("width")) * fraction,
        float(item.property("height")) / 2,
    ))
    qapp.sendEvent(window, QMouseEvent(
        QEvent.Type.MouseButtonPress, point, point,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    qapp.sendEvent(window, QMouseEvent(
        QEvent.Type.MouseButtonRelease, point, point,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier))


def test_mirror_menu_bar_defaults(qapp, no_adb, prefs_stub, settings_file):
        """2026-09-09 用户反馈：镜像卡右键菜单不够完整——把设置页的
        窗口栏默认（上巴 沉浸/系统；下巴 沉浸/系统/不显示）搬进来。

        设备镜像无应用包 → setDefaultBarMode 写的是 settings.json 的
        top/bottom_bar_mode（设置页同一对字段）；选中圆点 = 当前默认；
        选择后菜单关闭、状态栏报出、落盘可读。
        """
        from duo.core import settings as settings_mod

        settings_mod.save_settings(settings_mod.Settings())
        controller = _MirrorSpyController("/nonexistent/adb-for-tests")
        _bring_online(controller)
        engine = _make_engine(controller, SettingsApi())
        try:
                root = engine.rootObjects()[0]
                _pump(150)
                card = sip.cast(root.findChild(QObject, "mirrorCard"), QQuickItem)

                _mouse(qapp, root, card, Qt.MouseButton.RightButton)
                _pump(200)

                # 选项集与设置页对齐：上巴 2 项、下巴 3 项
                for name, text in (
                        ("mirrorTopImmersive", "沉浸"),
                        ("mirrorTopNative", "系统"),
                        ("mirrorBottomImmersive", "沉浸"),
                        ("mirrorBottomNative", "系统"),
                        ("mirrorBottomNone", "不显示")):
                        row = sip.cast(root.findChild(QObject, name), QQuickItem)
                        assert row is not None, name
                        assert row.property("text") == text

                # 默认：上巴沉浸、下巴不显示 → 圆点位置
                def dot_visible(name: str) -> bool:
                        row = sip.cast(root.findChild(QObject, name), QQuickItem)
                        dot = next(i for i in _walk(row)
                                   if i.objectName() == "menuCheckDot")
                        return bool(dot.property("visible"))

                assert dot_visible("mirrorTopImmersive") is True
                assert dot_visible("mirrorTopNative") is False
                assert dot_visible("mirrorBottomNone") is True
                assert dot_visible("mirrorBottomImmersive") is False

                # 选「上巴：系统」→ 落盘 + 关菜单 + 状态提示
                sip.cast(root.findChild(QObject, "mirrorTopNative"),
                         QQuickItem).click()
                _pump(120)
                raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
                assert raw["top_bar_mode"] == "native"
                assert raw["bottom_bar_mode"] == "none"   # 未动的字段原样
                assert controller.statusText == "默认上巴将使用系统栏"
                menu = root.findChild(QObject, "mirrorContextMenu")
                assert menu.property("openState") is False

                # 再开：圆点跟随新默认；改下巴 = 沉浸
                _mouse(qapp, root, card, Qt.MouseButton.RightButton)
                _pump(200)
                assert dot_visible("mirrorTopNative") is True
                assert dot_visible("mirrorTopImmersive") is False
                sip.cast(root.findChild(QObject, "mirrorBottomImmersive"),
                         QQuickItem).click()
                _pump(120)
                raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
                assert raw["bottom_bar_mode"] == "immersive"
                assert raw["top_bar_mode"] == "native"
                assert controller.statusText == "默认下巴将使用沉浸栏"
        finally:
                controller.shutdown()
                engine.deleteLater()
                _pump(20)


def test_mirror_card_media_volume_slider(qapp, no_adb, prefs_stub,
                                         settings_file):
    """镜像卡音量条：离线隐藏；在线为 16 高滑杆（轨道 4px 圆角、accent
    填充、拇指 12px 圆点零描边、无文字标签）；未知态无填充无拇指；拖动
    实时视觉 + 200ms 防抖后 setMediaVolume；Accessible.name=媒体音量。"""
    controller = _VolumeSpyController("/nonexistent/adb-for-tests")
    engine = _make_engine(controller, SettingsApi())
    try:
        root = engine.rootObjects()[0]
        _pump(150)
        slider = sip.cast(root.findChild(QObject, "mediaVolumeSlider"),
                          QQuickItem)
        assert slider is not None

        # 源码合同：Accessible.name=媒体音量（无文字标签的可访问名）
        src = QML_MAIN.read_text(encoding="utf-8")
        assert 'Accessible.name: "媒体音量"' in src

        # 设备离线：隐藏（扬声器图标同隐——与滑杆同一可见性）
        assert slider.property("visible") is False
        glyph = sip.cast(root.findChild(QObject, "mediaVolumeGlyph"), QQuickItem)
        assert glyph is not None
        assert glyph.property("visible") is False

        # 在线：出现；度量 = 16 高 / 轨道 4px 圆角 2 / 拇指 12px 圆点
        _bring_online(controller)
        _pump(120)
        assert slider.property("visible") is True
        # 2026-09-09 用户反馈“滑杆看起来像画质调节”：加单色扬声器图标
        assert glyph.property("visible") is True
        assert (float(glyph.property("width")), float(glyph.property("height"))) \
            == (14, 14)
        assert "ctx.arc(8.5, 7, 3, -0.85, 0.85)" in src
        assert float(slider.property("implicitHeight")) == 16
        track = next(i for i in _walk(slider) if i.objectName() == "volumeTrack")
        assert (float(track.property("height")), float(track.property("radius"))) \
            == (4, 2)
        thumb = next(i for i in _walk(slider) if i.objectName() == "volumeThumb")
        assert (float(thumb.property("width")), float(thumb.property("height")),
                float(thumb.property("radius"))) == (12, 12, 6)
        # 零描边：拇指块源码内不声明任何 border（Rectangle 默认 0）
        handle_src = src.split('objectName: "volumeThumb"', 1)[1].split("}", 1)[0]
        assert "border" not in handle_src
        assert not [i for i in _walk(slider)
                    if i.metaObject().className() == "QQuickText"]   # 无文字标签

        # 未知态（mediaVolume=-1，不预读）：无填充、无拇指
        fill = track.childItems()[-1]
        assert float(fill.property("width")) == 0
        assert thumb.property("visible") is False

        # 拖到 3/4 处：视觉实时（填充/拇指跟上手指），200ms 防抖后落命令
        _mouse_at(qapp, root, slider, 0.75)
        _pump(80)
        assert thumb.property("visible") is True
        assert float(fill.property("width")) > 0
        assert controller.volume_calls == []   # 防抖窗口内不起命令
        _pump(260)
        assert len(controller.volume_calls) == 1
        assert controller.volume_calls[0] == pytest.approx(11, abs=1)

        # 再拖到 1/4 处：又一次防抖落命令（值变小）
        _mouse_at(qapp, root, slider, 0.25)
        _pump(340)
        assert len(controller.volume_calls) == 2
        assert controller.volume_calls[1] < controller.volume_calls[0]

        # 设备离线：滑杆隐藏
        controller._devicesPolled.emit({"S1": "offline"})
        _pump(120)
        assert slider.property("visible") is False
    finally:
        controller.shutdown()
        engine.deleteLater()
        _pump(20)

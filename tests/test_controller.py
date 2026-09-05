"""PanelController: bindable launcher state (devices, sessions, status).

Runs headless via QT_QPA_PLATFORM=offscreen; adb, threads and process spawns
are faked, so controller behaviour is deterministic. The stub device monitor
never threads, which means the controller's private hop signals deliver
synchronously (direct connection) when tests raise them on the GUI thread.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QUrl  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import duo.ui.controller as controller_mod  # noqa: E402
from duo.ui.controller import (  # noqa: E402
        APP_CATALOG,
        MIRROR_KEY,
        PanelController,
        build_device_mirror_argv,
        build_launch_argv,
)


class _StubMonitor:
        """DeviceMonitor stand-in: no threads, no adb, deterministic state."""

        instances: list[_StubMonitor] = []

        def __init__(self, on_change, query=None, adb_binary=None, poll_interval_s=2.0):
                self.on_change = on_change
                self.adb_binary = adb_binary
                self.started = False
                self.stopped = False
                self.online: list[str] = []
                self._states: dict[str, str] = {}
                _StubMonitor.instances.append(self)

        def set_states(self, states: dict[str, str]) -> None:
                """Pretend a poll happened; ``online`` follows the map."""
                self._states = dict(states)
                self.online = [s for s, state in states.items() if state == "device"]

        @property
        def states(self) -> dict[str, str]:
                return dict(self._states)

        def poll_now(self) -> None:
                self.on_change(self.states)

        def start(self) -> None:
                self.started = True

        def stop(self) -> None:
                self.stopped = True


class _StubPrefsFile:
        """Minimal path duck-type for controller._prefs_path patching."""

        def __init__(self) -> None:
                self.payload: str | None = None
                self.parent = Path(".")

        def read_text(self, encoding: str = "utf-8") -> str:
                if self.payload is None:
                        raise OSError("missing")
                return self.payload

        def write_text(self, text: str, encoding: str = "utf-8") -> None:
                self.payload = text

        def mkdir(self, parents: bool = True, exist_ok: bool = True) -> None:
                pass


class _FakeProc:
        """subprocess.Popen stand-in: never a real process."""

        def __init__(self, argv: list[str], exit_code: int | None = None) -> None:
                self.argv = argv
                self.terminated = False
                self._exit_code = exit_code

        def poll(self) -> int | None:
                return self._exit_code

        def terminate(self) -> None:
                self.terminated = True


@pytest.fixture()
def qapp():
        """Ensure exactly one QApplication exists (offscreen)."""
        app = QApplication.instance() or QApplication([])
        yield app


@pytest.fixture()
def prefs_stub(monkeypatch):
        """Portrait prefs live in a stub file (never the real data dir)."""
        stub = _StubPrefsFile()
        monkeypatch.setattr(controller_mod, "_prefs_path", lambda: stub)
        return stub


@pytest.fixture()
def no_adb(monkeypatch):
        """Stub the device monitor and the installed-package lookup."""
        _StubMonitor.instances = []
        monkeypatch.setattr(controller_mod, "DeviceMonitor", _StubMonitor)
        checked: list[str] = []

        def fake_resolve_installed(adb_binary, done):
                checked.append(adb_binary)
                done(set())

        monkeypatch.setattr(controller_mod, "_resolve_installed", fake_resolve_installed)
        return checked


# ------------------------------------------------------------- QML app model


def test_apps_model_starts_from_catalog(no_adb, prefs_stub, qapp):
        """The QML grid model lists the catalog with installed flags."""
        controller = PanelController("/fake/adb.exe")
        assert [entry["package"] for entry in controller.apps] == [
                package for _, package in APP_CATALOG
        ]
        assert all(entry["installed"] is False for entry in controller.apps)
        assert all(entry["icon"] == "" for entry in controller.apps)


def test_apps_model_tracks_installed_icons_and_extras(no_adb, prefs_stub, qapp):
        """Installed flags, icon URLs and third-party apps all reach the model."""
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit({"tv.danmaku.bili"})
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["tv.danmaku.bili"]["installed"] is True
        assert by_package["com.tencent.mm"]["installed"] is False

        # iconReady hops a Path in; the model stores a QML-ready file URL.
        controller.iconReady.emit("tv.danmaku.bili", Path("/tmp/bili.png"))
        by_package = {e["package"]: e for e in controller.apps}
        expected = QUrl.fromLocalFile("/tmp/bili.png").toString()
        assert by_package["tv.danmaku.bili"]["icon"] == expected

        # A third-party listing extends the model; app info fills the label.
        controller.allAppsReady.emit(["org.foo.bar"])
        assert "org.foo.bar" in {e["package"] for e in controller.apps}
        # The batch hop patches the model and rebuilds once (not per app).
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        controller.appInfoReady.emit([("org.foo.bar", None, "Bar 应用")])
        assert len(rebuilds) == 1
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["org.foo.bar"]["label"] == "Bar 应用"

        # A later install poll rebuilds the catalog but keeps extras + icons.
        controller._installedResolved.emit(set())
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["tv.danmaku.bili"]["installed"] is False
        assert by_package["tv.danmaku.bili"]["icon"] == expected
        assert by_package["org.foo.bar"]["label"] == "Bar 应用"


def test_toggle_portrait_does_not_rebuild_app_model(no_adb, prefs_stub, qapp):
        """The long-press portrait path never touches the apps model.

        The QML QVariantList grid rebuilds every delegate per
        ``appsChanged`` emit (async icons blank out); togglePortrait only
        flips the pref + status, so no rebuild may ride along.
        """
        controller = PanelController("/fake/adb.exe")
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        controller.togglePortrait("tv.danmaku.bili")
        assert rebuilds == []
        assert controller.portraitFor("tv.danmaku.bili") is True


def test_icon_burst_notifies_once(no_adb, prefs_stub, qapp):
        """An icon sweep flushes as ONE appsChanged emit, not one per icon.

        The old per-icon emits fired a 5x full-grid rebuild storm at every
        startup - tiles visibly vanished under a held press (async images
        blank while delegates recycle). The deferred flush restores the
        batch contract the app-info path already follows.
        """
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit({"tv.danmaku.bili", "com.tencent.mm"})
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        for package in ("tv.danmaku.bili", "com.tencent.mm", "cn.wps.moffice_eng"):
                controller.iconReady.emit(package, Path(f"/tmp/{package}.png"))
        QApplication.processEvents()   # let the single-shot flush timer fire
        assert len(rebuilds) == 1
        by_package = {e["package"]: e for e in controller.apps}
        expected = QUrl.fromLocalFile("/tmp/tv.danmaku.bili.png").toString()
        assert by_package["tv.danmaku.bili"]["icon"] == expected
        assert by_package["com.tencent.mm"]["icon"] == \
                QUrl.fromLocalFile("/tmp/com.tencent.mm.png").toString()
        # A later, separate burst notifies exactly once more.
        controller.iconReady.emit("cn.wps.moffice_eng", None)   # no patch, no emit
        controller.iconReady.emit("tv.danmaku.bili", Path("/tmp/bili-2.png"))
        QApplication.processEvents()
        assert len(rebuilds) == 2
        assert by_package["tv.danmaku.bili"]["icon"] == \
                QUrl.fromLocalFile("/tmp/bili-2.png").toString()


# ------------------------------------------------------------------ defaults


def test_controller_defaults(no_adb, prefs_stub, qapp):
        """A fresh controller: empty devices/sessions, ready status, unlocked."""
        controller = PanelController("/fake/adb.exe")
        assert controller.devices == []
        assert controller.statusText == "就绪"
        assert controller.runningSessions == []
        assert controller.adbBinary == "/fake/adb.exe"
        assert not controller.engineLocked
        # Polling is up; the install check ran once against our adb.
        monitor = _StubMonitor.instances[-1]
        assert monitor.started
        assert no_adb == ["/fake/adb.exe"]


def test_devices_property_shape_and_signal(no_adb, prefs_stub, qapp):
        """The devices property lists serial + state text per adb state."""
        controller = PanelController("/fake/adb.exe")
        seen: list[list] = []
        controller.devicesChanged.connect(seen.append)
        controller._devicesPolled.emit({"S1": "device", "S2": "unauthorized"})
        assert controller.devices == [
                {
                        "serial": "S1",
                        "state": "device",
                        "stateText": "在线",
                        "online": True,
                },
                {
                        "serial": "S2",
                        "state": "unauthorized",
                        "stateText": "未授权 USB 调试",
                        "online": False,
                },
        ]
        assert seen and seen[-1] == controller.devices


# ---------------------------------------------------------------- argv reuse


def test_start_session_reuses_build_launch_argv(no_adb, prefs_stub, qapp, monkeypatch):
        """Spawning goes through build_launch_argv (chrome/serial/portrait)."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        monkeypatch.setattr(controller, "_spawn", fake_spawn)
        controller.startSession("tv.danmaku.bili")
        assert spawned == [build_launch_argv("tv.danmaku.bili", "S1", portrait=False)]
        entry = controller.runningSessions[0]
        assert entry["key"] == "tv.danmaku.bili"
        assert entry["label"] == "哔哩哔哩"
        assert entry["running"] is True
        assert entry["portrait"] is False
        assert controller.engineLocked


def test_start_session_without_device_sets_status(no_adb, prefs_stub, qapp):
        """No online serial: a status message, no session, nothing locked."""
        controller = PanelController("/fake/adb.exe")
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startSession("tv.danmaku.bili")
        assert statuses == ["设备未连接"]
        assert controller.runningSessions == []
        assert not controller.engineLocked


def test_device_mirror_session_uses_mirror_argv(no_adb, prefs_stub, qapp, monkeypatch):
        """startMirror spawns build_device_mirror_argv under the mirror key."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        monkeypatch.setattr(controller, "_spawn", fake_spawn)
        controller.startMirror()
        assert spawned == [build_device_mirror_argv("S1")]
        entry = controller.runningSessions[0]
        assert entry["key"] == MIRROR_KEY
        assert entry["label"] == "设备镜像"

        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startMirror()
        assert statuses == ["设备镜像已在运行"]
        assert len(spawned) == 1


# ------------------------------------------------------ start / stop / reap


def _spawn_recorder(controller, procs):
        def fake_spawn(argv):
                proc = _FakeProc(argv)
                procs.append(proc)
                return proc

        controller._spawn = fake_spawn  # type: ignore[method-assign]


def test_start_stop_reap_session(no_adb, prefs_stub, qapp, monkeypatch):
        """stop terminates; the session lingers until the reaper drops it."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.startSession("tv.danmaku.bili")
        controller.stopSession("tv.danmaku.bili")
        assert procs[0].terminated
        # terminate() is asynchronous: still counted while the process lives.
        assert controller.engineLocked
        assert len(controller.runningSessions) == 1

        procs[0]._exit_code = 0
        assert controller.reapSessions() == 1
        assert controller.runningSessions == []
        assert not controller.engineLocked
        # Stopping an unknown key is a quiet no-op.
        controller.stopSession("no.such.package")
        assert controller.reapSessions() == 0


def test_engine_locked_tracks_sessions(no_adb, prefs_stub, qapp, monkeypatch):
        """engineLocked flips with the live session map."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        assert not controller.engineLocked
        controller.startSession("tv.danmaku.bili")
        assert controller.engineLocked
        controller.startMirror()
        assert controller.activeSessionCount() == 2
        for proc in procs:
                proc._exit_code = 0
        controller.reapSessions()
        assert not controller.engineLocked
        assert controller.activeSessionCount() == 0


def test_duplicate_start_reports_running(no_adb, prefs_stub, qapp, monkeypatch):
        """Starting a live session twice reports instead of double-spawning."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.startSession("tv.danmaku.bili")
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startSession("tv.danmaku.bili")
        assert statuses == ["哔哩哔哩 已在运行"]
        assert len(procs) == 1


# -------------------------------------------------------------------- status


def test_status_signal_fires_on_state_changes(no_adb, prefs_stub, qapp, monkeypatch):
        """Launch and portrait changes surface through statusChanged."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startSession("cn.com.langeasy.LangEasyLexis")
        assert statuses[-1] == "已启动 不背单词 · 竖屏"   # DEFAULT_PORTRAIT
        controller.togglePortrait("cn.com.langeasy.LangEasyLexis")
        assert statuses[-1] == "不背单词 将以横屏启动"
        controller.stopSession("cn.com.langeasy.LangEasyLexis")
        assert statuses[-1] == "已关闭 不背单词"


# ------------------------------------------------------------------- portrait


def test_portrait_prefs_roundtrip(no_adb, prefs_stub, qapp):
        """togglePortrait persists; a fresh controller reads the choice back."""
        controller = PanelController("/fake/adb.exe")
        assert controller.portraitFor("tv.danmaku.bili") is False
        controller.togglePortrait("tv.danmaku.bili")
        assert controller.portraitFor("tv.danmaku.bili") is True
        assert prefs_stub.payload is not None
        saved = json.loads(prefs_stub.payload)
        assert saved["portrait"]["tv.danmaku.bili"] is True

        second = PanelController("/fake/adb.exe")
        assert second.portraitFor("tv.danmaku.bili") is True
        # Catalog defaults survive the save/load round trip.
        assert second.portraitFor("cn.com.langeasy.LangEasyLexis") is True

        second.togglePortrait("tv.danmaku.bili")   # flips back, persists again
        assert second.portraitFor("tv.danmaku.bili") is False
        assert json.loads(prefs_stub.payload)["portrait"]["tv.danmaku.bili"] is False


# ------------------------------------------------------------------ adb swap


def test_set_adb_rebuilds_monitor(no_adb, prefs_stub, qapp):
        """Same path keeps the monitor; a moved adb stops and rebuilds it."""
        controller = PanelController("/old/adb.exe")
        old_monitor = _StubMonitor.instances[-1]
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.setAdb("/old/adb.exe")
        assert controller.adbBinary == "/old/adb.exe"
        assert statuses == ["设置已保存，新会话生效"]
        assert not old_monitor.stopped

        controller.setAdb("/new/adb.exe")
        assert controller.adbBinary == "/new/adb.exe"
        assert old_monitor.stopped
        new_monitor = _StubMonitor.instances[-1]
        assert new_monitor is not old_monitor
        assert new_monitor.started
        assert statuses[-1] == "设置已保存，已切换 adb，新会话生效"
        # refreshInstalled re-ran against the new binary.
        assert no_adb == ["/old/adb.exe", "/new/adb.exe"]


def test_resolve_adb_uses_settings_probe_fallback(no_adb, prefs_stub, qapp, monkeypatch):
        """resolveAdb resolves off-thread (settings > probe > fallback) and applies."""
        controller = PanelController("/old/adb.exe")
        monkeypatch.setattr(
                controller_mod, "load_settings", lambda: (SimpleNamespace(), ["boom"])
        )
        monkeypatch.setattr(
                controller_mod,
                "probe",
                lambda name: SimpleNamespace(path="/discovered/adb.exe"),
        )
        monkeypatch.setattr(
                controller_mod,
                "resolve_adb_path",
                lambda settings, discovered, fallback: "/resolved/adb.exe",
        )
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.resolveAdb()
        deadline = time.monotonic() + 5.0
        while controller.adbBinary != "/resolved/adb.exe" and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
        assert controller.adbBinary == "/resolved/adb.exe"
        assert "boom" in statuses   # settings problems surfaced once
        assert statuses[-1] == "设置已保存，已切换 adb，新会话生效"

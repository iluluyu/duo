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

import duo.ui.controller as controller_mod
from duo.core.apps import AdbError
from duo.core.settings import Settings
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

        def wait(self, timeout: float | None = None) -> int | None:
                # The audio-restart path waits for the CLI to release the
                # audio lock; the fake exits immediately.
                return self._exit_code


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
def no_adb(monkeypatch, tmp_path_factory):
        """Stub the device monitor and the installed-package lookup.

        Panel session logs are also redirected into a temp dir so tests
        never touch the real data dir (startSession truncates the log file
        the display-id parser reads back).
        """
        _StubMonitor.instances = []
        monkeypatch.setattr(controller_mod, "DeviceMonitor", _StubMonitor)
        # Settings live in the real user data dir; controller session starts
        # re-read them (audio policy), so tests pin the defaults.
        monkeypatch.setattr(
                controller_mod, "load_settings", lambda: (Settings(), []))
        checked: list[str] = []

        def fake_resolve_installed(adb_binary, done):
                checked.append(adb_binary)
                done(set())

        monkeypatch.setattr(controller_mod, "_resolve_installed", fake_resolve_installed)
        log_root = tmp_path_factory.mktemp("panel-logs")
        monkeypatch.setattr(
                controller_mod, "panel_log_path", lambda pkg: log_root / f"{pkg}.log"
        )
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


def test_failed_installed_sweep_keeps_previous_and_retries_once(no_adb, prefs_stub, qapp):
        """A failed installed-sweep must NOT grey out every tile.

        ``done(None)`` = the probe itself failed (adb flake, cold server),
        which is NOT "nothing installed": the previous set stays
        authoritative (tiles stay clickable - the old behavior disabled
        every app on a single timeout and never healed, since this sweep
        has no 2s re-poll). One silent retry is armed; a later success
        re-arms the failure path for the next flake.
        """
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit({"tv.danmaku.bili"})
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["tv.danmaku.bili"]["installed"] is True

        # A flaked sweep: None arrives, the known set rides it out.
        controller._installedResolved.emit(None)
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["tv.danmaku.bili"]["installed"] is True
        assert controller._install_retry.isActive()   # one silent retry armed

        # The retry fires exactly once: a second failure must not re-arm.
        controller._install_retry.stop()
        controller._installedResolved.emit(None)
        assert not controller._install_retry.isActive()

        # Recovery disarms the gate; the next flake gets a fresh retry.
        controller._installedResolved.emit({"tv.danmaku.bili"})
        assert controller._install_retried is False
        controller._installedResolved.emit(None)
        assert controller._install_retry.isActive()


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


def test_duplicate_start_routes_to_display_move(no_adb, prefs_stub, qapp, monkeypatch, tmp_path):
        """Starting a live session moves the app onto its display, no 2nd spawn.

        The app may be running on the physical screen (or the user just
        wants it back on the virtual one): the click must not rebuild the
        session, it goes through startAppOnDisplay. Without a session log
        yet the move degrades to a status message.
        """
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        monkeypatch.setattr(
                controller_mod, "panel_log_path", lambda pkg: tmp_path / f"{pkg}.log"
        )

        controller.startSession("tv.danmaku.bili")
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startSession("tv.danmaku.bili")
        assert statuses == ["哔哩哔哩 虚拟屏未就绪，稍后重试"]
        assert len(procs) == 1   # degradation, not a session rebuild


class _FakeAdb:
        """Adb stand-in recording shell commands with canned outputs."""

        def __init__(self, binary: str, serial: str, results: list[str] | None = None):
                self.binary = binary
                self.serial = serial
                self.calls: list[tuple[str, ...]] = []
                self._results = list(results or [])

        def run(self, *args: str, timeout: float = 60.0) -> str:
                self.calls.append(args)
                return self._results.pop(0) if self._results else ""


RESOLVE_OUTPUT = "cn.com.langeasy.LangEasyLexis/cn.com.langeasy.LangEasyLexis.MainActivity\n"


def test_start_app_on_display_moves_running_app(
        no_adb, prefs_stub, qapp, monkeypatch, tmp_path
):
        """Known display id + resolvable component -> am start --display N.

        resolve-activity pre-parses the launchable component, then the app
        is started onto the virtual display read from the session log -
        no session rebuild.
        """
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        log_file = tmp_path / "bili.log"
        monkeypatch.setattr(controller_mod, "panel_log_path", lambda pkg: log_file)
        fake = _FakeAdb(
                "/fake/adb.exe",
                "S1",
                results=[RESOLVE_OUTPUT, "Starting: Intent { cmp=... }\n"],
        )
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)

        controller.startSession("tv.danmaku.bili")
        # The engine writes the announce line after the spawn (startSession
        # truncated the log), so the fixture writes it post-start.
        log_file.write_text(
                "[server] INFO: New display: 1200x1600/280 (id=157)\n", encoding="utf-8"
        )
        controller.startAppOnDisplay("tv.danmaku.bili")
        deadline = time.monotonic() + 5.0
        while controller.statusText != "已在虚拟屏打开 哔哩哔哩" \
                and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
        assert fake.calls == [
                ("shell", "cmd", "package", "resolve-activity", "--brief",
                 "tv.danmaku.bili"),
                ("shell", "am", "start", "--display", "157", "-n",
                 "cn.com.langeasy.LangEasyLexis/cn.com.langeasy.LangEasyLexis.MainActivity"),
        ]
        assert controller.statusText == "已在虚拟屏打开 哔哩哔哩"
        assert len(procs) == 1


def test_start_app_on_display_degradations(no_adb, prefs_stub, qapp, monkeypatch, tmp_path):
        """Every failure path degrades to a status line, never a rebuild."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        log_file = tmp_path / "bili.log"
        monkeypatch.setattr(controller_mod, "panel_log_path", lambda pkg: log_file)

        # Unknown session: chip click for something not tracked.
        controller.startAppOnDisplay("tv.danmaku.bili")
        assert controller.statusText == "哔哩哔哩 会话未运行"
        assert procs == []

        controller.startSession("tv.danmaku.bili")

        # Log without a display line yet (engine still starting).
        log_file.write_text("[server] INFO: connecting\n", encoding="utf-8")
        fake = _FakeAdb("/fake/adb.exe", "S1")
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        controller.startAppOnDisplay("tv.danmaku.bili")
        assert controller.statusText == "哔哩哔哩 虚拟屏未就绪，稍后重试"
        assert fake.calls == []

        def _move_until_status(fake_adb: _FakeAdb, expected: str) -> None:
                controller.startAppOnDisplay("tv.danmaku.bili")
                deadline = time.monotonic() + 5.0
                while controller.statusText != expected and time.monotonic() < deadline:
                        QApplication.processEvents()
                        time.sleep(0.01)
                assert controller.statusText == expected

        # resolve-activity yields nothing usable.
        log_file.write_text("[server] INFO: New display: 1x1/160 (id=9)\n", encoding="utf-8")
        fake = _FakeAdb("/fake/adb.exe", "S1", results=["\n"])
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        _move_until_status(fake, "打开失败：哔哩哔哩（无法解析应用入口）")

        # am start reports an in-band error.
        fake = _FakeAdb(
                "/fake/adb.exe",
                "S1",
                results=[RESOLVE_OUTPUT, "Error: Activity class does not exist\n"],
        )
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        _move_until_status(fake, "打开失败：哔哩哔哩（Error: Activity class does not exist）")

        # adb itself fails (device gone mid-click).
        def boom(*args: str, timeout: float = 60.0) -> str:
                raise AdbError("adb -s S1 shell failed: device offline")

        fake = _FakeAdb("/fake/adb.exe", "S1")
        fake.run = boom  # type: ignore[method-assign]
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        _move_until_status(fake, "打开失败：哔哩哔哩（adb -s S1 shell failed: device offline）")

        # No session rebuild ever happened; no am start succeeded.
        assert len(procs) == 1


def test_session_log_reset_between_sessions(no_adb, prefs_stub, qapp, monkeypatch, tmp_path):
        """A fresh session truncates the panel log: no stale display ids.

        The parser takes the LAST 'New display:' line, so a leftover file
        would hand out the previous run's (dead) display id until the new
        engine rewrites it.
        """
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        log_file = tmp_path / "bili.log"
        monkeypatch.setattr(controller_mod, "panel_log_path", lambda pkg: log_file)
        log_file.write_text("[server] INFO: New display: 1x1/160 (id=9)\n", encoding="utf-8")

        controller.startSession("tv.danmaku.bili")
        assert not log_file.exists()   # stale log removed before the spawn


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


# ------------------------------------------------- audio_policy 三态（面板侧）


def _policy(monkeypatch, value: str) -> None:
        """Pin the settings a session start will re-read."""
        monkeypatch.setattr(
                controller_mod, "load_settings",
                lambda: (Settings(audio_policy=value), []))


def test_audio_latest_restarts_running_sessions_muted(
        no_adb, prefs_stub, qapp, monkeypatch):
        """latest：新会话带音频启动时，旧音频会话以 --no-audio 重启。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                proc = _FakeProc(argv)
                procs.append(proc)
                return proc

        controller._spawn = fake_spawn  # type: ignore[method-assign]
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.startSession("tv.danmaku.bili")
        controller.startSession("com.tencent.mm")

        # spawns: bili(音频) → bili 静音重启 → mm(音频)
        assert len(spawned) == 3
        assert spawned[1] == build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=False, muted=True)
        assert "--no-audio" in spawned[1]
        assert spawned[2] == build_launch_argv(
                "com.tencent.mm", "S1", portrait=True)  # DEFAULT_PORTRAIT
        assert "--no-audio" not in spawned[2]
        assert procs[0].terminated          # the old bili CLI got SIGTERM
        assert controller._audio_keys == {"com.tencent.mm"}
        assert "哔哩哔哩 已静音重启" in statuses[-1]


def test_audio_latest_skips_muted_and_dead_sessions(
        no_adb, prefs_stub, qapp, monkeypatch):
        """latest 重启只针对存活的音频会话；静音重启过的不再被翻动。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                proc = _FakeProc(argv)
                procs.append(proc)
                return proc

        controller._spawn = fake_spawn  # type: ignore[method-assign]

        controller.startSession("tv.danmaku.bili")
        controller.startSession("com.tencent.mm")   # bili -> muted restart
        mute_spawn_count = len(spawned)
        mm_first = procs[2]                          # mm's first process
        controller.startSession("cn.wps.moffice_eng")   # third session
        # bili (muted) is not restarted again; only mm is.
        assert len(spawned) == mute_spawn_count + 2   # mm muted + wps
        assert mm_first.terminated is True
        assert not any("--no-audio" in a for a in spawned[mute_spawn_count + 1:])


def test_audio_off_pins_no_audio_in_panel_argv(
        no_adb, prefs_stub, qapp, monkeypatch):
        """off：面板直接在 spawn argv 上钉 --no-audio（CLI 侧同样兜底）。"""
        _policy(monkeypatch, "off")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []
        controller._spawn = lambda argv: (spawned.append(argv), _FakeProc(argv))[1]
        controller.startSession("tv.danmaku.bili")
        assert "--no-audio" in spawned[0]
        assert controller._audio_keys == set()


def test_audio_all_spawns_without_mute_and_without_restart(
        no_adb, prefs_stub, qapp, monkeypatch):
        """all：并行音频是显式要求，不做重启也不静音。"""
        _policy(monkeypatch, "all")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        controller.startSession("tv.danmaku.bili")
        controller.startSession("com.tencent.mm")
        assert len(procs) == 2
        assert not any(p.terminated for p in procs)
        assert "--no-audio" not in procs[0].argv
        assert controller._audio_keys == {"tv.danmaku.bili", "com.tencent.mm"}


def test_device_mirror_latest_restart_uses_mirror_argv(
        no_adb, prefs_stub, qapp, monkeypatch):
        """latest：镜像会话启动时同样把音频会话静音重启（镜像 argv 复用）。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        controller._spawn = fake_spawn  # type: ignore[method-assign]
        controller.startSession("tv.danmaku.bili")
        controller.startMirror()
        # spawns: bili(音频) → bili 静音重启 → mirror(音频，最新者胜)
        assert spawned[2] == build_device_mirror_argv("S1")
        assert "--no-audio" not in spawned[2]
        assert spawned[1][-1] == "--no-audio"   # bili's mute respawn

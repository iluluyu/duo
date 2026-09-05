"""Panel controller: the launcher's logic as a QObject that QML can bind to.

Single source of truth for panel behaviour - devices, the app catalog,
mirror sessions, portrait preferences and adb resolution - shared by the
widgets panel (:mod:`duo.ui.main_window`) and the upcoming QML front end.
Deliberately widgets-free: only QtCore lives here, so QML can bind the
signals and properties below directly without going through QMainWindow.

Worker threads (adb polls, install checks, icon pulls) never touch bindable
state directly; they raise private ``_*`` hop signals that Qt delivers on
the controller's thread (queued cross-thread, synchronous same-thread - so
offscreen tests can drive everything deterministically).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PyQt6 import QtCore
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from duo.core.apps import Adb, AdbError, app_info
from duo.core.devices import DeviceMonitor, poll_query
from duo.core.engine import probe
from duo.core.paths import data_dir
from duo.core.settings import load_settings, resolve_adb_path
from duo.core.winproc import creation_flags

# PyQt6 ships stubs for pyqtSignal/pyqtSlot but (as of 6.9) omits
# pyqtProperty, which exists at runtime - resolve it dynamically so mypy
# stays clean while QML still gets real, bindable properties.
pyqtProperty: Any = QtCore.pyqtProperty  # type: ignore[attr-defined]

#: Session key for whole-device mirroring (not an app package).
MIRROR_KEY = "__device_mirror__"


def package_to_label(package: str) -> str:
        """Human-ish fallback label for uncataloged packages."""
        tail = package.rsplit(".", 1)[-1]
        return tail[:1].upper() + tail[1:]


def elide_label(label: str, limit: int = 6) -> str:
        """Shorten a label to fit under a mini icon."""
        return label if len(label) <= limit else label[: limit - 1] + "…"

#: Small curated catalog; filtered against installed packages at startup.
APP_CATALOG: list[tuple[str, str]] = [
        ("不背单词", "cn.com.langeasy.LangEasyLexis"),
        ("哔哩哔哩", "tv.danmaku.bili"),
        ("微信", "com.tencent.mm"),
        ("WPS Office", "cn.wps.moffice_eng"),
        ("微信读书", "com.tencent.weread"),
]

#: Per-app portrait defaults (reading/vocabulary apps want a tall phone).
DEFAULT_PORTRAIT: dict[str, bool] = {
        "cn.com.langeasy.LangEasyLexis": True,
        "com.tencent.weread": True,
}

#: Human-readable adb state names for the device card.
_STATE_TEXT = {
        "device": "在线",
        "offline": "离线",
        "unauthorized": "未授权 USB 调试",
        "recovery": "recovery 模式",
}


def session_label(key: str) -> str:
        """Display label for a session key: catalog name, mirror, or package."""
        if key == MIRROR_KEY:
                return "设备镜像"
        for label, package in APP_CATALOG:
                if package == key:
                        return label
        return package_to_label(key)


def _prefs_path() -> Path:
        return data_dir() / "gui_prefs.json"


def load_portrait_prefs() -> dict[str, bool]:
        """Read the persisted per-app portrait choices (missing = defaults)."""
        try:
                raw = json.loads(_prefs_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
                return dict(DEFAULT_PORTRAIT)
        saved = raw.get("portrait", {})
        merged = dict(DEFAULT_PORTRAIT)
        merged.update({k: bool(v) for k, v in saved.items()})
        return merged


def save_portrait_prefs(prefs: dict[str, bool]) -> None:
        """Persist the per-app portrait choices for the next run."""
        _prefs_path().parent.mkdir(parents=True, exist_ok=True)
        _prefs_path().write_text(
                json.dumps({"portrait": prefs}, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def build_launch_argv(package: str, serial: str, portrait: bool) -> list[str]:
        """The mirror argv for a panel launch.

        Every panel window gets the borderless chrome. Audio is always
        requested - the CLI arbitrates ownership (single capture) via the
        audio lock, so the panel stays out of that policy. Under PyInstaller
        ``sys.executable`` IS the frozen duo binary, so sessions spawn as
        ``Duo.exe mirror ...`` and route through the CLI entry.
        """
        frozen = getattr(sys, "frozen", False)
        argv = [sys.executable, *([] if frozen else ["-m", "duo"])]
        argv += [
                "mirror",
                "--app",
                package,
                "--serial",
                serial,
                "--chrome",
        ]
        if portrait:
                argv.append("--portrait")
        return argv


def build_device_mirror_argv(serial: str) -> list[str]:
        """The argv for direct device mirroring (no virtual display)."""
        frozen = getattr(sys, "frozen", False)
        argv = [sys.executable, *([] if frozen else ["-m", "duo"])]
        argv += [
                "mirror",
                "--display",
                "mirror",
                "--serial",
                serial,
                "--chrome",
                "--title",
                "平板镜像",
        ]
        return argv


def _resolve_installed(adb_binary: str, done: Callable[[set[str]], None]) -> None:
        """Background check of which catalog apps are installed."""

        def work() -> None:
                try:
                        result = subprocess.run(
                                [adb_binary, "shell", "pm list packages"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                timeout=8,
                                check=False,
                                creationflags=creation_flags(),
                        )
                        installed = {
                                line.removeprefix("package:").strip()
                                for line in result.stdout.splitlines()
                                if line.startswith("package:")
                        }
                except (OSError, subprocess.TimeoutExpired):
                        installed = set()
                done(installed)

        threading.Thread(target=work, daemon=True).start()


class PanelController(QObject):
        """Bindable panel state: devices, catalog, sessions, status.

        QML consumes the signals/properties below directly. ``iconReady``
        carries an absolute PNG **path** (``Path``) from the icon cache, not
        a ``QIcon``: QML ``Image { source: "file://" + path }`` consumes a
        file path as-is, while a QIcon would need a custom image provider;
        the widgets panel builds ``QIcon(str(path))`` from the same value.
        """

        # ------------------------------------------------------ QML surface
        devicesChanged = pyqtSignal(list)            # list of device maps
        appsResolved = pyqtSignal(object)            # set of installed packages
        iconReady = pyqtSignal(str, object)          # package, icon path|None
        statusChanged = pyqtSignal(str)
        sessionsChanged = pyqtSignal(list)           # list of session maps
        allAppsReady = pyqtSignal(list)              # third-party packages
        appInfoReady = pyqtSignal(str, object, str)  # package, icon, label
        adbBinaryChanged = pyqtSignal(str)
        portraitChanged = pyqtSignal(str, bool)
        engineLockedChanged = pyqtSignal(bool)

        # ------------------------------------- thread hops (worker -> GUI)
        _devicesPolled = pyqtSignal(object)          # dict serial -> state
        _installedResolved = pyqtSignal(object)      # set of packages
        _adbResolved = pyqtSignal(str)

        def __init__(self, adb_binary: str, parent: QObject | None = None) -> None:
                super().__init__(parent)
                self._adb_binary = adb_binary
                self._devices: dict[str, str] = {}
                self._installed: set[str] | None = None
                self._portrait_prefs = load_portrait_prefs()
                self._sessions: dict[str, subprocess.Popen[bytes]] = {}
                self._status_text = "就绪"

                # Hops deliver on this object's thread: queued when raised on
                # a worker/monitor thread, synchronous when a test raises the
                # same hop on the GUI thread.
                self._devicesPolled.connect(self._apply_devices)
                self._installedResolved.connect(self._apply_installed)
                self._adbResolved.connect(self.setAdb)

                self._monitor = DeviceMonitor(
                        on_change=self._devicesPolled.emit,
                        query=poll_query(self._adb_binary),
                        poll_interval_s=2.0,
                )
                self._monitor.poll_now()
                self._monitor.start()
                self.refreshInstalled()

                # Reap dead sessions quietly; views re-render on the
                # sessionsChanged signal only (payload carries the change).
                self._reaper = QTimer(self)
                self._reaper.setInterval(1200)
                self._reaper.timeout.connect(self.reapSessions)
                self._reaper.start()
                self._emit_sessions()

        # ------------------------------------------------------ properties

        @pyqtProperty(list, notify=devicesChanged)
        def devices(self) -> list[dict[str, object]]:
                """Serial + state text per device, ready for a QML model."""
                return [
                        {
                                "serial": serial,
                                "state": state,
                                "stateText": _STATE_TEXT.get(state, state),
                                "online": state == "device",
                        }
                        for serial, state in self._devices.items()
                ]

        @pyqtProperty(str, notify=statusChanged)
        def statusText(self) -> str:
                """The single status line under the cards."""
                return self._status_text

        @pyqtProperty(list, notify=sessionsChanged)
        def runningSessions(self) -> list[dict[str, object]]:
                """Running sessions: key/label/running/portrait per entry."""
                return [
                        {
                                "key": key,
                                "label": session_label(key),
                                "running": proc.poll() is None,
                                "portrait": self._portrait_prefs.get(key, False),
                        }
                        for key, proc in self._sessions.items()
                ]

        @property
        def sessions(self) -> dict[str, subprocess.Popen[bytes]]:
                """The live session map (widgets compat; QML: runningSessions)."""
                return self._sessions

        @pyqtProperty(str, notify=adbBinaryChanged)
        def adbBinary(self) -> str:
                """adb shared by device polling, install checks and spawns."""
                return self._adb_binary

        @pyqtProperty(bool, notify=engineLockedChanged)
        def engineLocked(self) -> bool:
                """True while any mirror session lives (engine path lock)."""
                return bool(self._sessions)

        # ----------------------------------------------------------- slots

        @pyqtSlot(str)
        def startSession(self, package: str) -> None:
                """Spawn a chrome-clad mirror session and track it."""
                serial = next(iter(self._monitor.online), "")
                if not serial:
                        self._set_status("设备未连接")
                        return
                self.reapSessions()
                if package in self._sessions:
                        self._set_status(f"{session_label(package)} 已在运行")
                        return
                portrait = self._portrait_prefs.get(package, False)
                argv = build_launch_argv(package, serial, portrait)
                try:
                        proc = self._spawn(argv)
                except OSError as exc:
                        self._set_status(f"启动失败：{session_label(package)}（{exc}）")
                        return
                self._sessions[package] = proc
                self._emit_sessions()
                orientation = "竖屏" if portrait else "横屏"
                self._set_status(f"已启动 {session_label(package)} · {orientation}")

        @pyqtSlot()
        def startMirror(self) -> None:
                """Start whole-device mirroring (physical display, no app)."""
                serial = next(iter(self._monitor.online), "")
                if not serial:
                        self._set_status("设备未连接")
                        return
                self.reapSessions()
                if MIRROR_KEY in self._sessions:
                        self._set_status("设备镜像已在运行")
                        return
                try:
                        proc = self._spawn(build_device_mirror_argv(serial))
                except OSError as exc:
                        self._set_status(f"启动失败：设备镜像（{exc}）")
                        return
                self._sessions[MIRROR_KEY] = proc
                self._emit_sessions()
                self._set_status("已启动 设备镜像")

        @pyqtSlot(str)
        def stopSession(self, key: str) -> None:
                """Terminate one session; the CLI's SIGTERM handler cleans up."""
                proc = self._sessions.get(key)
                if proc is None:
                        return
                proc.terminate()
                self._set_status(f"已关闭 {session_label(key)}")

        @pyqtSlot(str)
        def togglePortrait(self, package: str) -> None:
                """Flip and persist the per-app orientation choice."""
                now = not self._portrait_prefs.get(package, False)
                self._portrait_prefs[package] = now
                save_portrait_prefs(self._portrait_prefs)
                orientation = "竖屏" if now else "横屏"
                self._set_status(f"{session_label(package)} 将以{orientation}启动")
                self.portraitChanged.emit(package, now)
                self.sessionsChanged.emit(self.runningSessions)

        @pyqtSlot(str, result="bool")
        def portraitFor(self, package: str) -> bool:
                """Remembered orientation for ``package`` (catalog default)."""
                return self._portrait_prefs.get(package, False)

        @pyqtSlot()
        def refreshInstalled(self) -> None:
                """Background check of which catalog apps are installed."""
                _resolve_installed(self._adb_binary, self._installedResolved.emit)

        @pyqtSlot()
        def resolveAdb(self) -> None:
                """Re-resolve adb (settings > probe > fallback) off the UI thread."""
                settings, problems = load_settings()
                if problems:
                        self._set_status(problems[0])

                def work() -> None:
                        adb = resolve_adb_path(settings, probe("adb").path, "adb.exe")
                        self._adbResolved.emit(adb)

                threading.Thread(target=work, daemon=True).start()

        @pyqtSlot(str)
        def setAdb(self, adb: str) -> None:
                """Swap the device monitor to a newly resolved adb, if it moved."""
                if adb == self._adb_binary:
                        self._set_status("设置已保存，新会话生效")
                        return
                self._adb_binary = adb
                self.adbBinaryChanged.emit(adb)
                self._restart_monitor()
                self.refreshInstalled()
                self._set_status("设置已保存，已切换 adb，新会话生效")

        @pyqtSlot(result="int")
        def reapSessions(self) -> int:
                """Drop sessions whose process has exited; returns the count."""
                dead = [
                        key for key, proc in self._sessions.items() if proc.poll() is not None
                ]
                for key in dead:
                        del self._sessions[key]
                if dead:
                        self._emit_sessions()
                return len(dead)

        def activeSessionCount(self) -> int:
                """Live mirror sessions; drives the settings page engine lock."""
                self.reapSessions()
                return len(self._sessions)

        def shutdown(self) -> None:
                """Stop background polling and the reaper (panel closing)."""
                self._reaper.stop()
                self._monitor.stop()

        # ------------------------------------------------------- internals

        def _spawn(self, argv: list[str]) -> subprocess.Popen[bytes]:
                """Launch one detached mirror session (tests inject a fake)."""
                return subprocess.Popen(
                        argv,
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creation_flags(),
                )

        def _restart_monitor(self) -> None:
                """Rebuild the poller for the current adb binary."""
                self._monitor.stop()
                self._monitor = DeviceMonitor(
                        on_change=self._devicesPolled.emit,
                        query=poll_query(self._adb_binary),
                        poll_interval_s=2.0,
                )
                self._monitor.poll_now()
                self._monitor.start()

        @pyqtSlot(object)
        def _apply_devices(self, states: object) -> None:
                """Adopt a fresh serial -> state map and notify bindings."""
                assert isinstance(states, dict)
                self._devices = dict(states)
                self.devicesChanged.emit(self.devices)

        @pyqtSlot(object)
        def _apply_installed(self, installed: object) -> None:
                """Adopt the installed set, report it, then resolve icons."""
                assert isinstance(installed, set)
                self._installed = installed
                self.appsResolved.emit(installed)
                self._load_catalog_icons()
                self._load_all_apps()

        def _set_status(self, text: str) -> None:
                self._status_text = text
                self.statusChanged.emit(text)

        def _emit_sessions(self) -> None:
                self.sessionsChanged.emit(self.runningSessions)
                self.engineLockedChanged.emit(bool(self._sessions))

        def _load_catalog_icons(self) -> None:
                """Resolve catalog icons in the background (first run pulls APKs)."""
                installed = set(self._installed or set())

                def work() -> None:
                        serial = next(iter(self._monitor.online), None)
                        if not serial:
                                return
                        adb = Adb(self._adb_binary, serial)
                        for _, package in APP_CATALOG:
                                if package not in installed:
                                        continue
                                try:
                                        info = app_info(adb, package)
                                except Exception:
                                        continue
                                self.iconReady.emit(package, info.icon_path)

                threading.Thread(target=work, daemon=True).start()

        def _load_all_apps(self) -> None:
                """Query every third-party package, then resolve icons lazily."""

                def work() -> None:
                        serial = next(iter(self._monitor.online), None)
                        if not serial:
                                return
                        adb = Adb(self._adb_binary, serial)
                        try:
                                packages = adb.third_party_packages()
                        except (AdbError, OSError):
                                return
                        self.allAppsReady.emit(packages)
                        # Sequential background resolution: real icon + label
                        # per app (cached in the data dir after first pass).
                        for package in packages:
                                try:
                                        info = app_info(adb, package)
                                except Exception:
                                        continue
                                self.appInfoReady.emit(package, info.icon_path, info.label)

                threading.Thread(target=work, daemon=True).start()

"""Panel controller: the launcher's logic as a QObject that QML can bind to.

Single source of truth for panel behaviour - devices, the app catalog,
mirror sessions, portrait preferences and adb resolution - consumed by the
QML front end (:mod:`duo.ui.app` registers it as the ``ctrl`` context
property). Deliberately widgets-free: only QtCore lives here, so QML binds
the signals and properties below directly.

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
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot

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


def _icon_url(path: object) -> str:
        """Local icon path as a file URL string for QML ``Image.source``.

        ``QUrl.fromLocalFile`` handles Windows drive letters, spaces and
        non-ASCII; the early ``"file://" + path`` string concatenation
        silently broke every one of those cases.
        """
        return QUrl.fromLocalFile(str(path)).toString() if path else ""

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


def _resolve_installed(adb_binary: str, done: Callable[[set[str] | None], None]) -> None:
        """Background check of which catalog apps are installed.

        ``done(None)`` signals a FAILED probe (adb missing, timeout, nonzero
        exit): the caller must keep its previous installed set - treating a
        flaked ``pm list packages`` as "nothing installed" greys out and
        disables every tile (the click-dead panel bug), and unlike the
        device list there is no 2s re-poll to heal it.
        """

        def work() -> None:
                installed: set[str] | None
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
                        if result.returncode != 0 and not installed:
                                installed = None
                except (OSError, subprocess.TimeoutExpired):
                        installed = None
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
        appsChanged = pyqtSignal()                   # the apps model mutated
        appsResolved = pyqtSignal(object)            # set of installed packages
        iconReady = pyqtSignal(str, object)          # package, icon path|None
        statusChanged = pyqtSignal(str)
        sessionsChanged = pyqtSignal(list)           # list of session maps
        allAppsReady = pyqtSignal(list)              # third-party packages
        # Resolved metadata for a whole batch: list of
        # (package, icon path|None, label). One hop per background sweep so
        # the QML grid rebuilds once, not once per app.
        appInfoReady = pyqtSignal(object)
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
                self._apps: list[dict[str, object]] = []
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
                # App-model maintenance for the QML grid (queued from workers).
                self.allAppsReady.connect(self._merge_all_apps)
                self.iconReady.connect(self._apply_icon)
                self.appInfoReady.connect(self._apply_app_info)
                # Icon bursts flush as ONE appsChanged emit: the QML
                # QVariantList model rebuilds the whole grid (and destroys
                # every delegate, blanking async images) per emit - the
                # per-icon emits of the first sweep read as tiles vanishing
                # under the user's finger mid long-press.
                self._dirty_icons: set[str] = set()
                self._icon_flush = QTimer(self)
                self._icon_flush.setSingleShot(True)
                self._icon_flush.timeout.connect(self._flush_icon_batch)
                # One silent retry after a failed installed-sweep (cold boot,
                # adb server still waking up). Without it the very first
                # failure would leave the grid empty until a manual refresh.
                self._install_retried = False
                self._install_retry = QTimer(self)
                self._install_retry.setSingleShot(True)
                self._install_retry.setInterval(5000)
                self._install_retry.timeout.connect(self.refreshInstalled)

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

        @pyqtProperty(list, notify=appsChanged)
        def apps(self) -> list[dict[str, object]]:
                """The QML grid model: catalog first, then third-party apps.

                Entries carry package/label/installed plus ``icon`` as a file
                URL string (empty until the icon worker delivers a path).
                """
                return self._apps

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

        @pyqtProperty(str, constant=True)
        def mirrorKey(self) -> str:
                """Session key for whole-device mirroring (QML hides its
                portrait toggle: a physical display has no orientation)."""
                return MIRROR_KEY

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
                self._install_retry.stop()   # manual/explicit run: fresh attempt
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
                """Adopt the installed set, report it, then resolve icons.

                ``None`` means the probe itself failed (adb flake) - NOT an
                empty device: the previous set stays authoritative (tiles
                keep their installed state, no click-dead grey-out), and a
                single silent retry re-checks shortly.
                """
                if not isinstance(installed, set):
                        if not self._install_retried:
                                self._install_retried = True
                                self._install_retry.start()
                        return
                self._install_retried = False
                self._installed = installed
                self.appsResolved.emit(installed)
                self._rebuild_apps(installed)
                self._load_catalog_icons()
                self._load_all_apps()

        def _rebuild_apps(self, installed: set[str]) -> None:
                """Rebuild the QML app model: catalog first, then extras.

                Third-party entries gathered from an earlier poll survive
                with their icons/labels; only their installed flag refreshes.
                """
                previous = {str(entry["package"]): entry for entry in self._apps}
                apps: list[dict[str, object]] = []
                for label, package in APP_CATALOG:
                        old = previous.pop(package, None)
                        apps.append({
                                "package": package,
                                "label": label,
                                "icon": str(old["icon"]) if old else "",
                                "installed": package in installed,
                        })
                for entry in previous.values():
                        entry["installed"] = str(entry["package"]) in installed
                        apps.append(entry)
                self._apps = apps
                self.appsChanged.emit()

        @pyqtSlot(list)
        def _merge_all_apps(self, packages: list[str]) -> None:
                """Extend the model with third-party packages just discovered."""
                known = {str(entry["package"]) for entry in self._apps}
                fresh = [
                        {
                                "package": package,
                                "label": package_to_label(package),
                                "icon": "",
                                "installed": True,   # a -3 listing IS the installed set
                        }
                        for package in packages
                        if package not in known
                ]
                if fresh:
                        self._apps.extend(fresh)
                        self.appsChanged.emit()

        @pyqtSlot(str, object)
        def _apply_icon(self, package: str, icon_path: object) -> None:
                """Adopt one resolved icon path (queued from the icon worker).

                The model patch lands immediately; the ``appsChanged`` emit
                is deferred to a single-shot timer so a whole icon burst
                notifies once (same contract as ``_apply_app_info``).
                """
                if icon_path and self._patch_app_entry(package, icon=_icon_url(icon_path)):
                        self._dirty_icons.add(package)
                        self._icon_flush.start()

        def _flush_icon_batch(self) -> None:
                """One grid rebuild per icon burst, not one per icon."""
                if self._dirty_icons:
                        self._dirty_icons.clear()
                        self.appsChanged.emit()

        @pyqtSlot(object)
        def _apply_app_info(self, batch: object) -> None:
                """Adopt resolved metadata for a batch of apps (worker hop).

                Every entry lands in the model before the single
                ``appsChanged`` emit, so QML rebuilds the grid once per
                sweep instead of once per app (the widgets-era grid only
                swapped icons in place; a QVariantList model cannot).
                """
                assert isinstance(batch, list)
                changed = False
                for package, icon_path, label in batch:
                        fields: dict[str, str] = {"label": label}
                        if icon_path:
                                fields["icon"] = _icon_url(icon_path)
                        changed = self._patch_app_entry(package, **fields) or changed
                if changed:
                        self.appsChanged.emit()

        def _patch_app_entry(self, package: str, **fields: str) -> bool:
                """Patch one model entry in place; False = unknown package.

                Emits nothing: callers batch patches and notify once.
                """
                for entry in self._apps:
                        if str(entry["package"]) == package:
                                entry.update(fields)
                                return True
                return False

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
                        # The whole sweep accumulates and hops ONCE: N per-app
                        # emits would mean N full grid rebuilds in QML.
                        batch: list[tuple[str, object, str]] = []
                        for package in packages:
                                try:
                                        info = app_info(adb, package)
                                except Exception:
                                        continue
                                batch.append((package, info.icon_path, info.label))
                        if batch:
                                self.appInfoReady.emit(batch)

                threading.Thread(target=work, daemon=True).start()

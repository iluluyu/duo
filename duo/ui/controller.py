"""Panel controller: the launcher's logic as a QObject that QML can bind to.

Single source of truth for panel behaviour - devices, the app catalog,
mirror sessions, portrait/display preferences and adb resolution - consumed
by the QML front end (:mod:`duo.ui.app` registers it as the ``ctrl``
context property). Deliberately widgets-free: only QtCore lives here, so QML binds
the signals and properties below directly.

Worker threads (adb polls, install checks, icon pulls) never touch bindable
state directly; they raise private ``_*`` hop signals that Qt delivers on
the controller's thread (queued cross-thread, synchronous same-thread - so
offscreen tests can drive everything deterministically).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PyQt6 import QtCore
from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot

from duo.core.adb import MEDIA_VOLUME_MAX, media_volume
from duo.core.apps import (
        Adb,
        AdbError,
        app_info,
        label_sort_key,
        parse_resolve_activity,
)
from duo.core.aspects import (
        BODY_LANDSCAPE_ID,
        BODY_PORTRAIT_ID,
        AspectPreset,
        body_aspect_from_wm_size,
        preset_by_id,
        transposed,
)
from duo.core.devices import DeviceMonitor, poll_query
from duo.core.engine import probe
from duo.core.paths import data_dir, logs_dir
from duo.core.session import display_id_from_log
from duo.core.settings import (
        VALID_BAR_MODES,
        load_settings,
        resolve_adb_path,
        save_settings,
)
from duo.core.winproc import creation_flags

# The curated app catalog lives in duo.core.catalog (entries are AppPreset
# records with .label/.package; that module is being built in parallel).
# Guard the import so the panel keeps running - with an empty catalog -
# until the module lands; the logic below assumes a populated catalog.
try:
        from duo.core.catalog import APP_CATALOG  # type: ignore[import-not-found]
except ImportError:                                # pragma: no cover
        APP_CATALOG = []

# Bundled fallback icons (duo.core.icon_presets, also built in parallel):
# same guard - without the module, new entries simply start icon-less.
try:
        from duo.core.icon_presets import preset_icon_path  # type: ignore[import-not-found]
        _PRESETS_READY = True
except ImportError:                                          # pragma: no cover

        def preset_icon_path(package: str) -> Path | None:
                """Guard stub: no preset available for any package."""
                return None

        _PRESETS_READY = False

# PyQt6 ships stubs for pyqtSignal/pyqtSlot but (as of 6.9) omits
# pyqtProperty, which exists at runtime - resolve it dynamically so mypy
# stays clean while QML still gets real, bindable properties.
pyqtProperty: Any = QtCore.pyqtProperty  # type: ignore[attr-defined]

#: Session key for whole-device mirroring (not an app package).
MIRROR_KEY = "__device_mirror__"

#: Body-preset probe freshness window (seconds). A panel ratio only
#: changes with display-zoom settings, so a slow cache costs nothing;
#: the window exists so a reconnected (possibly different) device
#: re-derives its body preset instead of serving the previous phone's.
_BODY_PROBE_TTL_S = 600.0

#: One `wm size` roundtrip happens on the GUI thread (menu click), so it
#: must stay click-cheap - long before the adb default of 60s.
_BODY_PROBE_TIMEOUT_S = 5.0


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

#: Per-app portrait seeds. EMPTY BY DESIGN (2026-09-06 晚，用户决策
#: “防过拟合”)：不再替 APP 猜初始方向 —— 设备上没有便宜的静态探测
#: （manifest screenOrientation 不在任何 dump/badging 输出里），猜错
#: 就是横屏开竖屏 APP 自留黑边。所有 APP 一律 16:9 开局；用户在面板
#: 长按切换的偏好持久化在 prefs 里（学习而非硬编码）。
DEFAULT_PORTRAIT: dict[str, bool] = {}

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
        for preset in APP_CATALOG:
                if str(preset.package) == key:
                        return str(preset.label)
        return package_to_label(key)


def _prefs_path() -> Path:
        return data_dir() / "gui_prefs.json"


def _read_prefs_doc() -> dict[str, object]:
        """The whole prefs document; a missing/corrupt file reads as empty.

        Every section (portrait, pinned, display) lives in one JSON document
        so a save of one section can never drop the others (read-modify-write).
        """
        try:
                raw = json.loads(_prefs_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
                return {}
        return raw if isinstance(raw, dict) else {}


def _write_prefs_doc(doc: dict[str, object]) -> None:
        """Persist the whole prefs document (all sections together)."""
        path = _prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_portrait_prefs() -> dict[str, bool]:
        """Read the persisted per-app portrait choices (missing = defaults)."""
        saved = _read_prefs_doc().get("portrait", {})
        merged = dict(DEFAULT_PORTRAIT)
        if isinstance(saved, dict):
                merged.update({
                        str(k): bool(v) for k, v in saved.items() if isinstance(k, str)
                })
        return merged


def save_portrait_prefs(prefs: dict[str, bool]) -> None:
        """Persist the per-app portrait choices for the next run."""
        doc = _read_prefs_doc()
        doc["portrait"] = prefs
        _write_prefs_doc(doc)


def load_pinned_prefs() -> set[str]:
        """Read the pinned app set (missing/old file = nothing pinned)."""
        saved = _read_prefs_doc().get("pinned", [])
        if isinstance(saved, list):
                return {str(p) for p in saved if isinstance(p, str)}
        return set()


def save_pinned_prefs(pinned: set[str]) -> None:
        """Persist the pinned set (sorted for stable diffs between runs)."""
        doc = _read_prefs_doc()
        doc["pinned"] = sorted(pinned)
        _write_prefs_doc(doc)


def load_display_prefs() -> dict[str, dict[str, object]]:
        """Read the persisted per-app display-mode choices (missing = flex).

        Shape (DESIGN.md §3.7 显示模式区)::

            {package: {"mode": "flex"} | {"mode": "fixed", "aspect": "16:9"}}

        Anything that is not a per-package dict is dropped rather than
        guessed at (a corrupt file costs one choice, not a broken menu).
        """
        saved = _read_prefs_doc().get("display", {})
        if not isinstance(saved, dict):
                return {}
        return {
                str(package): dict(choice)
                for package, choice in saved.items()
                if isinstance(package, str) and isinstance(choice, dict)
        }


def save_display_prefs(prefs: dict[str, dict[str, object]]) -> None:
        """Persist the per-app display-mode choices for the next run."""
        doc = _read_prefs_doc()
        doc["display"] = prefs
        _write_prefs_doc(doc)


def load_bar_prefs() -> dict[str, dict[str, str | None]]:
        """Read the persisted per-app window-bar overrides (missing = default).

        Shape (窗口栏按应用设置)::

            {package: {"top": "native" | None, "bottom": "none" | None}}

        ``None`` and absent keys read as "follow the settings default"
        exactly like an absent package; values outside the three-member
        enum immersive|native|none (a hand-edited file) are dropped rather
        than guessed at - the same discipline as
        :func:`load_display_prefs`.
        """
        saved = _read_prefs_doc().get("bars", {})
        if not isinstance(saved, dict):
                return {}
        prefs: dict[str, dict[str, str | None]] = {}
        for package, choice in saved.items():
                if not (isinstance(package, str) and isinstance(choice, dict)):
                        continue
                modes: dict[str, str | None] = {}
                for which in ("top", "bottom"):
                        value = choice.get(which)
                        modes[which] = value if value in VALID_BAR_MODES else None
                prefs[package] = modes
        return prefs


def save_bar_prefs(prefs: dict[str, dict[str, str | None]]) -> None:
        """Persist the per-app window-bar overrides for the next run."""
        doc = _read_prefs_doc()
        doc["bars"] = prefs
        _write_prefs_doc(doc)


def load_audio_prefs() -> dict[str, dict[str, bool]]:
        """Read the persisted per-app audio-exclusivity choices (missing = off).

        Shape（音频独占，右键菜单勾选，沿用 bars 节的纪律）::

            {package: {"exclusive": True}}

        Only well-formed per-package dicts survive a hand-edited file (a
        corrupt entry costs one choice, not a broken launch path) - the
        same discipline as :func:`load_bar_prefs`.
        """
        saved = _read_prefs_doc().get("audio", {})
        if not isinstance(saved, dict):
                return {}
        prefs: dict[str, dict[str, bool]] = {}
        for package, choice in saved.items():
                if (
                        isinstance(package, str)
                        and isinstance(choice, dict)
                        and isinstance(choice.get("exclusive"), bool)
                ):
                        prefs[package] = {"exclusive": choice["exclusive"]}
        return prefs


def save_audio_prefs(prefs: dict[str, dict[str, bool]]) -> None:
        """Persist the per-app audio-exclusivity choices for the next run."""
        doc = _read_prefs_doc()
        doc["audio"] = prefs
        _write_prefs_doc(doc)


def load_behavior_prefs() -> dict[str, dict[str, bool]]:
        """Read the persisted per-app session-exit choices (missing = default).

        Shape（断开保留画面，右键菜单勾选，新建独立 behavior 节而不动
        audio 节语义）::

            {package: {"keep_vd": True}}

        ``keep_vd`` = 该应用的会话断开后应用留在虚拟屏（scrcpy
        ``--no-vd-destroy-content``），不回落手机主屏。与 bars/audio 同款
        纪律：只收 bool，坏形状丢弃不硬猜（手改坏文件最多丢一条选择，
        不断启动链路）。
        """
        saved = _read_prefs_doc().get("behavior", {})
        if not isinstance(saved, dict):
                return {}
        prefs: dict[str, dict[str, bool]] = {}
        for package, choice in saved.items():
                if (
                        isinstance(package, str)
                        and isinstance(choice, dict)
                        and isinstance(choice.get("keep_vd"), bool)
                ):
                        prefs[package] = {"keep_vd": choice["keep_vd"]}
        return prefs


def save_behavior_prefs(prefs: dict[str, dict[str, bool]]) -> None:
        """Persist the per-app session-exit choices for the next run."""
        doc = _read_prefs_doc()
        doc["behavior"] = prefs
        _write_prefs_doc(doc)


def _pin_fixed_display(argv: list[str], width: int, height: int) -> None:
        """Inject the CLI's FIXED display mode onto a mirror argv (in place).

        The one place every fixed-geometry launch goes through (aspect
        presets, remembered per-app display choices): ``--display fixed
        --width/--height`` locks the virtual display (it physically cannot
        rotate - apps letterbox inside it like on a tablet) and
        ``--portrait`` follows the GEOMETRY (h > w), never a remembered
        pref - the fixed shape owns the orientation.
        """
        argv += ["--display", "fixed", "--width", str(width), "--height", str(height)]
        if height > width:
                argv.append("--portrait")


def _pin_chrome_bars(argv: list[str], package: str | None = None) -> None:
        """Inject the window-bar modes onto a mirror argv (in place).

        Same fresh-read discipline as ``_audio_policy``: the spawned CLI
        re-reads settings.json itself, and a save between two panel
        launches must reach the very next window - so the top/bottom bar
        modes come from a per-launch ``load_settings``, never a cached
        flag. A ``package`` with a remembered override in the gui_prefs
        ``bars`` section (窗口栏按应用设置, freshly read the same way)
        beats the settings default for that package; ``None`` (the device
        mirror, no app) takes the defaults untouched. Both values land in
        the three-member enum (immersive|native|none) validated by
        :mod:`duo.core.settings`, so the CLI flag contract never sees
        anything else.
        """
        settings, _problems = load_settings()
        override = load_bar_prefs().get(package, {}) if package else {}
        argv += [
                "--chrome-top",
                override.get("top") or settings.top_bar_mode,
                "--chrome-bottom",
                override.get("bottom") or settings.bottom_bar_mode,
        ]


def _display_size(display: dict[str, object] | None) -> tuple[int, int] | None:
        """Concrete (w, h) for a remembered display choice; None = flex.

        The controller resolves body ids (wm-size probe) before building
        the dict and ships the numbers along; a bare frozen-table id still
        resolves here so :func:`build_launch_argv` works standalone. Flex
        choices and unresolvable ids read as flex - the caller never
        silently loses audio flags over a display choice.
        """
        if not isinstance(display, dict) or display.get("mode") != "fixed":
                return None
        width, height = display.get("width"), display.get("height")
        if isinstance(width, int) and isinstance(height, int):
                return (width, height)
        preset = preset_by_id(str(display.get("aspect", "")))
        if preset is not None:
                return (preset.width, preset.height)
        return None


def panel_log_path(package: str) -> Path:
        """Session log path for a panel-managed session (one per package).

        Passed to the CLI via ``--session-log`` so the controller knows where
        to read the virtual display id from later (startAppOnDisplay); the
        CLI's own timestamped names are unfindable for a detached child.
        """
        return logs_dir() / f"panel-{package}.log"


def build_launch_argv(
        package: str,
        serial: str,
        portrait: bool,
        muted: bool = False,
        width: int | None = None,
        height: int | None = None,
        display: dict[str, object] | None = None,
        keep_vd: bool = False,
) -> list[str]:
        """The mirror argv for a panel launch.

        Every panel window gets the borderless chrome plus its window-bar
        modes (``--chrome-top/--chrome-bottom``, freshly read per launch -
        see :func:`_pin_chrome_bars`): a remembered per-app override in
        the gui_prefs ``bars`` section beats the settings default, so two
        apps can carry different bars from one settings page. Audio is requested by
        default - the CLI arbitrates ownership (single capture) via the
        audio lock and the settings ``audio_policy``; ``muted=True`` pins
        ``--no-audio`` for restart-muted sessions (see
        ``_restart_others_muted``). Under PyInstaller ``sys.executable`` IS
        the frozen duo binary, so sessions spawn as ``Duo.exe mirror ...``
        and route through the CLI entry.

        Fixed displays come in two shapes, both landing on the shared
        ``_pin_fixed_display`` injector: explicit ``width``/``height``
        (aspect-preset launches, DESIGN.md §3.6 按比例打开) or a remembered
        per-app ``display`` choice (§3.7 显示模式区,
        ``{"mode": "fixed", "aspect": id}`` - the controller resolves body
        ids to concrete sizes first). Either way ``--display fixed
        --width/--height`` locks the virtual display and the chrome overlay
        aspect-locks the window; ``--portrait`` then follows the GEOMETRY
        (h > w), not the remembered per-app pref: the fixed shape owns the
        orientation. ``display=None``/flex keeps the legacy flex argv.
        TODO(真机): fixed-ratio sessions are logic-ready, Windows 实测后回填.

        ``keep_vd=True``（gui_prefs ``behavior`` 节的按应用断开记忆）追加
        scrcpy ``--no-vd-destroy-content``：会话断开后应用留在虚拟屏而不
        回落手机主屏。flex/fixed 两种虚拟屏路径同享；设备镜像
        (:func:`build_device_mirror_argv`) 无自建显示，恒不注入。
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
                "--session-log",
                str(panel_log_path(package)),
        ]
        if width is not None and height is not None:
                _pin_fixed_display(argv, width, height)
        else:
                size = _display_size(display)
                if size is not None:
                        _pin_fixed_display(argv, *size)
                elif portrait:
                        argv.append("--portrait")
        # 窗口栏模式：每次启动按包取 effective（override 优先于设置页默认，
        # 见 _pin_chrome_bars），断开保留画面紧随其后，音频的 --no-audio
        # 仍恒居末位。
        _pin_chrome_bars(argv, package)
        if keep_vd:
                argv.append("--no-vd-destroy-content")
        if muted:
                argv.append("--no-audio")
        return argv


def build_device_mirror_argv(serial: str, muted: bool = False) -> list[str]:
        """The argv for direct device mirroring (no virtual display).

        Same chrome-bar injection as :func:`build_launch_argv` with one
        difference: the mirror has no app package, so the bar modes come
        from the settings DEFAULTS only - a per-app ``bars`` override
        never applies here.
        """
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
        _pin_chrome_bars(argv)
        if muted:
                argv.append("--no-audio")
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
        pinnedAppsChanged = pyqtSignal()             # the pinned row mutated
        appsResolved = pyqtSignal(object)            # set of installed packages
        iconReady = pyqtSignal(str, object)          # package, icon path|None
        statusChanged = pyqtSignal(str)
        sessionsChanged = pyqtSignal(list)           # list of session maps
        allAppsReady = pyqtSignal(list)              # third-party packages
        # Resolved metadata for a whole batch: list of
        # (package, icon path|None, label). One hop per background sweep so
        # the QML grid rebuilds once, not once per app.
        appInfoReady = pyqtSignal(object)
        # The info sweep ended (every batch delivered - or the sweep aborted
        # early). Grid order is FROZEN while batches land (see
        # _apply_app_info) and settles in exactly one re-sort here, so the
        # tiles stop "flowing" under the user's eyes mid-scan.
        infoSweepDone = pyqtSignal()
        adbBinaryChanged = pyqtSignal(str)
        portraitChanged = pyqtSignal(str, bool)
        # The remembered display mode of one package flipped (QML refreshes
        # the context-menu selection dots off this).
        displayModeChanged = pyqtSignal(str)
        # The per-app window-bar override of one package flipped (QML
        # refreshes the 窗口栏 submenu selection dots off this).
        barPrefsChanged = pyqtSignal(str)
        # The per-app audio-exclusivity choice of one package flipped (QML
        # refreshes the 音频独占 check dot off this).
        audioPrefsChanged = pyqtSignal(str)
        # The per-app session-exit choice (断开保留画面) of one package
        # flipped (QML refreshes that check dot off this).
        behaviorPrefsChanged = pyqtSignal(str)
        engineLockedChanged = pyqtSignal(bool)
        turnScreenOffChanged = pyqtSignal()
        # The device media volume index changed (-1 = unknown; the slider
        # is the only writer after the first drag).
        mediaVolumeChanged = pyqtSignal()

        # ------------------------------------- thread hops (worker -> GUI)
        _devicesPolled = pyqtSignal(object)          # dict serial -> state
        _installedResolved = pyqtSignal(object)      # set of packages
        _adbResolved = pyqtSignal(str)
        # startAppOnDisplay outcome: package, ok, detail line for status.
        _appMoved = pyqtSignal(str, bool, str)
        # setMediaVolume outcome: index, ok, detail (worker hop).
        _mediaVolumeDone = pyqtSignal(int, bool, str)

        def __init__(self, adb_binary: str, parent: QObject | None = None) -> None:
                super().__init__(parent)
                self._adb_binary = adb_binary
                self._devices: dict[str, str] = {}
                # Online-serial baseline for late-plug refreshes (see
                # _apply_devices): None until the first poll lands.
                self._known_online: set[str] | None = None
                self._apps: list[dict[str, object]] = []
                self._pinned_apps: list[dict[str, object]] = []
                self._installed: set[str] | None = None
                self._portrait_prefs = load_portrait_prefs()
                self._pinned = load_pinned_prefs()
                self._display_prefs = load_display_prefs()
                # 窗口栏按应用覆盖（bars 节）：None/缺包 = 跟随设置页默认；
                # 启动注入每次重读磁盘（_pin_chrome_bars），此处仅存菜单态。
                self._bar_prefs = load_bar_prefs()
                # 音频独占按应用（audio 节，右键菜单勾选）：启动仲裁现场查
                # 这份内存态（_exclusive_audio），与 bars 一样即选即持久化。
                self._audio_prefs = load_audio_prefs()
                # 断开保留画面按应用（behavior 节，右键菜单勾选）：启动时
                # 现场查这份内存态注入 --no-vd-destroy-content（_keep_vd），
                # 与 audio 一样即选即持久化。
                self._behavior_prefs = load_behavior_prefs()
                # 设备媒体音量 index（0..15）；-1 = 未知——预读已放弃
                # （--get 输出无数字可解析，见 duo.core.adb），首次拖动
                # 滑杆（setMediaVolume）才进入已知态。
                self._media_volume = -1
                self._sessions: dict[str, subprocess.Popen[bytes]] = {}
                # Keys spawned with audio requested (no --no-audio in argv);
                # the audio_policy=latest restart consults this set.
                self._audio_keys: set[str] = set()
                # Pinned virtual-display geometry per session key - (w, h)
                # from an aspect-preset launch (DESIGN.md §3.6); plain
                # starts carry none (flex). The audio handover
                # (_restart_others_muted) respawns through
                # build_launch_argv and must re-pin the SAME geometry:
                # losing it would silently turn a fixed 21:9 session into
                # a flex one.
                self._session_size: dict[str, tuple[int, int]] = {}
                # Body-preset probe cache: the landscape AspectPreset
                # derived from `wm size` (None = not probed yet or probe
                # failed) plus a monotonic timestamp; a fresh-enough
                # result is reused so a menu click never shells out twice.
                # A FAILED probe caches None too (a flaky device must not
                # be hammered on every menu click); no-device reads are
                # NOT cached - the next click after a connect probes.
                self._body_preset: AspectPreset | None = None
                self._body_probe_at: float | None = None
                self._status_text = "就绪"
                # Mirror-screen-off seed (§3.5 右键镜像卡勾选项)：与设置页
                # 共用 settings.json，切一次写一次（toggleTurnScreenOff）。
                self._turn_screen_off = load_settings()[0].turn_screen_off

                # Hops deliver on this object's thread: queued when raised on
                # a worker/monitor thread, synchronous when a test raises the
                # same hop on the GUI thread.
                self._devicesPolled.connect(self._apply_devices)
                self._installedResolved.connect(self._apply_installed)
                self._adbResolved.connect(self.setAdb)
                self._appMoved.connect(self._apply_app_moved)
                self._mediaVolumeDone.connect(self._apply_media_volume)
                # App-model maintenance for the QML grid (queued from workers).
                self.allAppsReady.connect(self._merge_all_apps)
                self.iconReady.connect(self._apply_icon)
                self.appInfoReady.connect(self._apply_app_info)
                self.infoSweepDone.connect(self._apply_info_sweep_done)
                # Icon bursts flush as ONE appsChanged emit: the QML
                # QVariantList model rebuilds the whole grid (and destroys
                # every delegate, blanking async images) per emit - the
                # per-icon emits of the first sweep read as tiles vanishing
                # under the user's finger mid long-press.
                self._dirty_icons: set[str] = set()
                self._icon_flush = QTimer(self)
                self._icon_flush.setSingleShot(True)
                self._icon_flush.timeout.connect(self._flush_icon_batch)
                # Order freeze while an info sweep runs (2026-10 Windows 真机
                # 反馈「磁贴来回流动，找不到应用」): batches patch labels and
                # icons in place; _order_stale just records that the pinyin
                # order drifted, and infoSweepDone settles it with ONE
                # re-sort (see _apply_app_info). _order_stale_pinned marks
                # whether any touched entry lives in the pinned row - its
                # settle needs its own notify signal.
                self._order_stale = False
                self._order_stale_pinned = False
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
                """The QML grid model: unpinned entries only, pinyin-initial
                label order (catalog and third-party apps interleaved).
                Pinned tiles live in :attr:`pinnedApps` instead - the grid
                never repeats them (DESIGN.md §3.3).

                Entries carry package/label/key/installed/pinned plus
                ``icon`` as a file URL string (the bundled preset until the
                icon worker delivers the real one). Only packages the device
                reports installed ever enter the model (2026-09 真机反馈:
                不铺预置灰块) - every entry is ``installed=True`` by
                construction, and an empty installed set leaves the grid
                empty (the QML empty state covers that).
                """
                return self._apps

        @pyqtProperty(list, notify=pinnedAppsChanged)
        def pinnedApps(self) -> list[dict[str, object]]:
                """The pinned row model: same entry shape as :attr:`apps`,
                pinyin order within the row. Empty until something is
                pinned (catalog and third-party apps alike). Synced to the
                grid's installed filter: an uninstalled pinned app drops
                off the row, but its pin survives in prefs and the tile
                returns automatically once the package is installed again
                (deliberate - see :meth:`_rebuild_apps`)."""
                return self._pinned_apps

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

        @pyqtProperty(bool, notify=turnScreenOffChanged)
        def turnScreenOff(self) -> bool:
                """Mirror sessions blank the device screen (--turn-screen-off)."""
                return self._turn_screen_off

        @pyqtProperty(float, notify=mediaVolumeChanged)
        def mediaVolume(self) -> float:
                """Device media stream volume index 0..15; -1.0 = unknown.

                未知是常态开局：``cmd media_session volume --get`` 输出
                日志文本无可解析数字（真机实测），所以从不预读——首次
                ``setMediaVolume`` 后即已知，滑杆从「拖动即设定」的中性
                态切到带填充/拇指的已知态。
                """
                return float(self._media_volume)

        # ----------------------------------------------------------- slots

        @pyqtSlot(str)
        def startSession(self, package: str) -> None:
                """Spawn a chrome-clad mirror session and track it.

                Clicking an app that already has a live session does NOT spawn
                a second engine: it moves the app onto that session's virtual
                display (startAppOnDisplay) - the task may live on the
                physical screen after an earlier run, and re-delivering it is
                what "the app won't come to the mirror window" means.
                """
                serial = next(iter(self._monitor.online), "")
                if not serial:
                        self._set_status("设备未连接")
                        return
                self.reapSessions()
                if package in self._sessions:
                        self.startAppOnDisplay(package)
                        return
                portrait = self._portrait_prefs.get(package, False)
                # 显示模式记忆（§3.7）：fixed 记忆解析成具体几何后注入 argv，
                # 并钉进 _session_size——音频切换的静音重启必须重新钉住同一
                # 几何，不能把固定比例会话悄悄降级成 flex。
                preset = self._remembered_display(package)
                if preset is None:
                        argv = build_launch_argv(
                                package, serial, portrait,
                                keep_vd=self._keep_vd(package),
                        )
                        self._session_size.pop(package, None)   # plain start = flex
                else:
                        argv = build_launch_argv(
                                package,
                                serial,
                                portrait,
                                display={
                                        "mode": "fixed",
                                        "aspect": preset.id,
                                        "width": preset.width,
                                        "height": preset.height,
                                },
                                keep_vd=self._keep_vd(package),
                        )
                        self._session_size[package] = (preset.width, preset.height)
                restarted = self._apply_audio_policy(package, serial, argv)
                # Fresh log per session: the display-id parser takes the last
                # 'New display:' line, and a stale id from a previous run
                # would point at a dead display until the engine rewrites it.
                # Unlink 失败无害（Windows WinError 32：旧子进程仍握着日志
                # 句柄，新子进程自己会重建/截断）——但未捕获的 OSError 会
                # 从槽里上抛成 qFatal，整个面板退出，必须压住。
                with contextlib.suppress(OSError):
                        panel_log_path(package).unlink(missing_ok=True)
                try:
                        proc = self._spawn(argv)
                except OSError as exc:
                        self._set_status(f"启动失败：{session_label(package)}（{exc}）")
                        return
                self._sessions[package] = proc
                self._track_audio(package, argv)
                self._emit_sessions()
                orientation = "竖屏" if portrait else "横屏"
                suffix = f"（{'、'.join(restarted)} 已静音重启）" if restarted else ""
                self._set_status(f"已启动 {session_label(package)} · {orientation}{suffix}")

        @pyqtSlot(str, str)
        def startSessionWithAspect(self, package: str, aspect_id: str) -> None:
                """Spawn a session whose virtual display is pinned to a ratio.

                右键菜单「按比例打开」(DESIGN.md §3.6): the frozen preset - or
                the device-body pair, lazily probed via ``wm size`` - becomes
                a ``--display fixed --width/--height`` launch with orientation
                derived from the geometry. A live session for the package keeps
                the startSession dedupe semantics (move the app onto its
                display, no rebuild). Body ids need a connected device to
                derive from; TODO(真机): fixed-ratio sessions and the body
                probe are logic-ready only until the Windows 回填 (TODO.md
                「按比例打开」/「机身比例预设」) - the argv below is exact.
                """
                preset = self._preset_for_aspect_id(aspect_id)
                if preset is None:
                        if aspect_id in (BODY_LANDSCAPE_ID, BODY_PORTRAIT_ID):
                                self._set_status("机身比例需连接设备后使用")
                        else:
                                self._set_status(f"未知比例：{aspect_id}")
                        return
                serial = next(iter(self._monitor.online), "")
                if not serial:
                        self._set_status("设备未连接")
                        return
                self.reapSessions()
                if package in self._sessions:
                        self.startAppOnDisplay(package)
                        return
                argv = build_launch_argv(
                        package,
                        serial,
                        portrait=not preset.landscape,   # geometry overrules it
                        width=preset.width,
                        height=preset.height,
                        keep_vd=self._keep_vd(package),
                )
                self._session_size[package] = (preset.width, preset.height)
                restarted = self._apply_audio_policy(package, serial, argv)
                # 同 startSession：旧日志 unlink 失败（句柄被旧子进程握着）
                # 无害且必须压住，否则槽内上抛成 qFatal 面板退出。
                with contextlib.suppress(OSError):
                        panel_log_path(package).unlink(missing_ok=True)
                try:
                        proc = self._spawn(argv)
                except OSError as exc:
                        self._session_size.pop(package, None)
                        self._set_status(f"启动失败：{session_label(package)}（{exc}）")
                        return
                self._sessions[package] = proc
                self._track_audio(package, argv)
                self._emit_sessions()
                suffix = f"（{'、'.join(restarted)} 已静音重启）" if restarted else ""
                self._set_status(f"以 {preset.label} 打开 {session_label(package)}{suffix}")

        def _preset_for_aspect_id(self, aspect_id: str) -> AspectPreset | None:
                """Resolve a menu id: frozen table first, then the body pair."""
                preset = preset_by_id(aspect_id)
                if preset is not None:
                        return preset
                if aspect_id in (BODY_LANDSCAPE_ID, BODY_PORTRAIT_ID):
                        body = self._body_aspect_preset()
                        if body is None:
                                return None
                        return body if aspect_id == BODY_LANDSCAPE_ID else transposed(body)
                return None

        def _remembered_display(self, package: str) -> AspectPreset | None:
                """The app's remembered fixed display, resolved; None = flex.

                Body ids resolve through the cached wm-size probe here (a
                bare argv builder cannot); a remembered body choice whose
                probe now fails degrades to flex - better a flexible window
                than a refused launch. The preset's ``id`` carries the
                remembered aspect id back into the launch argv.
                """
                display = self._display_prefs.get(package)
                if not isinstance(display, dict) or display.get("mode") != "fixed":
                        return None
                return self._preset_for_aspect_id(str(display.get("aspect", "")))

        def _body_aspect_preset(self) -> AspectPreset | None:
                """The landscape body preset, lazily probed via ``wm size``.

                A fresh probe (success OR failure - None) is cached for
                :data:`_BODY_PROBE_TTL_S`; with no device online the cache is
                left alone and a miss returned, so the moment a device
                connects the next body click probes for real. The synchronous
                adb roundtrip is click-cheap (5s cap) and rare (cached).
                """
                now = time.monotonic()
                if (
                        self._body_probe_at is not None
                        and now - self._body_probe_at < _BODY_PROBE_TTL_S
                ):
                        return self._body_preset
                serial = next(iter(self._monitor.online), None)
                if not serial:
                        return None   # not cached: retry once a device is up
                self._body_probe_at = now
                try:
                        output = Adb(self._adb_binary, serial).run(
                                "shell", "wm", "size", timeout=_BODY_PROBE_TIMEOUT_S
                        )
                except (AdbError, OSError):
                        self._body_preset = None
                        return None
                self._body_preset = body_aspect_from_wm_size(output)
                return self._body_preset

        @pyqtSlot(str)
        def startAppOnDisplay(self, package: str) -> None:
                """Deliver ``package`` onto a running session's virtual display.

                Reads the display id from the session log (scrcpy's
                ``New display: ... (id=N)``), resolves the launchable
                component via ``cmd package resolve-activity`` and moves/
                starts it with ``am start --display N -n cmp`` - no session
                rebuild. Unknown display id (log missing or engine still
                starting) or a failed adb step degrades to a status message.

                This is also where "HOME" lands conceptually for panel-side
                routing: HOME on a virtual display is globally intercepted by
                the physical launcher (docs/window-experience.md §7.1.3), so
                going home from a session means coming back to this panel,
                never sending keyevent 3.
                """
                proc = self._sessions.get(package)
                if proc is None or proc.poll() is not None:
                        self._set_status(f"{session_label(package)} 会话未运行")
                        return
                serial = next(iter(self._monitor.online), "")
                if not serial:
                        self._set_status("设备未连接")
                        return
                display_id = display_id_from_log(panel_log_path(package))
                if display_id is None:
                        self._set_status(
                                f"{session_label(package)} 虚拟屏未就绪，稍后重试"
                        )
                        return

                adb_binary = self._adb_binary

                def work() -> None:
                        adb = Adb(adb_binary, serial)
                        try:
                                component = parse_resolve_activity(
                                        adb.run(
                                                "shell", "cmd", "package",
                                                "resolve-activity", "--brief", package,
                                        )
                                )
                                if component is None:
                                        self._appMoved.emit(
                                                package, False, "无法解析应用入口"
                                        )
                                        return
                                output = adb.run(
                                        "shell", "am", "start",
                                        "--display", str(display_id), "-n", component,
                                )
                        except (AdbError, OSError) as exc:
                                self._appMoved.emit(package, False, str(exc))
                                return
                        # am start reports failures in-band ("Error: ...") while
                        # still exiting 0 on some builds; "Warning: ... delivered
                        # to running instance" IS a success (the task exists).
                        failed = "Error" in output
                        self._appMoved.emit(
                                package,
                                not failed,
                                output.strip().splitlines()[0] if output.strip() else "",
                        )

                threading.Thread(target=work, daemon=True).start()

        @pyqtSlot(str, bool, str)
        def _apply_app_moved(self, package: str, ok: bool, detail: str) -> None:
                """Adopt a startAppOnDisplay outcome (worker hop)."""
                label = session_label(package)
                if ok:
                        self._set_status(f"已在虚拟屏打开 {label}")
                else:
                        self._set_status(f"打开失败：{label}（{detail}）")

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
                argv = build_device_mirror_argv(serial)
                restarted = self._apply_audio_policy(MIRROR_KEY, serial, argv)
                try:
                        proc = self._spawn(argv)
                except OSError as exc:
                        self._set_status(f"启动失败：设备镜像（{exc}）")
                        return
                self._sessions[MIRROR_KEY] = proc
                self._track_audio(MIRROR_KEY, argv)
                self._emit_sessions()
                suffix = f"（{'、'.join(restarted)} 已静音重启）" if restarted else ""
                self._set_status(f"已启动 设备镜像{suffix}")

        @pyqtSlot(str)
        def stopSession(self, key: str) -> None:
                """Terminate one session; the CLI's SIGTERM handler cleans up."""
                proc = self._sessions.get(key)
                if proc is None:
                        return
                proc.terminate()
                self._set_status(f"已关闭 {session_label(key)}")

        @pyqtSlot()
        def toggleTurnScreenOff(self) -> None:
                """Flip and persist the mirror screen-off choice (§3.5 菜单).

                Fresh load → replace → save, so a hand-edited settings.json
                wins over the cached flag. A failed save (validation or IO)
                only surfaces a status message: the in-memory flag must
                never drift from what is on disk.
                """
                settings, _problems = load_settings()
                new_value = not settings.turn_screen_off
                try:
                        save_settings(
                                dataclasses.replace(settings, turn_screen_off=new_value)
                        )
                except (OSError, ValueError) as exc:
                        self._set_status(f"设置保存失败：{exc}")
                        return
                self._turn_screen_off = new_value
                self.turnScreenOffChanged.emit()
                self._set_status(
                        "镜像时将关闭设备屏幕" if new_value else "镜像时保持设备屏幕常亮"
                )

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

        @pyqtSlot(str)
        def togglePin(self, package: str) -> None:
                """Pin/unpin an app: MOVE it between grid and pinned row.

                One user action = exactly one model mutation + one
                ``appsChanged``/``pinnedAppsChanged`` emit pair: the move,
                flag patch, re-sort and notify all land together, so QML
                rebuilds once (the batch contract the icon/info hops
                already follow). A package not currently in either model
                still persists its pin state - it applies when the entry
                appears (catalog landing, a third-party listing, or a
                reinstall after the installed-only grid dropped it): orphan
                pins are never pruned from prefs on purpose.
                """
                label = session_label(package)
                if package in self._pinned:
                        self._pinned.discard(package)
                        self._set_status(f"已取消置顶 {label}")
                        self._move_entry(self._pinned_apps, self._apps, package, False)
                else:
                        self._pinned.add(package)
                        self._set_status(f"已置顶 {label}")
                        self._move_entry(self._apps, self._pinned_apps, package, True)
                save_pinned_prefs(self._pinned)
                self._sort_apps()
                self.appsChanged.emit()
                self.pinnedAppsChanged.emit()

        def _move_entry(
                self,
                source: list[dict[str, object]],
                target: list[dict[str, object]],
                package: str,
                pinned: bool,
        ) -> bool:
                """Move one entry between the two models; False = absent."""
                for index, entry in enumerate(source):
                        if str(entry["package"]) == package:
                                entry["pinned"] = pinned
                                target.append(source.pop(index))
                                return True
                return False

        @pyqtSlot(str, result="bool")
        def portraitFor(self, package: str) -> bool:
                """Remembered orientation for ``package`` (catalog default)."""
                return self._portrait_prefs.get(package, False)

        @pyqtSlot(str, result="QVariant")
        def displayModeFor(self, package: str) -> dict[str, object]:
                """Remembered display mode for the QML menu selection dots.

                ``{"mode": "flex"}`` (the default, aspect absent) or
                ``{"mode": "fixed", "aspect": "16:9"}`` - the same shape the
                prefs document stores, verbatim.
                """
                display = self._display_prefs.get(package)
                if isinstance(display, dict) and display.get("mode") == "fixed":
                        return {
                                "mode": "fixed",
                                "aspect": str(display.get("aspect", "")),
                        }
                return {"mode": "flex"}

        @pyqtSlot(str)
        def setDisplayFlex(self, package: str) -> None:
                """Remember 'follow the window' for the app (persist + notify)."""
                self._display_prefs[package] = {"mode": "flex"}
                save_display_prefs(self._display_prefs)
                self._set_status(f"{session_label(package)} 将自适应窗口")
                self.displayModeChanged.emit(package)

        @pyqtSlot(str, str)
        def setDisplayFixed(self, package: str, aspect_id: str) -> None:
                """Remember a fixed aspect for the app (persist + notify).

                右键菜单「固定比例 ▸」二级项（§3.7）：选择即记忆，此后的普通
                点击按该比例常驻启动。aspect id 走与 startSessionWithAspect
                同一张校验表——冻结表 id 直接过，机身对需设备探测（无设备/
                探测失败只报状态不落库），未知 id 拒绝。一次性按比例启动
                （startSessionWithAspect）不经此路，不改记忆。
                """
                preset = self._preset_for_aspect_id(aspect_id)
                if preset is None:
                        if aspect_id in (BODY_LANDSCAPE_ID, BODY_PORTRAIT_ID):
                                self._set_status("机身比例需连接设备后使用")
                        else:
                                self._set_status(f"未知比例：{aspect_id}")
                        return
                self._display_prefs[package] = {"mode": "fixed", "aspect": aspect_id}
                save_display_prefs(self._display_prefs)
                self._set_status(f"{session_label(package)} 将以 {preset.label} 常驻")
                self.displayModeChanged.emit(package)

        @pyqtSlot(str, str, result="QVariant")
        def barModeFor(self, package: str, which: str) -> dict[str, object]:
                """Window-bar state for the QML menu selection dots.

                ``{"explicit": bool, "mode": effective}`` - the effective
                mode is the per-app override when one is remembered, else
                the settings default (fresh read, so a settings-page save
                between two menu openings reaches the next one). The mirror
                key (no app) only ever reports the default.
                """
                settings, _problems = load_settings()
                default = (
                        settings.bottom_bar_mode if which == "bottom"
                        else settings.top_bar_mode
                )
                override: str | None = None
                if which in ("top", "bottom"):
                        value = (self._bar_prefs.get(package) or {}).get(which)
                        if value in VALID_BAR_MODES:
                                override = value
                return {"explicit": override is not None, "mode": override or default}

        @pyqtSlot(str, str, str)
        def setAppBar(self, package: str, which: str, mode: str) -> None:
                """Remember (or clear) one per-app window-bar override.

                右键菜单「窗口栏 ▸」二级项：``mode`` ∈ immersive|native|none
                钉住该应用的该条栏（none = 该边永不建栏，2026-09-09 新
                第三态）；空串清除 override（重新跟随设置页默认）。选择即
                持久化，此后该应用的启动 argv 注入 effective 值（见
                _pin_chrome_bars）；非法 which/mode 只报状态不落库。
                """
                if which not in ("top", "bottom"):
                        self._set_status(f"未知窗口栏：{which}")
                        return
                # 规整成完整两键形状（未设的键显式存 null，与 load 返回的
                # 形状对称；缺省手改文件同样被补齐）。
                remembered = self._bar_prefs.get(package) or {}
                choice: dict[str, str | None] = {
                        "top": remembered.get("top"),
                        "bottom": remembered.get("bottom"),
                }
                if mode == "":
                        choice[which] = None
                elif mode in VALID_BAR_MODES:
                        choice[which] = mode
                else:
                        self._set_status(f"未知窗口栏模式：{mode}")
                        return
                # 两条都清了的应用整条退场（跟从未设置过一样，不存空壳节）。
                if all(value is None for value in choice.values()):
                        self._bar_prefs.pop(package, None)
                else:
                        self._bar_prefs[package] = choice
                save_bar_prefs(self._bar_prefs)
                bar = "上巴" if which == "top" else "下巴"
                if mode == "":
                        self._set_status(
                                f"{session_label(package)} {bar}将跟随默认窗口栏")
                elif mode == "none":
                        self._set_status(f"{session_label(package)} {bar}将不显示")
                else:
                        style = "系统" if mode == "native" else "沉浸"
                        self._set_status(f"{session_label(package)} {bar}将使用{style}栏")
                self.barPrefsChanged.emit(package)

        @pyqtSlot(str, str)
        def setDefaultBarMode(self, which: str, mode: str) -> None:
                """Write the GLOBAL window-bar default (mirror card menu).

                镜像卡右键菜单的「窗口栏」小节：设备镜像没有应用包，
                :func:`_pin_chrome_bars` 只取设置页默认（top_bar_mode /
                bottom_bar_mode）——所以这里直接写 settings.json 那两个
                字段（设置页同一对值，两处永远一致）。Fresh load →
                replace → save，手改 settings.json 不丢；非法值/保存失败
                只报状态不落库。生效于下一次投屏（已在跑的会话不变）。
                """
                if which not in ("top", "bottom") or mode not in VALID_BAR_MODES:
                        self._set_status(f"未知窗口栏：{which}/{mode}")
                        return
                settings, _problems = load_settings()
                if which == "bottom":
                        if settings.bottom_bar_mode == mode:
                                return
                        new_settings = dataclasses.replace(
                                settings, bottom_bar_mode=mode)
                else:
                        if settings.top_bar_mode == mode:
                                return
                        new_settings = dataclasses.replace(settings, top_bar_mode=mode)
                try:
                        save_settings(new_settings)
                except (OSError, ValueError) as exc:
                        self._set_status(f"设置保存失败：{exc}")
                        return
                bar = "上巴" if which == "top" else "下巴"
                if mode == "none":
                        self._set_status(f"默认{bar}将不显示")
                else:
                        style = "系统" if mode == "native" else "沉浸"
                        self._set_status(f"默认{bar}将使用{style}栏")
                # 空串 = 镜像键（设备镜像无应用包）：镜像菜单选中点刷新
                self.barPrefsChanged.emit("")

        @pyqtSlot(str, result="bool")
        def audioExclusiveFor(self, package: str) -> bool:
                """Remembered audio-exclusivity for ``package`` (menu check state)."""
                return self._exclusive_audio(package)

        @pyqtSlot(str, bool)
        def setAudioExclusive(self, package: str, on: bool) -> None:
                """Remember (or clear) the per-app audio-exclusivity choice.

                右键菜单「音频独占」一级勾选：勾选 = 该应用启动时抢走音频
                （其余在跑会话含设备镜像全部静音重启，独占者退出后其余
                保持静音）；清掉的应用回到全局 audio_policy。与 bars 同款
                纪律：关不存空壳节，整条退场。
                """
                if on:
                        self._audio_prefs[package] = {"exclusive": True}
                else:
                        self._audio_prefs.pop(package, None)
                save_audio_prefs(self._audio_prefs)
                label = session_label(package)
                if on:
                        self._set_status(f"{label} 启动时将独占音频（其余会话静音）")
                else:
                        self._set_status(f"{label} 不再独占音频")
                self.audioPrefsChanged.emit(package)

        @pyqtSlot(str, result="bool")
        def keepVdFor(self, package: str) -> bool:
                """Remembered keep-on-vd choice for ``package`` (menu check)."""
                return self._keep_vd(package)

        @pyqtSlot(str, bool)
        def setKeepVd(self, package: str, on: bool) -> None:
                """Remember (or clear) the per-app 断开保留画面 choice.

                右键菜单「断开保留画面」一级勾选：勾选的应用启动时带
                scrcpy ``--no-vd-destroy-content``，会话断开后应用留在
                虚拟屏（不回落手机主屏，下次打开原地续用）；清掉回默认
                （断开即销毁虚拟屏，应用退回主屏）。与 bars/audio 同款
                纪律：关不存空壳节，整条退场。
                """
                if on:
                        self._behavior_prefs[package] = {"keep_vd": True}
                else:
                        self._behavior_prefs.pop(package, None)
                save_behavior_prefs(self._behavior_prefs)
                label = session_label(package)
                if on:
                        self._set_status(f"{label} 断开后将保留在虚拟屏")
                else:
                        self._set_status(f"{label} 断开后将退回手机主屏")
                self.behaviorPrefsChanged.emit(package)

        @pyqtSlot(int)
        def setMediaVolume(self, index: int) -> None:
                """Set the device media stream volume (0..15; QML 防抖 200ms).

                调 Android 侧媒体流——投屏的采集源就在那里，Windows 端
                增益救不了「音频比系统提示音小」。预读已放弃（--get 无
                数字可解析）：首次拖动经此槽进入已知态；设备不在线时
                只报状态，不动已知态（滑杆离线即隐藏，此路纯防御）。
                """
                serial = next(iter(self._monitor.online), "")
                if not serial:
                        self._set_status("设备未连接，音量未调整")
                        return
                clamped = max(0, min(MEDIA_VOLUME_MAX, int(index)))
                self._media_volume = clamped
                self.mediaVolumeChanged.emit()
                adb_binary = self._adb_binary

                def work() -> None:
                        try:
                                media_volume(Adb(adb_binary, serial), clamped)
                        except (AdbError, OSError) as exc:
                                self._mediaVolumeDone.emit(clamped, False, str(exc))
                                return
                        self._mediaVolumeDone.emit(clamped, True, "")

                threading.Thread(target=work, daemon=True).start()

        @pyqtSlot(int, bool, str)
        def _apply_media_volume(self, index: int, ok: bool, detail: str) -> None:
                """Adopt a media-volume command outcome (worker hop)."""
                if ok:
                        self._set_status(f"媒体音量 {index}/{MEDIA_VOLUME_MAX}")
                else:
                        self._set_status(f"媒体音量调整失败（{detail}）")

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
                        self._audio_keys.discard(key)
                        self._session_size.pop(key, None)
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

        # ------------------------------------------------- audio arbitration

        def _audio_policy(self) -> str:
                """Fresh ``audio_policy`` from settings (default ``latest``).

                Re-read per launch: the spawned CLI process re-reads the same
                settings.json itself, so the panel decision and the CLI
                decision can never diverge mid-launch.
                """
                try:
                        settings, _problems = load_settings()
                except OSError:
                        return "latest"
                return settings.audio_policy

        def _track_audio(self, key: str, argv: list[str]) -> None:
                """Record whether ``key`` was spawned with audio requested."""
                if "--no-audio" in argv:
                        self._audio_keys.discard(key)
                else:
                        self._audio_keys.add(key)

        def _apply_audio_policy(
                self, new_key: str, serial: str, argv: list[str]
        ) -> list[str]:
                """Apply the audio decision to a session about to be spawned.

                Mutates ``argv`` in place (off pins --no-audio) and returns
                the labels of restarted sessions (for the status line).
                Precedence order:

                1. Per-app 音频独占 (gui_prefs ``audio`` 节, right-click
                   menu) beats the global policy: the launch takes audio
                   whatever ``audio_policy`` says (argv never gets
                   --no-audio) and every other running audio session -
                   MIRROR_KEY included - restarts muted first, so the lock
                   is free when the exclusive session starts. When that
                   owner later exits, the muted sessions STAY muted on
                   purpose (no auto-restore; a manual restart is the way
                   back).
                2. ``off`` - pin --no-audio here (CLI re-arbitrates too).
                3. ``latest`` - newest session wins the audio lock: restart
                   the others muted first. ``all`` - parallel audio is the
                   explicit ask, nobody restarts.
                """
                if self._exclusive_audio(new_key):
                        return self._restart_others_muted(new_key, serial)
                policy = self._audio_policy()
                if policy == "off":
                        argv.append("--no-audio")
                        return []
                if policy != "latest":
                        return []   # all: parallel audio is the explicit ask
                return self._restart_others_muted(new_key, serial)

        def _exclusive_audio(self, key: str) -> bool:
                """Whether ``key`` launches with exclusive audio (per-app pref).

                The mirror key has no menu entry, so it can never be
                exclusive; a corrupt entry reads as off.
                """
                if key == MIRROR_KEY:
                        return False
                choice = self._audio_prefs.get(key)
                return isinstance(choice, dict) and bool(choice.get("exclusive"))

        def _keep_vd(self, key: str) -> bool:
                """Whether ``key`` keeps its virtual display on exit.

                The mirror key has no virtual display of its own (and no
                menu entry), so it can never keep one; a corrupt entry
                reads as the default (destroy on exit).
                """
                if key == MIRROR_KEY:
                        return False
                choice = self._behavior_prefs.get(key)
                return isinstance(choice, dict) and bool(choice.get("keep_vd"))

        def _restart_others_muted(self, new_key: str, serial: str) -> list[str]:
                """audio_policy=latest handover: newest session wins the audio.

                Two parallel audio captures crackle (duo.core.audio_lock).
                This panel owns its child CLI processes, so it can hand audio
                to the newcomer: each other live audio session is terminated
                (its CLI releases the audio lock on SIGTERM), waited for, then
                respawned with ``--no-audio`` - the same build_*_argv +
                _spawn path every session already uses, so a restart is just
                a stop+start with a different flag. Sessions this panel does
                not own (standalone CLI runs) cannot be restarted; the CLI's
                AudioLock fallback mutes the new session instead and prints
                the reason to its session log.
                """
                restarted: list[str] = []
                for key in list(self._sessions):
                        proc = self._sessions.get(key)
                        if key == new_key or key not in self._audio_keys:
                                continue
                        if proc is None or proc.poll() is not None:
                                continue
                        self._sessions.pop(key, None)
                        self._audio_keys.discard(key)
                        proc.terminate()
                        # Bounded wait: the CLI's SIGTERM handler releases the
                        # audio lock; on timeout respawn anyway (the old one
                        # dies on its own without taking the lock again).
                        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                                proc.wait(timeout=5.0)
                        if key == MIRROR_KEY:
                                respawn_argv = build_device_mirror_argv(serial, muted=True)
                        else:
                                # Re-pin the session's aspect geometry: the
                                # handover must not quietly downgrade a
                                # fixed-ratio session to flex - nor drop its
                                # keep-vd choice (same session, same exit
                                # behavior).
                                size = self._session_size.get(key)
                                respawn_argv = build_launch_argv(
                                        key,
                                        serial,
                                        self._portrait_prefs.get(key, False),
                                        muted=True,
                                        width=size[0] if size else None,
                                        height=size[1] if size else None,
                                        keep_vd=self._keep_vd(key),
                                )
                        # 同 startSession：Windows 上旧子进程可能仍握着
                        # 日志句柄（WinError 32），unlink 失败无害——新
                        # 子进程自己重建/截断日志；未捕获上抛 = qFatal
                        # 整面板退出（真机「固定比例启动后 Duo.exe 退出」
                        # 的真凶），必须压住。
                        with contextlib.suppress(OSError):
                                panel_log_path(key).unlink(missing_ok=True)
                        try:
                                self._sessions[key] = self._spawn(respawn_argv)
                        except OSError as exc:
                                self._set_status(
                                        f"音频切换失败：{session_label(key)}（{exc}）")
                                continue
                        self._track_audio(key, respawn_argv)
                        restarted.append(session_label(key))
                if restarted:
                        self._emit_sessions()
                return restarted

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
                """Adopt a fresh serial -> state map and notify bindings.

                A device that appears AFTER startup also re-runs the
                installed sweep: the constructor's one-shot check left every
                tile grey (installed=False) for a late plug, and neither the
                third-party listing nor icon pulling would ever run - they
                only ride ``_apply_installed``. The first poll just records
                the baseline (the constructor already swept once); only
                ADDITIONS re-check - a dropped device changes nothing on
                disk, and a re-sweep would only poke adb to learn the same
                nothing-installed-anywhere-new answer.
                """
                assert isinstance(states, dict)
                self._devices = dict(states)
                online = {s for s, state in states.items() if state == "device"}
                if self._known_online is not None and online - self._known_online:
                        self.refreshInstalled()
                self._known_online = online
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
                """Rebuild the grid + pinned-row models, then re-sort both.

                网格只显示设备上真实安装的应用（2026-09 真机反馈「预设的
                软件应该关闭，不要预设软件」）：目录条目仅在包名出现在
                ``installed`` 里时进模型——空集即空网格（空态由 QML 既有
                逻辑显示），不再无条件铺预置灰块。第三方条目随卸载（不在
                ``installed`` 里）一起从模型移除，不灰显滞留；幸存者保住
                已解析的图标/标签。每个条目只落一个列表——固定的包在固定
                行，其余在网格；模型内条目一律 ``installed=True``（构造
                期即过滤，QML 的 installedCount 判断自然成立）。

                孤儿 pin（固定过但设备上已卸载）随固定卡一起消失，但保留
                在 prefs 里——有意行为：重装后包回到 ``installed``，固定卡
                自动恢复，用户的固定选择不因一次卸载而丢（见 togglePin）。
                """
                previous = {
                        str(entry["package"]): entry
                        for entry in (*self._apps, *self._pinned_apps)
                }
                pinned_before = {str(entry["package"]) for entry in self._pinned_apps}
                entries: list[dict[str, object]] = []
                for preset in APP_CATALOG:
                        package = str(preset.package)
                        if package not in installed:
                                continue   # 未安装的目录项：不铺预置灰块
                        label = str(preset.label)
                        old = previous.pop(package, None)
                        if old is not None:
                                old["installed"] = True
                                old["pinned"] = package in self._pinned
                                old["key"] = label_sort_key(str(old["label"]))
                                entries.append(old)
                                continue
                        preset_icon = preset_icon_path(package) if _PRESETS_READY else None
                        entries.append({
                                "package": package,
                                "label": label,
                                "key": label_sort_key(label),
                                "icon": _icon_url(preset_icon) if preset_icon else "",
                                "installed": True,
                                "pinned": package in self._pinned,
                        })
                for entry in previous.values():
                        package = str(entry["package"])
                        if package not in installed:
                                continue   # 卸载即消失，不灰显滞留
                        entry["installed"] = True
                        entry["pinned"] = package in self._pinned
                        entry["key"] = label_sort_key(str(entry["label"]))
                        entries.append(entry)
                self._apps = [entry for entry in entries if not entry["pinned"]]
                self._pinned_apps = [entry for entry in entries if entry["pinned"]]
                self._sort_apps()
                self.appsChanged.emit()
                # Emit when the row emptied too (uninstall dropped the last
                # pinned tile): a silent stale pinned card is worse than a
                # rebuild.
                if self._pinned_apps or pinned_before:
                        self.pinnedAppsChanged.emit()

        def _sort_apps(self) -> None:
                """Order grid and pinned row by the cached pinyin search key.

                The key was computed at construction / label-patch time, so
                sorting never re-runs the pinyin walk per comparison; label
                and package tie-break so rebuilds stay deterministic.
                Emits nothing - callers notify once with their batched
                signals.
                """

                def entry_key(entry: dict[str, object]) -> tuple[str, str, str]:
                        return (str(entry["key"]), str(entry["label"]), str(entry["package"]))

                self._apps.sort(key=entry_key)
                self._pinned_apps.sort(key=entry_key)

        @pyqtSlot(list)
        def _merge_all_apps(self, packages: list[str]) -> None:
                """Extend the models with third-party packages just discovered."""
                known = {str(entry["package"]) for entry in self._apps}
                known |= {str(entry["package"]) for entry in self._pinned_apps}
                fresh: list[dict[str, object]] = []
                for package in packages:
                        if package in known:
                                continue
                        label = package_to_label(package)
                        preset_icon = preset_icon_path(package) if _PRESETS_READY else None
                        fresh.append({
                                "package": package,
                                "label": label,
                                "key": label_sort_key(label),
                                "icon": _icon_url(preset_icon) if preset_icon else "",
                                "installed": True,   # a -3 listing IS the installed set
                                "pinned": package in self._pinned,
                        })
                if fresh:
                        self._apps.extend(entry for entry in fresh if not entry["pinned"])
                        self._pinned_apps.extend(entry for entry in fresh if entry["pinned"])
                        self._sort_apps()
                        self.appsChanged.emit()
                        if self._pinned_apps:
                                self.pinnedAppsChanged.emit()

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
                """One rebuild per icon burst, not one per icon (both models)."""
                if self._dirty_icons:
                        pinned_touched = self._dirty_icons & {
                                str(entry["package"]) for entry in self._pinned_apps
                        }
                        self._dirty_icons.clear()
                        self.appsChanged.emit()
                        if pinned_touched:
                                self.pinnedAppsChanged.emit()

        @pyqtSlot(object)
        def _apply_app_info(self, batch: object) -> None:
                """Adopt resolved metadata for a batch of apps (worker hop).

                Every entry lands in the model before the single
                ``appsChanged`` emit, so QML rebuilds the grid once per
                batch instead of once per app (the widgets-era grid only
                swapped icons in place; a QVariantList model cannot).

                扫描期间顺序冻结（2026-10 Windows 真机反馈「刷新图标/标签
                时磁贴来回流动，找不到应用」）：这里只原位 patch —— 新图标
                在原地弹出（QML 的图像缓存让旧图标不闪白），标签换字不换
                位，刻意不调 ``_sort_apps`` —— 每批（每 8 个）重排一次等于
                网格反复洗牌，用户正在找的磁贴一直在漂。顺序改由
                :meth:`_apply_info_sweep_done` 在 ``infoSweepDone`` 后一次
                落位；期间真实变化的 label 只记入 ``_order_stale``
                （未变化的同值 patch 不算 —— 全缓存命中的重扫免付落位重建）。
                """
                assert isinstance(batch, list)
                changed = False
                pinned_changed = False
                pinned_packages = {str(entry["package"]) for entry in self._pinned_apps}
                labels_before = {
                        str(entry["package"]): str(entry["label"])
                        for entry in (*self._apps, *self._pinned_apps)
                }
                for package, icon_path, label in batch:
                        # The label patch syncs the cached pinyin key so
                        # search follows the real name; the ORDER settles
                        # at sweep end (_apply_info_sweep_done).
                        fields: dict[str, str] = {"label": label, "key": label_sort_key(label)}
                        if icon_path:
                                fields["icon"] = _icon_url(icon_path)
                        if self._patch_app_entry(package, **fields):
                                changed = True
                                pinned_changed = pinned_changed or package in pinned_packages
                                if labels_before.get(package) != label:
                                        # A real label change may move the
                                        # pinyin order - deferred, not
                                        # applied here (tiles must not flow).
                                        self._order_stale = True
                                        self._order_stale_pinned = (
                                                self._order_stale_pinned
                                                or package in pinned_packages
                                        )
                if changed:
                        self.appsChanged.emit()
                        if pinned_changed:
                                self.pinnedAppsChanged.emit()

        @pyqtSlot()
        def _apply_info_sweep_done(self) -> None:
                """Settle the frozen grid order once, when the sweep ends.

                扫描期间顺序冻结的兑现点（见 _apply_app_info）：真实的拼音
                序在网格最终落位 —— 整个扫描期用户看到的都是稳定不动的磁
                贴，结束时只经历一次可预期的重排；无 label 变化的扫描
                （全缓存命中/无设备提前返回）连这一次重排也省掉。固定行
                同批落位，被触及过的固定行补发自己的 pinnedAppsChanged。
                """
                if not self._order_stale:
                        return
                self._order_stale = False
                pinned_stale = self._order_stale_pinned
                self._order_stale_pinned = False
                self._sort_apps()
                self.appsChanged.emit()
                if pinned_stale:
                        self.pinnedAppsChanged.emit()

        def _patch_app_entry(self, package: str, **fields: str) -> bool:
                """Patch one entry in place (grid or pinned row); False =
                unknown package.

                Emits nothing: callers batch patches and notify once.
                """
                for entry in (*self._apps, *self._pinned_apps):
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
                        for preset in APP_CATALOG:
                                package = str(preset.package)
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
                        try:
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
                                # Results hop in chunks of 8: a first sweep over
                                # ~100 third-party packages costs minutes of adb
                                # roundtrips, and a single end-of-sweep emit leaves
                                # every label as its package-derived fallback the
                                # whole time (reads as "exploration stopped").
                                # Chunking keeps the batch contract per hop - one
                                # appsChanged = one grid rebuild - while progress
                                # lands visibly; _apply_app_info patches in place
                                # and freezes the order until the sweep ends.
                                chunk_size = 8
                                batch: list[tuple[str, object, str]] = []
                                for package in packages:
                                        try:
                                                info = app_info(adb, package)
                                        except Exception:
                                                continue
                                        batch.append((package, info.icon_path, info.label))
                                        if len(batch) >= chunk_size:
                                                self.appInfoReady.emit(batch)
                                                batch = []
                                if batch:
                                        self.appInfoReady.emit(batch)
                        finally:
                                # Sweep over - on success, on an early return
                                # (no device / listing failed) or on any
                                # unexpected error: the frozen order settles
                                # exactly once (see _apply_info_sweep_done).
                                # Without this, a stale _order_stale could
                                # ride an aborted sweep forever.
                                self.infoSweepDone.emit()

                threading.Thread(target=work, daemon=True).start()

"""Engine tooling: locate scrcpy/adb binaries and probe their versions.

Development happens inside WSL, while experiments run on the Windows side
(see plan.md, section 7). WSL interop lets us launch Windows executables
directly, so under WSL the ``.exe`` variants are preferred: they see USB
devices and open real windows on the Windows desktop.

This module is the single place that knows about engine binary details
(see plan.md, section 3 "分层原则"). All functions are Qt-free so the
core layer stays importable and testable without a GUI stack.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from duo.core.winproc import creation_flags

#: Tools Duo depends on for the mirroring engine.
REQUIRED_TOOLS: tuple[str, ...] = ("scrcpy", "adb")

#: Timeout (seconds) for version probes against external binaries.
_PROBE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class ToolInfo:
        """Result of probing one external tool."""

        name: str
        path: str | None
        version: str | None

        @property
        def available(self) -> bool:
                """Whether the tool was found and is executable."""
                return self.path is not None


@lru_cache(maxsize=1)
def is_wsl() -> bool:
        """Whether we are running inside the Windows Subsystem for Linux."""
        if os.environ.get("WSL_DIST_NAME") or os.environ.get("WSL_INTEROP"):
                return True
        try:
                release = subprocess.run(
                        ["uname", "-r"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=_PROBE_TIMEOUT_S,
                        check=False,
                ).stdout.lower()
        except (OSError, subprocess.TimeoutExpired):
                return False
        return "microsoft" in release or "wsl" in release


def tool_names(tool: str) -> tuple[str, ...]:
        """Candidate executable names for ``tool``, best match first.

        Under WSL the Windows build wins: only it can reach USB devices and
        spawn windows on the Windows desktop. The Linux build is kept as a
        fallback so Duo also works in plain Linux environments.
        """
        if is_wsl():
                return (f"{tool}.exe", tool)
        return (tool,)


def probe(tool: str) -> ToolInfo:
        """Locate ``tool`` on PATH and read the first line of its ``--version`` output."""
        for name in tool_names(tool):
                found = shutil.which(name)
                if found is None:
                        continue
                try:
                        result = subprocess.run(
                                [found, "--version"],
                                capture_output=True,
                                text=True,
                        encoding="utf-8",
                        errors="replace",
                                timeout=_PROBE_TIMEOUT_S,
                                check=False,
                                creationflags=creation_flags(),
                        )
                except (OSError, subprocess.TimeoutExpired):
                        return ToolInfo(name=tool, path=found, version=None)
                lines = (result.stdout or result.stderr).strip().splitlines()
                version = lines[0] if lines else None
                return ToolInfo(name=tool, path=found, version=version)
        return ToolInfo(name=tool, path=None, version=None)


def probe_binary(path: str, name: str = "") -> ToolInfo:
        """Probe an explicit binary path with ``--version`` (settings override).

        Unlike :func:`probe` (PATH discovery), the user-supplied path is used
        as-is: one argv element, no shell, no joined command strings. Available
        means the binary ran with exit code 0 and produced a version line.
        """
        label = name or Path(path).name
        try:
                result = subprocess.run(
                        [path, "--version"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=_PROBE_TIMEOUT_S,
                        check=False,
                        creationflags=creation_flags(),
                )
        except (OSError, subprocess.TimeoutExpired):
                return ToolInfo(name=label, path=None, version=None)
        lines = (result.stdout or result.stderr).strip().splitlines()
        if result.returncode != 0:
                return ToolInfo(name=label, path=None, version=None)
        version = lines[0] if lines else None
        return ToolInfo(name=label, path=path, version=version)


# ----------------------------------------------------------------------------
# Engine argument assembly (EngineArgs)
#
# The dataclasses below compile a session description into scrcpy argv.
# Version quirks discovered by experiment (see plan.md, section 7) live here:
#   - scrcpy >= 3.0 has no positive ``--clipboard-autosync`` flag (default on,
#     only ``--no-clipboard-autosync`` exists) — never emit the positive form.
#   - ``--flex-display`` must be paired with ``--new-display``.
#   - Flex size source (2026-09-06 user decision): ALWAYS the original
#     resolution - a bare ``--new-display`` (=/dpi with a custom density)
#     sizes the virtual display at the MAIN display's full resolution
#     (verified live on a 2400x3392 panel). The same-day baseline tiers
#     (FLEX_SIZES / settings ``flex_resolution``) were withdrawn: picking a
#     tier confused users, and the smoothness budget is carried by
#     video_codec=h264 + fps=60 instead (docs/mirroring-quality.md §7).
#   - Virtual displays keep system decorations ON: the AOSP
#     SecondaryDisplayLauncher that appears is the in-session "virtual
#     desktop" the chin's long-press opens (2026-09-06 user decision -
#     flipping back from the earlier suppression, which left app sessions
#     with no reachable desktop page).
#   - ``--start-app`` values always carry the ``+`` prefix: without it an app
#     that already has a live task on the physical screen stays there
#     ("delivered to running instance") and the virtual display shows
#     nothing (§7.1.4). The prefix force-stops first, so the task reliably
#     lands on the new display.
# ----------------------------------------------------------------------------

DisplayMode = Literal["mirror", "flex", "fixed"]

@dataclass(frozen=True)
class WindowGeometry:
        """A scrcpy window placement (screen coordinates)."""

        x: int
        y: int
        width: int
        height: int


def _int_flag(name: str, value: int | None) -> list[str]:
        """Emit a window geometry flag only when a value is set."""
        return [f"--{name}={value}"] if value is not None else []


@dataclass(frozen=True)
class DisplaySpec:
        """Which Android display to stream.

        mirror: the physical device screen.
        flex: a virtual display that continuously resizes to match the window
                (needs scrcpy >= 4.1 with ``--flex-display``). Always created
                at the main display's original resolution (bare
                ``--new-display``; 2026-09-06 user decision - the former
                baseline-size tiers were withdrawn, smoothness is carried by
                video_codec=h264 + fps=60 instead).
        fixed: a virtual display with a locked resolution.
        """

        mode: DisplayMode = "flex"
        width: int | None = None
        height: int | None = None
        dpi: int | None = 480

        def to_flags(self) -> list[str]:
                """Compile to scrcpy display flags.

                Virtual displays (flex/fixed) always disable system
                decorations: with them, Android raises the AOSP
                SecondaryDisplayLauncher on the display (the "confusing app
                selector", docs/window-experience.md §7.1) and an empty
                flex session shows that picker full-screen. Trade-off
                (recorded, pending Windows check in TODO task 7): with no
                app the display renders no frames at all.
                """
                if self.mode == "mirror":
                        return []
                if self.mode == "flex":
                        # ONE-WAY follow (2026-09-06): window -> display
                        # follows (the app reflows to the user's window), but
                        # display/app -> NEVER moves the window. scrcpy only
                        # auto-resizes its window while the user has not
                        # manually resized it; the overlay performs one
                        # no-op size nudge at session start to mark
                        # user-resized from tick one, so orientation flips
                        # (pilipili's portrait video page) letterbox inside
                        # instead of flipping the window.
                        # Size source (2026-09-06 user decision): ALWAYS the
                        # original resolution - a bare --new-display (=/dpi
                        # with a custom density) builds the virtual display at
                        # the main display's full size. The same-day baseline
                        # tiers (FLEX_SIZES / flex_resolution) were withdrawn:
                        # picking a tier confused users; smoothness is carried
                        # by video_codec=h264 + fps=60 (docs §7). Explicit
                        # width/height never pin a flex display anyway - flex
                        # follows the window. System decorations stay ON: the
                        # secondary-display launcher is the in-session
                        # "virtual desktop" (chin long-press).
                        value = f"/{self.dpi}" if self.dpi else ""
                        new_display = f"--new-display={value}" if value else "--new-display"
                        return [new_display, "--flex-display"]
                if self.width is None or self.height is None:
                        raise ValueError("fixed display mode requires width and height")
                value = f"{self.width}x{self.height}"
                if self.dpi:
                        value += f"/{self.dpi}"
                return [f"--new-display={value}"]


@dataclass(frozen=True)
class VideoSpec:
        """Video encoding parameters.

        ``encoder=None`` lets scrcpy pick the device's default encoder;
        since the ``video_codec`` setting landed, the pin normally comes
        from the hardware-encoder probe (duo.core.codec, cached in
        ``data_dir/encoders.json``) rather than a hand preset.
        """

        codec: str = "h265"
        encoder: str | None = None
        bitrate_mbps: int = 30
        max_fps: int = 90

        def to_flags(self) -> list[str]:
                """Compile to scrcpy video flags."""
                flags = [f"--video-codec={self.codec}"]
                if self.encoder:
                        flags.append(f"--video-encoder={self.encoder}")
                flags.append(f"--video-bit-rate={self.bitrate_mbps}M")
                flags.append(f"--max-fps={self.max_fps}")
                return flags


@dataclass(frozen=True)
class EngineArgs:
        """A complete mirroring session, compilable to a scrcpy command."""

        serial: str
        adb_binary: str | None = None       # pin scrcpy to our adb (server wars)
        display: DisplaySpec = DisplaySpec()
        video: VideoSpec = VideoSpec()
        app_package: str | None = None
        screen_off: bool = True
        stay_awake: bool = True
        keyboard: str = "uhid"
        audio: bool = True
        audio_codec: str = "flac"          # lossless; bandwidth is cheap on USB
        audio_buffer_ms: int = 100         # >50ms default: kills crackling
        window_title: str | None = None
        window_x: int | None = None
        window_y: int | None = None
        window_width: int | None = None
        window_height: int | None = None
        borderless: bool = False
        print_fps: bool = True            # stderr 周期 fps 行 → 会话日志（卡顿诊断）

        def to_argv(self, binary: str = "scrcpy") -> list[str]:
                """Compile to a full argv for the scrcpy binary."""
                argv = [binary, f"--serial={self.serial}"]
                # NOTE: adb_binary deliberately does NOT become an argv flag:
                # scrcpy 4.1 has no --adb option ("unknown option" + restart
                # loop, found live 2026-09-05). The pin happens through the
                # ADB environment variable instead - see adb_pin_env().
                argv += self.display.to_flags()
                if self.app_package:
                        # '+' force-stops before starting: without it an app with
                        # a live task elsewhere is "delivered" there and never
                        # lands on our virtual display (§7.1.4). Idempotent so a
                        # pre-prefixed value never becomes '++'.
                        package = self.app_package.removeprefix("+")
                        argv.append(f"--start-app=+{package}")
                if self.screen_off:
                        argv.append("--turn-screen-off")
                if self.stay_awake:
                        argv.append("--stay-awake")
                argv.append(f"--keyboard={self.keyboard}")
                argv += self.video.to_flags()
                if self.print_fps:
                        # fps 行进会话日志：设备端掉帧 = 编码/采集侧，设备端正常
                        # 而画面卡 = PC 解码侧（docs/mirroring-quality.md §6）。
                        argv.append("--print-fps")
                if not self.audio:
                        argv.append("--no-audio")
                else:
                        argv.append(f"--audio-codec={self.audio_codec}")
                        argv.append(f"--audio-buffer={self.audio_buffer_ms}")
                if self.window_title:
                        argv.append(f"--window-title={self.window_title}")
                # A borderless window loses its title bar and decorations; the
                # Windows-side chrome overlay (duo.core.chrome) supplies hover
                # controls and repairs the resize frame in that case.
                if self.borderless:
                        argv.append("--window-borderless")
                # Position flags are allowed with --flex-display; size flags
                # are rejected by it (experiment finding) and only make sense
                # together with a fixed-size display.
                argv += _int_flag("window-x", self.window_x)
                argv += _int_flag("window-y", self.window_y)
                if self.display.mode != "flex":
                        argv += _int_flag("window-width", self.window_width)
                        argv += _int_flag("window-height", self.window_height)
                return argv


def adb_pin_env(adb_path: str, to_windows: Callable[[str], str] | None = None) -> dict[str, str]:
        """Environment that pins scrcpy to ``adb_path`` (server-version wars).

        scrcpy locates adb via the ``ADB`` environment variable (it ships its
        own adb.exe otherwise; a version mismatch makes two clients kill each
        other's adb server). Under WSL the path must be Windows-shaped and the
        variable must be allow-listed in ``WSLENV`` to cross the interop
        boundary - verified live: scrcpy echoes the exact bogus path it was
        handed, so the mechanism provably reaches the Windows process.
        """
        from duo.core.chrome import wsl_to_windows_path

        translate = to_windows or wsl_to_windows_path
        if is_wsl():
                win_path = translate(adb_path)
                wslenv = os.environ.get("WSLENV", "")
                parts = [p for p in wslenv.split(":") if p and p != "ADB"]
                parts.append("ADB")
                return {"ADB": win_path, "WSLENV": ":".join(parts)}
        return {"ADB": adb_path}

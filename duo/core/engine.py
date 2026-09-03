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
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

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
                                timeout=_PROBE_TIMEOUT_S,
                                check=False,
                        )
                except (OSError, subprocess.TimeoutExpired):
                        return ToolInfo(name=tool, path=found, version=None)
                lines = (result.stdout or result.stderr).strip().splitlines()
                version = lines[0] if lines else None
                return ToolInfo(name=tool, path=found, version=version)
        return ToolInfo(name=tool, path=None, version=None)


# ----------------------------------------------------------------------------
# Engine argument assembly (EngineArgs)
#
# The dataclasses below compile a session description into scrcpy argv.
# Version quirks discovered by experiment (see plan.md, section 7) live here:
#   - scrcpy >= 3.0 has no positive ``--clipboard-autosync`` flag (default on,
#     only ``--no-clipboard-autosync`` exists) — never emit the positive form.
#   - ``--flex-display`` must be paired with ``--new-display``.
# ----------------------------------------------------------------------------

DisplayMode = Literal["mirror", "flex", "fixed"]


@dataclass(frozen=True)
class WindowGeometry:
        """Optional scrcpy window placement (screen coordinates)."""

        x: int
        y: int
        width: int
        height: int

        def to_flags(self) -> list[str]:
                """Compile to scrcpy window geometry flags."""
                return [
                        f"--window-x={self.x}",
                        f"--window-y={self.y}",
                        f"--window-width={self.width}",
                        f"--window-height={self.height}",
                ]


@dataclass(frozen=True)
class DisplaySpec:
        """Which Android display to stream.

        mirror: the physical device screen.
        flex: a virtual display that continuously resizes to match the window
                (needs scrcpy >= 4.1 with ``--flex-display``).
        fixed: a virtual display with a locked resolution.
        """

        mode: DisplayMode = "flex"
        width: int | None = None
        height: int | None = None
        dpi: int | None = 480

        def to_flags(self) -> list[str]:
                """Compile to scrcpy display flags."""
                if self.mode == "mirror":
                        return []
                if self.mode == "flex":
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

        ``encoder=None`` lets scrcpy pick the device's default hardware
        encoder; pinning a specific encoder is a per-device preset decision.
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
        display: DisplaySpec = DisplaySpec()
        video: VideoSpec = VideoSpec()
        app_package: str | None = None
        screen_off: bool = True
        stay_awake: bool = True
        keyboard: str = "uhid"
        audio: bool = True
        window_title: str | None = None
        window: WindowGeometry | None = None

        def to_argv(self, binary: str = "scrcpy") -> list[str]:
                """Compile to a full argv for the scrcpy binary."""
                argv = [binary, f"--serial={self.serial}"]
                argv += self.display.to_flags()
                if self.app_package:
                        argv.append(f"--start-app={self.app_package}")
                if self.screen_off:
                        argv.append("--turn-screen-off")
                if self.stay_awake:
                        argv.append("--stay-awake")
                argv.append(f"--keyboard={self.keyboard}")
                argv += self.video.to_flags()
                if not self.audio:
                        argv.append("--no-audio")
                if self.window_title:
                        argv.append(f"--window-title={self.window_title}")
                if self.window:
                        argv += self.window.to_flags()
                return argv

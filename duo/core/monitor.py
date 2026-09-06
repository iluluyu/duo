"""PC monitor geometry and DPI recommendations.

scrcpy sets the virtual display density once at creation, so a good portrait
experience requires picking the DPI for the window geometry we intend to
create. Duo therefore reads the primary monitor work area and derives:

    landscape: flex display; DPI maps the maximized window to ~1280dp (tablet)
    portrait:  fixed WxH display sized to a tall window (~60% of the work-area
               height wide) mapped to ~640dp (large phone / small tablet)

Two hard-won details (see plan.md, section 7):

- The work area must be queried in PHYSICAL pixels: scrcpy windows use
  physical pixels, but a non-DPI-aware powershell reports the 150%-scaled
  logical size. The query calls ``SetProcessDPIAware()`` first.
- ``--window-width/--window-height`` are rejected together with
  ``--flex-display``, so portrait (which wants a preset window size) runs in
  fixed display mode while landscape (free resizing) runs in flex mode.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from duo.core.engine import WindowGeometry
from duo.core.winproc import creation_flags

_QUERY_TIMEOUT_S = 15.0

#: Fallback work area when detection fails (4K minus a taskbar).
_FALLBACK = (3840, 2054)

#: Target layout widths in dp.
LANDSCAPE_TARGET_DP = 1280
PORTRAIT_TARGET_DP = 640

#: Portrait window width as a fraction of the work-area height.
PORTRAIT_WIDTH_RATIO = 0.6

_PS_QUERY = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -MemberDefinition "
        "'[DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware();' "
        "-Name Win32 -Namespace U; "
        "[U.Win32]::SetProcessDPIAware() | Out-Null; "
        "$a=[System.Windows.Forms.SystemInformation]::WorkingArea; "
        "Write-Output \"$($a.Width)x$($a.Height)\""
)


@dataclass(frozen=True)
class WorkArea:
        """Usable area of the primary monitor in physical pixels."""

        width: int
        height: int


@dataclass(frozen=True)
class DisplayRecommendation:
        """Display mode parameters recommended for one orientation.

        ``display_width/height`` are set for portrait (fixed WxH display);
        landscape uses flex and needs no display size, only ``dpi``.
        """

        dpi: int
        display_width: int | None = None
        display_height: int | None = None
        window: WindowGeometry | None = None


def primary_work_area() -> WorkArea:
        """Return the primary monitor work area in physical pixels."""
        try:
                result = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-Command", _PS_QUERY],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=_QUERY_TIMEOUT_S,
                        check=False,
                        creationflags=creation_flags(),
                )
                match = re.search(r"(\d+)\s*x\s*(\d+)", result.stdout or "")
                if match:
                        width, height = int(match.group(1)), int(match.group(2))
                        if width >= 640 and height >= 480:
                                return WorkArea(width, height)
        except (OSError, subprocess.TimeoutExpired):
                pass
        return WorkArea(*_FALLBACK)


def recommend_landscape(
        area: WorkArea, target_dp: int = LANDSCAPE_TARGET_DP
) -> DisplayRecommendation:
        """Fixed 16:9 landscape preset: 1920x1080/240dpi = 1280x720dp
        (classic reference-tablet layout; the display then flex-follows the
        window, so this only sets the startup shape and dp class)."""
        return DisplayRecommendation(dpi=240)


def recommend_portrait(
        area: WorkArea, target_dp: int = PORTRAIT_TARGET_DP
) -> DisplayRecommendation:
        """Fixed 9:16 portrait preset: 1080x1920/270dpi = 640x1138dp,
        window docked to the right edge (same tablet dp class the user
        verified all day with 1252x2088/313 = 641x1067dp). The window is
        clamped to the work area; the display preset itself stays 1080x1920
        because flex re-follows whatever window actually shows."""
        height = min(1920, area.height)
        width = min(1080, area.width)
        window = WindowGeometry(
                x=max(0, area.width - width),
                y=max(0, (area.height - height) // 2),
                width=width,
                height=height,
        )
        return DisplayRecommendation(
                dpi=270,
                display_width=1080,
                display_height=1920,
                window=window,
        )

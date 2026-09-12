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

from duo.core.aspects import scaled_size
from duo.core.engine import DisplaySpec, WindowGeometry
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
        """Fixed 16:9 landscape preset: 1920x1080, window = display size.

        Density is NOT monitor-derived anymore (2026-09-06 晚)： the CLI
        injects the device's own effective density (wm density, Override
        first — e.g. Pad 4 Pro 356) so element sizes match the physical
        screen; this dpi is an inert last-ditch fallback only.

        Researched baselines (real devices): phones 360-420dp short side
        (Find X8 419dp@480), iPad mini 744pt / iPad 10.9" 820pt, Pixel
        Tablet 927dp@276, iPad Pro 12.9" 1024pt, user's Pad 4 Pro
        1078dp@356-override. Density is fixed at display creation (`wm
        density -d` does not work on virtual displays, verified live),
        and scrcpy's no-dpi automatic value (201) preserves main-display
        dp, not element size - hence the explicit device density. The
        inert fallback follows the settings default: 160 since 2026-09-11
        (desktop-like, was 356).
        """
        return DisplayRecommendation(dpi=160)


def recommend_portrait(
        area: WorkArea, target_dp: int = PORTRAIT_TARGET_DP
) -> DisplayRecommendation:
        """Fixed 9:16 portrait preset: 1080x1920, window docked to the right
        edge; density comes from the device probe (CLI), this dpi is an
        inert last-ditch fallback. The window is clamped to the work area;
        the display preset itself stays 1080x1920 because flex re-follows
        whatever window actually shows."""
        height = min(1920, area.height)
        width = min(1080, area.width)
        window = WindowGeometry(
                x=max(0, area.width - width),
                y=max(0, (area.height - height) // 2),
                width=width,
                height=height,
        )
        return DisplayRecommendation(
                dpi=160,
                display_width=1080,
                display_height=1920,
                window=window,
        )


def apply_render_scale(
        display: DisplaySpec, area: WorkArea, scale: float
) -> DisplaySpec:
        """flex DisplaySpec × 倍率 → fixed DisplaySpec（窗口÷k 渲染）。

        倍率 > 1.0 时 flex 会话换成固定屏：基准 = 意图窗口（横屏 flex 无显
        式几何 → 主屏工作区，即最大化窗口；竖屏 flex 已带 1080x1920 初始
        形状则按它缩），render = 基准 ÷ k（偶数取整见 aspects.scaled_size）。
        固定屏 + 窗口比例锁 = 零失真放大（“窗口 4K、安卓 1K 渲染”）——
        scrcpy flex+--max-size 是逐维钳制（比例被拉歪），不可用，论证见
        docs/mirroring-quality.md §5。fixed/mirror 原样返回：固定比例会话
        已有自己的几何（倍率不叠加），物理屏无法缩。
        """
        if scale <= 1.0 or display.mode != "flex":
                return display
        base_w = display.width if display.width is not None else area.width
        base_h = display.height if display.height is not None else area.height
        width, height = scaled_size(base_w, base_h, scale)
        return DisplaySpec(mode="fixed", width=width, height=height, dpi=display.dpi)

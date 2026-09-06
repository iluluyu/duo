"""Tests for monitor-based DPI and window recommendations."""

from __future__ import annotations

from duo.core.engine import WindowGeometry
from duo.core.monitor import (
        WorkArea,
        recommend_landscape,
        recommend_portrait,
)

AREA_4K = WorkArea(width=3840, height=2054)
AREA_1080P = WorkArea(width=1920, height=1040)


def test_landscape_4k_recommends_480():
        """Landscape is a fixed 16:9 preset now: dpi 240 (1920x1080 = 1280x720dp),
        no window geometry (the initial window equals the display preset)."""
        rec = recommend_landscape(AREA_4K)
        assert rec.dpi == 240
        assert rec.window is None


def test_landscape_1080p_scales_down():
        """The preset is monitor-independent (flex follows the window after)."""
        rec = recommend_landscape(AREA_1080P)
        assert rec.dpi == 240


def test_portrait_window_geometry():
        """Portrait recommends the 9:16 preset plus a right-edge window."""
        rec = recommend_portrait(AREA_4K)
        assert rec.display_width == 1080
        assert rec.display_height == 1920
        assert rec.window is not None
        window: WindowGeometry = rec.window
        assert window.width == 1080
        assert window.height == 1920
        assert window.x == AREA_4K.width - window.width
        # 640dp layout width: the tablet-ish portrait class the user verified.
        assert window.width * 160 // rec.dpi == 640


def test_portrait_dpi_smaller_than_landscape():
        """Portrait maps the same pixel width to a smaller dp class."""
        portrait = recommend_portrait(AREA_4K)
        landscape = recommend_landscape(AREA_4K)
        assert portrait.dpi > landscape.dpi


def test_portrait_on_narrow_monitor():
        """A narrow work area clamps the window to it (display preset unchanged,
        flex re-follows whatever window shows)."""
        rec = recommend_portrait(WorkArea(width=1000, height=800))
        assert rec.window is not None
        assert rec.window.width <= 1000
        assert rec.window.height <= 800
        assert rec.display_width == 1080
        assert rec.dpi == 270

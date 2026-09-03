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
        """A 4K work area maps the maximized window to ~1280dp (dpi 480)."""
        rec = recommend_landscape(AREA_4K)
        assert rec.dpi == 480
        assert rec.window is None


def test_landscape_1080p_scales_down():
        """A 1080p work area halves the dpi so the layout width still ~1280dp."""
        rec = recommend_landscape(AREA_1080P)
        assert rec.dpi == 240


def test_portrait_window_geometry():
        """Portrait recommends a fixed WxH display plus a right-edge window."""
        rec = recommend_portrait(AREA_4K)
        assert rec.display_width is not None
        assert rec.display_height == AREA_4K.height
        assert rec.window is not None
        window: WindowGeometry = rec.window
        assert window.height == AREA_4K.height
        assert window.x == AREA_4K.width - window.width
        assert window.width == rec.display_width
        # ~640dp layout width for a phone-like portrait layout.
        assert window.width * 160 // rec.dpi == 640


def test_portrait_dpi_smaller_than_landscape():
        """Portrait maps the same physical width to fewer dp (no giant UI)."""
        portrait = recommend_portrait(AREA_4K)
        landscape = recommend_landscape(AREA_4K)
        assert portrait.dpi < landscape.dpi


def test_portrait_on_narrow_monitor():
        """A narrow work area keeps a sane window (>=480px, 90% cap)."""
        rec = recommend_portrait(WorkArea(width=1000, height=800))
        assert rec.window is not None
        assert 480 <= rec.display_width <= 900
        assert rec.dpi >= 160

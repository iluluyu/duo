"""Tests for monitor-based DPI, window and render-scale recommendations."""

from __future__ import annotations

from duo.core.engine import DisplaySpec, WindowGeometry
from duo.core.monitor import (
        WorkArea,
        apply_render_scale,
        recommend_landscape,
        recommend_portrait,
)

AREA_4K = WorkArea(width=3840, height=2054)
AREA_1080P = WorkArea(width=1920, height=1040)


def test_landscape_4k_recommends_480():
        """Landscape is a fixed 16:9 preset now; dpi is only an inert
        fallback (real density comes from the device probe in the CLI),
        no window geometry (the initial window equals the display preset).
        The fallback follows the settings default: 160 since 2026-09-11."""
        rec = recommend_landscape(AREA_4K)
        assert rec.dpi == 160
        assert rec.window is None


def test_landscape_1080p_scales_down():
        """The preset is monitor-independent (flex follows the window after)."""
        rec = recommend_landscape(AREA_1080P)
        assert rec.dpi == 160


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
        # 密度来自设备探测；回退值 160 跟随新设置页默认（2026-09-11），
        # 不再承担 dp 目标。
        assert rec.dpi == 160


def test_portrait_dpi_smaller_than_landscape():
        """Both presets share the device-density fallback (density is
        device-derived, not orientation-derived)."""
        portrait = recommend_portrait(AREA_4K)
        landscape = recommend_landscape(AREA_4K)
        assert portrait.dpi == landscape.dpi == 160


def test_portrait_on_narrow_monitor():
        """A narrow work area clamps the window to it (display preset unchanged,
        flex re-follows whatever window shows)."""
        rec = recommend_portrait(WorkArea(width=1000, height=800))
        assert rec.window is not None
        assert rec.window.width <= 1000
        assert rec.window.height <= 800
        assert rec.display_width == 1080
        assert rec.dpi == 160


class TestApplyRenderScale:
        """渲染倍率：flex DisplaySpec × k → 窗口÷k 的固定屏。"""

        def test_scale_one_returns_display_unchanged(self):
                flex = DisplaySpec(mode="flex")
                assert apply_render_scale(flex, AREA_4K, 1.0) is flex

        def test_fixed_and_mirror_untouched(self):
                """固定比例会话自带几何，倍率不叠加；物理屏无法缩。"""
                fixed = DisplaySpec(mode="fixed", width=2560, height=1440, dpi=160)
                assert apply_render_scale(fixed, AREA_4K, 2.0) is fixed
                mirror = DisplaySpec(mode="mirror")
                assert apply_render_scale(mirror, AREA_4K, 2.0) is mirror

        def test_landscape_flex_scales_from_work_area(self):
                """横屏 flex 无显式几何 → 基准 = 主屏工作区（最大化窗口）。
                4K 工作区 ÷2 = 1K 渲染（用户定稿的倍率语义）。"""
                scaled = apply_render_scale(DisplaySpec(mode="flex", dpi=160),
                                            AREA_4K, 2.0)
                assert scaled.mode == "fixed"
                assert (scaled.width, scaled.height) == (1920, 1028)
                assert scaled.dpi == 160

        def test_portrait_flex_scales_from_initial_shape(self):
                """竖屏 flex 已带 1080x1920 初始形状 → 按它缩。"""
                scaled = apply_render_scale(
                        DisplaySpec(mode="flex", width=1080, height=1920, dpi=240),
                        AREA_4K, 2.0)
                assert scaled.mode == "fixed"
                assert (scaled.width, scaled.height) == (540, 960)
                assert scaled.dpi == 240

        def test_odd_results_snap_to_even(self):
                """奇数取整后上调到偶（编码器/窗口整数配置）。"""
                scaled = apply_render_scale(DisplaySpec(mode="flex"),
                                            WorkArea(3841, 2055), 1.75)
                assert scaled.width % 2 == 0
                assert scaled.height % 2 == 0

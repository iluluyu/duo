"""Tests for EngineArgs command assembly."""

from __future__ import annotations

import pytest

from duo.core.engine import DisplaySpec, EngineArgs, VideoSpec


def _argv(**kwargs) -> list[str]:
        """Build argv for the given EngineArgs overrides."""
        return EngineArgs(**kwargs).to_argv()


def test_adb_pin_never_becomes_an_argv_flag():
        """The adb pin stays out of argv: scrcpy 4.1 has no --adb option and
        exits with "unknown option" (live finding 2026-09-05, restart loop).
        Pinning happens via the ADB environment variable - adb_pin_env()."""
        argv = _argv(serial="s", adb_binary="C:\\tools\\adb.exe")
        assert not any(a.startswith("--adb") for a in argv)
        assert not any(a.startswith("--adb") for a in _argv(serial="s"))


def test_flex_default_matches_verified_preset():
        """The default args reproduce the experiment-verified session shape.

        The default flex_resolution (settings default 1440p) pins the virtual
        display baseline at 2560x1440: a bare --new-display would size the
        display at the main display's full resolution and max out both the
        device encoder and the PC decoder during fullscreen animations.
        """
        argv = _argv(serial="4444bd6b", app_package="cn.com.langeasy.LangEasyLexis")
        joined = " ".join(argv)
        assert "--serial=4444bd6b" in argv
        assert "--new-display=2560x1440/480" in argv
        # One-way follow (2026-09-06): --flex-display stays (window drives
        # the display); the overlay's startup nudge keeps scrcpy from ever
        # auto-resizing the window back.
        # '+' prefix is mandatory: without it an app with a live task on the
        # physical screen never lands on the virtual display (§7.1.4).
        assert "--start-app=+cn.com.langeasy.LangEasyLexis" in argv
        # Virtual displays keep system decorations ON: the secondary-display
        # launcher is the in-session "virtual desktop" the chin long-press
        # opens (2026-09-06 decision; suppression cut that page off).
        assert "--no-vd-system-decorations" not in argv
        assert "--turn-screen-off" in argv
        assert "--stay-awake" in argv
        assert "--keyboard=uhid" in argv
        assert "--video-codec=h265" in argv
        assert "--video-bit-rate=30M" in argv
        assert "--max-fps=90" in argv
        # No positive clipboard flag exists in scrcpy >= 3.0 (experiment finding).
        assert "clipboard" not in joined


def test_virtual_display_decorations_on_both_modes():
        """flex AND fixed sessions keep decorations (virtual desktop page);
        mirror never had the flag."""
        flex = _argv(serial="s", display=DisplaySpec(mode="flex", dpi=None))
        assert "--no-vd-system-decorations" not in flex
        fixed = _argv(
                serial="s",
                display=DisplaySpec(mode="fixed", width=1200, height=1600, dpi=280),
        )
        assert "--no-vd-system-decorations" not in fixed
        mirror = _argv(serial="s", display=DisplaySpec(mode="mirror"))
        assert "--no-vd-system-decorations" not in mirror


def test_start_app_plus_prefix_is_idempotent():
        """A pre-prefixed package never becomes '++' (scrcpy parses it as '?')."""
        argv = _argv(serial="s", app_package="+cn.com.langeasy.LangEasyLexis")
        assert "--start-app=+cn.com.langeasy.LangEasyLexis" in argv
        assert not any("++" in a for a in argv)


        assert "--flex-display" in argv


def test_flex_without_dpi_uses_bare_new_display():
        """native（不加尺寸）+ dpi=None emits a bare --new-display + flex."""
        argv = _argv(serial="s",
                     display=DisplaySpec(mode="flex", dpi=None,
                                         flex_resolution="native"))
        assert "--new-display" in argv
        assert not any(a.startswith("--new-display=") for a in argv)
        assert "--flex-display" in argv


def test_flex_resolution_three_tiers():
        """三档 flex_resolution → 三种 --new-display 形状（卡顿修复的核心）。

        裸 --new-display 会把虚拟屏建成主屏全尺寸（2400x3392 面板实测同尺寸），
        全屏动画时设备端硬编 + PC 端软解双端吃满；1440p/1080p 钉住基准尺寸。
        """
        assert DisplaySpec().flex_resolution == "1440p"   # 默认值 = 平衡档

        def new_display(spec: DisplaySpec) -> str:
                argv = EngineArgs(serial="s", display=spec).to_argv()
                return next(f for f in argv if f.startswith("--new-display"))

        assert new_display(DisplaySpec(mode="flex", dpi=480)) == \
                "--new-display=2560x1440/480"
        assert new_display(DisplaySpec(mode="flex", dpi=480,
                                       flex_resolution="1080p")) == \
                "--new-display=1920x1080/480"
        assert new_display(DisplaySpec(mode="flex", dpi=480,
                                       flex_resolution="native")) == \
                "--new-display=/480"
        # 尺寸可与 dpi 解耦：无 dpi 时只拼 WxH
        assert new_display(DisplaySpec(mode="flex", dpi=None)) == \
                "--new-display=2560x1440"
        # flex 三档（跟随保留：窗口驱动显示；装饰开启：虚拟桌面页）
        for value in ("1440p", "1080p", "native"):
                argv = _argv(serial="s", display=DisplaySpec(
                        mode="flex", dpi=480, flex_resolution=value))
                assert "--flex-display" in argv
                assert "--no-vd-system-decorations" not in argv


def test_flex_explicit_size_never_overridden_by_resolution():
        """带显式尺寸的 spec（竖屏推荐/fixed 路径）不被 flex_resolution 覆盖。"""
        spec = DisplaySpec(mode="flex", width=1120, height=1872, dpi=313,
                           flex_resolution="1080p")
        argv = _argv(serial="s", display=spec)
        assert "--new-display=/313" in argv
        assert not any(a.startswith("--new-display=1920x1080") for a in argv)
        assert not any(a.startswith("--new-display=2560x1440") for a in argv)


def test_fixed_mode_ignores_flex_resolution():
        """fixed 模式已有显式尺寸，flex_resolution 不参与拼装。"""
        display = DisplaySpec(mode="fixed", width=2560, height=1440, dpi=268,
                              flex_resolution="native")
        argv = _argv(serial="s", display=display)
        assert "--new-display=2560x1440/268" in argv
        assert "--flex-display" not in argv


def test_fixed_display_size_and_dpi():
        """Fixed mode emits --new-display=WxH/DPI."""
        display = DisplaySpec(mode="fixed", width=2560, height=1440, dpi=268)
        argv = _argv(serial="s", display=display)
        assert "--new-display=2560x1440/268" in argv
        assert "--flex-display" not in argv


def test_fixed_display_requires_dimensions():
        """Fixed mode without width/height is a configuration error."""
        with pytest.raises(ValueError, match="width and height"):
                DisplaySpec(mode="fixed").to_flags()


def test_mirror_mode_emits_no_display_flags():
        """Mirror mode streams the physical screen with no display flags."""
        argv = _argv(serial="s", display=DisplaySpec(mode="mirror"))
        prefixes = ("--new-display", "--flex-display")
        assert not any(a.startswith(prefixes) for a in argv)


def test_video_encoder_optional():
        """A pinned encoder is emitted; the default lets scrcpy choose."""
        pinned = _argv(serial="s", video=VideoSpec(encoder="c2.qti.hevc.encoder"))
        assert "--video-encoder=c2.qti.hevc.encoder" in pinned
        default = _argv(serial="s")
        assert not any(a.startswith("--video-encoder") for a in default)


def test_audio_and_title_flags():
        """Audio forwarding and window title are switchable."""
        argv = _argv(serial="s", audio=False, window_title="不背单词")
        assert "--no-audio" in argv
        assert "--window-title=不背单词" in argv
        argv = _argv(serial="s")
        assert "--no-audio" not in argv
        # Lossless codec + roomier capture buffer by default.
        assert "--audio-codec=flac" in argv
        assert "--audio-buffer=100" in argv


def test_screen_off_switchable():
        """Screen-off (immersive mode) can be disabled."""
        argv = _argv(serial="s", screen_off=False)
        assert "--turn-screen-off" not in argv
        assert "--stay-awake" in argv


def test_binary_position():
        """The compiled command starts with the resolved binary path."""
        argv = _argv(serial="s")
        assert argv[0] == "scrcpy"
        argv = EngineArgs(serial="s").to_argv(binary="/usr/bin/scrcpy.exe")
        assert argv[0] == "/usr/bin/scrcpy.exe"


def test_window_position_emitted_with_flex():
        """Position flags are allowed alongside flex display."""
        argv = _argv(
                serial="s",
                window_x=100,
                window_y=50,
        )
        assert "--window-x=100" in argv
        assert "--window-y=50" in argv
        size_flags = ("--window-width", "--window-height")
        assert not any(a.startswith(size_flags) for a in argv)


def test_window_size_suppressed_under_flex():
        """Size flags are rejected by --flex-display, so they must not be emitted."""
        argv = _argv(serial="s", window_width=800, window_height=1200)
        assert not any(a.startswith("--window-") for a in argv)


def test_window_size_emitted_for_fixed():
        """Fixed display mode can pin the full window geometry."""
        display = DisplaySpec(mode="fixed", width=1252, height=2088, dpi=313)
        argv = _argv(
                serial="s",
                display=display,
                window_x=10,
                window_y=20,
                window_width=1252,
                window_height=2088,
        )
        assert "--window-x=10" in argv
        assert "--window-y=20" in argv
        assert "--window-width=1252" in argv
        assert "--window-height=2088" in argv


def test_borderless_flag():
        """--chrome sessions emit --window-borderless; default keeps decorations."""
        argv = _argv(serial="s", borderless=True)
        assert "--window-borderless" in argv
        argv = _argv(serial="s")
        assert "--window-borderless" not in argv


def test_print_fps_on_by_default():
        """会话默认带 --print-fps：stderr 周期 fps 行进日志（卡顿诊断）。"""
        argv = _argv(serial="s")
        assert "--print-fps" in argv
        assert "--print-fps" in _argv(serial="s", display=DisplaySpec(mode="mirror"))


def test_print_fps_opt_out():
        """print_fps=False 不发旗标（未来 UI 开关的预留位）。"""
        argv = _argv(serial="s", print_fps=False)
        assert not any(a.startswith("--print-fps") for a in argv)

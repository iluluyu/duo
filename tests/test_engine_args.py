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

        Fixed-size virtual display (2026-09-06 final): resizable displays
        cannot coexist with orientation-pushing apps (rotation ping-pong,
        A/B verified); a fixed 2560x1440/480 display cannot rotate - apps
        letterbox inside it like on a tablet.
        """
        argv = _argv(serial="4444bd6b", app_package="cn.com.langeasy.LangEasyLexis")
        joined = " ".join(argv)
        assert "--serial=4444bd6b" in argv
        assert "--new-display=2560x1440/480" in argv
        assert "--no-window-aspect-ratio-lock" in argv
        # flex display（2026-09-06 晚定稿）：虚拟屏持续跟随窗口（原生填满，
        # unscaled 渲染）；风暴根因（ROTATES_WITH_CONTENT + APP 方向请求）
        # 由 overlay 的 set-ignore-orientation-request 一次性锁死。
        assert "--flex-display" in argv
        # stretched 在这里只平滑跟随过渡期（flex 默认 unscaled 会露空白）；
        # 稳态三尺寸相等 = 零失真原生分辨率（与单独 stretched 的永久失真不同）。
        assert "--render-fit=stretched" in argv
        assert "--capture-orientation" not in joined
        assert "--no-vd-system-decorations" not in argv
        # Fixed 2560x1440/480 virtual display (2026-09-06 final): cannot
        # rotate, orientation-pushing apps letterbox like on a tablet.
        assert "--new-display=2560x1440/480" in argv
        # '+' prefix is mandatory: without it an app with a live task on the
        # physical screen never lands on the virtual display (§7.1.4).
        assert "--start-app=+cn.com.langeasy.LangEasyLexis" in argv
        # Fixed display, decorations ON (rotation storms were a resizable-display
        # artifact), window ratio unlocked, pin veto guards the window rect.
        assert "--no-vd-system-decorations" not in argv
        assert "--no-window-aspect-ratio-lock" in argv
        assert "--flex-display" in argv   # 2026-09-06 晚：原生跟随 + 方向锁
        assert "--turn-screen-off" in argv
        assert "--stay-awake" in argv
        assert "--keyboard=uhid" in argv
        assert "--video-codec=h265" in argv
        assert "--video-bit-rate=30M" in argv
        assert "--max-fps=90" in argv
        # No positive clipboard flag exists in scrcpy >= 3.0 (experiment finding).
        assert "clipboard" not in joined
        # No positive clipboard flag exists in scrcpy >= 3.0 (experiment finding).
        assert "clipboard" not in joined


def test_virtual_display_decorations_on_for_flex_only():
        """flex (fixed-size) sessions keep decorations - the rotation storm
        was a resizable-display artifact; fixed displays cannot rotate.
        fixed and mirror never had the flag."""
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


def test_flex_without_dpi_uses_default_size():
        """dpi=None 的 flex 会话用默认初始尺寸 2560x1440（无密度后缀），
        显示随后 flex 跟随窗口（2026-09-06 晚定稿）。"""
        argv = _argv(serial="s", display=DisplaySpec(mode="flex", dpi=None))
        assert "--new-display=2560x1440" in argv
        assert not any(a.endswith("/None") for a in argv)
        assert "--flex-display" in argv
        assert "--no-vd-system-decorations" not in argv


def test_flex_explicit_size_pins_display():
        """显式尺寸 spec（竖屏推荐/--width/--height）给出初始形状（不再钉
        死：flex 跟随始终开，2026-09-06 晚定稿）。"""
        spec = DisplaySpec(mode="flex", width=1120, height=1872, dpi=313)
        argv = _argv(serial="s", display=spec)
        assert "--new-display=1120x1872/313" in argv
        assert "--flex-display" in argv


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

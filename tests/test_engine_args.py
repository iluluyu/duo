"""Tests for EngineArgs command assembly."""

from __future__ import annotations

import pytest

from duo.core.engine import DisplaySpec, EngineArgs, VideoSpec


def _argv(**kwargs) -> list[str]:
        """Build argv for the given EngineArgs overrides."""
        return EngineArgs(**kwargs).to_argv()


def test_flex_default_matches_verified_preset():
        """The default args reproduce the experiment-verified session shape."""
        argv = _argv(serial="4444bd6b", app_package="cn.com.langeasy.LangEasyLexis")
        joined = " ".join(argv)
        assert "--serial=4444bd6b" in argv
        assert "--new-display=/480" in argv
        assert "--flex-display" in argv
        assert "--start-app=cn.com.langeasy.LangEasyLexis" in argv
        assert "--turn-screen-off" in argv
        assert "--stay-awake" in argv
        assert "--keyboard=uhid" in argv
        assert "--video-codec=h265" in argv
        assert "--video-bit-rate=30M" in argv
        assert "--max-fps=90" in argv
        # No positive clipboard flag exists in scrcpy >= 3.0 (experiment finding).
        assert "clipboard" not in joined


def test_flex_without_dpi_uses_bare_new_display():
        """dpi=None emits a bare --new-display (engine picks default density)."""
        argv = _argv(serial="s", display=DisplaySpec(mode="flex", dpi=None))
        assert "--new-display" in argv
        assert not any(a.startswith("--new-display=") for a in argv)
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

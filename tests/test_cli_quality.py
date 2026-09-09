"""CLI quality decisions: audio_policy argv, turn_screen_off argv, codec argv.

The pure resolvers in duo.__main__ map settings/flags to EngineArgs values;
the argv assertions pin the scrcpy flags each state must (not) emit.
"""

from __future__ import annotations

import pytest

from duo.__main__ import (
    _build_parser,
    _resolve_audio,
    _resolve_bar_mode,
    _resolve_screen_off,
)
from duo.core.engine import EngineArgs, VideoSpec


def _audio_argv(audio: bool) -> list[str]:
    """argv for an otherwise-default session with the resolved audio flag."""
    return EngineArgs(serial="s", audio=audio).to_argv()


class TestAudioPolicyArgv:
    """audio_policy 三态 → argv（经 _resolve_audio 组装 EngineArgs）。"""

    def test_off_mutes_every_session(self):
        audio, arbitrate = _resolve_audio(no_audio_flag=False, audio_policy="off")
        assert (audio, arbitrate) == (False, False)
        assert "--no-audio" in _audio_argv(audio)

    def test_all_forwards_without_lock(self):
        audio, arbitrate = _resolve_audio(no_audio_flag=False, audio_policy="all")
        assert (audio, arbitrate) == (True, False)   # parallel audio, no lock
        assert "--no-audio" not in _audio_argv(audio)

    def test_latest_forwards_and_arbitrates(self):
        audio, arbitrate = _resolve_audio(no_audio_flag=False, audio_policy="latest")
        assert (audio, arbitrate) == (True, True)
        assert "--no-audio" not in _audio_argv(audio)

    def test_explicit_no_audio_flag_wins_over_any_policy(self):
        for policy in ("latest", "all", "off"):
            audio, arbitrate = _resolve_audio(no_audio_flag=True, audio_policy=policy)
            assert (audio, arbitrate) == (False, False)
            assert "--no-audio" in _audio_argv(audio)

    def test_default_still_requests_flac_with_buffer(self):
        argv = _audio_argv(True)
        assert "--audio-codec=flac" in argv
        assert "--audio-buffer=100" in argv


class TestTurnScreenOffArgv:
    """turn_screen_off 设置 → --turn-screen-off 旗标。"""

    def test_default_false_omits_flag(self):
        assert _resolve_screen_off(False, False) is False
        assert "--turn-screen-off" not in EngineArgs(
            serial="s", screen_off=False).to_argv()

    def test_true_emits_flag(self):
        assert _resolve_screen_off(False, True) is True
        argv = EngineArgs(serial="s", screen_off=True).to_argv()
        assert "--turn-screen-off" in argv

    def test_cli_no_screen_off_forces_on_screen(self):
        """--no-screen-off wins over a settings true (CLI > settings)."""
        assert _resolve_screen_off(True, True) is False

    def test_stay_awake_unaffected(self):
        argv = EngineArgs(serial="s", screen_off=False).to_argv()
        assert "--stay-awake" in argv


class TestChromeBarModes:
    """--chrome-top/--chrome-bottom：解析（choices）+ 旗标优先于设置。"""

    def test_resolver_follows_settings_when_flag_absent(self):
        """旗标未传（None）→ 跟随设置存值（none = 该边不建栏）。"""
        assert _resolve_bar_mode(None, "native") == "native"
        assert _resolve_bar_mode(None, "immersive") == "immersive"
        assert _resolve_bar_mode(None, "none") == "none"

    def test_resolver_flag_beats_setting(self):
        """显式旗标赢过任意设置值（CLI > settings 全局优先级）。"""
        for flag, setting in (("immersive", "native"), ("native", "immersive"),
                              ("none", "immersive"), ("immersive", "none")):
            assert _resolve_bar_mode(flag, setting) == flag

    def test_parser_defaults_none_following_settings(self):
        args = _build_parser().parse_args(["mirror"])
        assert args.chrome_top is None
        assert args.chrome_bottom is None

    def test_parser_accepts_both_enums_per_edge(self):
        """上/下两旗标独立解析，各收 immersive|native|none。"""
        args = _build_parser().parse_args(
            ["mirror", "--chrome-top", "native", "--chrome-bottom", "immersive"])
        assert args.chrome_top == "native"
        assert args.chrome_bottom == "immersive"
        args = _build_parser().parse_args(
            ["mirror", "--chrome-top", "none", "--chrome-bottom", "none"])
        assert args.chrome_top == "none"
        assert args.chrome_bottom == "none"
        args = _build_parser().parse_args(["mirror", "--chrome-bottom", "native"])
        assert args.chrome_top is None
        assert args.chrome_bottom == "native"

    def test_parser_rejects_values_outside_enum(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["mirror", "--chrome-top", "floating"])
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["mirror", "--chrome-bottom", "titanium"])


class TestVdKeepContentFlag:
    """--no-vd-destroy-content（断开保留画面）：面板 behavior 节透传。"""

    def test_parser_defaults_off(self):
        args = _build_parser().parse_args(["mirror"])
        assert args.no_vd_destroy_content is False

    def test_parser_accepts_flag(self):
        args = _build_parser().parse_args(
            ["mirror", "--no-vd-destroy-content"])
        assert args.no_vd_destroy_content is True

    def test_flag_reaches_engine_argv(self):
        argv = EngineArgs(serial="s", vd_keep_content=True).to_argv()
        assert "--no-vd-destroy-content" in argv


class TestVideoCodecArgv:
    """resolve_codec 结果 → VideoSpec → argv（码率沿用 bitrate_mbps）。"""

    def test_hw_choice_pins_codec_and_encoder(self):
        argv = EngineArgs(
            serial="s",
            video=VideoSpec(codec="h265", encoder="c2.qti.hevc.encoder",
                            bitrate_mbps=30, max_fps=90),
        ).to_argv()
        assert "--video-codec=h265" in argv
        assert "--video-encoder=c2.qti.hevc.encoder" in argv
        assert "--video-bit-rate=30M" in argv

    def test_fallback_without_pin(self):
        """Probe unavailable: h264 with no --video-encoder flag."""
        argv = EngineArgs(
            serial="s",
            video=VideoSpec(codec="h264", encoder=None, bitrate_mbps=8,
                            max_fps=60),
        ).to_argv()
        assert "--video-codec=h264" in argv
        assert not any(a.startswith("--video-encoder=") for a in argv)
        assert "--video-bit-rate=8M" in argv


def test_app_title_degrades_to_package_when_metadata_fails(capsys):
        """元数据失败不得阻断启动：超大 APK（QQ/微信）只降级标题。"""
        from duo.__main__ import _resolve_app_title
        from duo.core.apps import Adb, AdbError, AppInfo

        def boom(adb, package):
                raise AdbError("com.tencent.mobileqq apk too large (371 MB)")

        import duo.__main__ as cli
        original = cli.app_info
        cli.app_info = boom
        try:
                assert _resolve_app_title(Adb("adb", "s"), "com.tencent.mm") == "com.tencent.mm"
                out = capsys.readouterr().out
                assert "launching anyway" in out

                cli.app_info = lambda adb, package: AppInfo(
                        package, "微信", "8.0", None
                )
                assert _resolve_app_title(Adb("adb", "s"), "com.tencent.mm") == "微信"
        finally:
                cli.app_info = original

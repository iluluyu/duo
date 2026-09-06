"""CLI quality decisions: audio_policy argv, turn_screen_off argv, codec argv.

The pure resolvers in duo.__main__ map settings/flags to EngineArgs values;
the argv assertions pin the scrcpy flags each state must (not) emit.
"""

from __future__ import annotations

from duo.__main__ import _resolve_audio, _resolve_screen_off
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

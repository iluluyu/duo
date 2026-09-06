"""Codec discovery: --list-encoders parsing, cache TTL, selection priority.

The parse fixture below mirrors the OPD2409 probe verbatim (alias lines,
.cq/.hdr variants, sw tiers); the selection tests mock probe results to
exercise each priority tier independently of any device.
"""

from __future__ import annotations

import json

import pytest

from duo.core.codec import (
        ENCODERS_TTL_S,
        CodecChoice,
        EncoderInfo,
        load_cached_encoders,
        parse_encoders,
        probe_encoders,
        resolve_codec,
        save_encoders_cache,
)

#: Verbatim shape of `scrcpy --list-encoders` on OPD2409 (Snapdragon;
#: stderr carries the INFO lines, stdout is empty).
OPPO_OUTPUT = """\
scrcpy 4.1 <https://github.com/Genymobile/scrcpy>
[server] INFO: Device: [OPPO] OPPO OPD2409 (Android 16)
[server] INFO: List of video encoders:
    --video-codec=h264 --video-encoder=c2.qti.avc.encoder             (hw) [vendor]
    --video-codec=h264 --video-encoder=OMX.qcom.video.encoder.avc (hw) (alias for c2.qti.avc.enc)
    --video-codec=h264 --video-encoder=c2.android.avc.encoder         (sw)
    --video-codec=h265 --video-encoder=c2.qti.hevc.encoder            (hw) [vendor]
    --video-codec=h265 --video-encoder=c2.qti.hevc.encoder.cq         (hw) [vendor]
    --video-codec=h265 --video-encoder=c2.qti.hevc.encoder.hdr        (hw) [vendor]
    --video-codec=h265 --video-encoder=c2.android.hevc.encoder        (sw)
    --video-codec=av1 --video-encoder=c2.android.av1.encoder          (sw)
[server] INFO: List of audio encoders:
    --audio-codec=flac --audio-encoder=c2.android.flac.encoder        (sw)
"""


# ------------------------------------------------------------------- parsing


def test_parse_encoders_extracts_hw_and_sw():
    """(hw) marks hardware; audio-encoder lines never match."""
    encoders = parse_encoders(OPPO_OUTPUT)
    names = {(e.codec, e.name, e.hardware) for e in encoders}
    assert ("h264", "c2.qti.avc.encoder", True) in names
    assert ("h264", "c2.android.avc.encoder", False) in names
    assert ("h265", "c2.qti.hevc.encoder", True) in names
    assert ("av1", "c2.android.av1.encoder", False) in names
    assert all(codec in ("h264", "h265", "av1") for codec, _, _ in names)


def test_parse_encoders_skips_aliases():
    """Alias lines duplicate an already-listed component: dropped."""
    encoders = parse_encoders(OPPO_OUTPUT)
    assert not any("OMX." in e.name for e in encoders)


def test_parse_encoders_empty_on_garbage():
    assert parse_encoders("no encoders here") == []


# ------------------------------------------------------- cache read / write


@pytest.fixture()
def encoders() -> list[EncoderInfo]:
    return parse_encoders(OPPO_OUTPUT)


def test_cache_roundtrip(tmp_path, encoders):
    path = tmp_path / "encoders.json"
    save_encoders_cache(path, "4444bd6b", encoders, now=1000.0)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["serial"] == "4444bd6b"
    assert raw["probed_at"] == 1000.0
    assert raw["encoders"][0]["codec"] == "h264"
    assert load_cached_encoders(path, "4444bd6b", now=1000.0 + 3600) == encoders


def test_cache_expires_past_ttl(tmp_path, encoders):
    path = tmp_path / "encoders.json"
    save_encoders_cache(path, "4444bd6b", encoders, now=1000.0)
    assert load_cached_encoders(
        path, "4444bd6b", now=1000.0 + ENCODERS_TTL_S + 1) is None
    # within TTL still fresh
    assert load_cached_encoders(
        path, "4444bd6b", now=1000.0 + ENCODERS_TTL_S) == encoders


def test_cache_rejects_foreign_serial(tmp_path, encoders):
    """Encoder components are model-specific: another serial must re-probe."""
    path = tmp_path / "encoders.json"
    save_encoders_cache(path, "4444bd6b", encoders, now=1000.0)
    assert load_cached_encoders(path, "OTHER", now=1001.0) is None


def test_cache_survives_corrupt_file(tmp_path):
    path = tmp_path / "encoders.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_cached_encoders(path, "4444bd6b") is None
    assert load_cached_encoders(tmp_path / "missing.json", "4444bd6b") is None


def test_cache_rejects_bad_entries(tmp_path):
    path = tmp_path / "encoders.json"
    path.write_text(
        json.dumps({"serial": "S", "probed_at": 1.0, "encoders": [{"codec": "h264"}]}),
        encoding="utf-8",
    )
    assert load_cached_encoders(path, "S") is None


# ------------------------------------------------------- selection priority


def test_auto_prefers_h265_hardware(encoders):
    """Tier 1: h265 hardware beats everything (the OPD2409 case)."""
    choice = resolve_codec("auto", encoders)
    assert choice == CodecChoice(
        "h265", "c2.qti.hevc.encoder", True,
        "自动选择：h265 硬件编码（c2.qti.hevc.encoder）")


def test_auto_falls_to_h264_hardware():
    """Tier 2: no h265 hw -> h264 hardware with an encoder pin."""
    only_h264_hw = [
        EncoderInfo("h264", "c2.qti.avc.encoder", True),
        EncoderInfo("h264", "c2.android.avc.encoder", False),
        EncoderInfo("h265", "c2.android.hevc.encoder", False),
    ]
    choice = resolve_codec("auto", only_h264_hw)
    assert choice.codec == "h264"
    assert choice.encoder == "c2.qti.avc.encoder"
    assert choice.hardware is True


def test_auto_falls_to_h264_software():
    """Tier 3: no hardware encoders at all -> software h264."""
    sw_only = [EncoderInfo("h264", "c2.android.avc.encoder", False)]
    choice = resolve_codec("auto", sw_only)
    assert choice == CodecChoice("h264", "c2.android.avc.encoder", False,
                                 "自动选择：h264 软件编码（c2.android.avc.encoder）")


def test_auto_with_no_h264_at_all_uses_fallback():
    """Nothing usable in the probe -> h264 without a pin."""
    alien = [EncoderInfo("vp9", "c2.android.vp9.encoder", False)]
    choice = resolve_codec("auto", alien)
    assert choice.codec == "h264"
    assert choice.encoder is None


def test_av1_hardware_only_reached_when_confirmed():
    """av1 hardware is a real auto tier - but only with probe confirmation."""
    with_av1_hw = [
        EncoderInfo("h264", "c2.qti.avc.encoder", True),
        EncoderInfo("av1", "c2.qti.av1.encoder", True),
    ]
    choice = resolve_codec("auto", with_av1_hw)
    assert choice.codec == "h264"       # h264 hw outranks av1 hw
    hw_av1_only = [EncoderInfo("av1", "c2.qti.av1.encoder", True)]
    choice = resolve_codec("auto", hw_av1_only)
    assert choice.codec == "av1"
    assert choice.hardware is True


def test_probe_failure_degrades_to_plain_h264():
    """No probe data -> h264 without a pin (scrcpy's own default pick)."""
    choice = resolve_codec("auto", None)
    assert choice.codec == "h264"
    assert choice.encoder is None
    assert choice.hardware is None


def test_explicit_h265_pins_hardware_encoder(encoders):
    choice = resolve_codec("h265", encoders)
    assert choice.codec == "h265"
    assert choice.encoder == "c2.qti.hevc.encoder"


def test_explicit_h264_pins_hardware_encoder(encoders):
    choice = resolve_codec("h264", encoders)
    assert choice.encoder == "c2.qti.avc.encoder"


def test_explicit_av1_without_hardware_degrades_to_h264(encoders):
    """The OPD2409 case: av1 sw-only -> h264 hardware, never a sw AV1 encode."""
    choice = resolve_codec("av1", encoders)
    assert choice.codec == "h264"
    assert choice.encoder == "c2.qti.avc.encoder"
    assert "av1" in choice.note


def test_explicit_h265_without_hardware_degrades_to_h264():
    choice = resolve_codec("h265", [EncoderInfo("h264", "c2.qti.avc.encoder", True)])
    assert choice.codec == "h264"
    assert choice.encoder == "c2.qti.avc.encoder"


# ---------------------------------------------------------------- the probe


def test_probe_encoders_returns_none_on_binary_missing(tmp_path):
    assert probe_encoders(str(tmp_path / "no-such-scrcpy"), "S") is None

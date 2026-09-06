"""Video codec discovery and selection (settings ``video_codec``).

scrcpy can report a device's MediaCodec encoders via
``scrcpy --list-encoders``. Duo probes the device once, caches the result
in ``data_dir/encoders.json`` (with a timestamp + serial) and picks the
mirroring codec:

    h264 硬件 > h265 硬件 > av1 硬件 > h264 软件 > h264（scrcpy 默认）

h264-hw is the auto top pick deliberately: the PC side decodes in
software, where AVC is much cheaper than HEVC (live regression: video
playback stuttered under h265). h265 remains a manual choice.

AV1 hardware encoders are practically nonexistent on phones, so auto only
reaches the av1 tier when probing confirms one; without hardware an
explicit ``av1`` degrades to h264 instead of burning CPU on a software
encode. Probe failure (device gone, timeout, unparseable output) degrades
to plain h264 without an ``--video-encoder`` pin - scrcpy's own default
pick - never a hard error: mirroring must still start.

Qt-free by design (plan.md §3 分层原则): the core layer stays importable
without a GUI stack. All selection logic is a pure function of the probe
result, which is what the priority tests mock.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from duo.core.paths import data_dir
from duo.core.winproc import creation_flags

#: A cached probe older than this is re-run (encoders only change with
#: system updates; a week is generous while still self-healing).
ENCODERS_TTL_S = 7 * 24 * 3600.0

#: Timeout for one ``--list-encoders`` run (pushes the scrcpy server to the
#: device first; observed ~2s live on USB, so 20s is a wide margin).
_PROBE_TIMEOUT_S = 20.0

#: One encoder line of ``scrcpy --list-encoders`` output::
#:
#:     --video-codec=h265 --video-encoder=c2.qti.hevc.encoder            (hw) [vendor]
_ENCODER_LINE_RE = re.compile(
        r"--video-codec=(?P<codec>\S+) --video-encoder=(?P<name>\S+)")

#: Codec selection priority for ``video_codec=auto``: pairs of
#: (codec, hardware-required), best first. h264-hw beats h265-hw on
#: purpose: scrcpy decodes on the PC in *software*, where AVC is far
#: lighter than HEVC - sustained video playback stuttered under h265 on
#: the user's rig (2026-09-06 live regression). h265 stays manually
#: selectable for strong PCs / tight USB bandwidth.
_AUTO_PRIORITY: tuple[tuple[str, bool], ...] = (
        ("h264", True),
        ("h265", True),
        ("av1", True),
        ("h264", False),   # software tier: any h264 entry, hardware preferred
)


@dataclass(frozen=True)
class EncoderInfo:
        """One device video encoder as reported by ``--list-encoders``."""

        codec: str          # h264 / h265 / av1 / vp8 / vp9
        name: str           # MediaCodec component name, e.g. c2.qti.hevc.encoder
        hardware: bool


@dataclass(frozen=True)
class CodecChoice:
        """The codec decision for one session.

        ``encoder=None`` means "no pin": scrcpy picks the device default
        (its own first choice, typically hardware when one exists).
        ``hardware=None`` means unknown (no usable probe data).
        """

        codec: str
        encoder: str | None = None
        hardware: bool | None = None
        note: str = ""      # one-line human reason, printed by the CLI


# ----------------------------------------------------------------------------
# Parsing (pure - directly testable against recorded --list-encoders output)
# ----------------------------------------------------------------------------

def parse_encoders(output: str) -> list[EncoderInfo]:
        """Parse ``scrcpy --list-encoders`` text into encoder entries.

        Alias lines (``OMX.qcom... (alias for c2.qti...)``) are skipped:
        they resolve to the same MediaCodec component already listed, and
        pinning an alias name would be redundant. Audio-encoder lines use
        ``--audio-codec`` and never match the video pattern.
        """
        encoders: list[EncoderInfo] = []
        for line in output.splitlines():
                if "(alias" in line:
                        continue
                match = _ENCODER_LINE_RE.search(line)
                if match is None:
                        continue
                encoders.append(EncoderInfo(
                        codec=match.group("codec"),
                        name=match.group("name"),
                        hardware="(hw)" in line,
                ))
        return encoders


# ----------------------------------------------------------------------------
# Cache: data_dir/encoders.json  {serial, probed_at, encoders[]}
# ----------------------------------------------------------------------------

def encoders_cache_path() -> Path:
        """Where the probe result lives: ``data_dir()/encoders.json``."""
        return data_dir() / "encoders.json"


def load_cached_encoders(
        path: Path,
        serial: str,
        now: float | None = None,
        ttl_s: float = ENCODERS_TTL_S,
) -> list[EncoderInfo] | None:
        """Cached probe result, or ``None`` when missing/corrupt/expired/foreign.

        A different serial never reads another device's cache: encoder
        components are model-specific (c2.qti.* vs c2.mtk.*), so a stale
        hit would pin a nonexistent encoder and scrcpy would fail to start.
        """
        try:
                raw = Path(path).read_text(encoding="utf-8")
                data = json.loads(raw)
        except (OSError, ValueError, TypeError):
                return None
        if not isinstance(data, dict):
                return None
        probed_at = data.get("probed_at")
        cached_serial = data.get("serial")
        entries = data.get("encoders")
        if (not isinstance(probed_at, (int, float))
                or not isinstance(cached_serial, str)
                or not isinstance(entries, list)):
                return None
        if cached_serial != serial:
                return None
        timestamp = now if now is not None else time.time()
        if timestamp - probed_at > ttl_s:
                return None
        encoders: list[EncoderInfo] = []
        for entry in entries:
                if not isinstance(entry, dict):
                        return None
                try:
                        encoders.append(EncoderInfo(
                                codec=str(entry["codec"]),
                                name=str(entry["name"]),
                                hardware=bool(entry["hardware"]),
                        ))
                except (KeyError, TypeError):
                        return None
        return encoders or None


def save_encoders_cache(
        path: Path,
        serial: str,
        encoders: Sequence[EncoderInfo],
        now: float | None = None,
) -> None:
        """Persist one probe result (timestamped; hand-inspectable JSON)."""
        payload = {
                "serial": serial,
                "probed_at": now if now is not None else time.time(),
                "encoders": [
                        {
                                "codec": info.codec,
                                "name": info.name,
                                "hardware": info.hardware,
                        }
                        for info in encoders
                ],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ----------------------------------------------------------------------------
# Probe (the only side-effectful step)
# ----------------------------------------------------------------------------

def probe_encoders(
        scrcpy_path: str,
        serial: str,
        timeout: float = _PROBE_TIMEOUT_S,
) -> list[EncoderInfo] | None:
        """Run ``scrcpy --list-encoders`` against one device.

        scrcpy logs to stderr and lists to stdout; both are concatenated
        before parsing. ``None`` = the probe itself failed (binary missing,
        device gone, timeout) or produced no usable entries - callers
        degrade to h264 without a pin.
        """
        try:
                result = subprocess.run(
                        [scrcpy_path, f"--serial={serial}", "--list-encoders"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=timeout,
                        check=False,
                        creationflags=creation_flags(),
                )
        except (OSError, subprocess.TimeoutExpired):
                return None
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        encoders = parse_encoders(output)
        return encoders or None


# ----------------------------------------------------------------------------
# Selection (pure - the priority tests exercise exactly this)
# ----------------------------------------------------------------------------

def _best_entry(
        encoders: Sequence[EncoderInfo], codec: str, hardware: bool
) -> EncoderInfo | None:
        """First probed entry for ``codec`` at the requested hardware tier."""
        for info in encoders:
                if info.codec == codec and info.hardware == hardware:
                        return info
        return None


def _h264_fallback(encoders: Sequence[EncoderInfo]) -> CodecChoice:
        """Best h264 among the probed entries (hardware first), no hardware
        found -> no pin (scrcpy's default h264 pick is software then)."""
        hw = _best_entry(encoders, "h264", True)
        if hw is not None:
                return CodecChoice("h264", hw.name, True, "使用 h264 硬件编码")
        sw = _best_entry(encoders, "h264", False)
        note = "设备无硬件编码器，回退 h264 软编" if sw else "探测结果无可用编码器，回退 h264"
        return CodecChoice(
                "h264",
                sw.name if sw else None,
                False if sw else None,
                note,
        )


def resolve_codec(
        video_codec: str,
        encoders: Sequence[EncoderInfo] | None,
) -> CodecChoice:
        """Turn the ``video_codec`` setting + probe data into one decision.

        ``encoders=None`` (probe failed/unavailable) degrades to plain h264
        without an encoder pin; explicit ``h264``/``h265`` without a
        hardware entry keeps the codec unpinned; explicit ``av1`` without
        hardware degrades to the h264 fallback (a software AV1 encode would
        peg the device CPU at mirroring bitrates).
        """
        if encoders is None:
                return CodecChoice(
                        "h264", None, None, "编码器探测不可用，回退 h264（scrcpy 默认选择）")
        if video_codec == "auto":
                for codec, hardware in _AUTO_PRIORITY:
                        entry = _best_entry(encoders, codec, hardware)
                        if entry is not None:
                                label = "硬件" if hardware else "软件"
                                return CodecChoice(
                                        entry.codec,
                                        entry.name,
                                        hardware,
                                        f"自动选择：{codec} {label}编码（{entry.name}）",
                                )
                return _h264_fallback(encoders)
        if video_codec == "h264":
                hw = _best_entry(encoders, "h264", True)
                if hw is not None:
                        return CodecChoice("h264", hw.name, True,
                                           f"h264 硬件编码（{hw.name}）")
                return CodecChoice("h264", None, False,
                                   "无 h264 硬件编码器，使用 scrcpy 默认")
        if video_codec in ("h265", "av1"):
                hw = _best_entry(encoders, video_codec, True)
                if hw is not None:
                        return CodecChoice(video_codec, hw.name, True,
                                           f"{video_codec} 硬件编码（{hw.name}）")
                if video_codec == "h265":
                        return _h264_fallback(encoders)
                # av1 without confirmed hardware: degrade, never software-encode.
                fallback = _h264_fallback(encoders)
                return CodecChoice(
                        fallback.codec, fallback.encoder, fallback.hardware,
                        "设备无 av1 硬件编码器，回退 h264",
                )
        # Settings validation makes this unreachable; keep a safe default.
        return _h264_fallback(encoders)

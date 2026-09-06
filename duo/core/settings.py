"""User settings: load, validate, persist (JSON under the data dir).

Contract (docs/window-experience.md §4):
- storage: ``data_dir()/settings.json``, transparent and hand-editable
- load must never raise: missing/corrupt/ill-typed files fall back to
  defaults and report problems for the UI to surface once
- save validates first, then replaces atomically (tmp file + os.replace)
- priority everywhere: explicit CLI args > saved settings > built-in defaults

Qt-free by design: the core layer stays importable without a GUI stack.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from duo.core.paths import data_dir

VALID_CORNER_MODES = ("system", "g2", "none")
VALID_AUDIO_POLICIES = ("latest", "all", "off")
VALID_VIDEO_CODECS = ("auto", "h264", "h265", "av1")

# Input ranges, not hardware promises (docs §4.1).
FPS_RANGE = (1, 240)
BITRATE_RANGE = (1, 200)
DPI_RANGE = (120, 640)
CORNER_RANGE = (0, 96)          # above 96 is test territory (160 verified live)


@dataclass(frozen=True)
class Settings:
        """Persisted user preferences for engine defaults and appearance."""

        version: int = 1
        scrcpy_path: str = ""
        adb_path: str = ""
        fps: int | None = 60        # 60: even pacing on 120Hz panels (90
                                     # would judder 2:1); 120 for max UI
                                     # smoothness on strong PCs
        bitrate_mbps: int | None = 30
        dpi: int | None = None
        corner_mode: str = "system"    # system = DWM default rounding;
                                         # g2 = quartic region, long-term goal
                                         # (edge quality/clipping unresolved)
        corner_size_dip: int = 48      # iPhone/iPad-like squircle proportion
        glass_enabled: bool = True
        # 投屏质量三项（docs/mirroring-quality.md）：
        # latest = 新会话带音频时，其他音频会话自动重启为 --no-audio
        # all = 不做单音频仲裁（多会话并行音频，用户自担混音嘈杂）
        # off = 全部静音
        audio_policy: str = "latest"
        # auto = 探测设备硬件编码器并择优（duo.core.codec）；显式指定则用之
        video_codec: str = "auto"
        # --turn-screen-off：黑屏防误触，主要对 mirror（整机镜像）有意义
        turn_screen_off: bool = False

# 历史（2026-09-06）：曾有 flex_resolution 基准分辨率档（1440p/1080p/native），
# 用户决策撤除——档位选择造成困扰，flex 一律原始分辨率；性能由
# codec=h264+fps=60 承担。旧 settings.json 里残留的 flex_resolution 键被
# _sanitize 无害忽略（只读已知键）。


def settings_path() -> Path:
        """Where settings live: ``data_dir()/settings.json``."""
        return data_dir() / "settings.json"


def _clamp_or_none(value: object, low: int, high: int, field: str,
                   fallback: int | None, problems: list[str]) -> int | None:
        """Coerce to an int in range; anything else falls back to ``fallback``."""
        if value is None:
                return fallback
        if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"{field}: 期望整数，实际为 {value!r}")
                return fallback
        if not low <= value <= high:
                problems.append(f"{field}: {value} 超出范围 {low}–{high}")
                return fallback
        return value


def _sanitize(raw: dict, problems: list[str]) -> Settings:
        """Build Settings from a raw dict, dropping anything invalid.

        Invalid values fall back to the field default (never to a guess), so
        a hand-edited mistake cannot silently change behaviour elsewhere.
        """
        defaults = Settings()
        version = raw.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool):
                problems.append(f"version: 期望整数，实际为 {version!r}")
                version = 1

        def text(field: str) -> str:
                value = raw.get(field, "")
                if isinstance(value, str):
                        return value
                problems.append(f"{field}: 期望字符串，实际为 {value!r}")
                return ""

        scrcpy_path = text("scrcpy_path")
        adb_path = text("adb_path")

        fps = _clamp_or_none(raw.get("fps", defaults.fps), *FPS_RANGE,
                             "fps", defaults.fps, problems)
        bitrate = _clamp_or_none(raw.get("bitrate_mbps", defaults.bitrate_mbps),
                                 *BITRATE_RANGE, "bitrate_mbps",
                                 defaults.bitrate_mbps, problems)
        dpi = _clamp_or_none(raw.get("dpi"), *DPI_RANGE, "dpi", None, problems)
        corner_size = _clamp_or_none(raw.get("corner_size_dip",
                                             defaults.corner_size_dip),
                                     *CORNER_RANGE, "corner_size_dip",
                                     defaults.corner_size_dip, problems)

        corner_mode = raw.get("corner_mode", defaults.corner_mode)
        if corner_mode not in VALID_CORNER_MODES:
                problems.append(
                        f"corner_mode: {corner_mode!r} 不在 {VALID_CORNER_MODES}")
                corner_mode = defaults.corner_mode

        glass = raw.get("glass_enabled", defaults.glass_enabled)
        if not isinstance(glass, bool):
                problems.append(f"glass_enabled: 期望布尔，实际为 {glass!r}")
                glass = defaults.glass_enabled

        audio_policy = raw.get("audio_policy", defaults.audio_policy)
        if audio_policy not in VALID_AUDIO_POLICIES:
                problems.append(
                        f"audio_policy: {audio_policy!r} 不在 {VALID_AUDIO_POLICIES}")
                audio_policy = defaults.audio_policy

        video_codec = raw.get("video_codec", defaults.video_codec)
        if video_codec not in VALID_VIDEO_CODECS:
                problems.append(
                        f"video_codec: {video_codec!r} 不在 {VALID_VIDEO_CODECS}")
                video_codec = defaults.video_codec

        turn_screen_off = raw.get("turn_screen_off", defaults.turn_screen_off)
        if not isinstance(turn_screen_off, bool):
                problems.append(f"turn_screen_off: 期望布尔，实际为 {turn_screen_off!r}")
                turn_screen_off = defaults.turn_screen_off

        # 注意：这里只读上方已知键。settings.json 里已撤除的键（如历史
        # flex_resolution）或任何多余键都会被无害忽略，不进 problems。
        return Settings(
                version=version,
                scrcpy_path=scrcpy_path,
                adb_path=adb_path,
                fps=fps,
                bitrate_mbps=bitrate,
                dpi=dpi,
                corner_mode=corner_mode,
                corner_size_dip=corner_size if corner_size is not None else 0,
                glass_enabled=glass,
                audio_policy=audio_policy,
                video_codec=video_codec,
                turn_screen_off=turn_screen_off,
        )


def load_settings() -> tuple[Settings, list[str]]:
        """Read settings.json; never raises.

        Returns the effective settings plus a list of human-readable
        problems found in the file (missing/corrupt/invalid fields).
        """
        path = settings_path()
        try:
                data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
                return Settings(), []
        except (OSError, ValueError) as exc:
                return Settings(), [f"settings.json 无法读取（{exc}），已用默认值"]
        if not isinstance(data, dict):
                return Settings(), ["settings.json 顶层不是对象，已用默认值"]
        problems: list[str] = []
        return _sanitize(data, problems), problems


def validate(settings: Settings) -> list[str]:
        """Problems of this Settings instance; empty list means savable."""
        problems: list[str] = []
        _sanitize(asdict(settings), problems)   # appends problems in place
        return problems


def save_settings(settings: Settings) -> None:
        """Validate then atomically replace settings.json."""
        problems = validate(settings)
        if problems:
                raise ValueError("; ".join(problems))
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
                json.dumps(asdict(settings), ensure_ascii=False, indent=2),
                encoding="utf-8",
        )
        os.replace(tmp, path)


def resolve_adb_path(settings: Settings, discovered: str | None, fallback: str) -> str:
        """adb binary for the panel: settings override > discovery > fallback.

        Resolving once per process (GUI startup, or after saving settings)
        keeps device polling, install checks and spawned CLI sessions on the
        same adb - no mixed server versions killing each other.
        """
        return resolve_tool("adb", settings, discovered) or fallback


def corner_radius_dip(settings: Settings) -> int:
        """Overlay corner radius for a session (0 = no region).

        Only the experimental ``g2`` mode requests a region; ``system``
        (the default) and ``none`` keep the window untouched so Windows'
        own DWM rounding applies.
        """
        if settings.corner_mode != "g2":
                return 0
        return settings.corner_size_dip


def resolve_tool(name: str, settings: Settings, found: str | None) -> str | None:
        """Preferred binary for ``name``: settings override > discovery.

        An explicitly configured path wins as-is (the user owns it); empty
        settings fall back to PATH discovery. Returns None when neither.
        """
        configured = settings.scrcpy_path if name == "scrcpy" else settings.adb_path
        return configured or found


__all__ = [
        "CORNER_RANGE",
        "Settings",
        "VALID_AUDIO_POLICIES",
        "VALID_CORNER_MODES",
        "VALID_VIDEO_CODECS",
        "corner_radius_dip",
        "load_settings",
        "resolve_adb_path",
        "resolve_tool",
        "save_settings",
        "settings_path",
        "validate",
]

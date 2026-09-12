"""Tests for the settings core (load/validate/save, priority helpers)."""

from __future__ import annotations

import json

import pytest

import duo.core.settings as settings_mod
from duo.core.settings import (
        Settings,
        corner_radius_dip,
        load_settings,
        resolve_adb_path,
        resolve_tool,
        save_settings,
        validate,
)


def test_defaults_roundtrip(tmp_path, monkeypatch):
        """Save→load preserves values; file is hand-readable JSON."""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        save_settings(Settings(fps=120, bitrate_mbps=8, corner_size_dip=64,
                               adb_path=r"C:\工具\adb.exe"))
        loaded, problems = load_settings()
        assert problems == []
        assert loaded.fps == 120
        assert loaded.bitrate_mbps == 8
        assert loaded.corner_size_dip == 64
        assert loaded.adb_path == r"C:\工具\adb.exe"
        raw = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
        assert raw["fps"] == 120


def test_missing_file_gives_defaults(tmp_path, monkeypatch):
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        loaded, problems = load_settings()
        assert problems == []
        assert loaded == Settings()


def test_default_corner_mode_is_system_rounding():
        """Out of the box we keep Windows' own DWM rounding: the G2 region
        path stays opt-in until its edge quality is solved (long-term goal)."""
        fresh = Settings()
        assert fresh.corner_mode == "system"
        assert corner_radius_dip(fresh) == 0


def test_corrupt_file_falls_back_with_problem(tmp_path, monkeypatch):
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text("{not json", encoding="utf-8")
        loaded, problems = load_settings()
        assert loaded == Settings()
        assert len(problems) == 1


def test_invalid_values_reported_and_dropped(tmp_path, monkeypatch):
        """Ill-typed or out-of-range fields fall back to defaults per field."""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text(
                json.dumps({"fps": 999, "bitrate_mbps": "high", "corner_mode": "circle",
                            "corner_size_dip": True, "glass_enabled": "yes"}),
                encoding="utf-8",
        )
        loaded, problems = load_settings()
        assert loaded.fps == Settings().fps            # out of range -> default
        assert loaded.bitrate_mbps == Settings().bitrate_mbps
        assert loaded.corner_mode == Settings().corner_mode
        assert loaded.corner_size_dip == Settings().corner_size_dip
        assert loaded.glass_enabled is True
        assert len(problems) == 5


def test_save_rejects_invalid(tmp_path, monkeypatch):
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        with pytest.raises(ValueError):
                save_settings(Settings(fps=0))
        with pytest.raises(ValueError):
                save_settings(Settings(corner_size_dip=500))
        # nothing written on rejection
        assert not (tmp_path / "s.json").exists()


def test_atomic_write_leaves_no_tmp(tmp_path, monkeypatch):
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        save_settings(Settings())
        assert (tmp_path / "s.json").exists()
        assert not (tmp_path / "s.json.tmp").exists()
        assert list(tmp_path.iterdir()) == [tmp_path / "s.json"]


def test_corner_radius_mapping():
        """g2 mode maps to its size; system/none map to 0 (no region)."""
        assert corner_radius_dip(Settings(corner_mode="g2", corner_size_dip=48)) == 48
        assert corner_radius_dip(Settings(corner_mode="system")) == 0
        assert corner_radius_dip(Settings(corner_mode="none")) == 0


def test_resolve_tool_settings_win_over_discovery():
        """Explicit settings paths win as-is; empty falls back to discovery."""
        s = Settings(scrcpy_path=r"C:\bin\scrcpy.exe")
        assert resolve_tool("scrcpy", s, "/usr/bin/scrcpy") == r"C:\bin\scrcpy.exe"
        assert resolve_tool("adb", s, "/usr/bin/adb") == "/usr/bin/adb"
        assert resolve_tool("adb", Settings(), None) is None


def test_resolve_adb_path_priority():
        """Panel adb: settings override > PATH discovery > literal fallback."""
        override = Settings(adb_path=r"C:\o\adb.exe")
        assert resolve_adb_path(override, "/found/adb", "adb.exe") == r"C:\o\adb.exe"
        assert resolve_adb_path(Settings(), "/found/adb", "adb.exe") == "/found/adb"
        assert resolve_adb_path(Settings(), None, "adb.exe") == "adb.exe"


def test_validate_clean_instance():
        assert validate(Settings()) == []


# --------------------------------------------------- 投屏质量三字段（新）


def test_quality_fields_roundtrip(tmp_path, monkeypatch):
    """audio_policy / video_codec / turn_screen_off persist and reload."""
    monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
    save_settings(Settings(audio_policy="all", video_codec="h265",
                           turn_screen_off=True))
    loaded, problems = load_settings()
    assert problems == []
    assert loaded.audio_policy == "all"
    assert loaded.video_codec == "h265"
    assert loaded.turn_screen_off is True


def test_quality_fields_defaults():
    """latest/auto/false: latest 会话优先音频，auto 走编码器探测，屏幕默认不关。"""
    fresh = Settings()
    assert fresh.audio_policy == "latest"
    assert fresh.video_codec == "auto"
    assert fresh.turn_screen_off is False


def test_quality_fields_invalid_reported_and_dropped(tmp_path, monkeypatch):
    """Ill-typed/out-of-set values fall back per field with one problem each."""
    monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
    (tmp_path / "s.json").write_text(
        json.dumps({"audio_policy": "loudest", "video_codec": "mpeg2",
                    "turn_screen_off": "yes"}),
        encoding="utf-8",
    )
    loaded, problems = load_settings()
    defaults = Settings()
    assert loaded.audio_policy == defaults.audio_policy
    assert loaded.video_codec == defaults.video_codec
    assert loaded.turn_screen_off == defaults.turn_screen_off
    assert len(problems) == 3
    assert any("audio_policy" in p for p in problems)
    assert any("video_codec" in p for p in problems)
    assert any("turn_screen_off" in p for p in problems)


def test_quality_fields_save_rejects_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
    with pytest.raises(ValueError):
        save_settings(Settings(audio_policy="loudest"))
    with pytest.raises(ValueError):
        save_settings(Settings(video_codec="mpeg2"))
    assert not (tmp_path / "s.json").exists()


def test_stale_flex_resolution_key_is_ignored(tmp_path, monkeypatch):
        """已撤除的 flex_resolution 键残留在旧 settings.json 里被无害忽略。

        2026-09-06 用户决策撤除基准分辨率档位后，旧文件可能仍带该键：
        _sanitize 只读已知键，残留键不进 problems、不产生字段。
        """
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text(
                json.dumps({"flex_resolution": "1080p", "fps": 90}), encoding="utf-8")
        loaded, problems = load_settings()
        assert problems == []
        assert loaded.fps == 90
        assert not hasattr(loaded, "flex_resolution")


# ------------------------------------------- 窗口栏模式（上巴/下巴）


def test_bar_mode_fields_roundtrip(tmp_path, monkeypatch):
        """top_bar_mode / bottom_bar_mode 落盘重读不丢（含 none）。"""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        save_settings(Settings(top_bar_mode="native", bottom_bar_mode="none"))
        loaded, problems = load_settings()
        assert problems == []
        assert loaded.top_bar_mode == "native"
        assert loaded.bottom_bar_mode == "none"


def test_bar_mode_fields_defaults():
        """新默认（2026-09-09）：上巴 immersive（无边框 + overlay 悬浮控件），
        下巴 none（scrcpy 右键已是返回，下巴对多数用户冗余）。"""
        fresh = Settings()
        assert fresh.top_bar_mode == "immersive"
        assert fresh.bottom_bar_mode == "none"


def test_bar_mode_enum_accepts_none_roundtrip(tmp_path, monkeypatch):
        """VALID_BAR_MODES 三枚举（immersive|native|none）全量放行：none
        进入枚举后，设置文件往返自动持久化（无需专门分支）。"""
        assert settings_mod.VALID_BAR_MODES == ("immersive", "native", "none")
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        for top, bottom in (("none", "none"), ("none", "immersive"),
                            ("immersive", "none")):
                save_settings(Settings(top_bar_mode=top, bottom_bar_mode=bottom))
                loaded, problems = load_settings()
                assert problems == []
                assert (loaded.top_bar_mode, loaded.bottom_bar_mode) == (top, bottom)


def test_bar_mode_invalid_falls_back_with_problem(tmp_path, monkeypatch):
        """非法值逐字段回退各自默认（上巴 immersive / 下巴 none），各报一条
        问题（同 audio_policy 模式）。"""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text(
                json.dumps({"top_bar_mode": "floating", "bottom_bar_mode": 3}),
                encoding="utf-8",
        )
        loaded, problems = load_settings()
        assert loaded.top_bar_mode == "immersive"
        assert loaded.bottom_bar_mode == "none"
        assert len(problems) == 2
        assert any("top_bar_mode" in p for p in problems)
        assert any("bottom_bar_mode" in p for p in problems)


def test_bar_mode_save_rejects_invalid(tmp_path, monkeypatch):
        """save 前校验拒绝非法枚举，不落盘。"""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        with pytest.raises(ValueError):
                save_settings(Settings(top_bar_mode="floating"))
        with pytest.raises(ValueError):
                save_settings(Settings(bottom_bar_mode="titanium"))
        assert not (tmp_path / "s.json").exists()


def test_old_savefile_without_bar_modes_loads_defaults(tmp_path, monkeypatch):
        """from_dict 兼容旧存档：字段缺失 → 各自默认（上巴 immersive / 下巴
        none），且不产生问题。老档已存的显式值不迁移、原样保留（另测）。"""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        legacy = {
                "version": 1, "fps": 90, "audio_policy": "all",
                "video_codec": "h264", "turn_screen_off": True,
                "window_aspect": "free",   # 残留历史键无害忽略
        }
        (tmp_path / "s.json").write_text(json.dumps(legacy), encoding="utf-8")
        loaded, problems = load_settings()
        assert problems == []
        assert loaded.top_bar_mode == "immersive"
        assert loaded.bottom_bar_mode == "none"


def test_saved_explicit_bar_modes_not_migrated(tmp_path, monkeypatch):
        """改默认不迁移老用户：settings.json 里已存的显式值原样保留
        （2026-09-09 决策——下巴老默认 immersive 的用户不被静默改掉）。"""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text(
                json.dumps({"top_bar_mode": "native", "bottom_bar_mode": "immersive"}),
                encoding="utf-8")
        loaded, problems = load_settings()
        assert problems == []
        assert loaded.top_bar_mode == "native"
        assert loaded.bottom_bar_mode == "immersive"


# ---------------------- DPI 默认值与渲染倍率（2026-09-11）


def test_dpi_defaults_to_desktop_160():
        """新默认 = 160 桌面密度（不再是设备密度探测）；倍率默认 1.0 原生。"""
        fresh = Settings()
        assert fresh.dpi == 160
        assert fresh.render_scale == 1.0


def test_dpi_explicit_null_keeps_follow_device(tmp_path, monkeypatch):
        """显式 "dpi": null = 跟随设备（保存页开关的持久形态），不得被新
        默认 160 顶掉——老文件静默翻语义比缺省更糟。"""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text(json.dumps({"dpi": None}), encoding="utf-8")
        loaded, problems = load_settings()
        assert problems == []
        assert loaded.dpi is None


def test_dpi_missing_key_falls_back_to_default(tmp_path, monkeypatch):
        """缺键（新装/手删）= 新默认 160。"""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text(json.dumps({"fps": 60}), encoding="utf-8")
        loaded, _problems = load_settings()
        assert loaded.dpi == 160


@pytest.mark.parametrize("raw,expected", [
        (2.5, 2.5), (2, 2.0), (1.0, 1.0), (3.0, 3.0),
])
def test_render_scale_accepts_numbers(tmp_path, monkeypatch, raw, expected):
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text(
                json.dumps({"render_scale": raw}), encoding="utf-8")
        loaded, problems = load_settings()
        assert problems == []
        assert loaded.render_scale == expected


@pytest.mark.parametrize("raw", ["fast", True, 0.5, 3.5, 8.0, None, [2.0]])
def test_render_scale_rejects_junk(tmp_path, monkeypatch, raw):
        """坏值/超范围回默认 1.0 并进问题清单（保存前校验同路）。"""
        monkeypatch.setattr(settings_mod, "settings_path", lambda: tmp_path / "s.json")
        (tmp_path / "s.json").write_text(
                json.dumps({"render_scale": raw}), encoding="utf-8")
        loaded, problems = load_settings()
        assert loaded.render_scale == 1.0
        assert any("render_scale" in p for p in problems)
        with pytest.raises(ValueError):
            save_settings(Settings(render_scale=9.0))

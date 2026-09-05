"""Tests for the settings core (load/validate/save, priority helpers)."""

from __future__ import annotations

import json

import pytest

import duo.core.settings as settings_mod
from duo.core.settings import (
        Settings,
        corner_radius_dip,
        load_settings,
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


def test_validate_clean_instance():
        assert validate(Settings()) == []

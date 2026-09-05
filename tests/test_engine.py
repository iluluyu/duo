"""Tests for duo.core.engine."""

from __future__ import annotations

from duo.core import engine
from duo.core.engine import REQUIRED_TOOLS, ToolInfo, probe


def test_required_tools_listing():
        """scrcpy and adb are the two engine dependencies."""
        assert REQUIRED_TOOLS == ("scrcpy", "adb")


def test_tool_names_prefers_windows_build_under_wsl(monkeypatch):
        """Under WSL the .exe variant must be probed first."""
        monkeypatch.setattr(engine, "is_wsl", lambda: True)
        assert engine.tool_names("scrcpy") == ("scrcpy.exe", "scrcpy")


def test_tool_names_native_linux(monkeypatch):
        """Outside WSL only the plain name is probed."""
        monkeypatch.setattr(engine, "is_wsl", lambda: False)
        assert engine.tool_names("scrcpy") == ("scrcpy",)


def test_probe_missing_tool():
        """A tool absent from PATH reports unavailable with no version."""
        info = probe("duo-definitely-not-a-real-tool")
        assert info.path is None
        assert info.version is None
        assert not info.available


def test_probe_returns_toolinfo():
        """Probing a real tool yields a ToolInfo with a resolved path."""
        info = probe("scrcpy")
        assert isinstance(info, ToolInfo)
        assert info.name == "scrcpy"
        if info.path is None:
                assert info.version is None
                assert not info.available
        else:
                assert info.available
                assert info.version is None or "scrcpy" in info.version.lower()


def test_available_flag_consistency():
        """The available flag mirrors whether a path was resolved."""
        present = ToolInfo(name="x", path="/usr/bin/x", version="1.0")
        missing = ToolInfo(name="x", path=None, version=None)
        assert present.available
        assert not missing.available


def test_adb_pin_env_native_windows(monkeypatch):
        """Native Windows pins adb through the ADB variable as-is."""
        monkeypatch.setattr(engine, "is_wsl", lambda: False)
        assert engine.adb_pin_env(r"C:\\platform-tools\\adb.exe") == {
                "ADB": r"C:\\platform-tools\\adb.exe"
        }


def test_adb_pin_env_wsl_translates_and_allowlists(monkeypatch):
        """Under WSL the path becomes Windows-shaped and ADB joins WSLENV
        (interop drops unlisted variables), preserving existing entries."""
        monkeypatch.setattr(engine, "is_wsl", lambda: True)
        monkeypatch.setenv("WSLENV", "FOO")
        env = engine.adb_pin_env(
                "/mnt/c/tools/adb.exe",
                to_windows=lambda p: r"C:\\tools\\adb.exe",
        )
        assert env["ADB"] == r"C:\\tools\\adb.exe"
        assert env["WSLENV"] == "FOO:ADB"
        # No duplicate when ADB is already allow-listed.
        monkeypatch.setenv("WSLENV", "FOO:ADB")
        env = engine.adb_pin_env(
                "/mnt/c/tools/adb.exe",
                to_windows=lambda p: r"C:\\tools\\adb.exe",
        )
        assert env["WSLENV"] == "FOO:ADB"

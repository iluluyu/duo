"""Tests for duo.core.engine."""

from __future__ import annotations

from duo.core.engine import REQUIRED_TOOLS, ToolInfo, probe


def test_required_tools_listing():
        """scrcpy and adb are the two engine dependencies."""
        assert REQUIRED_TOOLS == ("scrcpy", "adb")


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

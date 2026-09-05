"""Tests for the window chrome overlay launcher (C# overlay + csc build)."""

from __future__ import annotations

from pathlib import Path

import pytest

import duo.core.chrome as chrome
from duo.core.chrome import (
        ChromeError,
        compile_command,
        overlay_command,
)


def test_overlay_source_shipped_and_hardened():
        """The C# overlay source exists and carries the known traps' antidotes."""
        assert chrome.OVERLAY_SOURCE.is_file()
        text = chrome.OVERLAY_SOURCE.read_text(encoding="utf-8-sig")
        # DPI trap: overlay coordinates must be physical pixels.
        assert "SetProcessDPIAware" in text
        # Per-pixel alpha acrylic (SetWindowCompositionAttribute is dead on 24H2).
        assert "UpdateLayeredWindow" in text
        assert "PrintWindow" in text
        # Top-level minimum-width clamp would distort the capsule.
        assert "WM_GETMINMAXINFO" in text
        # Borderless repairs: native resize frame + Win11 rounded corners.
        assert "0x00040000" in text  # WS_THICKFRAME
        assert "DwmSetWindowAttribute" in text
        # Taskbar-safe emulated maximize with DWM frame insets.
        assert "FrameInsets" in text
        assert "GetMonitorInfoW" in text
        # Persistent chin tracks window moves in realtime.
        assert "SetWinEventHook" in text
        # Honor-style GDI+ nav symbols and Fluent window glyphs.
        assert "DrawChevron" in text
        assert "DrawRing" in text
        assert "0xE921" in text and "0xE8BB" in text
        # Navigation keys for the chin (BACK=4, HOME=3).
        # mBack chin: tap = BACK (keyevent 4), long-press = HOME (keyevent 3).
        assert "AdbKey(4)" in text
        assert "AdbKey(3)" in text
        assert "--home" in text
        assert "AdbKey" in text
        # Ratio-locked resize: display mode channel, live video sizes tailed
        # from the session log ("INFO: Texture: WxH"), aspect convergence,
        # DIP minimums and capture-loss guards on every drag surface.
        assert "--display-mode" in text
        assert "--session-log" in text
        assert "Texture:" in text
        assert "ConvergeToVideoAspect" in text
        assert "LogicalMinW" in text and "LogicalMinH" in text
        assert "MouseCaptureChanged" in text
        # All four edges and corners have their own hot zones.
        assert "new EdgeStrip[9]" in text
        assert "IsZoomed" in text


def test_compile_command_shape():
        """The csc argv targets a windowed exe with WinForms references."""
        argv = compile_command(
                "/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe",
                "\\\\wsl.localhost\\archlinux\\home\\duo\\chrome_overlay.cs",
                "\\\\wsl.localhost\\archlinux\\home\\.cache\\DuoChromeOverlay.exe",
        )
        assert argv[0].endswith("csc.exe")
        assert "-nologo" in argv
        assert "-target:winexe" in argv
        assert "-optimize+" in argv
        assert any(a.startswith("-out:") for a in argv)
        refs = [a for a in argv if a.startswith("-r:")]
        assert "-r:System.Windows.Forms.dll" in refs
        assert "-r:System.Drawing.dll" in refs
        assert argv[-1].endswith("chrome_overlay.cs")


def test_overlay_command_plain_argv():
        """The overlay argv uses plain --title/--serial/--adb/--home."""
        argv = overlay_command("/x/DuoChromeOverlay.exe", "不背单词", "4444bd6b", "C:\\a.exe", True)
        assert argv[0] == "/x/DuoChromeOverlay.exe"
        assert argv[argv.index("--title") + 1] == "不背单词"
        assert argv[argv.index("--serial") + 1] == "4444bd6b"
        assert argv[argv.index("--adb") + 1] == "C:\\a.exe"
        assert argv[argv.index("--home") + 1] == "1"
        assert "TitleB64" not in " ".join(argv)


def test_overlay_command_carries_display_mode_and_log():
        """Mirror/fixed windows get the mode + live-size channel; fixed also
        gets its known initial video size; flex gets no video flags."""
        argv = overlay_command(
                "/x.exe", "t", "s", "a", False,
                display_mode="mirror",
                session_log=r"C:\logs\\1.log",
        )
        assert argv[argv.index("--display-mode") + 1] == "mirror"
        assert argv[argv.index("--session-log") + 1] == r"C:\logs\\1.log"
        assert "--video-w" not in argv
        argv = overlay_command(
                "/x.exe", "t", "s", "a", False,
                display_mode="fixed",
                video_width=1252,
                video_height=2088,
        )
        assert argv[argv.index("--video-w") + 1] == "1252"
        assert argv[argv.index("--video-h") + 1] == "2088"
        argv = overlay_command("/x.exe", "t", "s", "a", False)
        assert argv[argv.index("--display-mode") + 1] == "flex"
        assert "--session-log" not in argv


def test_overlay_command_home_off_for_virtual_displays():
        """App windows (virtual displays) disable the long-press-home ring."""
        argv = overlay_command("/x/DuoChromeOverlay.exe", "t", "s", "a", False)
        assert argv[argv.index("--home") + 1] == "0"


def test_build_is_fresh_matches_stamp(tmp_path):
        """Freshness compares the cached exe sidecar against the source hash."""
        exe = tmp_path / "DuoChromeOverlay.exe"
        stamp = tmp_path / "DuoChromeOverlay.exe.sha256"
        assert not chrome.build_is_fresh(exe, stamp, "abc")
        exe.write_bytes(b"MZ")
        assert not chrome.build_is_fresh(exe, stamp, "abc")
        stamp.write_text("abc\n", encoding="utf-8")
        assert chrome.build_is_fresh(exe, stamp, "abc")
        assert not chrome.build_is_fresh(exe, stamp, "other")


def test_wsl_path_translation(monkeypatch, tmp_path):
        """Absolute paths go through wslpath; failures raise ChromeError."""

        class FakeResult:
                def __init__(self, returncode: int, stdout: str) -> None:
                        self.returncode = returncode
                        self.stdout = stdout

        def fake_run(cmd, **kwargs):
                assert cmd[:2] == ["wslpath", "-w"]
                return FakeResult(0, "\\\\wsl.localhost\\archlinux" + str(cmd[2]) + "\n")

        monkeypatch.setattr(chrome.subprocess, "run", fake_run)
        assert chrome.wsl_to_windows_path(str(tmp_path)) == (
                "\\\\wsl.localhost\\archlinux" + str(tmp_path)
        )
        assert chrome.wsl_to_windows_path("C:\\bin\\adb.exe") == "C:\\bin\\adb.exe"
        monkeypatch.setattr(
                chrome.subprocess, "run", lambda cmd, **kwargs: FakeResult(1, "")
        )
        with pytest.raises(ChromeError):
                chrome.wsl_to_windows_path(str(tmp_path))


def test_ensure_built_missing_source_raises(monkeypatch, tmp_path):
        """A missing overlay source is a build error."""
        monkeypatch.setattr(chrome, "OVERLAY_SOURCE", tmp_path / "gone.cs")
        with pytest.raises(ChromeError):
                chrome.ensure_built()


def test_stop_before_start_is_noop(monkeypatch):
        """Stopping an overlay that never started must not raise."""
        monkeypatch.setattr(chrome, "ensure_built", lambda: Path("/x/y.exe"))
        overlay = chrome.ChromeOverlay(title="t", serial="s", adb_path="adb")
        assert not overlay.running
        overlay.stop()
        assert not overlay.running

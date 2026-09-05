"""GUI launch logic: argv composition and per-app portrait preferences."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from duo.ui.main_window import (
        DEFAULT_PORTRAIT,
        build_device_mirror_argv,
        build_launch_argv,
        load_portrait_prefs,
        save_portrait_prefs,
)


class _StubFile:
        """Minimal path duck-type for _prefs_path monkeypatching."""

        def __init__(self, payload: str | None) -> None:
                self.payload = payload
                self.written: str | None = None
                self.parent = Path(".")

        def read_text(self, encoding: str = "utf-8") -> str:
                if self.payload is None:
                        raise OSError("missing")
                return self.payload

        def write_text(self, text: str, encoding: str = "utf-8") -> None:
                self.written = text

        def mkdir(self, parents: bool = True, exist_ok: bool = True) -> None:
                pass


def test_launch_argv_carries_chrome_serial_and_orientation():
        """A panel launch always gets --chrome; portrait follows the flag."""
        argv = build_launch_argv("tv.danmaku.bili", "S1", portrait=False)
        assert argv[argv.index("--app") + 1] == "tv.danmaku.bili"
        assert argv[argv.index("--serial") + 1] == "S1"
        assert "--chrome" in argv
        assert "--portrait" not in argv
        # Audio is always requested; the CLI arbitrates ownership.
        assert "--no-audio" not in argv


def test_launch_argv_portrait_flag():
        """Portrait is a flag."""
        argv = build_launch_argv("cn.com.langeasy.LangEasyLexis", "S1", portrait=True)
        assert "--portrait" in argv


def test_device_mirror_argv():
        """Direct mirroring uses the mirror display mode with its own title."""
        argv = build_device_mirror_argv("S1")
        assert argv[argv.index("--display") + 1] == "mirror"
        assert argv[argv.index("--title") + 1] == "平板镜像"
        assert "--chrome" in argv
        assert "--app" not in argv


def test_launch_argv_frozen_routes_through_exe(monkeypatch):
        """Under PyInstaller the exe itself is the CLI entry."""
        monkeypatch.setattr("sys.frozen", True, raising=False)
        argv = build_launch_argv("tv.danmaku.bili", "S1", portrait=False)
        assert argv[0] == sys.executable
        assert "-m" not in argv


def test_portrait_prefs_roundtrip(tmp_path, monkeypatch):
        """Prefs persist as JSON; defaults come back when the file is absent."""
        from duo.ui import controller  # prefs logic was migrated here

        stub = _StubFile(None)
        monkeypatch.setattr(controller, "_prefs_path", lambda: stub)
        prefs = load_portrait_prefs()
        assert prefs["cn.com.langeasy.LangEasyLexis"] is True  # default portrait
        prefs["tv.danmaku.bili"] = True
        save_portrait_prefs(prefs)
        assert stub.written is not None
        assert json.loads(stub.written)["portrait"]["tv.danmaku.bili"] is True


def test_portrait_prefs_corrupt_file_falls_back_to_defaults(monkeypatch):
        """A corrupt prefs file must not take the panel down."""
        from duo.ui import controller  # prefs logic was migrated here

        monkeypatch.setattr(controller, "_prefs_path", lambda: _StubFile("{not json"))
        assert load_portrait_prefs() == dict(DEFAULT_PORTRAIT)


def test_gui_importable_without_display():
        """The panel module imports cleanly headless (Qt lazy-loaded)."""
        import duo.ui.main_window as mw  # noqa: F401


def test_columns_fit_width_with_bounds():
        """Column counts follow the available width within sane bounds."""
        from duo.ui.main_window import MainWindow

        cols = MainWindow._columns_for
        assert cols(0, 58, 4, 10) == 4          # never below minimum
        assert cols(120, 58, 4, 10) == 4        # ~2 cells -> clamped up
        assert cols(360, 58, 4, 10) == 5
        assert cols(1000, 58, 4, 10) == 10      # capped at maximum

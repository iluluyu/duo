"""GUI launch logic: argv composition and per-app portrait preferences."""

from __future__ import annotations

import json
from pathlib import Path

from duo.ui.main_window import (
        DEFAULT_PORTRAIT,
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


def test_portrait_prefs_roundtrip(tmp_path, monkeypatch):
        """Prefs persist as JSON; defaults come back when the file is absent."""
        from duo.ui import main_window

        stub = _StubFile(None)
        monkeypatch.setattr(main_window, "_prefs_path", lambda: stub)
        prefs = load_portrait_prefs()
        assert prefs["cn.com.langeasy.LangEasyLexis"] is True  # default portrait
        prefs["tv.danmaku.bili"] = True
        save_portrait_prefs(prefs)
        assert stub.written is not None
        assert json.loads(stub.written)["portrait"]["tv.danmaku.bili"] is True


def test_portrait_prefs_corrupt_file_falls_back_to_defaults(monkeypatch):
        """A corrupt prefs file must not take the panel down."""
        from duo.ui import main_window

        monkeypatch.setattr(main_window, "_prefs_path", lambda: _StubFile("{not json"))
        assert load_portrait_prefs() == dict(DEFAULT_PORTRAIT)


def test_gui_importable_without_display():
        """The panel module imports cleanly headless (Qt lazy-loaded)."""
        import duo.ui.main_window as mw  # noqa: F401

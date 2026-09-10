"""Packaging contract: the app icon must survive both bundle channels.

duo.spec has two independent icon paths and both broke separately before:
the EXE-level ``icon=`` (Explorer's exe resource) and the datas entry that
lets the frozen runtime call setWindowIcon (taskbar/title bar). These
guards fail the build in CI before a Windows box ships a stale icon.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from duo.ui.app import _bundled_icon

SPEC = Path(__file__).resolve().parents[1] / "duo.spec"
VERIFY = Path(__file__).resolve().parents[1] / "scripts" / "verify_exe_icon.py"

_spec = importlib.util.spec_from_file_location("verify_exe_icon", VERIFY)
assert _spec is not None and _spec.loader is not None
verify_exe_icon = importlib.util.module_from_spec(_spec)
sys.modules["verify_exe_icon"] = verify_exe_icon
_spec.loader.exec_module(verify_exe_icon)


def test_spec_embeds_exe_icon() -> None:
        """The EXE() icon= line must point at assets/duo.ico."""
        text = SPEC.read_text(encoding="utf-8")
        icon_lines = [
                line for line in text.splitlines()
                if line.strip().startswith("icon=")
        ]
        assert len(icon_lines) == 1
        assert "duo.ico" in icon_lines[0]


def test_spec_ships_icon_for_runtime_window() -> None:
        """datas must ship assets/duo.ico so the frozen runtime can load it."""
        text = SPEC.read_text(encoding="utf-8")
        datas_block = text.split("datas = [", 1)[1].split("]", 1)[0]
        assert "duo.ico" in datas_block
        assert '"assets"' in datas_block


def test_bundled_icon_resolves_in_dev_tree() -> None:
        """Dev-tree fallback finds the repo ico and it is a real ICO file."""
        path = _bundled_icon()
        assert path is not None
        assert path.name == "duo.ico"
        assert path.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_verify_exe_icon_roundtrip(tmp_path, monkeypatch, capsys) -> None:
        """The build-time verifier detects a frame embedded in junk vs absent."""
        ico = Path(__file__).resolve().parents[1] / "assets" / "duo.ico"
        frames = verify_exe_icon.ico_frames(ico.read_bytes())
        assert len(frames) >= 5 and all(frames)

        argv = ["verify_exe_icon.py", str(tmp_path / "x.exe"), str(ico)]
        junk = bytearray(b"\x00junk" * 64)
        (tmp_path / "hit.exe").write_bytes(junk + b"".join(frames) + junk)
        (tmp_path / "miss.exe").write_bytes(junk)

        monkeypatch.setattr(sys, "argv", [argv[0], str(tmp_path / "hit.exe"), argv[2]])
        assert verify_exe_icon.main(sys.argv) == 0
        monkeypatch.setattr(sys, "argv", [argv[0], str(tmp_path / "miss.exe"), argv[2]])
        assert verify_exe_icon.main(sys.argv) == 1
        assert "NOT embedded" in capsys.readouterr().out

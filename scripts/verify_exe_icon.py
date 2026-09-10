"""Verify a built exe embeds every frame of an .ico file.

PyInstaller writes each .ico frame as an RT_ICON resource with its raw
bytes (PNG or DIB) preserved, so a full-frame byte search of the exe is a
faithful embed check. Exits 0 = embedded, 1 = frames missing, 2 = usage.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ico_frames(data: bytes) -> list[bytes]:
        """Raw frame bytes of every entry in an ICO container."""
        if len(data) < 6 or data[:4] != b"\x00\x00\x01\x00":
                raise ValueError("not an ICO file (bad header)")
        count = int.from_bytes(data[4:6], "little")
        frames: list[bytes] = []
        for i in range(count):
                entry = data[6 + 16 * i : 6 + 16 * (i + 1)]
                size = int.from_bytes(entry[8:12], "little")
                offset = int.from_bytes(entry[12:16], "little")
                frames.append(data[offset : offset + size])
        return frames


def main(argv: list[str]) -> int:
        if len(argv) != 3:
                print("usage: verify_exe_icon.py <exe> <ico>")
                return 2
        exe = Path(argv[1]).read_bytes()
        frames = ico_frames(Path(argv[2]).read_bytes())
        missing = [f for f in frames if exe.find(f) < 0]
        if missing:
                print(f"FAIL: {len(missing)}/{len(frames)} icon frames NOT embedded")
                return 1
        print(f"OK: all {len(frames)} icon frames embedded")
        return 0


if __name__ == "__main__":
        raise SystemExit(main(sys.argv))

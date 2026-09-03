"""Windows subprocess flags: never flash console windows.

The panel is a windowed executable; spawning console tools (adb, scrcpy,
csc.exe, aapt2) from it would open a fresh console window for each call -
the panel polls adb every two seconds, so the desktop flickers constantly.
Every duo subprocess that talks to an external binary passes
:func:`creation_flags` to ``Popen``/``run`` so the tools run silently.
"""

from __future__ import annotations

import subprocess
import sys


def creation_flags() -> int:
        """``CREATE_NO_WINDOW`` on Windows, ``0`` everywhere else."""
        if sys.platform == "win32":
                return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return 0

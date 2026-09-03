"""Filesystem locations used by Duo.

All runtime artifacts (logs, apk/icon caches, downloaded tools) live under a
single data directory so that everything Duo writes is easy to inspect and
wipe. The default targets ``~/.local/share/duo``; M5 packaging will swap this
for a platform-appropriate location in this one place.
"""

from __future__ import annotations

from pathlib import Path

_BASE_NAME = "duo"


def data_dir() -> Path:
        """Return the base data directory, creating it if necessary."""
        path = Path.home() / ".local" / "share" / _BASE_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path


def logs_dir() -> Path:
        """Directory for session logs (one file per session run)."""
        path = data_dir() / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path


def apks_dir() -> Path:
        """Cache directory for device APKs pulled for metadata parsing."""
        path = data_dir() / "apks"
        path.mkdir(parents=True, exist_ok=True)
        return path


def icons_dir() -> Path:
        """Cache directory for extracted app icons."""
        path = data_dir() / "icons"
        path.mkdir(parents=True, exist_ok=True)
        return path


def tools_dir() -> Path:
        """Cache directory for tools Duo downloads (e.g. aapt2)."""
        path = data_dir() / "tools"
        path.mkdir(parents=True, exist_ok=True)
        return path

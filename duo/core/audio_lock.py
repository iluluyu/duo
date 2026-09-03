"""Single-audio arbitration across Duo mirror sessions.

Android lets several scrcpy clients capture the device mixer at once, but the
captures contend and the result crackles. Duo therefore grants audio to at
most one live session: a small lock file in the data dir records the owning
PID, later sessions start muted, and the lock is released when the owner
exits (stale locks are detected by PID liveness).
"""

from __future__ import annotations

import contextlib
import os

from duo.core.paths import data_dir


def _lock_path():
        return data_dir() / "audio.lock"


def _pid_alive(pid: int) -> bool:
        """Whether a process with this PID exists (0 means dead).

        ``os.kill(pid, 0)`` is the portable probe: ProcessLookupError means
        dead, PermissionError means alive-but-foreign, and on Windows a dead
        PID surfaces as ``OSError`` WinError 6 (invalid handle) - also dead.
        """
        if pid <= 0:
                return False
        try:
                os.kill(pid, 0)
        except PermissionError:
                return True  # exists, owned by someone else
        except OSError:
                return False  # POSIX: no such process; Windows: invalid handle
        return True


def _read_owner() -> int:
        """The PID recorded in the lock file, or 0 when absent/unreadable."""
        try:
                text = _lock_path().read_text(encoding="utf-8").strip()
        except OSError:
                return 0
        try:
                return int(text)
        except ValueError:
                return 0


class AudioLock:
        """Claim the device audio stream for this process.

        Usage::

                lock = AudioLock()
                if lock.acquire():
                        ...  # this session forwards audio
                lock.release()  # no-op when not held
        """

        def __init__(self) -> None:
                self._held = False

        @property
        def held(self) -> bool:
                """Whether this instance owns the audio lock."""
                return self._held

        def acquire(self) -> bool:
                """Take the lock unless a live owner already holds it."""
                owner = _read_owner()
                if owner and owner != os.getpid() and _pid_alive(owner):
                        return False
                _lock_path().parent.mkdir(parents=True, exist_ok=True)
                _lock_path().write_text(f"{os.getpid()}\n", encoding="utf-8")
                self._held = True
                return True

        def release(self) -> None:
                """Give the lock back; a no-op when not held."""
                if not self._held:
                        return
                self._held = False
                if _read_owner() == os.getpid():
                        with contextlib.suppress(OSError):
                                _lock_path().unlink()

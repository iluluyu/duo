"""Single-audio arbitration across Duo mirror sessions."""

from __future__ import annotations

import os

from duo.core import audio_lock
from duo.core.audio_lock import AudioLock, _pid_alive, _read_owner


def test_acquire_and_release_roundtrip(tmp_path, monkeypatch):
        """A lock is taken once and freed by its owner."""
        monkeypatch.setattr(audio_lock, "_lock_path", lambda: tmp_path / "audio.lock")
        lock = AudioLock()
        assert lock.acquire() is True
        assert _read_owner() == os.getpid()
        lock.release()
        assert _read_owner() == 0
        lock.release()  # double release is a no-op


def test_second_process_is_denied(tmp_path, monkeypatch):
        """A live foreign owner blocks acquisition; dead owners do not."""
        monkeypatch.setattr(audio_lock, "_lock_path", lambda: tmp_path / "audio.lock")
        lock_path = tmp_path / "audio.lock"
        lock_path.write_text("999999999\n", encoding="utf-8")
        assert _pid_alive(999999999) is False  # nobody owns that PID
        assert AudioLock().acquire() is True  # stale lock is reclaimed

        lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        monkeypatch.setattr(audio_lock, "os", type("FakeOs", (), {
                "getpid": staticmethod(lambda: 4242),
                "kill": staticmethod(lambda pid, sig: None),
        }))
        assert AudioLock().acquire() is False  # live foreign owner


def test_corrupt_lock_is_treated_as_free(tmp_path, monkeypatch):
        """Garbage in the lock file must not wedge audio forever."""
        lock_path = tmp_path / "audio.lock"
        lock_path.write_text("not-a-pid\n", encoding="utf-8")
        monkeypatch.setattr(audio_lock, "_lock_path", lambda: lock_path)
        assert AudioLock().acquire() is True


def test_pid_alive_zero_is_dead():
        """PID 0 is never alive."""
        assert _pid_alive(0) is False

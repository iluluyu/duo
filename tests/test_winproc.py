"""winproc: silent-spawn flags, tree termination, kill-on-close job."""

from __future__ import annotations

import subprocess
import sys

import pytest

from duo.core.winproc import ChildJob, creation_flags, notify_already_running


class _FakeProc:
        """Popen stand-in: records the termination attempt."""

        def __init__(self, *, exit_code: int | None = None) -> None:
                self.pid = 0
                self.terminated = False
                self._exit_code = exit_code

        def poll(self) -> int | None:
                return self._exit_code

        def terminate(self) -> None:
                self.terminated = True


@pytest.mark.skipif(sys.platform != "win32", reason="win32-only flag")
def test_creation_flags_hide_console_on_windows():
        assert creation_flags() & subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(sys.platform == "win32", reason="posix branch")
def test_creation_flags_zero_off_windows():
        assert creation_flags() == 0


@pytest.mark.skipif(sys.platform == "win32", reason="posix branch")
def test_terminate_tree_falls_back_to_terminate():
        """Off Windows the tree kill IS the graceful terminate."""
        from duo.core.winproc import terminate_tree

        proc = _FakeProc()
        terminate_tree(proc)
        assert proc.terminated


@pytest.mark.skipif(sys.platform == "win32", reason="posix branch")
def test_terminate_tree_swallows_dead_process_race(capsys):
        """A pid that died a beat ago must not break the shutdown path."""
        from duo.core.winproc import terminate_tree

        class _Dying:
                pid = 0

                def poll(self) -> int | None:
                        return None

                def terminate(self) -> None:
                        raise ProcessLookupError(0, "already gone")

        terminate_tree(_Dying())   # type: ignore[arg-type]
        assert capsys.readouterr().err == ""


@pytest.mark.skipif(sys.platform == "win32", reason="posix branch")
def test_child_job_is_noop_off_windows():
        """No job object exists off Windows: add/close stay transparent."""
        job = ChildJob()
        assert not job.active
        job.add(_FakeProc())   # type: ignore[arg-type]
        job.close()
        assert not job.active


@pytest.mark.skipif(sys.platform != "win32", reason="win32-only job object")
def test_child_job_kills_members_on_close():
        """A real spawned process must die when the job handle closes."""
        import time

        proc = subprocess.Popen(
                ["cmd", "/c", "ping -n 30 127.0.0.1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags(),
        )
        job = ChildJob()
        assert job.active
        job.add(proc)
        job.close()
        deadline = time.monotonic() + 10.0
        while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
        assert proc.poll() is not None


@pytest.mark.skipif(sys.platform == "win32", reason="posix branch")
def test_notify_already_running_prints_to_stderr_off_windows(capsys):
        notify_already_running("duo panel already running")
        assert "duo panel already running" in capsys.readouterr().err

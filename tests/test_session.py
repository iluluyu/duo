"""Tests for session supervision (uses a real short-lived process)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from duo.core.session import Session, SessionSpec


def _sleep_session(tmp_path: Path) -> Session:
        """A session running a python process that sleeps for a long time."""
        spec = SessionSpec(
                command=[sys.executable, "-c", "import time; time.sleep(30)"],
                log_path=tmp_path / "session.log",
        )
        return Session(spec)


def test_start_stop_lifecycle(tmp_path: Path):
        """A started session is alive; stop() terminates it."""
        session = _sleep_session(tmp_path)
        session.start()
        try:
                assert session.is_alive()
        finally:
                session.stop()
        deadline = time.monotonic() + 5
        while session.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
        assert not session.is_alive()


def test_stop_before_start_is_noop(tmp_path: Path):
        """Stopping a never-started session must not raise."""
        session = _sleep_session(tmp_path)
        session.stop()
        assert not session.is_alive()


def test_run_returns_zero_on_clean_exit(tmp_path: Path):
        """A process that exits cleanly returns its exit code without restarts."""
        spec = SessionSpec(
                command=[sys.executable, "-c", "raise SystemExit(0)"],
                log_path=tmp_path / "clean.log",
                max_restarts=3,
        )
        assert Session(spec).run() == 0


def test_run_restarts_then_gives_up(tmp_path: Path):
        """A crashing process is restarted up to max_restarts times."""
        spec = SessionSpec(
                command=[sys.executable, "-c", "raise SystemExit(1)"],
                log_path=tmp_path / "crash.log",
                max_restarts=2,
                restart_delay_s=0.05,
        )
        session = Session(spec)
        assert session.run() == 1
        assert session.restarts == 2


def test_log_file_written(tmp_path: Path):
        """Engine output is captured into the session log file."""
        spec = SessionSpec(
                command=[sys.executable, "-c", "print('engine says hi'); raise SystemExit(0)"],
                log_path=tmp_path / "logged.log",
        )
        Session(spec).run()
        assert "engine says hi" in spec.log_path.read_text(encoding="utf-8")


def test_env_vars_reach_the_child(tmp_path: Path):
        """The ADB pin (and WSLENV allowance) survives into the engine child."""
        spec = SessionSpec(
                command=[sys.executable, "-c", "import os; print(os.environ.get('ADB', ''))"],
                log_path=tmp_path / "env.log",
                env={"ADB": r"C:\\tools\\adb.exe"},
        )
        Session(spec).run()
        assert r"C:\\tools\\adb.exe" in spec.log_path.read_text(encoding="utf-8")

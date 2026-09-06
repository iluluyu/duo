"""Session lifecycle: spawn the engine, capture logs, restart on crash."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from duo.core.winproc import creation_flags


@dataclass
class SessionSpec:
        """Everything needed to run and supervise one engine process."""

        command: list[str]
        log_path: Path
        max_restarts: int = 3
        restart_delay_s: float = 2.0
        env: dict[str, str] | None = None   # extra vars (ADB pin) merged over os.environ


_POLL_INTERVAL_S = 0.5


# ----------------------------------------------------------------------------
# Session log parsing (the same channel the C# overlay tails for Texture sizes)
# ----------------------------------------------------------------------------

#: scrcpy announces each virtual display exactly once per session:
#: ``[server] INFO: New display: 1200x1600/280 (id=157)``. The id is what
#: ``am start --display N`` needs to move apps onto the running session's
#: display without rebuilding it (docs/window-experience.md §7.3 R3).
_DISPLAY_ID_RE = re.compile(r"New display:.*\(id=(\d+)\)")

#: How much of a session log tail to scan for the display id. scrcpy logs a
#: few KiB at startup; 64 KiB is generous and keeps the read bounded even if
#: a long-lived session accumulates output.
_LOG_TAIL_BYTES = 64 * 1024


def parse_display_id(log_text: str) -> int | None:
        """Extract the virtual display id from session log text.

        The LAST match wins: panel-managed sessions append to one file, so
        the most recent ``New display:`` line belongs to the latest engine
        run. ``None`` = no line yet (engine still starting, or a mirror
        session, which has no virtual display).
        """
        matches = _DISPLAY_ID_RE.findall(log_text)
        return int(matches[-1]) if matches else None


def display_id_from_log(log_path: Path) -> int | None:
        """Read the tail of a session log and parse the display id from it.

        Click-time read, no tailer thread: the panel only needs the id when
        the user asks an app onto the display. Missing/unreadable file (or a
        log without the line yet) returns ``None`` - the caller degrades to
        a status message instead of inventing a display.
        """
        try:
                with open(log_path, "rb") as log_file:
                        log_file.seek(0, os.SEEK_END)
                        size = log_file.tell()
                        log_file.seek(max(0, size - _LOG_TAIL_BYTES))
                        text = log_file.read().decode("utf-8", errors="replace")
        except OSError:
                return None
        return parse_display_id(text)


class Session:
        """One engine process lifecycle with crash-restart supervision.

        The engine (scrcpy) writes stdout/stderr to a per-session log file so
        that crashes can be diagnosed after the fact (plan.md R-M1: 结构化日志).
        """

        def __init__(self, spec: SessionSpec) -> None:
                self.spec = spec
                self.restarts = 0
                self._proc: subprocess.Popen[bytes] | None = None

        @property
        def log_path(self) -> Path:
                """Path of this session's log file."""
                return self.spec.log_path

        def start(self) -> None:
                """Spawn the engine process with output redirected to the log."""
                self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
                child_env = None
                if self.spec.env:
                        child_env = {**os.environ, **self.spec.env}
                with open(self.spec.log_path, "ab") as log_file:
                        self._proc = subprocess.Popen(
                                self.spec.command,
                                stdout=log_file,
                                stderr=subprocess.STDOUT,
                                env=child_env,
                                creationflags=creation_flags(),
                        )

        def is_alive(self) -> bool:
                """Whether the engine process is currently running."""
                return self._proc is not None and self._proc.poll() is None

        def stop(self) -> None:
                """Terminate the engine gracefully, escalating to kill."""
                if self._proc is None or self._proc.poll() is not None:
                        return
                self._proc.terminate()
                try:
                        self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                        self._proc.kill()
                        self._proc.wait(timeout=5)

        def run(self, should_stop: Callable[[], bool] | None = None) -> int:
                """Run the session, restarting on crashes, until clean exit.

                ``should_stop`` is polled twice a second; when it returns True
                the session is stopped and 2 (device lost) is returned.
                KeyboardInterrupt stops the session and returns 130.
                """
                try:
                        self.start()
                        assert self._proc is not None
                        while True:
                                while self.is_alive():
                                        if should_stop is not None and should_stop():
                                                self.stop()
                                                return 2
                                        time.sleep(_POLL_INTERVAL_S)
                                return_code = self._proc.returncode
                                if return_code == 0 or self.restarts >= self.spec.max_restarts:
                                        return return_code
                                self.restarts += 1
                                time.sleep(self.spec.restart_delay_s)
                                self.start()
                except KeyboardInterrupt:
                        self.stop()
                        return 130

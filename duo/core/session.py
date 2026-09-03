"""Session lifecycle: spawn the engine, capture logs, restart on crash."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionSpec:
        """Everything needed to run and supervise one engine process."""

        command: list[str]
        log_path: Path
        max_restarts: int = 3
        restart_delay_s: float = 2.0


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
                with open(self.spec.log_path, "ab") as log_file:
                        self._proc = subprocess.Popen(
                                self.spec.command, stdout=log_file, stderr=subprocess.STDOUT
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

        def run(self) -> int:
                """Run the session, restarting on crashes, until clean exit.

                Returns the final exit code. KeyboardInterrupt stops the
                session and returns 130 (conventional SIGINT exit code).
                """
                try:
                        self.start()
                        assert self._proc is not None
                        while True:
                                return_code = self._proc.wait()
                                if return_code == 0 or self.restarts >= self.spec.max_restarts:
                                        return return_code
                                self.restarts += 1
                                time.sleep(self.spec.restart_delay_s)
                                self.start()
                except KeyboardInterrupt:
                        self.stop()
                        return 130

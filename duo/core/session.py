"""Session lifecycle: spawn the engine, capture logs, restart on crash."""

from __future__ import annotations

import os
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

"""Python client shim over the ``duo-core`` Rust binary's JSON-lines CLI.

Roadmap context: TODO.md §0 (0.2.2/0.2.3, core 下沉) - the panel calls the
Rust core through three subcommands: ``devices`` (one-shot state map),
``watch`` (streamed state maps) and ``session`` (supervised engine). This
module is the only Python surface for that protocol.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from pyduo.core.paths import tools_dir
from pyduo.core.winproc import creation_flags

#: Repo root (parent of the ``duo`` package), located like chrome.OVERLAY_SOURCE.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_EXE_NAME = "duo-core.exe" if sys.platform == "win32" else "duo-core"
_QUERY_TIMEOUT_S = 15.0
_TERMINATE_TIMEOUT_S = 5.0


class DuoCoreError(RuntimeError):
    """Raised when duo-core is missing or violates its CLI contract."""


def find_duo_core() -> Path | None:
        """First existing duo-core binary: DUO_CORE_BIN, cargo build, tools dir."""
        candidates: list[Path] = []
        override = os.environ.get("DUO_CORE_BIN")
        if override:
                candidates.append(Path(override))
        candidates += [
                _REPO_ROOT / "rust" / "target" / "release" / _EXE_NAME,
                _REPO_ROOT / "rust" / "target" / "debug" / _EXE_NAME,
                tools_dir() / _EXE_NAME,
        ]
        for candidate in candidates:
                if candidate.is_file():
                        return candidate
        return None


def _resolve(binary: Path | None) -> Path:
        if binary is None:
                binary = find_duo_core()
        if binary is None:
                raise DuoCoreError("duo-core binary not found")
        return binary


def query_devices(adb_binary: str, binary: Path | None = None) -> dict[str, str]:
        """One-shot serial -> state map from ``duo-core devices``."""
        exe = _resolve(binary)
        try:
                result = subprocess.run(
                        [str(exe), "devices", "--adb", adb_binary],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=_QUERY_TIMEOUT_S,
                        check=False,
                        creationflags=creation_flags(),
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
                raise DuoCoreError(f"duo-core devices failed to run: {exc}") from exc
        if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()[:120]
                raise DuoCoreError(
                        f"duo-core devices failed (rc={result.returncode}): {detail}"
                )
        try:
                data = json.loads(result.stdout or "")
        except ValueError as exc:
                raise DuoCoreError("duo-core devices emitted invalid JSON") from exc
        if not isinstance(data, dict):
                raise DuoCoreError("duo-core devices emitted non-object JSON")
        return {str(serial): str(state) for serial, state in data.items()}


class DeviceWatch:
        """Stream ``duo-core watch`` state maps to a callback via a daemon thread."""

        def __init__(
                self,
                adb_binary: str,
                on_change: Callable[[dict[str, str]], None],
                binary: Path | None = None,
                interval_s: float = 2.0,
        ) -> None:
                self._adb_binary = adb_binary
                self._on_change = on_change
                self._binary = binary
                self._interval_s = interval_s
                self._proc: subprocess.Popen[str] | None = None
                self._thread: threading.Thread | None = None
                self._states: dict[str, str] = {}
                self.degraded = False

        @property
        def states(self) -> dict[str, str]:
                """Most recent serial -> state map (copy; empty before the first event)."""
                return dict(self._states)

        def start(self) -> None:
                if self._proc is not None:
                        return
                exe = _resolve(self._binary)
                argv = [
                        str(exe),
                        "watch",
                        "--adb",
                        self._adb_binary,
                        "--interval",
                        str(self._interval_s),
                ]
                try:
                        self._proc = subprocess.Popen(
                                argv,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                creationflags=creation_flags(),
                        )
                except OSError as exc:
                        self._proc = None
                        raise DuoCoreError(f"duo-core watch failed to spawn: {exc}") from exc
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

        def stop(self) -> None:
                """Terminate the watcher; safe before start and when repeated."""
                proc = self._proc
                if proc is None:
                        return
                if proc.poll() is None:
                        proc.terminate()
                        try:
                                proc.wait(timeout=_TERMINATE_TIMEOUT_S)
                        except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=_TERMINATE_TIMEOUT_S)
                if self._thread is not None:
                        self._thread.join(timeout=_TERMINATE_TIMEOUT_S)

        def _run(self) -> None:
                proc = self._proc
                if proc is None or proc.stdout is None:
                        return
                for line in proc.stdout:
                        try:
                                event = json.loads(line)
                        except ValueError:
                                continue
                        if not isinstance(event, dict) or event.get("type") != "devices":
                                continue
                        states = event.get("states")
                        if isinstance(states, dict):
                                self._states = {str(k): str(v) for k, v in states.items()}
                                self._on_change(self.states)
                        self.degraded = bool(event.get("degraded", False))


class SessionProcess:
        """Drive one ``duo-core session`` child and collect its event stream."""

        def __init__(self, spec: dict, binary: Path | None = None) -> None:
                env = spec.get("env")
                if env is not None and (
                        not isinstance(env, dict)
                        or not all(
                                isinstance(k, str) and isinstance(v, str)
                                for k, v in env.items()
                        )
                ):
                        raise ValueError("spec env must map str -> str")
                self._spec = spec
                self._binary = binary
                self._proc: subprocess.Popen[str] | None = None
                self._thread: threading.Thread | None = None
                self.events: list[dict] = []

        @property
        def running(self) -> bool:
                """Whether the session child is alive (False before start)."""
                return self._proc is not None and self._proc.poll() is None

        @property
        def returncode(self) -> int | None:
                """Child exit status, or None while running / never started."""
                return None if self._proc is None else self._proc.poll()

        def start(self) -> None:
                if self._proc is not None:
                        return
                exe = _resolve(self._binary)
                argv = [str(exe), "session", "--spec", json.dumps(self._spec)]
                try:
                        self._proc = subprocess.Popen(
                                argv,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                creationflags=creation_flags(),
                        )
                except OSError as exc:
                        self._proc = None
                        raise DuoCoreError(f"duo-core session failed to spawn: {exc}") from exc
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

        def wait(self, timeout: float | None = None) -> int:
                """Block for child exit; the event reader drains before returning."""
                if self._proc is None:
                        raise DuoCoreError("session not started")
                code = self._proc.wait(timeout=timeout)
                if self._thread is not None:
                        self._thread.join(timeout=_TERMINATE_TIMEOUT_S)
                return code

        def stop(self) -> None:
                """Terminate the child (terminate -> wait -> kill); idempotent."""
                proc = self._proc
                if proc is None:
                        return
                if proc.poll() is None:
                        proc.terminate()
                        try:
                                proc.wait(timeout=_TERMINATE_TIMEOUT_S)
                        except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=_TERMINATE_TIMEOUT_S)
                if self._thread is not None:
                        self._thread.join(timeout=_TERMINATE_TIMEOUT_S)

        def _run(self) -> None:
                proc = self._proc
                if proc is None or proc.stdout is None:
                        return
                for line in proc.stdout:
                        try:
                                event = json.loads(line)
                        except ValueError:
                                continue
                        if isinstance(event, dict):
                                self.events.append(event)

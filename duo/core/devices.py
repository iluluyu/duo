"""Device hotplug monitoring by polling ``adb devices``.

An earlier attempt streamed ``adb track-devices``, but modern adb speaks a
binary length-prefixed frame protocol there (no line separators), which buys
nothing over a 2-second poll for hotplug UX. Polling is version-proof and
trivially testable, so Duo polls (plan.md, M1: "track-devices 或轮询").
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable

from duo.core.winproc import creation_flags

#: Exit code used by supervisors when the device went away.
EXIT_DEVICE_LOST = 2

_DEFAULT_INTERVAL_S = 2.0
_QUERY_TIMEOUT_S = 10.0


_KNOWN_STATES = {"device", "offline", "unauthorized", "recovery"}


def parse_device_states(devices_output: str) -> dict[str, str]:
        """Parse ``adb devices`` output into serial -> state."""
        states: dict[str, str] = {}
        for line in devices_output.splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 2 and fields[1] in _KNOWN_STATES:
                        states[fields[0]] = fields[1]
        return states


def poll_query(adb_binary: str) -> Callable[[], dict[str, str]]:
        """Build a poll function that queries adb for the current device states."""

        def query() -> dict[str, str]:
                try:
                        result = subprocess.run(
                                [adb_binary, "devices"],
                                capture_output=True,
                                text=True,
                        encoding="utf-8",
                        errors="replace",
                                timeout=_QUERY_TIMEOUT_S,
                                check=False,
                                creationflags=creation_flags(),
                        )
                except (OSError, subprocess.TimeoutExpired):
                        return {}
                return parse_device_states(result.stdout or "")

        return query


class DeviceMonitor:
        """Poll the device list and invoke a callback on every change."""

        def __init__(
                self,
                on_change: Callable[[dict[str, str]], None],
                query: Callable[[], dict[str, str]] | None = None,
                adb_binary: str | None = None,
                poll_interval_s: float = _DEFAULT_INTERVAL_S,
        ) -> None:
                self._on_change = on_change
                if query is None:
                        if adb_binary is None:
                                raise ValueError("provide either query or adb_binary")
                        query = poll_query(adb_binary)
                self._query = query
                self._interval = poll_interval_s
                self._states: dict[str, str] = {}
                self._stop = threading.Event()
                self._thread: threading.Thread | None = None

        @property
        def states(self) -> dict[str, str]:
                """Current serial -> state map (empty before the first poll)."""
                return dict(self._states)

        @property
        def online(self) -> list[str]:
                """Serials currently in the ``device`` state."""
                return [s for s, state in self._states.items() if state == "device"]

        def start(self) -> None:
                """Begin polling in a daemon thread."""
                if self._thread is not None:
                        return
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

        def stop(self) -> None:
                """Stop polling."""
                self._stop.set()
                if self._thread is not None:
                        self._thread.join(timeout=5)

        def poll_now(self) -> None:
                """Run one poll synchronously (used at startup)."""
                self._apply(self._query())

        def _run(self) -> None:
                while not self._stop.is_set():
                        self._apply(self._query())
                        self._stop.wait(self._interval)

        def _apply(self, states: dict[str, str]) -> None:
                if states != self._states:
                        self._states = states
                        self._on_change(self.states)

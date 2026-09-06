"""Device hotplug monitoring by polling ``adb devices``.

An earlier attempt streamed ``adb track-devices``, but modern adb speaks a
binary length-prefixed frame protocol there (no line separators), which buys
nothing over a 2-second poll for hotplug UX. Polling is version-proof and
trivially testable, so Duo polls (plan.md, M1: "track-devices 或轮询").

Failure contract: a poll that cannot produce a trustworthy device list
(adb.exe missing, hung past the timeout, server wedged) RAISES - it never
returns an empty map, because "query failed" and "no devices" are different
facts. :class:`DeviceMonitor` rides out failed queries: the last known map is
kept (no callback, the GUI list holds still) and only after
``_MAX_CONSECUTIVE_FAILURES`` consecutive failures is the list really
cleared. One flaked ``adb devices`` (USB re-enumeration, daemon restart,
packed-exe PATH hiccup) must not blink the panel's device card.
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
        """Build a poll function that queries adb for the current device states.

        The callable RAISES when the query itself fails - binary missing
        (``OSError``), hung past the timeout (``TimeoutExpired``), or adb
        exiting nonzero (wedged server: version-mismatch kill wars leave no
        usable device list on stdout). Only an exit-0 listing is parsed;
        "query failed" and "no devices" must stay distinguishable or a
        single flake wipes the GUI list (the device-card blink bug).
        """

        def query() -> dict[str, str]:
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
                if result.returncode != 0:
                        raise OSError(
                                f"adb devices failed (rc={result.returncode}): "
                                f"{(result.stderr or result.stdout or '').strip()[:120]}"
                        )
                return parse_device_states(result.stdout or "")

        return query


class DeviceMonitor:
        """Poll the device list and invoke a callback on every change.

        A failing poll (the query callable raises) is NOT a device list:
        during the grace window the last known map is kept and no callback
        fires, so one flaked ``adb devices`` cannot blink the GUI list.
        Only ``_MAX_CONSECUTIVE_FAILURES`` consecutive failures clear the
        map (a "truly gone" verdict); the first successful poll resets the
        counter and reports normally.
        """

        #: Consecutive failed queries tolerated before the list really
        #: clears. At the 2s poll cadence this is ~6s of adb outage -
        #: longer than any USB re-enumeration or daemon restart we have
        #: seen in the field, far shorter than a real unplug feels.
        _MAX_CONSECUTIVE_FAILURES = 3

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
                self._failures = 0
                self._stop = threading.Event()
                self._thread: threading.Thread | None = None

        @property
        def states(self) -> dict[str, str]:
                """Current serial -> state map (empty before the first poll)."""
                return dict(self._states)

        @property
        def degraded(self) -> bool:
                """True while queries are failing inside the grace window.

                Observability/test hook: the reported ``states`` stay at
                their last known values during this time (the panel keeps
                showing the device rather than blinking)."""
                return self._failures > 0

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
                self._poll_once()

        def _run(self) -> None:
                while not self._stop.is_set():
                        self._poll_once()
                        self._stop.wait(self._interval)

        def _poll_once(self) -> None:
                """One query, with the failure-grace contract.

                Bare ``Exception`` (not just OSError/TimeoutExpired) is
                deliberate: any raising poll must ride out the grace window
                rather than kill the poll thread and freeze hotplug updates
                for the rest of the process lifetime.
                """
                try:
                        states = self._query()
                except Exception:
                        self._failures += 1
                        if self._failures >= self._MAX_CONSECUTIVE_FAILURES:
                                self._apply({})   # sustained failure: truly gone
                        return
                self._failures = 0
                self._apply(states)

        def _apply(self, states: dict[str, str]) -> None:
                if states != self._states:
                        self._states = states
                        self._on_change(self.states)

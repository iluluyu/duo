"""Tests for device hotplug monitoring (polling based, no device needed)."""

from __future__ import annotations

import subprocess
import time

import pytest

from duo.core.devices import (
        EXIT_DEVICE_LOST,
        DeviceMonitor,
        parse_device_states,
        poll_query,
)

DEVICES_OUTPUT = """List of devices attached
4444bd6b               device product:OPD2409 model:OPD2409 device:OP615CL1 transport_id:1
emulator-5554          offline
ABC123                 unauthorized
"""


def test_parse_device_states():
        """States map keeps offline/unauthorized; junk lines are ignored."""
        assert parse_device_states(DEVICES_OUTPUT) == {
                "4444bd6b": "device",
                "emulator-5554": "offline",
                "ABC123": "unauthorized",
        }
        assert parse_device_states("") == {}
        assert parse_device_states("random junk\nmore junk") == {}


class _FakeResult:
        """Minimal subprocess.run stand-in (returncode/stdout only)."""

        def __init__(self, returncode: int, stdout: str) -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = ""


def test_poll_query_builds_callable(monkeypatch):
        """poll_query parses exit-0 adb output; a failed adb RAISES.

        Failure and "no devices" are different facts: returning {} for a
        broken adb made the GUI device list blink off on every flake. The
        monitor (not the query) decides what a failure means.
        """
        query = poll_query("definitely-not-a-real-adb-binary")
        with pytest.raises(OSError):   # binary missing
                query()

        monkeypatch.setattr(
                subprocess,
                "run",
                lambda *a, **k: _FakeResult(0, DEVICES_OUTPUT),
        )
        assert poll_query("adb")() == {
                "4444bd6b": "device",
                "emulator-5554": "offline",
                "ABC123": "unauthorized",
        }

        # Nonzero exit with no usable listing = wedged server, not "empty".
        monkeypatch.setattr(
                subprocess,
                "run",
                lambda *a, **k: _FakeResult(1, "* failed to start daemon\n"),
        )
        with pytest.raises(OSError):
                poll_query("adb")()

        # Hung adb: the timeout must surface, not read as "no devices".
        def hang(*a, **k):
                raise subprocess.TimeoutExpired(cmd="adb", timeout=10)

        monkeypatch.setattr(subprocess, "run", hang)
        with pytest.raises(subprocess.TimeoutExpired):
                poll_query("adb")()


def _scripted_monitor(
        sequence: list[dict[str, str]], events: list[dict[str, str]]
) -> DeviceMonitor:
        """A monitor whose query walks a scripted state sequence."""
        steps = iter(sequence)

        def query() -> dict[str, str]:
                try:
                        return next(steps)
                except StopIteration:
                        return sequence[-1]

        return DeviceMonitor(
                on_change=events.append,
                query=query,
                poll_interval_s=0.02,
        )


def test_monitor_emits_add_change_and_removal():
        """Each distinct snapshot produces exactly one callback."""
        events: list[dict[str, str]] = []
        monitor = _scripted_monitor(
                [
                        {"4444bd6b": "device"},
                        {"4444bd6b": "device"},  # duplicate: no event
                        {"4444bd6b": "offline"},
                        {"4444bd6b": "device", "NEW": "device"},
                        {"NEW": "device"},  # 4444bd6b removed
                ],
                events,
        )
        monitor.start()
        deadline = time.monotonic() + 3
        while len(events) < 4 and time.monotonic() < deadline:
                time.sleep(0.01)
        monitor.stop()
        assert len(events) == 4
        assert events[0] == {"4444bd6b": "device"}
        assert events[1] == {"4444bd6b": "offline"}
        assert events[2] == {"4444bd6b": "device", "NEW": "device"}
        assert events[3] == {"NEW": "device"}
        assert monitor.online == ["NEW"]


def test_monitor_poll_now_is_synchronous():
        """poll_now applies the first snapshot immediately."""
        events: list[dict[str, str]] = []
        monitor = _scripted_monitor([{"A": "device"}], events)
        monitor.poll_now()
        assert events == [{"A": "device"}]
        assert monitor.states == {"A": "device"}


# ------------------------------------------------- failure grace (no blink)


def _raising_monitor(
        steps: list, events: list[dict[str, str]]
) -> DeviceMonitor:
        """A monitor whose query walks ``steps``: dicts succeed, exceptions
        are raised, and the last step repeats once the script runs out."""
        sequence = iter(steps)

        def query() -> dict[str, str]:
                step = next(sequence, steps[-1])
                if isinstance(step, BaseException):
                        raise step
                return step

        return DeviceMonitor(on_change=events.append, query=query, poll_interval_s=0.02)


def test_single_query_failure_keeps_last_states():
        """One flaked poll must NOT emit an empty list (the blink bug).

        adb.exe flakes for many reasons (USB re-enum, daemon restart,
        packed-exe PATH); treating one failure as "no devices" cleared the
        panel's device card for a 2s cycle. The last known map rides it
        out: no callback, ``states`` unchanged, ``degraded`` exposed.
        """
        events: list[dict[str, str]] = []
        monitor = _raising_monitor(
                [
                        {"4444bd6b": "device"},
                        subprocess.TimeoutExpired(cmd="adb", timeout=10),
                        {"4444bd6b": "device"},
                ],
                events,
        )
        monitor.poll_now()
        assert events == [{"4444bd6b": "device"}]

        monitor.poll_now()   # the flake
        assert events == [{"4444bd6b": "device"}]   # NO empty-list emit
        assert monitor.states == {"4444bd6b": "device"}
        assert monitor.online == ["4444bd6b"]
        assert monitor.degraded is True

        monitor.poll_now()   # recovery
        assert monitor.degraded is False
        assert monitor.states == {"4444bd6b": "device"}


def test_monitor_clears_only_after_sustained_failures():
        """Three consecutive failures clear the list exactly once."""
        events: list[dict[str, str]] = []
        flake = OSError("adb wedged")
        monitor = _raising_monitor(
                [
                        {"4444bd6b": "device"},
                        flake,
                        flake,
                        flake,
                ],
                events,
        )
        monitor.poll_now()
        monitor.poll_now()   # failure 1: grace
        monitor.poll_now()   # failure 2: grace
        assert events == [{"4444bd6b": "device"}]
        assert monitor.online == ["4444bd6b"]
        monitor.poll_now()   # failure 3: truly gone
        assert events == [{"4444bd6b": "device"}, {}]
        assert monitor.states == {}
        assert monitor.online == []
        # Further failures stay quiet (already empty, nothing to emit).
        monitor.poll_now()
        assert len(events) == 2


def test_monitor_failure_grace_resets_on_success():
        """Two failures then a success: never cleared, counter reset."""
        events: list[dict[str, str]] = []
        flake = OSError("adb wedged")
        monitor = _raising_monitor(
                [
                        {"A": "device"},
                        flake,
                        flake,
                        {"A": "device"},
                        flake,
                        flake,
                        flake,
                ],
                events,
        )
        for _ in range(4):   # good, fail, fail, good
                monitor.poll_now()
        assert events == [{"A": "device"}]
        assert monitor.degraded is False
        # Counter was reset by the success: three NEW failures to clear.
        monitor.poll_now()
        monitor.poll_now()
        assert monitor.states == {"A": "device"}
        monitor.poll_now()
        assert monitor.states == {}
        assert events == [{"A": "device"}, {}]


def test_monitor_poll_now_failure_keeps_states_and_does_not_raise():
        """A raising query is absorbed: poll_now neither throws nor clears."""
        events: list[dict[str, str]] = []
        monitor = _raising_monitor(
                [
                        {"A": "device"},
                        OSError("missing adb.exe"),
                ],
                events,
        )
        monitor.poll_now()
        monitor.poll_now()
        assert monitor.states == {"A": "device"}
        assert events == [{"A": "device"}]


def test_monitor_stop_is_idempotent():
        """Stopping twice and before start must not raise."""
        monitor = _scripted_monitor([{}], [])
        monitor.stop()
        monitor.stop()


def test_exit_device_lost_constant():
        """The device-lost exit code is stable for supervisors."""
        assert EXIT_DEVICE_LOST == 2

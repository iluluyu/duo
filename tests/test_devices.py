"""Tests for device hotplug monitoring (polling based, no device needed)."""

from __future__ import annotations

import time

from duo.core.devices import (
        EXIT_DEVICE_LOST,
        DeviceMonitor,
        parse_device_states,
        poll_query,
)

DEVICES_OUTPUT = """List of devices attached
a1b2c3d4               device product:TESTDEV model:TESTDEV device:OP615CL1 transport_id:1
emulator-5554          offline
ABC123                 unauthorized
"""


def test_parse_device_states():
        """States map keeps offline/unauthorized; junk lines are ignored."""
        assert parse_device_states(DEVICES_OUTPUT) == {
                "a1b2c3d4": "device",
                "emulator-5554": "offline",
                "ABC123": "unauthorized",
        }
        assert parse_device_states("") == {}
        assert parse_device_states("random junk\nmore junk") == {}


def test_poll_query_builds_callable():
        """poll_query returns a callable that parses adb output."""
        query = poll_query("definitely-not-a-real-adb-binary")
        assert query() == {}


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
                        {"a1b2c3d4": "device"},
                        {"a1b2c3d4": "device"},  # duplicate: no event
                        {"a1b2c3d4": "offline"},
                        {"a1b2c3d4": "device", "NEW": "device"},
                        {"NEW": "device"},  # a1b2c3d4 removed
                ],
                events,
        )
        monitor.start()
        deadline = time.monotonic() + 3
        while len(events) < 4 and time.monotonic() < deadline:
                time.sleep(0.01)
        monitor.stop()
        assert len(events) == 4
        assert events[0] == {"a1b2c3d4": "device"}
        assert events[1] == {"a1b2c3d4": "offline"}
        assert events[2] == {"a1b2c3d4": "device", "NEW": "device"}
        assert events[3] == {"NEW": "device"}
        assert monitor.online == ["NEW"]


def test_monitor_poll_now_is_synchronous():
        """poll_now applies the first snapshot immediately."""
        events: list[dict[str, str]] = []
        monitor = _scripted_monitor([{"A": "device"}], events)
        monitor.poll_now()
        assert events == [{"A": "device"}]
        assert monitor.states == {"A": "device"}


def test_monitor_stop_is_idempotent():
        """Stopping twice and before start must not raise."""
        monitor = _scripted_monitor([{}], [])
        monitor.stop()
        monitor.stop()


def test_exit_device_lost_constant():
        """The device-lost exit code is stable for supervisors."""
        assert EXIT_DEVICE_LOST == 2

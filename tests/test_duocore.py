"""Tests for the duo-core client shim (fake binary via DUO_CORE_BIN, no Rust)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

import duo.core.duocore as duocore
from duo.core.duocore import (
        DeviceWatch,
        DuoCoreError,
        SessionProcess,
        find_duo_core,
        query_devices,
)

# The fake duo-core: a Python script run through its shebang, branching on
# the subcommand. ``watch`` blocks on stdin (the real watcher is long-lived),
# ``devices`` fails with rc=2 for the magic broken adb path.
_FAKE_CORE = """\
#!{python}
import json
import sys

argv = sys.argv[1:]
if argv[0] == "devices":
    if argv[argv.index("--adb") + 1] == "broken-adb":
        print("query failed", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"4444bd6b": "device", "emulator-5554": "offline"}))
elif argv[0] == "watch":
    for line in [
        {"type": "devices", "states": {"A": "device"}, "degraded": False},
        {"type": "devices", "states": {"A": "offline", "B": "device"}, "degraded": True},
    ]:
        print(json.dumps(line), flush=True)
    sys.stdin.buffer.read()
elif argv[0] == "session":
    spec = json.loads(argv[argv.index("--spec") + 1])
    if not isinstance(spec.get("command"), list):
        sys.exit(2)
    print(json.dumps({"type": "session", "event": "started"}), flush=True)
    print(json.dumps({"type": "session", "event": "exit", "code": 0}), flush=True)
    sys.exit(0)
"""


def _install_fake_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Write the fake duo-core executable and point DUO_CORE_BIN at it."""
        script = tmp_path / "duo-core"
        script.write_text(_FAKE_CORE.replace("{python}", sys.executable), encoding="utf-8")
        script.chmod(0o755)
        monkeypatch.setenv("DUO_CORE_BIN", str(script))
        return script


def _isolate_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Point every find_duo_core candidate at an empty tmp area."""
        monkeypatch.delenv("DUO_CORE_BIN", raising=False)
        monkeypatch.setattr(duocore, "_REPO_ROOT", tmp_path / "empty-repo")
        monkeypatch.setattr(duocore, "tools_dir", lambda: tmp_path / "empty-tools")


def _touch(root: Path, *parts: str) -> Path:
        path = root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return path


def test_find_duo_core_env_overrides_repo_and_tools(tmp_path, monkeypatch):
        """DUO_CORE_BIN wins over an existing repo cargo build."""
        repo = _touch(tmp_path, "repo", "rust", "target", "release", duocore._EXE_NAME)
        monkeypatch.setattr(duocore, "_REPO_ROOT", repo.parents[3])
        override = _touch(tmp_path, "override", duocore._EXE_NAME)
        monkeypatch.setattr(duocore, "tools_dir", lambda: tmp_path / "no-tools")
        monkeypatch.setenv("DUO_CORE_BIN", str(override))
        assert find_duo_core() == override


def test_find_duo_core_repo_release_beats_debug_and_tools(tmp_path, monkeypatch):
        """Without the env var: release beats debug, debug beats the tools dir."""
        _isolate_lookup(tmp_path, monkeypatch)
        monkeypatch.setattr(duocore, "_REPO_ROOT", tmp_path)
        tools = _touch(tmp_path, "tools", duocore._EXE_NAME)
        monkeypatch.setattr(duocore, "tools_dir", lambda: tools.parent)
        assert find_duo_core() == tools
        debug = _touch(tmp_path, "rust", "target", "debug", duocore._EXE_NAME)
        assert find_duo_core() == debug
        release = _touch(tmp_path, "rust", "target", "release", duocore._EXE_NAME)
        assert find_duo_core() == release


def test_find_duo_core_missing_paths_return_none(tmp_path, monkeypatch):
        """A nonexistent DUO_CORE_BIN plus no candidates anywhere -> None."""
        monkeypatch.setenv("DUO_CORE_BIN", str(tmp_path / "nope" / duocore._EXE_NAME))
        monkeypatch.setattr(duocore, "_REPO_ROOT", tmp_path / "empty")
        monkeypatch.setattr(duocore, "tools_dir", lambda: tmp_path / "empty-tools")
        assert find_duo_core() is None


def test_query_devices_parses_states(tmp_path, monkeypatch):
        """Exit-0 JSON maps straight to serial -> state."""
        _install_fake_core(tmp_path, monkeypatch)
        assert query_devices("adb") == {
                "4444bd6b": "device",
                "emulator-5554": "offline",
        }


def test_query_devices_nonzero_exit_raises(tmp_path, monkeypatch):
        """rc=2 with a stderr line surfaces as DuoCoreError, never as {}."""
        _install_fake_core(tmp_path, monkeypatch)
        with pytest.raises(DuoCoreError, match="rc=2"):
                query_devices("broken-adb")


def test_query_devices_without_binary_raises(tmp_path, monkeypatch):
        """No duo-core anywhere -> the canonical not-found error."""
        _isolate_lookup(tmp_path, monkeypatch)
        monkeypatch.setattr(duocore, "_REPO_ROOT", tmp_path)
        with pytest.raises(DuoCoreError, match="not found"):
                query_devices("adb")


def test_device_watch_streams_states_and_stops_idempotently(tmp_path, monkeypatch):
        """Both watch events reach the callback; states/degraded track the last one."""
        _install_fake_core(tmp_path, monkeypatch)
        events: list[dict[str, str]] = []
        watch = DeviceWatch("adb", on_change=events.append, interval_s=0.1)
        watch.stop()   # stop before start must not raise
        watch.start()
        deadline = time.monotonic() + 5
        while len(events) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
        watch.stop()
        watch.stop()   # idempotent
        assert events == [{"A": "device"}, {"A": "offline", "B": "device"}]
        assert watch.states == {"A": "offline", "B": "device"}
        assert watch.degraded is True
        snapshot = watch.states
        snapshot["A"] = "mutated"
        assert watch.states == {"A": "offline", "B": "device"}   # copies, not aliasing


def test_device_watch_requires_binary(tmp_path, monkeypatch):
        """start() with no duo-core available raises the not-found error."""
        _isolate_lookup(tmp_path, monkeypatch)
        monkeypatch.setattr(duocore, "_REPO_ROOT", tmp_path)
        watch = DeviceWatch("adb", on_change=lambda states: None)
        with pytest.raises(DuoCoreError, match="not found"):
                watch.start()


def test_session_process_events_wait_and_idempotent_stop(tmp_path, monkeypatch):
        """started + exit land in events; wait returns the child's code; stop is safe."""
        _install_fake_core(tmp_path, monkeypatch)
        spec = {
                "command": ["scrcpy", "--serial", "4444bd6b"],
                "log_path": str(tmp_path / "session.log"),
                "max_restarts": 3,
                "restart_delay_s": 2.0,
                "env": {"FOO": "bar"},
        }
        session = SessionProcess(spec)
        session.start()
        deadline = time.monotonic() + 5
        while not any(e.get("event") == "exit" for e in session.events):
                if time.monotonic() > deadline:
                        break
                time.sleep(0.01)
        assert any(e.get("event") == "started" for e in session.events)
        assert session.wait(timeout=5) == 0
        assert session.returncode == 0
        assert session.running is False
        session.stop()
        session.stop()   # idempotent on an already-exited child
        events = [e.get("event") for e in session.events]
        assert "started" in events and "exit" in events


def test_session_process_rejects_non_str_env():
        """A non-str env value is a caller bug caught at construction."""
        with pytest.raises(ValueError):
                SessionProcess({"command": ["x"], "env": {"FOO": 1}})


def test_source_contract_winproc_discipline():
        """Every spawn goes through creation_flags() (no console windows on Windows)."""
        src = Path(duocore.__file__).read_text(encoding="utf-8")
        assert src.count("creationflags=creation_flags()") == 3
        assert "tools_dir" in src

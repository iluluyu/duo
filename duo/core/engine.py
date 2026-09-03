"""Engine tooling: locate scrcpy/adb binaries and probe their versions.

Development happens inside WSL, while experiments run on the Windows side
(see plan.md, section 7). WSL interop lets us launch Windows executables
directly, so under WSL the ``.exe`` variants are preferred: they see USB
devices and open real windows on the Windows desktop.

This module is the single place that knows about engine binary details
(see plan.md, section 3 "分层原则"). All functions are Qt-free so the
core layer stays importable and testable without a GUI stack.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache

#: Tools Duo depends on for the mirroring engine.
REQUIRED_TOOLS: tuple[str, ...] = ("scrcpy", "adb")

#: Timeout (seconds) for version probes against external binaries.
_PROBE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class ToolInfo:
        """Result of probing one external tool."""

        name: str
        path: str | None
        version: str | None

        @property
        def available(self) -> bool:
                """Whether the tool was found and is executable."""
                return self.path is not None


@lru_cache(maxsize=1)
def is_wsl() -> bool:
        """Whether we are running inside the Windows Subsystem for Linux."""
        if os.environ.get("WSL_DIST_NAME") or os.environ.get("WSL_INTEROP"):
                return True
        try:
                release = subprocess.run(
                        ["uname", "-r"],
                        capture_output=True,
                        text=True,
                        timeout=_PROBE_TIMEOUT_S,
                        check=False,
                ).stdout.lower()
        except (OSError, subprocess.TimeoutExpired):
                return False
        return "microsoft" in release or "wsl" in release


def tool_names(tool: str) -> tuple[str, ...]:
        """Candidate executable names for ``tool``, best match first.

        Under WSL the Windows build wins: only it can reach USB devices and
        spawn windows on the Windows desktop. The Linux build is kept as a
        fallback so Duo also works in plain Linux environments.
        """
        if is_wsl():
                return (f"{tool}.exe", tool)
        return (tool,)


def probe(tool: str) -> ToolInfo:
        """Locate ``tool`` on PATH and read the first line of its ``--version`` output."""
        for name in tool_names(tool):
                found = shutil.which(name)
                if found is None:
                        continue
                try:
                        result = subprocess.run(
                                [found, "--version"],
                                capture_output=True,
                                text=True,
                                timeout=_PROBE_TIMEOUT_S,
                                check=False,
                        )
                except (OSError, subprocess.TimeoutExpired):
                        return ToolInfo(name=tool, path=found, version=None)
                lines = (result.stdout or result.stderr).strip().splitlines()
                version = lines[0] if lines else None
                return ToolInfo(name=tool, path=found, version=version)
        return ToolInfo(name=tool, path=None, version=None)

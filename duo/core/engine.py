"""Engine tooling: locate scrcpy/adb binaries and probe their versions.

This module is the single place that knows about engine binary details
(see plan.md, section 3 "分层原则"). All functions are Qt-free so the
core layer stays importable and testable without a GUI stack.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

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


def probe(tool: str) -> ToolInfo:
        """Locate ``tool`` on PATH and read the first line of its ``--version`` output."""
        found = shutil.which(tool)
        if found is None:
                return ToolInfo(name=tool, path=None, version=None)
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

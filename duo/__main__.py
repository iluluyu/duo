"""Entry point for ``python -m duo`` and the ``duo`` console script."""

from __future__ import annotations

import argparse
import sys

from duo import __version__
from duo.core.engine import REQUIRED_TOOLS, is_wsl, probe


def _run_check() -> int:
        """Print an environment report and return an exit code (0 = all tools present)."""
        print(f"duo {__version__}")
        env = "wsl (windows binaries via interop)" if is_wsl() else "native"
        print(f"environment: {env}")
        print("-" * 48)
        missing: list[str] = []
        for tool in REQUIRED_TOOLS:
                info = probe(tool)
                if info.available:
                        print(f"[ok]   {tool}: {info.path} ({info.version})")
                else:
                        print(f"[miss] {tool}: not found on PATH")
                        missing.append(tool)
        if missing:
                print()
                print(f"missing tools: {', '.join(missing)}")
                return 1
        return 0


def main(argv: list[str] | None = None) -> int:
        """Parse CLI arguments and dispatch subcommands."""
        parser = argparse.ArgumentParser(
                prog="duo",
                description="Duo - turn the Android device into a headless app server for the PC.",
        )
        parser.add_argument("--version", action="version", version=f"duo {__version__}")
        parser.add_argument(
                "--check",
                action="store_true",
                help="probe scrcpy/adb and report environment status",
        )
        args = parser.parse_args(argv)

        if not args.check:
                print("Duo GUI is under development (see plan.md, milestone M1).")
        return _run_check()


if __name__ == "__main__":
        sys.exit(main())

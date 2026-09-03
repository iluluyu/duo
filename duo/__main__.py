"""Entry point for ``python -m duo`` and the ``duo`` console script."""

from __future__ import annotations

import argparse
import sys
import time

from duo import __version__
from duo.core.apps import Adb, AdbError, app_info, list_device_serials
from duo.core.engine import (
        REQUIRED_TOOLS,
        DisplaySpec,
        EngineArgs,
        VideoSpec,
        is_wsl,
        probe,
)
from duo.core.paths import logs_dir
from duo.core.session import Session, SessionSpec


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


def _pick_serial(explicit: str | None) -> str:
        """Return the device serial to use, auto-picking when exactly one is online."""
        adb_info = probe("adb")
        if not adb_info.available or adb_info.path is None:
                raise AdbError("adb not found on PATH (run: duo --check)")
        serials = list_device_serials(adb_info.path)
        if explicit:
                if explicit not in serials:
                        found = ", ".join(serials) or "none"
                        raise AdbError(f"device {explicit} is not online (found: {found})")
                return explicit
        if not serials:
                raise AdbError("no device online - connect one and enable USB debugging")
        if len(serials) > 1:
                raise AdbError(f"multiple devices online, pass --serial: {', '.join(serials)}")
        return serials[0]


def _run_mirror(args: argparse.Namespace) -> int:
        """Launch a branded mirroring session for one app."""
        serial = _pick_serial(args.serial)

        adb_info = probe("adb")
        scrcpy_info = probe("scrcpy")
        if not adb_info.available or adb_info.path is None:
                raise AdbError("adb not found on PATH (run: duo --check)")
        if not scrcpy_info.available or scrcpy_info.path is None:
                raise AdbError("scrcpy not found on PATH (run: duo --check)")

        display = DisplaySpec(
                mode=args.display,
                width=args.width,
                height=args.height,
                dpi=args.dpi,
        )
        video = VideoSpec(bitrate_mbps=args.bitrate, max_fps=args.fps)
        adb = Adb(adb_info.path, serial)

        title = args.title
        if title is None and args.app:
                info = app_info(adb, args.app)
                title = info.label
                print(f"app: {info.label} ({info.package} {info.version_name or ''})")

        engine_args = EngineArgs(
                serial=serial,
                display=display,
                video=video,
                app_package=args.app,
                screen_off=not args.no_screen_off,
                audio=not args.no_audio,
                window_title=title,
        )
        command = engine_args.to_argv(binary=scrcpy_info.path)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = logs_dir() / f"{stamp}-{args.app or 'mirror'}.log"
        session = Session(SessionSpec(command=command, log_path=log_path))
        print(f"session log: {log_path}")
        print("starting engine... (Ctrl+C to stop)")
        return session.run()


def _build_parser() -> argparse.ArgumentParser:
        """Construct the CLI argument parser."""
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
        subparsers = parser.add_subparsers(dest="command")

        mirror = subparsers.add_parser("mirror", help="mirror an app in a branded session")
        mirror.add_argument("--app", help="app package to launch on a virtual display")
        mirror.add_argument("--serial", help="device serial (auto-picked when only one device)")
        mirror.add_argument(
                "--display",
                choices=["flex", "fixed", "mirror"],
                default="flex",
                help="display mode: flex follows window, fixed locks WxH, mirror is screen",
        )
        mirror.add_argument("--width", type=int, help="virtual display width (fixed mode)")
        mirror.add_argument("--height", type=int, help="virtual display height (fixed mode)")
        mirror.add_argument(
                "--dpi", type=int, default=480, help="virtual display density (default 480)"
        )
        mirror.add_argument("--fps", type=int, default=90, help="max fps (default 90)")
        mirror.add_argument(
                "--bitrate", type=int, default=30, help="video bitrate in Mbps (default 30)"
        )
        mirror.add_argument(
                "--no-screen-off", action="store_true", help="keep the device screen on"
        )
        mirror.add_argument("--no-audio", action="store_true", help="disable audio forwarding")
        mirror.add_argument("--title", help="window title (defaults to the app label)")

        return parser


def main(argv: list[str] | None = None) -> int:
        """Parse CLI arguments and dispatch subcommands."""
        parser = _build_parser()
        args = parser.parse_args(argv)

        if args.command == "mirror":
                try:
                        return _run_mirror(args)
                except AdbError as exc:
                        print(f"error: {exc}", file=sys.stderr)
                        return 1
        if not args.check:
                print("Duo GUI is under development (see plan.md, milestone M1).")
        return _run_check()


if __name__ == "__main__":
        sys.exit(main())

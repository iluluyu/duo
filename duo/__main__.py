"""Entry point for ``python -m duo`` and the ``duo`` console script."""

from __future__ import annotations

import argparse
import signal
import sys
import time

from duo import __version__
from duo.core.apps import Adb, AdbError, app_info
from duo.core.audio_lock import AudioLock
from duo.core.chrome import ChromeError, ChromeOverlay
from duo.core.devices import DeviceMonitor, poll_query
from duo.core.engine import (
        REQUIRED_TOOLS,
        DisplaySpec,
        EngineArgs,
        VideoSpec,
        adb_pin_env,
        is_wsl,
        probe,
)
from duo.core.monitor import primary_work_area, recommend_landscape, recommend_portrait
from duo.core.paths import logs_dir
from duo.core.session import Session, SessionSpec
from duo.core.settings import (
        corner_radius_dip,
        load_settings,
        resolve_tool,
)


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


def _pick_serial(explicit: str | None, adb_path: str) -> str:
        """Return the device serial to use, auto-picking when exactly one is online."""
        states = poll_query(adb_path)()
        serials = [s for s, state in states.items() if state == "device"]
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
        # Priority everywhere: explicit CLI args > saved settings > defaults.
        settings, problems = load_settings()
        for problem in problems:
                print(f"settings: {problem}", flush=True)

        adb_path = resolve_tool("adb", settings, probe("adb").path)
        scrcpy_path = resolve_tool("scrcpy", settings, probe("scrcpy").path)
        if adb_path is None:
                raise AdbError("adb not found (set its path in settings or PATH)")
        if scrcpy_path is None:
                raise AdbError("scrcpy not found (set its path in settings or PATH)")

        serial = _pick_serial(args.serial, adb_path)

        fps = args.fps if args.fps is not None else (settings.fps or 90)
        bitrate = (
                args.bitrate if args.bitrate is not None
                else (settings.bitrate_mbps or 30)
        )
        dpi = args.dpi if args.dpi is not None else settings.dpi
        corner = (
                args.corner_radius if args.corner_radius is not None
                else corner_radius_dip(settings)
        )

        display = DisplaySpec(
                mode=args.display,
                width=args.width,
                height=args.height,
                dpi=dpi,
        )
        # Display recommendation from the PC monitor when not pinned.
        engine_window: dict[str, int | None] = {}
        if display.mode != "mirror":
                area = primary_work_area()
                portrait = args.portrait or (args.height is not None and args.width is not None
                        and args.height > args.width)
                if portrait:
                        rec = recommend_portrait(area, target_dp=args.dp or 640)
                        display = DisplaySpec(
                                mode=display.mode,
                                width=args.width or rec.display_width,
                                height=args.height or rec.display_height,
                                dpi=dpi or rec.dpi,
                        )
                        # Position the window but never lock its size: the user
                        # manages window geometry (e.g. PowerToys zones) and
                        # flex keeps the display following every resize.
                        if display.mode == "flex" and rec.window is not None:
                                engine_window = {"window_x": rec.window.x, "window_y": rec.window.y}
                        else:
                                engine_window = {
                                        "window_x": rec.window.x if rec.window else None,
                                        "window_y": rec.window.y if rec.window else None,
                                        "window_width": rec.display_width,
                                        "window_height": rec.display_height,
                                }
                else:
                        rec = recommend_landscape(area, target_dp=args.dp or 1280)
                        if dpi is None:
                                display = DisplaySpec(
                                        mode=display.mode,
                                        width=display.width,
                                        height=display.height,
                                        dpi=rec.dpi,
                                )
                        engine_window = {}
                area_text = f"work area {area.width}x{area.height}"
                print(f"display: {display.mode} dpi={display.dpi} ({area_text})", flush=True)
        video = VideoSpec(bitrate_mbps=bitrate, max_fps=fps)
        adb = Adb(adb_path, serial)

        title = args.title
        if title is None and args.app:
                info = app_info(adb, args.app)
                title = info.label
                print(f"app: {info.label} ({info.package} {info.version_name or ''})", flush=True)

        # Single-audio arbitration: only one live session may capture the
        # device mixer (two captures crackle). Later windows start muted.
        audio = not args.no_audio
        audio_lock = AudioLock()
        if audio and not audio_lock.acquire():
                audio = False
                print("audio already owned by another duo window - muted", flush=True)

        engine_args = EngineArgs(
                serial=serial,
                adb_binary=adb_path,
                display=display,
                video=video,
                app_package=args.app,
                screen_off=not args.no_screen_off,
                audio=audio,
                window_title=title,
                window_x=engine_window.get("window_x"),
                window_y=engine_window.get("window_y"),
                window_width=engine_window.get("window_width"),
                window_height=engine_window.get("window_height"),
                borderless=args.chrome,
        )
        command = engine_args.to_argv(binary=scrcpy_path)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = logs_dir() / f"{stamp}-{args.app or 'mirror'}.log"
        session = Session(
                SessionSpec(
                        command=command,
                        log_path=log_path,
                        env=adb_pin_env(adb_path),
                )
        )
        print(f"session log: {log_path}", flush=True)

        # Window chrome: borderless window + Windows-side hover overlay.
        overlay: ChromeOverlay | None = None
        if args.chrome:
                if not title:
                        raise ChromeError("--chrome needs a window title: pass --app or --title")
                # Long-press-home only makes sense when mirroring the real
                # display; a virtual display has no launcher behind the app.
                # mirror/fixed windows keep the video aspect ratio (sizes
                # stream from the session log); flex windows resize freely.
                overlay = ChromeOverlay(
                        title=title,
                        serial=serial,
                        adb_path=adb_path,
                        home=args.app is None,
                        display_mode=args.display,
                        video_width=display.width if display.mode == "fixed" else None,
                        video_height=display.height if display.mode == "fixed" else None,
                        session_log=log_path,
                        corner_radius_dip=corner,
                )
                overlay_log = overlay.start()
                print(f"chrome overlay log: {overlay_log}", flush=True)

        # Hotplug watch: stop the session when the device goes away.
        changes: list[dict[str, str]] = []
        monitor = DeviceMonitor(on_change=changes.append, adb_binary=adb_path)
        monitor.start()

        def device_gone() -> bool:
                states = monitor.states
                return bool(states) and states.get(serial) != "device"

        print("starting engine... (Ctrl+C to stop)", flush=True)
        try:
                code = session.run(should_stop=device_gone)
        finally:
                monitor.stop()
                if overlay is not None:
                        overlay.stop()
                audio_lock.release()
        if code == 2:
                print("device disconnected - session stopped", flush=True)
        return code


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
        parser.add_argument(
                "--gui",
                action="store_true",
                help="launch the Duo panel (requires the gui extra: pip install duo[gui])",
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
                "--dpi", type=int, default=None, help="virtual display density (auto by default)"
        )
        mirror.add_argument(
                "--dp",
                type=int,
                default=None,
                help="target layout width in dp (default 1280 landscape / 640 portrait)",
        )
        mirror.add_argument(
                "--portrait",
                action="store_true",
                help="open a tall window on the right with portrait-tuned dpi",
        )
        mirror.add_argument("--fps", type=int, default=None, help="max fps (settings/90)")
        mirror.add_argument(
                "--bitrate", type=int, default=None, help="video bitrate Mbps (settings/30)"
        )
        mirror.add_argument(
                "--no-screen-off", action="store_true", help="keep the device screen on"
        )
        mirror.add_argument("--no-audio", action="store_true", help="disable audio forwarding")
        mirror.add_argument("--title", help="window title (defaults to the app label)")
        mirror.add_argument(
                "--chrome",
                action="store_true",
                help="borderless window with hover-revealed edge controls "
                "(min/max/close, back/home overlay)",
        )
        mirror.add_argument(
                "--corner-radius",
                type=int,
                default=None,
                help="experimental G2 corner radius in DIP; default follows "
                     "settings (system rounding). 0 disables, 48 = iPhone-like",
        )

        return parser


def main(argv: list[str] | None = None) -> int:
        """Parse CLI arguments and dispatch subcommands."""
        # The GUI panel terminates sessions with SIGTERM; route it through
        # SystemExit so the finally blocks stop the overlay and scrcpy.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        parser = _build_parser()
        args = parser.parse_args(argv)

        if args.gui:
                try:
                        from duo.ui.main_window import run_app
                except ImportError as exc:
                        print(f"error: gui extras missing ({exc})", file=sys.stderr)
                        print("install with: pip install duo[gui]", file=sys.stderr)
                        return 1
                return run_app()
        if args.command == "mirror":
                try:
                        return _run_mirror(args)
                except (AdbError, ChromeError) as exc:
                        print(f"error: {exc}", file=sys.stderr)
                        return 1
        if not args.check:
                print("Duo GUI is under development (see plan.md, milestone M1).")
        return _run_check()


if __name__ == "__main__":
        sys.exit(main())

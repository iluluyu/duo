"""PyInstaller entry: the Duo panel as a windowed executable.

When the frozen exe is invoked with command-line arguments it delegates to
the regular CLI (so the panel can spawn `Duo.exe mirror ...` sessions); with
no arguments it opens the GUI panel.

GUI mode is SINGLE-INSTANCE (2026-09-06): every extra panel instance owns
its own session map, blindly unaware of the others - launching an app from
instance B force-stops it off instance A's virtual display (`+` prefix),
leaving A's window as a dead launcher (the "全部失效" incident: three
panels, three engines, apps stolen between displays). CLI invocations and
source-tree `python -m duo` are unaffected: only GUI mode takes the lock.
"""

import sys

_PANEL_LOCK_STOLEN = 85   # exit code: another panel already owns the lock


def _acquire_panel_lock():
    """Take the panel single-instance lock; None if another panel runs.

    Uses QLockFile: it self-heals stale locks left by crashed panels (dead
    pid detection) - no manual cleanup path is needed.
    """
    from PyQt6.QtCore import QLockFile

    from duo.core.paths import data_dir

    lock = QLockFile(str(data_dir() / "panel.lock"))
    if lock.tryLock(0):
        return lock
    return None


def _main() -> int:
    if len(sys.argv) > 1:
        from duo.__main__ import main
        return main(sys.argv[1:])
    lock = _acquire_panel_lock()
    if lock is None:
        # A second panel would fight the first over apps and displays
        # (verified live 2026-09-06); fail fast and loudly instead.
        print("duo panel already running - not starting a second instance",
              file=sys.stderr)
        return _PANEL_LOCK_STOLEN
    from duo.ui.app import run_app
    code = run_app()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(_main())

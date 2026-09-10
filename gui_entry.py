"""PyInstaller entry: the Duo panel as a windowed executable.

When the frozen exe is invoked with command-line arguments it delegates to
the regular CLI (so the panel can spawn `Duo.exe mirror ...` sessions); with
no arguments it opens the GUI panel.

The SINGLE-INSTANCE lock lives in ``duo.ui.app.run_app`` (not here): that
way every GUI entry - the frozen no-arg exe, the ``duo --gui`` console
script and ``python -m duo --gui`` - enforces the same one-panel rule, and
a source-tree panel can never fight a packaged one. Session CLI invocations
never reach ``run_app`` and stay exempt: they are the panel's children, not
competing panels.
"""

import sys


def _install_crash_capture():
    """Log unhandled exceptions and hard crashes to logs/panel-errors.log.

    PyQt aborts the process after an exception escapes a slot (qFatal),
    and a windowed PyInstaller exe has no stderr to print the traceback
    to - without this capture a real-machine crash (2026-09-08:
    fixed-ratio launch killed Duo.exe) leaves no evidence at all. The
    hook writes the traceback BEFORE the abort, and faulthandler catches
    native faults the same way.
    """
    import datetime
    import faulthandler
    import traceback

    from duo.core.paths import data_dir

    log = data_dir() / "logs" / "panel-errors.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    def _hook(exc_type, value, tb):
        try:
            with log.open("a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.datetime.now()} unhandled ---\n")
                traceback.print_exception(exc_type, value, tb, file=f)
        finally:
            sys.__excepthook__(exc_type, value, tb)

    sys.excepthook = _hook
    faulthandler.enable(log.open("a", encoding="utf-8"), all_threads=True)


def _main() -> int:
    if len(sys.argv) > 1:
        from duo.__main__ import main

        return main(sys.argv[1:])
    _install_crash_capture()
    from duo.ui.app import run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(_main())

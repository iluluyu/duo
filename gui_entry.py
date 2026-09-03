"""PyInstaller entry: the Duo panel as a windowed executable.

When the frozen exe is invoked with command-line arguments it delegates to
the regular CLI (so the panel can spawn `Duo.exe mirror ...` sessions); with
no arguments it opens the GUI panel.
"""

import sys


def _main() -> int:
    if len(sys.argv) > 1:
        from duo.__main__ import main
        return main(sys.argv[1:])
    from duo.ui.main_window import run_app
    return run_app()


if __name__ == "__main__":
    sys.exit(_main())

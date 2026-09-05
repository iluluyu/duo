"""Window chrome overlay: Windows-side hover controls for borderless sessions.

``duo mirror --chrome`` runs scrcpy with ``--window-borderless``: the mirror
window looks like a standalone app window (no "forehead" title bar, no "chin"
decorations). The missing affordances come back on demand from an overlay
that runs on the Windows side, because it must talk Win32 to the scrcpy
window (FindWindow by title, GetWindowRect, styles, z-order):

    cursor near the top edge    -> top-right capsule: minimize / maximize
                                   (taskbar-safe, emulated) / close
    window active               -> persistent chin: physical mirroring shows
                                   the mBack ring, tap = back, long-press =
                                   home; virtual displays show a plain back
                                   chevron and long-press = session close
                                   (they have no launcher - HOME there raises
                                   the system launcher's all-apps picker on
                                   the mirrored display, the "confusing app
                                   selector")

The overlay itself is the C# program ``duo/resources/chrome_overlay.cs``,
compiled on first use with the .NET Framework ``csc.exe`` that every Windows
installs (no SDK needed, ~0.2s build) and cached in the Duo data dir. A real
executable was chosen over the earlier PowerShell/WinForms prototype after
interop experiments (see plan.md section 7): a PE binary receives true UTF-16
argv (CJK window titles pass through raw), it owns its WinForms message loop
without console-host quirks, and its layered windows use per-pixel alpha
acrylic sampled from the mirrored window.

The module runs both under WSL (paths translated via ``wslpath -w``) and on
native Windows (paths are already Windows-shaped and pass through).
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from pathlib import Path

from duo.core.paths import data_dir, logs_dir
from duo.core.winproc import creation_flags

#: The overlay source shipped inside the package resources.
OVERLAY_SOURCE = Path(__file__).resolve().parent.parent / "resources" / "chrome_overlay.cs"

#: .NET Framework compilers, best first. The same binary is reachable both
#: through the WSL mount and natively on Windows; existence checks pick the
#: one that exists in the current environment.
CSC_CANDIDATES = (
        "C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe",
        "C:\\Windows\\Microsoft.NET\\Framework\\v4.0.30319\\csc.exe",
        "/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe",
        "/mnt/c/Windows/Microsoft.NET/Framework/v4.0.30319/csc.exe",
)

#: Cached build artifacts (exe + source hash sidecar) under the data dir.
EXE_NAME = "DuoChromeOverlay.exe"

_COMPILE_TIMEOUT_S = 60.0
_WSLPATH_TIMEOUT_S = 10.0
_TERMINATE_TIMEOUT_S = 5.0


class ChromeError(RuntimeError):
        """Raised when the chrome overlay cannot be prepared or launched."""


def source_stamp() -> str:
        """Content hash of the overlay source (cache invalidation key)."""
        return hashlib.sha256(OVERLAY_SOURCE.read_bytes()).hexdigest()


def build_is_fresh(exe: Path, stamp_file: Path, stamp: str) -> bool:
        """Whether the cached exe matches the given source stamp.

        Pure over its inputs; both files must exist and the sidecar must hold
        exactly ``stamp`` (plus a newline from its last write).
        """
        if not exe.is_file() or not stamp_file.is_file():
                return False
        return stamp_file.read_text(encoding="utf-8").strip() == stamp


def compile_command(csc: str, source_win: str, out_win: str) -> list[str]:
        """Assemble the csc.exe argv that builds the overlay executable."""
        return [
                csc,
                "-nologo",
                "-target:winexe",
                "-optimize+",
                f"-out:{out_win}",
                "-r:System.dll",
                "-r:System.Drawing.dll",
                "-r:System.Windows.Forms.dll",
                source_win,
        ]


def overlay_command(
        exe: str,
        title: str,
        serial: str,
        adb_path: str,
        home: bool,
        display_mode: str = "flex",
        video_width: int | None = None,
        video_height: int | None = None,
        session_log: str | None = None,
        corner_radius_dip: int = 0,
) -> list[str]:
        """Assemble the argv that launches the compiled overlay.

        The overlay takes plain parameters: as a PE binary it receives real
        UTF-16 argv, so CJK titles need no base64 transport (that workaround
        was PowerShell-specific). ``home`` marks a device-mirroring session
        (no ``--app``): the chin's long-press sends HOME there. On virtual
        displays (flex/fixed) the long-press instead CLOSES the session
        window - a virtual display has no launcher, and keyevent 3 raises
        the system launcher's all-apps picker on the mirrored display.

        ``display_mode`` drives resize policy AND the chin affordance:
        mirror keeps the window glued to the video aspect ratio (sizes
        arrive through ``session_log`` as scrcpy ``Texture:`` lines) and
        shows the ring glyph; flex/fixed let the window resize freely and
        show a plain back chevron (no home affordance - see above and
        docs/window-experience.md §7). ``video_*`` is the initial size when
        already known (fixed mode only); ``session_log`` is a Windows path.
        """
        argv = [
                exe,
                "--title",
                title,
                "--serial",
                serial,
                "--adb",
                adb_path,
                "--home",
                "1" if home else "0",
                "--display-mode",
                display_mode,
        ]
        if video_width and video_height:
                argv += ["--video-w", str(video_width), "--video-h", str(video_height)]
        if session_log:
                argv += ["--session-log", session_log]
        if corner_radius_dip > 0:
                argv += ["--corner-radius", str(corner_radius_dip)]
        return argv


def wsl_to_windows_path(path: str) -> str:
        """Translate a WSL absolute path to its Windows form via ``wslpath -w``.

        Non-absolute paths are returned unchanged (already Windows-shaped).
        Raises :class:`ChromeError` when the translation is needed but fails.
        """
        if not path.startswith("/"):
                return path
        try:
                result = subprocess.run(
                        ["wslpath", "-w", path],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=_WSLPATH_TIMEOUT_S,
                        check=False,
                        creationflags=creation_flags(),
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
                raise ChromeError(f"wslpath failed for {path}: {exc}") from exc
        translated = (result.stdout or "").strip()
        if result.returncode != 0 or not translated:
                raise ChromeError(f"wslpath could not translate {path}")
        return translated


def find_csc() -> str:
        """Return the first installed .NET Framework compiler path."""
        for candidate in CSC_CANDIDATES:
                if Path(candidate).is_file():
                        return candidate
        raise ChromeError(
                "no .NET Framework csc.exe found under /mnt/c/Windows/Microsoft.NET"
        )


def ensure_built(to_windows: Callable[[str], str] = wsl_to_windows_path) -> Path:
        """Compile the overlay if the cache is stale; return the exe path.

        The exe is cached in the Duo data dir and addressed through its
        WSL-visible path: the interop layer runs PE binaries from the Linux
        filesystem once the execute bit is set, which keeps the overlay a
        direct child of the Duo process (clean terminate semantics).
        """
        if not OVERLAY_SOURCE.is_file():
                raise ChromeError(f"overlay source missing: {OVERLAY_SOURCE}")
        build_dir = data_dir() / "overlay"
        build_dir.mkdir(parents=True, exist_ok=True)
        exe = build_dir / EXE_NAME
        stamp_file = build_dir / f"{EXE_NAME}.sha256"
        stamp = source_stamp()
        if build_is_fresh(exe, stamp_file, stamp):
                exe.chmod(0o755)
                return exe
        csc = find_csc()
        command = compile_command(csc, to_windows(str(OVERLAY_SOURCE)), to_windows(str(exe)))
        try:
                result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",  # csc emits localized (GBK) warnings
                        timeout=_COMPILE_TIMEOUT_S,
                        check=False,
                        creationflags=creation_flags(),
                )
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                if build_is_fresh(exe, stamp_file, stamp) or exe.is_file():
                        exe.chmod(0o755)
                        return exe  # a concurrent build won the race; reuse it
                raise ChromeError(f"csc failed to launch: {exc}") from exc
        if result.returncode != 0 or not exe.is_file():
                raise ChromeError(f"csc failed: {(result.stderr or result.stdout).strip()}")
        stamp_file.write_text(stamp + "\n", encoding="utf-8")
        exe.chmod(0o755)
        return exe


class ChromeOverlay:
        """The overlay child process, bound to one mirroring session."""

        def __init__(
                self,
                title: str,
                serial: str,
                adb_path: str,
                home: bool = False,
                display_mode: str = "flex",
                video_width: int | None = None,
                video_height: int | None = None,
                session_log: Path | None = None,
                corner_radius_dip: int = 0,
        ) -> None:
                self._title = title
                self._serial = serial
                self._adb_path = adb_path
                self._exe = ensure_built()
                log_arg = None
                if session_log is not None:
                        log_arg = wsl_to_windows_path(str(session_log))
                self.command: list[str] = overlay_command(
                        str(self._exe),
                        title,
                        serial,
                        wsl_to_windows_path(adb_path),
                        home,
                        display_mode=display_mode,
                        video_width=video_width,
                        video_height=video_height,
                        session_log=log_arg,
                        corner_radius_dip=corner_radius_dip,
                )
                self._proc: subprocess.Popen[bytes] | None = None

        @property
        def running(self) -> bool:
                """Whether the overlay process is currently alive."""
                return self._proc is not None and self._proc.poll() is None

        def start(self) -> Path:
                """Spawn the overlay with its output captured to a session log."""
                log_path = logs_dir() / "overlay" / "chrome-latest.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                # The child inherits the descriptor; the parent-side handle can
                # close right after the spawn (same pattern as Session.start).
                with open(log_path, "ab") as log_file:
                        self._proc = subprocess.Popen(
                                self.command,
                                stdout=log_file,
                                stderr=subprocess.STDOUT,
                                creationflags=creation_flags(),
                        )
                return log_path

        def stop(self) -> None:
                """Terminate the overlay process (no-op when not running)."""
                if self._proc is None:
                        return
                if self._proc.poll() is None:
                        self._proc.terminate()
                        try:
                                self._proc.wait(timeout=_TERMINATE_TIMEOUT_S)
                        except subprocess.TimeoutExpired:
                                self._proc.kill()
                                self._proc.wait(timeout=_TERMINATE_TIMEOUT_S)
                self._proc = None

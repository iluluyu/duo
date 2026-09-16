"""Windows subprocess plumbing: silent spawn, tree kill, kill-on-close jobs.

The panel is a windowed executable; spawning console tools (adb, scrcpy,
csc.exe, aapt2) from it would open a fresh console window for each call -
the panel polls adb every two seconds, so the desktop flickers constantly.
Every duo subprocess that talks to an external binary passes
:func:`creation_flags` to ``Popen``/``run`` so the tools run silently.

进程生命周期契约（设计细节见 docs/window-experience.md「进程生命周期」）：
面板是唯一主进程；会话 CLI 是它的孩子，scrcpy/overlay 是孙子。停会话必须
整树终止（Windows 上 ``Popen.terminate`` 是 TerminateProcess，SIGTERM 处理
器从不执行，裸杀会把 scrcpy/overlay 留成孤儿）；面板退出（含崩溃）则由
kill-on-close Job Object 把树里剩余进程一并拖走。非 Windows 平台全部降级
为 POSIX 语义（terminate + 显式清单杀）。
"""

from __future__ import annotations

import contextlib
import subprocess
import sys

#: taskkill 的完整树终止最坏情况（多层子进程逐个 TerminateProcess）远快
#: 于这个上限；超时说明 taskkill 本身出问题了，走回退分支。
_TASKKILL_TIMEOUT_S = 10.0


def creation_flags() -> int:
        """``CREATE_NO_WINDOW`` on Windows, ``0`` everywhere else."""
        if sys.platform == "win32":
                return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return 0


def terminate_tree(proc: subprocess.Popen[bytes]) -> None:
        """Terminate ``proc`` and all of its descendants.

        On Windows ``Popen.terminate`` is a bare ``TerminateProcess``: the
        session CLI's SIGTERM cleanup never runs and its scrcpy/overlay
        children survive as orphans (2026-09-10 真机：面板关了一夜，
        task manager 里残留 scrcpy.exe 与会话 Duo.exe). ``taskkill /T /F``
        takes the whole tree down; a failed taskkill (process already gone)
        falls back to the plain terminate, which is a no-op on a dead pid.
        POSIX keeps the graceful ``terminate`` (SIGTERM handlers run).
        """
        if sys.platform == "win32" and proc.poll() is None:
                result = subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                        capture_output=True,
                        timeout=_TASKKILL_TIMEOUT_S,
                        check=False,
                        creationflags=creation_flags(),
                )
                if result.returncode == 0:
                        return
        with contextlib.suppress(OSError, ValueError):
                proc.terminate()


def _win_job_handle() -> int | None:
        """Create a kill-on-close job object; None off-Windows/on failure."""
        if sys.platform != "win32":
                return None
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        job_object_extended_limit_information = 9
        job_object_limit_kill_on_job_close = 0x2000

        class _IoCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                        "ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount",
                        "WriteTransferCount", "OtherTransferCount")]

        class _BasicLimits(ctypes.Structure):
                _fields_ = [
                        ("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", ctypes.c_ulong),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_ulong),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", ctypes.c_ulong),
                        ("SchedulingClass", ctypes.c_ulong),
                ]

        class _ExtendedLimits(ctypes.Structure):
                _fields_ = [
                        ("BasicLimitInformation", _BasicLimits),
                        ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
                return None
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
        ok = kernel32.SetInformationJobObject(
                job, job_object_extended_limit_information,
                ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
                kernel32.CloseHandle(job)
                return None
        return int(job)


class ChildJob:
        """Kill-on-close container for spawned session trees (Windows).

        Every process assigned here - and every process THEY spawn - dies
        the moment the last job handle closes: that covers the panel being
        killed outright (crash, task manager, logoff), where no Python
        cleanup ever runs. Off-Windows it is a transparent no-op: there,
        ``PanelController.shutdown`` explicitly tree-terminates its sessions
        instead. ``add`` failures are swallowed on purpose: a session that
        missed the job still works, it just loses the crash-safety net.
        """

        def __init__(self) -> None:
                self._handle = _win_job_handle()

        @property
        def active(self) -> bool:
                """Whether a live job object backs this container."""
                return self._handle is not None

        def add(self, proc: subprocess.Popen[bytes]) -> None:
                """Assign one spawned session process into the job."""
                if self._handle is None:
                        return
                import ctypes

                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                process_set_quota = 0x0100
                process_terminate = 0x0001
                handle = kernel32.OpenProcess(
                        process_set_quota | process_terminate, False, proc.pid
                )
                if not handle:
                        return
                try:
                        kernel32.AssignProcessToJobObject(self._handle, handle)
                finally:
                        kernel32.CloseHandle(handle)

        def close(self) -> None:
                """Close the job handle: kills every remaining member tree."""
                if self._handle is None:
                        return
                import ctypes

                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                kernel32.CloseHandle(self._handle)
                self._handle = None


def notify_already_running(message: str) -> None:
        """Tell the user a second panel was refused (native box on Windows).

        A windowed frozen exe has no console: the stderr fallback is
        invisible there, and a silently-nothing-happens double click reads
        as "the app is broken". ``MessageBoxW`` needs no Qt module and no
        application instance, so the refused instance stays cheap.
        """
        if sys.platform == "win32":
                import ctypes

                mb_iconinformation = 0x40
                mb_topmost = 0x40000
                ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                        None, message, "Duo", mb_iconinformation | mb_topmost
                )
                return
        print(message, file=sys.stderr)

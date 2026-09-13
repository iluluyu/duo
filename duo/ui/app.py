"""QML front end: application bootstrap and the settings backend.

:func:`run_app` is the ``--gui`` entry (wired from :mod:`duo.__main__` and
the frozen ``gui_entry.py``): it resolves adb once (settings override >
PATH probe > the literal ``"adb.exe"`` fallback, same as the CLI), hands a
:class:`~duo.ui.controller.PanelController` and a :class:`SettingsApi` to
the QML engine as the ``ctrl`` / ``settingsApi`` context properties, loads
``qml/Main.qml`` and runs the event loop.

SettingsApi implements the contract SettingsPage.qml was written against:
``load()`` mirrors the Settings fields, ``loadProblems()`` reports what was
wrong with settings.json (the page shows it as its red bar, like the
widgets page did), ``save(map)`` validates and persists atomically while
returning the problem list (empty = saved), and ``probe()`` runs a tool
check on a background thread, reporting via the ``probeDone`` signal.
While a mirror session lives, the QML page binds ``engineLocked`` off the
controller, so the engine rows lock exactly like the widgets page did.

Frozen packaging (PyInstaller one-exe): the QML sidecar files must ship
inside the bundle - pass ``--add-data duo/ui/qml`` (Main.qml,
SettingsPage.qml, Style.qml, qmldir) or ``QML_MAIN`` below has nothing to
load.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QLockFile, QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine

from duo.core.engine import is_wsl, probe_binary
from duo.core.engine import probe as probe_on_path
from duo.core.paths import data_dir
from duo.core.settings import (
        Settings,
        load_settings,
        resolve_adb_path,
        save_settings,
        validate,
)
from duo.core.winproc import notify_already_running
from duo.ui.controller import PanelController

#:audio_policy values shown in the settings page, in display order.
AUDIO_POLICIES = ("latest", "all", "off")

#: video_codec values shown in the settings page, in display order.
VIDEO_CODECS = ("auto", "h264", "h265", "av1")

#: The panel document (qmldir next to it declares the Style singleton).
#: Frozen builds must include the directory (PyInstaller
#: ``--add-data duo/ui/qml``); __file__-relative lookup fails otherwise.
QML_MAIN = Path(__file__).with_name("qml") / "Main.qml"

#: Exit code when another panel already holds the single-instance lock.
PANEL_LOCK_STOLEN = 85

#: 第二实例提示语（原生 MessageBox，无需 Qt 窗口）。
_ALREADY_RUNNING = "Duo 面板已在运行，本次启动已退出（单实例限制）。"


def _panel_lock_path() -> Path:
        """Where the panel's single-instance lock lives."""
        return data_dir() / "panel.lock"


def _acquire_panel_lock() -> QLockFile | None:
        """Take the panel single-instance lock; None if another panel runs.

        The lock lives in ``run_app`` so EVERY GUI entry enforces it - the
        frozen no-arg exe, the ``duo --gui`` console script and
        ``python -m duo --gui`` (the old lock covered only the frozen
        entry, leaving source-tree panels free to fight a packaged one:
        two panels = two session maps stealing apps between virtual
        displays). QLockFile self-heals stale locks left by crashed panels
        (dead pid detection), so no manual cleanup path exists. Session CLI
        runs (``duo mirror ...``) never reach ``run_app`` and stay exempt:
        they are the panel's children, not competing panels.
        """
        lock = QLockFile(str(_panel_lock_path()))
        if lock.tryLock(0):
                return lock
        return None


def _bundled_icon() -> Path | None:
        """assets/duo.ico where the bundle ships it (frozen) or the repo tree."""
        base = getattr(sys, "_MEIPASS", None)
        if base is not None:
                frozen = Path(base) / "assets" / "duo.ico"
                if frozen.is_file():
                        return frozen
        dev = Path(__file__).resolve().parents[2] / "assets" / "duo.ico"
        return dev if dev.is_file() else None


def _number(value: object) -> Any:
        """Collapse whole JS doubles to int; junk passes through untouched.

        QVariantMap payloads arrive with JS typing - every number is a
        double, so ``90.0`` must become ``90`` before validate() sees it.
        Deliberately returns Any: Settings is a plain dataclass, and wrong
        types must reach validate() to be reported, not be guessed away.
        """
        if isinstance(value, float) and value.is_integer():
                return int(value)
        return value


def _text(value: object) -> Any:
        """Path fields: ``None`` becomes "" so validate() never sees null."""
        return "" if value is None else value


def _flag(value: object) -> Any:
        """Bool fields: passthrough (Any) so wrong types reach validate()."""
        return value


class SettingsApi(QObject):
        """The settings page's backend: load/save/probe behind the QML contract."""

        probeDone = pyqtSignal(str, bool, str)   # tool, ok, version detail

        @pyqtSlot(result="QVariantMap")
        def load(self) -> dict[str, object]:
                """Effective settings as a plain map (load_settings never raises)."""
                settings, _problems = load_settings()
                return {
                        "scrcpy_path": settings.scrcpy_path,
                        "adb_path": settings.adb_path,
                        "fps": settings.fps,
                        "bitrate_mbps": settings.bitrate_mbps,
                        "dpi": settings.dpi,
                        "render_scale": settings.render_scale,
                        "corner_mode": settings.corner_mode,
                        "corner_size_dip": settings.corner_size_dip,
                        "glass_enabled": settings.glass_enabled,
                        "theme": settings.theme,
                        "audio_policy": settings.audio_policy,
                        "video_codec": settings.video_codec,
                        "turn_screen_off": settings.turn_screen_off,
                        "top_bar_mode": settings.top_bar_mode,
                        "bottom_bar_mode": settings.bottom_bar_mode,
                }

        @pyqtSlot(result="QVariantList")
        def loadProblems(self) -> list[str]:
                """Problems found in settings.json (missing = no problems).

                The page shows these in its red bar on open, like the
                widgets page's ``_load`` did - run_app's stderr line is
                invisible in windowed frozen builds.
                """
                _settings, problems = load_settings()
                return problems

        @pyqtSlot("QVariantMap", result="QVariantList")
        def save(self, values: dict[str, object]) -> list[str]:
                """Validate then persist; the problem list (empty = saved)."""
                settings = Settings(
                        scrcpy_path=_text(values.get("scrcpy_path", "")),
                        adb_path=_text(values.get("adb_path", "")),
                        fps=_number(values.get("fps", 90)),
                        bitrate_mbps=_number(values.get("bitrate_mbps", 30)),
                        dpi=_number(values.get("dpi")),
                        render_scale=_number(values.get("render_scale", 1.0)),
                        corner_mode=_text(values.get("corner_mode", "system")),
                        corner_size_dip=_number(values.get("corner_size_dip", 48)),
                        glass_enabled=_flag(values.get("glass_enabled", True)),
                        theme=_text(values.get("theme", "light")),
                        audio_policy=_text(values.get("audio_policy", "latest")),
                        video_codec=_text(values.get("video_codec", "auto")),
                        turn_screen_off=_flag(values.get("turn_screen_off", False)),
                        top_bar_mode=_text(values.get("top_bar_mode", "immersive")),
                        bottom_bar_mode=_text(values.get("bottom_bar_mode", "immersive")),
                )
                problems = validate(settings)
                if problems:
                        return problems
                save_settings(settings)
                return []

        @pyqtSlot(str, str)
        def probe(self, tool: str, path: str) -> None:
                """Check one tool off the UI thread; probeDone reports back."""
                def work() -> None:
                        info = (
                                probe_binary(path, tool) if path else probe_on_path(tool)
                        )
                        self.probeDone.emit(tool, info.available, info.version or "")

                threading.Thread(target=work, daemon=True).start()


def run_app() -> int:
        """Create the QML panel, resolve adb once, run the Qt event loop.

        SINGLE-INSTANCE here (not in the frozen entry): see
        :func:`_acquire_panel_lock`. A refused instance tells the user via
        a native message box (windowed exes have no visible stderr) and
        exits with :data:`PANEL_LOCK_STOLEN`.
        """
        lock = _acquire_panel_lock()
        if lock is None:
                notify_already_running(_ALREADY_RUNNING)
                return PANEL_LOCK_STOLEN
        try:
                return _run_app_locked()
        finally:
                lock.unlock()


def _run_app_locked() -> int:
        # High-DPI 契约：Qt6 默认开启 per-monitor High-DPI 缩放，这里刻意
        # 不设 QT_ENABLE_HIGHDPI_SCALING / QT_SCALE_FACTOR 等任何覆盖，让
        # QML 里的 px 值保持 DIP 语义、按每屏 DPR 渲染（混合 DPI 双屏下
        # 100% 缩放屏的舒适档位在 Main.qml 的 uiScale 处理）。
        app = QGuiApplication(sys.argv)
        # spec 的 icon= 只埋 exe 资源；任务栏/标题栏图标必须 Qt 侧设置
        # （冻结包里从 datas 的 assets/duo.ico 取，早于任何窗口创建）。
        icon = _bundled_icon()
        if icon is not None:
                app.setWindowIcon(QIcon(str(icon)))
        # Same resolution as the CLI: settings override > PATH probe > the
        # literal "adb.exe" fallback, so panel and spawned sessions share adb.
        settings, problems = load_settings()
        for problem in problems:
                print(f"settings: {problem}", file=sys.stderr)
        adb = resolve_adb_path(settings, probe_on_path("adb").path, "adb.exe")

        controller = PanelController(adb)
        api = SettingsApi()
        engine = QQmlApplicationEngine()
        context = engine.rootContext()
        assert context is not None   # the engine always has a root context
        context.setContextProperty("ctrl", controller)
        context.setContextProperty("settingsApi", api)
        # 亚克力模糊采样闸门：WSL 的 Mesa/Vulkan 栈会让着色器输出异常，
        # 在圆角卡上露出直角快照块（“未连接设备”卡曾复现）。Windows 真机
        # （ANGLE/OpenGL）不受影响；Style.glassBlur 据此降级为纯半透明卡。
        context.setContextProperty("shadersUsable", not is_wsl())
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        if not engine.rootObjects():
                print("error: Main.qml 加载失败", file=sys.stderr)
                controller.shutdown()
                return 1
        code = app.exec()
        controller.shutdown()
        return code


if __name__ == "__main__":
        sys.exit(run_app())

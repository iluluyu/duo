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

from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

from duo.core.engine import probe as probe_on_path
from duo.core.engine import probe_binary
from duo.core.settings import (
        Settings,
        load_settings,
        resolve_adb_path,
        save_settings,
        validate,
)
from duo.ui.controller import PanelController

#:audio_policy values shown in the settings page, in display order.
AUDIO_POLICIES = ("latest", "all", "off")

#: video_codec values shown in the settings page, in display order.
VIDEO_CODECS = ("auto", "h264", "h265", "av1")

#: The panel document (qmldir next to it declares the Style singleton).
#: Frozen builds must include the directory (PyInstaller
#: ``--add-data duo/ui/qml``); __file__-relative lookup fails otherwise.
QML_MAIN = Path(__file__).with_name("qml") / "Main.qml"


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
                        "corner_mode": settings.corner_mode,
                        "corner_size_dip": settings.corner_size_dip,
                        "glass_enabled": settings.glass_enabled,
                        "audio_policy": settings.audio_policy,
                        "video_codec": settings.video_codec,
                        "turn_screen_off": settings.turn_screen_off,
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
                        corner_mode=_text(values.get("corner_mode", "system")),
                        corner_size_dip=_number(values.get("corner_size_dip", 48)),
                        glass_enabled=_flag(values.get("glass_enabled", True)),
                        audio_policy=_text(values.get("audio_policy", "latest")),
                        video_codec=_text(values.get("video_codec", "auto")),
                        turn_screen_off=_flag(values.get("turn_screen_off", False)),
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
        """Create the QML panel, resolve adb once, run the Qt event loop."""
        # High-DPI 契约：Qt6 默认开启 per-monitor High-DPI 缩放，这里刻意
        # 不设 QT_ENABLE_HIGHDPI_SCALING / QT_SCALE_FACTOR 等任何覆盖，让
        # QML 里的 px 值保持 DIP 语义、按每屏 DPR 渲染（混合 DPI 双屏下
        # 100% 缩放屏的舒适档位在 Main.qml 的 uiScale 处理）。
        app = QGuiApplication(sys.argv)
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

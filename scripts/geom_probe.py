"""设置页几何探针：dump QML item 树的 y/height（像素合同取证）。

与 qml_shots.py 同一套桩（设备/目录/临时目录），push 设置页后遍历
QQuickItem 树，输出每项的 class/objectName/y/height/implicitHeight，
供 rustduo settings.rs geom 常量逐值核对。不修改任何文件。
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from PyQt6.QtCore import QEventLoop, QObject, QTimer, QUrl  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtQml import QQmlApplicationEngine  # noqa: E402

import pyduo.core.settings as settings_mod  # noqa: E402
import pyduo.ui.controller as controller_mod  # noqa: E402
from pyduo.ui.app import QML_MAIN, SettingsApi  # noqa: E402
from pyduo.ui.controller import APP_CATALOG, PanelController  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="duo_geom_probe_"))


class _StubMonitor:
        def __init__(self, on_change, query=None, adb_binary=None, poll_interval_s=2.0):
                self.on_change = on_change
                self.online: list[str] = []
                self._states = {"4444bd6b": "device"}

        @property
        def states(self):
                return dict(self._states)

        def poll_now(self):
                self.on_change(self.states)

        def start(self):
                pass

        def stop(self):
                pass


def patch_adb_boundary():
        controller_mod.DeviceMonitor = _StubMonitor  # type: ignore[assignment]
        controller_mod._resolve_installed = (  # type: ignore[assignment]
                lambda adb, done: done(
                        {p.package for p in APP_CATALOG}
                        | {"com.android.chrome", "org.mozilla.firefox",
                           "com.spotify.music", "com.discord"}
                )
        )
        controller_mod._prefs_path = lambda: TMP / "gui_prefs.json"  # type: ignore[assignment]
        settings_mod.settings_path = lambda: TMP / "settings.json"  # type: ignore[assignment]


def pump(ms: int):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()


def dump(item, depth: int = 0, out: list[str] | None = None):
        from PyQt6.QtQuick import QQuickItem
        if not isinstance(item, QQuickItem):
                return
        name = item.objectName() or ""
        cls = item.metaObject().className()
        try:
                ih = round(float(item.implicitHeight), 1)
        except (AttributeError, TypeError):
                ih = -1.0
        out.append(
                f"{'  ' * depth}{cls.split('(')[0]} name={name!r} "
                f"y={item.y():.1f} h={item.height():.1f} ih={ih}"
        )
        for child in item.childItems():
                dump(child, depth + 1, out)


def main() -> int:
        _ = QGuiApplication(["geom_probe"])
        patch_adb_boundary()
        controller = PanelController("/nonexistent/adb-for-shots")
        api = SettingsApi()
        engine = QQmlApplicationEngine()
        context = engine.rootContext()
        context.setContextProperty("ctrl", controller)
        context.setContextProperty("settingsApi", api)
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        if not engine.rootObjects():
                print("[fatal] Main.qml load failed", file=sys.stderr)
                return 2
        window = engine.rootObjects()[0]
        pump(400)
        gear = window.findChild(QObject, "gearButton")
        if gear is None:
                print("[fatal] no gearButton", file=sys.stderr)
                return 2
        gear.click()
        pump(600)

        page = window.findChild(QObject, "settingsPageQml")
        if page is None:
                print("[fatal] settings page not pushed", file=sys.stderr)
                return 2
        from PyQt6 import sip
        from PyQt6.QtQuick import QQuickItem
        root = sip.cast(page, QQuickItem)
        lines: list[str] = []
        dump(root, 0, lines)
        print("\n".join(lines))
        return 0


if __name__ == "__main__":
        raise SystemExit(main())

"""真数据 QML 出图：主面板 / 设置页默认 / 设置页 g2（多模态自检用）。

offscreen + software 渲染；只在 adb 边界打桩（一台在线设备 + 目录应用全部
已装 + 偏好/设置落到临时目录），其余全走生产代码路径：真实 PanelController、
真实 SettingsApi、真实 Main.qml / SettingsPage.qml / Style 单例。

用法（仓库根目录）::

    .venv/bin/python scripts/qml_shots.py

输出三张 PNG 到 docs/validation/assets/：
    qml-main.png           主面板正常态（设备在线、目录应用已装、状态 toast）
    qml-settings.png       设置页默认态（system 圆角、dpi 自动、玻璃开）
    qml-settings-g2.png    设置页 g2 态（G2 大圆角 + 滑块 72 + 预览重绘）
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QEventLoop, QObject, QTimer, QUrl  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtQml import QQmlApplicationEngine  # noqa: E402
from PyQt6.QtQuick import QQuickWindow  # noqa: E402

import duo.core.settings as settings_mod  # noqa: E402
import duo.ui.controller as controller_mod  # noqa: E402
from duo.ui.app import QML_MAIN, SettingsApi  # noqa: E402
from duo.ui.controller import APP_CATALOG, PanelController  # noqa: E402

OUT = REPO / "docs" / "validation" / "assets"
TMP = Path(tempfile.mkdtemp(prefix="duo_qml_shots_"))


class _StubMonitor:
        """设备监控桩：一台在线设备，不线程、不 adb。

        ``online`` 特意留空：让 controller 的图标/元数据后台线程直接短路，
        避免它们拿假 adb 去起子进程。
        """

        def __init__(self, on_change, query=None, adb_binary=None,
                     poll_interval_s: float = 2.0) -> None:
                self.on_change = on_change
                self.online: list[str] = []
                self._states: dict[str, str] = {"4444bd6b": "device"}

        @property
        def states(self) -> dict[str, str]:
                return dict(self._states)

        def poll_now(self) -> None:
                self.on_change(self.states)

        def start(self) -> None:
                pass

        def stop(self) -> None:
                pass


def patch_adb_boundary() -> None:
        """把 adb / 磁盘边界换掉：出图绝不触碰真实设备与真实用户数据。"""
        controller_mod.DeviceMonitor = _StubMonitor  # type: ignore[assignment]
        controller_mod._resolve_installed = (  # type: ignore[assignment]
                lambda adb, done: done({package for _, package in APP_CATALOG})
        )
        controller_mod._prefs_path = lambda: TMP / "gui_prefs.json"  # type: ignore[assignment]
        settings_mod.settings_path = lambda: TMP / "settings.json"  # type: ignore[assignment]


def pump(ms: int) -> None:
        """驱动事件循环 ms 毫秒，让绑定/过渡/首帧走完。"""
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()


def grab(window: QObject, name: str) -> None:
        """抓图存盘到 docs/validation/assets/<name>.png。"""
        OUT.mkdir(parents=True, exist_ok=True)
        out = OUT / name
        # rootObjects()[0] 被 PyQt 包装成基类 QWindow，grabWindow 在 QQuickWindow 上
        quick = sip.cast(window, QQuickWindow)
        image = quick.grabWindow()
        if not image.save(str(out)):
                raise RuntimeError(f"截图保存失败：{out}")
        print(f"[shot] {out} ({image.width()}x{image.height()})")


def main() -> int:
        _ = QGuiApplication(["qml_shots"])
        patch_adb_boundary()

        controller = PanelController("/nonexistent/adb-for-shots")
        api = SettingsApi()
        engine = QQmlApplicationEngine()
        context = engine.rootContext()
        context.setContextProperty("ctrl", controller)
        context.setContextProperty("settingsApi", api)
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        if not engine.rootObjects():
                print("[fatal] Main.qml 加载失败", file=sys.stderr)
                return 2
        window = engine.rootObjects()[0]
        pump(400)
        grab(window, "qml-main.png")

        # 设置页默认态：走真实齿轮路径 push
        gear = window.findChild(QObject, "gearButton")
        if gear is None:
                print("[fatal] 找不到 gearButton", file=sys.stderr)
                return 2
        gear.click()
        pump(600)
        grab(window, "qml-settings.png")

        # g2 态：cornerMode=g2 + 滑块 72，Canvas 预览即时重绘
        page = window.findChild(QObject, "settingsPageQml")
        slider = window.findChild(QObject, "cornerSlider")
        if page is None or slider is None:
                print("[fatal] 找不到设置页/滑块", file=sys.stderr)
                return 2
        page.setProperty("cornerMode", "g2")
        slider.setProperty("value", 72)
        pump(400)
        grab(window, "qml-settings-g2.png")

        # 按测试同款顺序拆卸：先杀 QML 引擎（绑定不再重估），再停控制器，
        # 避免进程退出期 GC 顺序导致的 "ctrl of null" 绑定噪音。
        engine.deleteLater()
        pump(60)
        controller.shutdown()
        pump(40)
        return 0


if __name__ == "__main__":
        raise SystemExit(main())

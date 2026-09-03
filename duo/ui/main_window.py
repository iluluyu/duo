"""Duo panel: a quiet launcher for mirroring sessions.

Design language: Apple-flavoured restraint — one accent colour, hairline
separators, generous whitespace, no decorative chrome. The panel is a single
column: device status, app list, one option, one status line.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QFont, QIcon
from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QVBoxLayout,
        QWidget,
)

from duo.core.apps import Adb, app_info
from duo.core.devices import DeviceMonitor, poll_query

#: Small curated catalog; filtered against installed packages at startup.
APP_CATALOG: list[tuple[str, str]] = [
        ("不背单词", "cn.com.langeasy.LangEasyLexis"),
        ("哔哩哔哩", "tv.danmaku.bili"),
        ("微信", "com.tencent.mm"),
        ("WPS Office", "cn.wps.moffice_eng"),
        ("微信读书", "com.tencent.weread"),
]

STYLE = """
QMainWindow, QWidget#root { background: #F5F5F7; }
QLabel#title { color: #1D1D1F; font-size: 22px; font-weight: 600; }
QLabel#caption { color: #86868B; font-size: 12px; }
QLabel#section { color: #86868B; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QFrame#card { background: #FFFFFF; border-radius: 14px; border: 1px solid #ECECF0; }
QLabel#dot { border-radius: 5px; background: #D2D2D7; }
QLabel#dot[state="device"] { background: #30D158; }
QLabel#dot[state="offline"], QLabel#dot[state="unauthorized"] { background: #FF9F0A; }
QLabel#device-name { color: #1D1D1F; font-size: 14px; font-weight: 600; }
QPushButton#app-icon {
        background: transparent; border: none; border-radius: 14px;
        font-size: 18px; font-weight: 600; color: #C7C7CC;
}
QPushButton#app-icon:hover { background: #F0F0F3; }
QPushButton#app-icon:pressed { background: #E8E8ED; }
QPushButton#app-icon:disabled { color: #E5E5EA; }
QCheckBox { color: #1D1D1F; font-size: 13px; spacing: 10px; }
QCheckBox::indicator {
        width: 22px; height: 22px; border-radius: 11px;
        border: 1.5px solid #D2D2D7; background: #FFFFFF;
}
QCheckBox::indicator:checked {
        background: #0071E3; border-color: #0071E3;
        image: url(none);
}
QLabel#status { color: #86868B; font-size: 12px; }
"""

_STATE_TEXT = {
        "device": "在线",
        "offline": "离线",
        "unauthorized": "未授权 USB 调试",
        "recovery": "recovery 模式",
}


class _Bridge(QObject):
        """Marshals monitor callbacks into the Qt event loop."""

        devices_changed = pyqtSignal(object)
        apps_resolved = pyqtSignal(object)
        icon_ready = pyqtSignal(str, object)


def _resolve_installed(adb_binary: str, done: Callable[[set[str]], None]) -> None:
        """Background check of which catalog apps are installed."""

        def work() -> None:
                try:
                        result = subprocess.run(
                                [adb_binary, "shell", "pm list packages"],
                                capture_output=True,
                                text=True,
                                timeout=8,
                                check=False,
                        )
                        installed = {
                                line.removeprefix("package:").strip()
                                for line in result.stdout.splitlines()
                                if line.startswith("package:")
                        }
                except (OSError, subprocess.TimeoutExpired):
                        installed = set()
                done(installed)

        threading.Thread(target=work, daemon=True).start()


def _label(text: str, name: str) -> QLabel:
        """A QLabel with a QSS objectName set explicitly (stub-friendly)."""
        widget = QLabel(text)
        widget.setObjectName(name)
        return widget


class MainWindow(QMainWindow):
        """The Duo launcher panel."""

        def __init__(self, adb_binary: str) -> None:
                super().__init__()
                self._adb_binary = adb_binary
                self._bridge = _Bridge()
                self._bridge.devices_changed.connect(self._on_devices)
                self._bridge.apps_resolved.connect(self._on_apps)
                self._installed: set[str] | None = None
                self._icon_buttons: dict[str, QPushButton] = {}
                self._build_ui()

                self._monitor = DeviceMonitor(
                        on_change=self._bridge.devices_changed.emit,
                        query=poll_query(adb_binary),
                        poll_interval_s=2.0,
                )
                self._monitor.poll_now()
                self._monitor.start()
                self._bridge.icon_ready.connect(self._on_icon)
                _resolve_installed(adb_binary, self._bridge.apps_resolved.emit)

        # ---------------------------------------------------------------- UI

        def _build_ui(self) -> None:
                self.setWindowTitle("Duo")
                self.setFixedWidth(400)
                self.setMinimumHeight(520)

                root = QWidget()
                root.setObjectName("root")
                column = QVBoxLayout(root)
                column.setContentsMargins(24, 28, 24, 20)
                column.setSpacing(10)

                title = _label("Duo", "title")
                column.addWidget(title)
                column.addSpacing(12)

                column.addWidget(self._build_device_card())
                column.addSpacing(6)
                column.addWidget(self._build_apps_card())
                column.addSpacing(6)
                column.addWidget(self._build_option_card())
                column.addStretch(1)

                self._status = _label("就绪", "status")
                column.addWidget(self._status)

                self.setCentralWidget(root)
                self.setStyleSheet(STYLE)
                font = QFont()
                families = ["Inter", "SF Pro Text", "Segoe UI", "Noto Sans CJK SC", "sans-serif"]
                font.setFamilies(families)
                font.setPixelSize(13)
                self.setFont(font)

        def _card(self) -> tuple[QFrame, QVBoxLayout]:
                """A white rounded card with 18px inner padding."""
                card = QFrame()
                card.setObjectName("card")
                inner = QVBoxLayout(card)
                inner.setContentsMargins(18, 16, 18, 16)
                inner.setSpacing(12)
                return card, inner

        def _build_device_card(self) -> QFrame:
                card, inner = self._card()
                section = _label("设备", "section")
                inner.addWidget(section)

                row = QHBoxLayout()
                row.setSpacing(12)
                self._dot = _label("", "dot")
                self._dot.setFixedSize(10, 10)
                self._device_name = _label("未检测到设备", "device-name")
                self._device_state = _label("连接并开启 USB 调试", "caption")
                vbox = QVBoxLayout()
                vbox.setSpacing(2)
                vbox.addWidget(self._device_name)
                vbox.addWidget(self._device_state)
                row.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignVCenter)
                row.addLayout(vbox)
                row.addStretch(1)
                inner.addLayout(row)
                return card

        def _build_apps_card(self) -> QFrame:
                card, inner = self._card()
                inner.addWidget(_label("应用", "section"))
                row = QHBoxLayout()
                row.setSpacing(8)
                for label, package in APP_CATALOG:
                        button = QPushButton(label[0])
                        button.setObjectName("app-icon")
                        button.setFixedSize(56, 56)
                        button.setIconSize(QSize(46, 46))
                        button.setToolTip(label)
                        button.setEnabled(False)
                        button.clicked.connect(
                                lambda _=False, pkg=package, text=label: self._launch(pkg, text)
                        )
                        self._icon_buttons[package] = button
                        row.addWidget(button)
                row.addStretch(1)
                inner.addLayout(row)
                return card

        def _build_option_card(self) -> QFrame:
                card, inner = self._card()
                self._portrait = QCheckBox("竖屏窗口")
                inner.addWidget(self._portrait)
                return card

        # ------------------------------------------------------------ events

        def _on_devices(self, states: object) -> None:
                assert isinstance(states, dict)
                online = [s for s, state in states.items() if state == "device"]
                if online:
                        serial = online[0]
                        state = states[serial]
                        self._dot.setProperty("state", state)
                        self._device_name.setText(serial)
                        self._device_state.setText(_STATE_TEXT.get(state, state))
                else:
                        self._dot.setProperty("state", "")
                        self._device_name.setText("未检测到设备")
                        self._device_state.setText("连接并开启 USB 调试")
                # Property-based styling needs a re-polish.
                style = self._dot.style()
                if style is not None:
                        style.unpolish(self._dot)
                        style.polish(self._dot)
                enabled = bool(online)
                if self._installed is not None:
                        for package, button in self._icon_buttons.items():
                                button.setEnabled(enabled and package in self._installed)

        def _on_apps(self, installed: object) -> None:
                assert isinstance(installed, set)
                self._installed = installed
                self._on_devices(dict.fromkeys(self._monitor.online, "device"))
                present = [t for t, p in APP_CATALOG if p in installed]
                text = f"已安装：{', '.join(present)}" if present else "目录中无已安装应用"
                self._status.setText(text)
                self._load_icons()

        def _load_icons(self) -> None:
                """Resolve icons in the background (first run pulls APKs)."""
                installed = set(self._installed or set())

                def work() -> None:
                        serial = next(iter(self._monitor.online), None)
                        if not serial:
                                return
                        adb = Adb(self._adb_binary, serial)
                        for _, package in APP_CATALOG:
                                if package not in installed:
                                        continue
                                try:
                                        info = app_info(adb, package)
                                except Exception:
                                        continue
                                self._bridge.icon_ready.emit(package, info.icon_path)

                threading.Thread(target=work, daemon=True).start()

        def _on_icon(self, package: str, icon_path: object) -> None:
                """Swap a letter button for the real app icon."""
                button = self._icon_buttons.get(package)
                if button is None or icon_path is None:
                        return
                icon = QIcon(str(icon_path))
                if icon.isNull():
                        return
                button.setText("")
                button.setIcon(icon)

        def _launch(self, package: str, label: str) -> None:
                """Spawn `duo mirror` detached and report in the status line."""
                argv = [sys.executable, "-m", "duo", "mirror", "--app", package]
                if self._portrait.isChecked():
                        argv.append("--portrait")
                ok = subprocess.Popen(
                        [*argv, "--serial", next(iter(self._monitor.online), "")],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                )
                orientation = "竖屏" if self._portrait.isChecked() else "横屏"
                text = f"已启动 {label} · {orientation}" if ok else f"启动失败：{label}"
                self._status.setText(text)

        def closeEvent(self, event: QCloseEvent | None) -> None:
                """Stop polling on close."""
                self._monitor.stop()
                super().closeEvent(event)


def run_app() -> int:
        """Create the panel and run the Qt event loop."""
        app = QApplication(sys.argv)
        window = MainWindow("adb.exe")
        window.show()
        return app.exec()


if __name__ == "__main__":
        sys.exit(run_app())

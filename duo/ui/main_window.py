"""Duo panel: a quiet launcher for mirroring sessions.

Design language: Apple-flavoured restraint — one accent colour, hairline
separators, generous whitespace, no decorative chrome. The panel is a single
column: device status, app list, one option, one status line.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QFont, QIcon
from PyQt6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QToolButton,
        QVBoxLayout,
        QWidget,
)

from duo.core.apps import Adb, app_info
from duo.core.devices import DeviceMonitor, poll_query
from duo.core.paths import data_dir

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
QLabel#running-chip {
        background: #F0F0F3; color: #1D1D1F; font-size: 12px;
        border-radius: 11px; padding: 4px 6px 4px 12px;
}
QToolButton#chip-close { background: transparent; border: none; color: #86868B; font-size: 13px; }
QToolButton#chip-close:hover { color: #1D1D1F; }
QLabel#empty-hint { color: #C7C7CC; font-size: 12px; }
QLabel#status { color: #86868B; font-size: 12px; }
"""

_STATE_TEXT = {
        "device": "在线",
        "offline": "离线",
        "unauthorized": "未授权 USB 调试",
        "recovery": "recovery 模式",
}

#: Per-app portrait defaults (reading/vocabulary apps want a tall phone).
DEFAULT_PORTRAIT: dict[str, bool] = {
        "cn.com.langeasy.LangEasyLexis": True,
        "com.tencent.weread": True,
}


def _prefs_path() -> Path:
        return data_dir() / "gui_prefs.json"


def load_portrait_prefs() -> dict[str, bool]:
        """Read the persisted per-app portrait choices (missing = defaults)."""
        try:
                raw = json.loads(_prefs_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
                return dict(DEFAULT_PORTRAIT)
        saved = raw.get("portrait", {})
        merged = dict(DEFAULT_PORTRAIT)
        merged.update({k: bool(v) for k, v in saved.items()})
        return merged


def save_portrait_prefs(prefs: dict[str, bool]) -> None:
        """Persist the per-app portrait choices for the next run."""
        _prefs_path().parent.mkdir(parents=True, exist_ok=True)
        _prefs_path().write_text(
                json.dumps({"portrait": prefs}, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def build_launch_argv(package: str, serial: str, portrait: bool) -> list[str]:
        """The mirror argv for a panel launch.

        Every panel window gets the borderless chrome. Audio is always
        requested - the CLI arbitrates ownership (single capture) via the
        audio lock, so the panel stays out of that policy.
        """
        argv = [
                sys.executable,
                "-m",
                "duo",
                "mirror",
                "--app",
                package,
                "--serial",
                serial,
                "--chrome",
        ]
        if portrait:
                argv.append("--portrait")
        return argv


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
                self._portrait_prefs = load_portrait_prefs()
                self._sessions: dict[str, subprocess.Popen[bytes]] = {}
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

                # Reap dead sessions and refresh the running chips quietly.
                self._reaper = QTimer(self)
                self._reaper.setInterval(1200)
                self._reaper.timeout.connect(self._refresh_sessions)
                self._reaper.start()

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
                column.addWidget(self._build_running_card())
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
                        self._set_app_tooltip(button, label, package)
                        button.setEnabled(False)
                        button.clicked.connect(
                                lambda _=False, pkg=package, text=label: self._launch(pkg, text)
                        )
                        # Right-click toggles this app's portrait preference.
                        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                        toggle = self._toggle_portrait
                        button.customContextMenuRequested.connect(
                                lambda pos, b=button, pkg=package, text=label, fn=toggle: fn(
                                        b, pkg, text
                                )
                        )
                        self._icon_buttons[package] = button
                        row.addWidget(button)
                row.addStretch(1)
                inner.addLayout(row)
                return card

        def _set_app_tooltip(self, button: QPushButton, label: str, package: str) -> None:
                portrait = self._portrait_prefs.get(package, False)
                orientation = "竖屏" if portrait else "横屏"
                button.setToolTip(f"{label} · {orientation}（右键切换）")

        def _build_running_card(self) -> QFrame:
                """Live session chips with quiet close buttons."""
                card, inner = self._card()
                inner.addWidget(_label("运行中", "section"))
                self._running_row = QHBoxLayout()
                self._running_row.setSpacing(8)
                self._running_row.addWidget(_label("暂无窗口", "empty-hint"))
                inner.addLayout(self._running_row)
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
                """Spawn a chrome-clad mirror session and track it."""
                serial = next(iter(self._monitor.online), "")
                if not serial:
                        self._status.setText("设备未连接")
                        return
                self._reap_sessions()
                if package in self._sessions:
                        self._status.setText(f"{label} 已在运行")
                        return
                portrait = self._portrait_prefs.get(package, False)
                argv = build_launch_argv(package, serial, portrait)
                try:
                        proc = subprocess.Popen(
                                argv, start_new_session=True, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                        )
                except OSError as exc:
                        self._status.setText(f"启动失败：{label}（{exc}）")
                        return
                self._sessions[package] = proc
                self._refresh_sessions()
                orientation = "竖屏" if portrait else "横屏"
                self._status.setText(f"已启动 {label} · {orientation}")

        def _toggle_portrait(self, button: QPushButton, package: str, label: str) -> None:
                """Right-click on an app icon flips its remembered orientation."""
                now = not self._portrait_prefs.get(package, False)
                self._portrait_prefs[package] = now
                save_portrait_prefs(self._portrait_prefs)
                self._set_app_tooltip(button, label, package)
                orientation = "竖屏" if now else "横屏"
                self._status.setText(f"{label} 将以{orientation}启动")

        def _reap_sessions(self) -> None:
                """Drop sessions whose process has exited."""
                for package in [p for p, proc in self._sessions.items() if proc.poll() is not None]:
                        del self._sessions[package]

        def _refresh_sessions(self) -> None:
                """Redraw the running chips from the live session map."""
                self._reap_sessions()
                while self._running_row.count():
                        item = self._running_row.takeAt(0)
                        if item is None:
                                continue
                        widget = item.widget()
                        if widget is not None:
                                widget.deleteLater()
                if not self._sessions:
                        self._running_row.addWidget(_label("暂无窗口", "empty-hint"))
                        return
                labels = {pkg: label for label, pkg in APP_CATALOG}
                for package in self._sessions:
                        chip = QFrame()
                        chip.setObjectName("running-chip")
                        layout = QHBoxLayout(chip)
                        layout.setContentsMargins(0, 0, 6, 0)
                        layout.setSpacing(4)
                        text = _label(labels.get(package, package), "")
                        layout.addWidget(text)
                        close = QToolButton()
                        close.setObjectName("chip-close")
                        close.setText("✕")
                        close.clicked.connect(
                                lambda _=False, pkg=package: self._stop_session(pkg)
                        )
                        layout.addWidget(close)
                        self._running_row.addWidget(chip)
                self._running_row.addStretch(1)

        def _stop_session(self, package: str) -> None:
                """Terminate one session; the CLI's SIGTERM handler cleans up."""
                proc = self._sessions.get(package)
                if proc is None:
                        return
                proc.terminate()
                labels = {pkg: label for label, pkg in APP_CATALOG}
                self._status.setText(f"已关闭 {labels.get(package, package)}")

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

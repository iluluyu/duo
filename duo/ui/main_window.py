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
from PyQt6.QtGui import (
        QCloseEvent,
        QColor,
        QFont,
        QIcon,
        QKeySequence,
        QPainter,
        QPixmap,
        QResizeEvent,
        QShortcut,
)
from PyQt6.QtWidgets import (
        QApplication,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QScrollArea,
        QToolButton,
        QVBoxLayout,
        QWidget,
)

from duo.core.apps import Adb, AdbError, app_info
from duo.core.devices import DeviceMonitor, poll_query
from duo.core.engine import probe
from duo.core.paths import data_dir
from duo.core.settings import load_settings, resolve_adb_path
from duo.core.winproc import creation_flags
from duo.ui.settings_page import SettingsPage

#: Session key for whole-device mirroring (not an app package).
MIRROR_KEY = "__device_mirror__"


def package_to_label(package: str) -> str:
        """Human-ish fallback label for uncataloged packages."""
        tail = package.rsplit(".", 1)[-1]
        return tail[:1].upper() + tail[1:]


def elide_label(label: str, limit: int = 6) -> str:
        """Shorten a label to fit under a mini icon."""
        return label if len(label) <= limit else label[: limit - 1] + "…"

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
QPushButton#device-mirror {
        background: #FFFFFF; border: 1px solid #ECECF0; border-radius: 10px;
        color: #1D1D1F; font-size: 13px; padding: 9px 0;
}
QPushButton#device-mirror:hover { background: #F7F7FA; }
QPushButton#device-mirror:pressed { background: #F0F0F3; }
QLabel#running-chip {
        background: #F0F0F3; color: #1D1D1F; font-size: 12px;
        border-radius: 11px; padding: 4px 6px 4px 12px;
}
QToolButton#chip-close { background: transparent; border: none; color: #86868B; font-size: 13px; }
QToolButton#chip-close:hover { color: #1D1D1F; }
QScrollArea#all-apps { background: transparent; border: none; }
QWidget#all-apps-host { background: transparent; }
QToolButton#mini-icon {
        background: transparent; border: none; border-radius: 10px;
        font-size: 11px; color: #86868B; padding: 2px;
}
QToolButton#mini-icon:hover { background: #F0F0F3; }
QToolButton#mini-icon:pressed { background: #E8E8ED; }
QLabel#empty-hint { color: #C7C7CC; font-size: 12px; }
QLabel#status { color: #86868B; font-size: 12px; }
QToolButton#gear {
        background: transparent; border: none; border-radius: 10px;
        color: #86868B; font-size: 17px;
}
QToolButton#gear:hover { background: #F0F0F3; color: #1D1D1F; }
QToolButton#gear:pressed { background: #E8E8ED; }
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
        audio lock, so the panel stays out of that policy. Under PyInstaller
        ``sys.executable`` IS the frozen duo binary, so sessions spawn as
        ``Duo.exe mirror ...`` and route through the CLI entry.
        """
        frozen = getattr(sys, "frozen", False)
        argv = [sys.executable, *([] if frozen else ["-m", "duo"])]
        argv += [
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


def build_device_mirror_argv(serial: str) -> list[str]:
        """The argv for direct device mirroring (no virtual display)."""
        frozen = getattr(sys, "frozen", False)
        argv = [sys.executable, *([] if frozen else ["-m", "duo"])]
        argv += [
                "mirror",
                "--display",
                "mirror",
                "--serial",
                serial,
                "--chrome",
                "--title",
                "平板镜像",
        ]
        return argv


class _Bridge(QObject):
        """Marshals monitor callbacks into the Qt event loop."""

        devices_changed = pyqtSignal(object)
        apps_resolved = pyqtSignal(object)
        icon_ready = pyqtSignal(str, object)
        all_apps_ready = pyqtSignal(object)
        all_icon_ready = pyqtSignal(str, object, str)
        adb_resolved = pyqtSignal(str)


def _resolve_installed(adb_binary: str, done: Callable[[set[str]], None]) -> None:
        """Background check of which catalog apps are installed."""

        def work() -> None:
                try:
                        result = subprocess.run(
                                [adb_binary, "shell", "pm list packages"],
                                capture_output=True,
                                text=True,
                        encoding="utf-8",
                        errors="replace",
                                timeout=8,
                                check=False,
                                creationflags=creation_flags(),
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
                self._bridge.all_apps_ready.connect(self._on_all_apps)
                self._bridge.all_icon_ready.connect(self._on_all_icon)
                self._installed: set[str] | None = None
                self._icon_buttons: dict[str, QPushButton] = {}
                self._portrait_prefs = load_portrait_prefs()
                self._sessions: dict[str, subprocess.Popen[bytes]] = {}
                self._build_ui()
                self._bridge.adb_resolved.connect(self._on_adb_resolved)
                shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
                shortcut.activated.connect(self._open_settings)
                self._settings_shortcut = shortcut

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
                # Freely resizable: a sensible minimum, no locked width. The
                # grids below recompute their column counts on resize.
                self.setMinimumSize(QSize(360, 520))
                self.resize(420, 660)

                root = QWidget()
                root.setObjectName("root")
                column = QVBoxLayout(root)
                column.setContentsMargins(24, 28, 24, 20)
                column.setSpacing(10)

                title_row = QHBoxLayout()
                title_row.addWidget(_label("Duo", "title"))
                title_row.addStretch(1)
                title_row.addWidget(self._build_gear_button())
                column.addLayout(title_row)
                column.addSpacing(12)

                column.addWidget(self._build_device_card())
                column.addSpacing(6)
                column.addWidget(self._build_apps_card())
                column.addSpacing(6)
                column.addWidget(self._build_all_apps_card())
                column.addSpacing(6)
                column.addWidget(self._build_running_card())
                column.addSpacing(6)
                column.addWidget(self._build_mirror_card())
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

        def _build_gear_button(self) -> QToolButton:
                """Top-right settings entry: glyph, tooltip, ≥32 DIP click target."""
                gear = QToolButton()
                gear.setObjectName("gear")
                gear.setText("⚙")
                gear.setToolTip("设置（Ctrl+,）")
                gear.setAccessibleName("设置")
                gear.setFixedSize(34, 34)
                gear.clicked.connect(self._open_settings)
                return gear

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

        def _build_mirror_card(self) -> QFrame:
                """Direct whole-device mirroring, outside the app grid."""
                card, inner = self._card()
                button = QPushButton("镜像设备屏幕")
                button.setObjectName("device-mirror")
                button.clicked.connect(self._launch_device_mirror)
                inner.addWidget(button)
                return card

        def _build_all_apps_card(self) -> QFrame:
                """Every user-installed app: letter avatars first, real icons
                resolving in the background (APK pull + aapt2, cached)."""
                card, inner = self._card()
                inner.addWidget(_label("全部应用", "section"))
                self._all_grid_host = QWidget()
                self._all_grid_host.setObjectName("all-apps-host")
                self._all_grid = QGridLayout(self._all_grid_host)
                self._all_grid.setContentsMargins(0, 0, 0, 0)
                self._all_grid.setSpacing(8)
                self._all_buttons: dict[str, QToolButton] = {}
                self._all_packages: list[str] = []
                self._all_per_row = 0
                scroll = QScrollArea()
                scroll.setObjectName("all-apps")
                scroll.setWidgetResizable(True)
                scroll.setFixedHeight(190)
                scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                scroll.setWidget(self._all_grid_host)
                inner.addWidget(scroll)
                hint = _label("字母头像为占位，图标后台解析中…", "caption")
                self._all_hint = hint
                inner.addWidget(hint)
                return card

        @staticmethod
        def _columns_for(width: int, cell: int, minimum: int, maximum: int) -> int:
                """Column count that fits ``width`` for ``cell``-wide items."""
                usable = max(0, width - 24)   # card inner margins
                per_row = max(minimum, usable // cell)
                return min(per_row, maximum)

        def _relayout_all_grid(self) -> None:
                """Re-wrap the all-apps grid for the current panel width."""
                if not self._all_buttons:
                        return
                per_row = self._columns_for(self._all_grid_host.width(), 58, 4, 10)
                if per_row == self._all_per_row:
                        return
                self._all_per_row = per_row
                while self._all_grid.count():
                        item = self._all_grid.takeAt(0)
                        widget = item.widget() if item is not None else None
                        if widget is not None:
                                widget.setParent(self._all_grid_host)
                for index, package in enumerate(self._all_packages):
                        button = self._all_buttons.get(package)
                        if button is not None:
                                self._all_grid.addWidget(
                                        button, index // per_row, index % per_row
                                )

        def _set_app_tooltip(self, button: QPushButton, label: str, package: str) -> None:
                portrait = self._portrait_prefs.get(package, False)
                orientation = "竖屏" if portrait else "横屏"
                button.setToolTip(f"{label} · {orientation}（右键切换）")

        def _build_running_card(self) -> QFrame:
                """Live session chips with quiet close buttons, wrapping rows."""
                card, inner = self._card()
                inner.addWidget(_label("运行中", "section"))
                self._running_host = QWidget()
                self._running_host.setObjectName("all-apps-host")
                self._running_grid = QGridLayout(self._running_host)
                self._running_grid.setContentsMargins(0, 0, 0, 0)
                self._running_grid.setSpacing(8)
                inner.addWidget(self._running_host)
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
                self._load_all_apps()

        def _load_all_apps(self) -> None:
                """Query every third-party package, then resolve icons lazily."""

                def work() -> None:
                        serial = next(iter(self._monitor.online), None)
                        if not serial:
                                return
                        adb = Adb(self._adb_binary, serial)
                        try:
                                packages = adb.third_party_packages()
                        except (AdbError, OSError):
                                return
                        self._bridge.all_apps_ready.emit(packages)
                        # Sequential background resolution: real icon + label
                        # per app (cached in the data dir after first pass).
                        for package in packages:
                                try:
                                        info = app_info(adb, package)
                                except Exception:
                                        continue
                                self._bridge.all_icon_ready.emit(
                                        package, info.icon_path, info.label
                                )

                threading.Thread(target=work, daemon=True).start()

        def _on_all_apps(self, packages: object) -> None:
                """Create letter-avatar buttons (placeholders); layout happens
                in _relayout_all_grid so column count follows the width."""
                assert isinstance(packages, list)
                self._all_packages = list(packages)
                for package in packages:
                        short = package.rsplit(".", 1)[-1][:2].upper()
                        label = package_to_label(package)
                        button = QToolButton()
                        button.setObjectName("mini-icon")
                        button.setFixedSize(46, 62)
                        button.setIconSize(QSize(34, 34))
                        button.setToolButtonStyle(
                                Qt.ToolButtonStyle.ToolButtonTextUnderIcon
                        )
                        button.setText(elide_label(label))
                        button.setToolTip(f"{label} · {package}")
                        button.clicked.connect(
                                lambda _=False, pkg=package, fn=self._launch: fn(
                                        pkg, package_to_label(pkg)
                                )
                        )
                        # The avatar "glyph" rides in the icon slot.
                        avatar = QPixmap(34, 34)
                        avatar.fill(Qt.GlobalColor.transparent)
                        painter = QPainter(avatar)
                        font = painter.font()
                        font.setPixelSize(15)
                        font.setBold(True)
                        painter.setFont(font)
                        painter.setPen(QColor(0xC7, 0xC7, 0xCC))
                        painter.drawText(avatar.rect(), Qt.AlignmentFlag.AlignCenter, short)
                        painter.end()
                        button.setIcon(QIcon(avatar))
                        self._all_buttons[package] = button
                self._relayout_all_grid()
                count = len(packages)
                self._all_hint.setText(
                        f"共 {count} 个应用 · 图标后台解析中（仅首次较慢）"
                )

        def _on_all_icon(self, package: str, icon_path: object, label: str) -> None:
                """Swap a letter avatar for the real icon + label."""
                button = self._all_buttons.get(package)
                if button is None or icon_path is None:
                        return
                icon = QIcon(str(icon_path))
                if icon.isNull():
                        return
                button.setIcon(icon)
                button.setText(elide_label(label))
                button.setToolTip(f"{label} · {package}")

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
                                creationflags=creation_flags(),
                        )
                except OSError as exc:
                        self._status.setText(f"启动失败：{label}（{exc}）")
                        return
                self._sessions[package] = proc
                self._refresh_sessions()
                orientation = "竖屏" if portrait else "横屏"
                self._status.setText(f"已启动 {label} · {orientation}")

        def _launch_device_mirror(self) -> None:
                """Start whole-device mirroring (physical display, no app)."""
                serial = next(iter(self._monitor.online), "")
                if not serial:
                        self._status.setText("设备未连接")
                        return
                self._reap_sessions()
                if MIRROR_KEY in self._sessions:
                        self._status.setText("设备镜像已在运行")
                        return
                argv = build_device_mirror_argv(serial)
                try:
                        proc = subprocess.Popen(
                                argv, start_new_session=True, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=creation_flags(),
                        )
                except OSError as exc:
                        self._status.setText(f"启动失败：设备镜像（{exc}）")
                        return
                self._sessions[MIRROR_KEY] = proc
                self._refresh_sessions()
                self._status.setText("已启动 设备镜像")

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
                while self._running_grid.count():
                        item = self._running_grid.takeAt(0)
                        if item is None:
                                continue
                        widget = item.widget()
                        if widget is not None:
                                widget.deleteLater()
                if not self._sessions:
                        self._running_grid.addWidget(_label("暂无窗口", "empty-hint"), 0, 0)
                        return
                labels = {pkg: label for label, pkg in APP_CATALOG}
                labels[MIRROR_KEY] = "设备镜像"
                per_row = self._columns_for(self._running_host.width(), 150, 1, 6)
                for index, package in enumerate(self._sessions):
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
                        self._running_grid.addWidget(
                                chip, index // per_row, index % per_row
                        )

        def _stop_session(self, package: str) -> None:
                """Terminate one session; the CLI's SIGTERM handler cleans up."""
                proc = self._sessions.get(package)
                if proc is None:
                        return
                proc.terminate()
                labels = {pkg: label for label, pkg in APP_CATALOG}
                labels[MIRROR_KEY] = "设备镜像"
                self._status.setText(f"已关闭 {labels.get(package, package)}")

        # ---------------------------------------------------------- settings

        def active_session_count(self) -> int:
                """Live mirror sessions; drives the settings page engine lock."""
                self._reap_sessions()
                return len(self._sessions)

        def _make_settings_page(self) -> SettingsPage:
                """Build the settings page (locked while sessions are running)."""
                return SettingsPage(
                        engine_locked=self.active_session_count() > 0, parent=self
                )

        def _open_settings(self) -> None:
                """Open settings; on save, refresh the panel for the next session."""
                page = self._make_settings_page()
                if page.exec() == SettingsPage.DialogCode.Accepted:
                        self._refresh_after_settings()

        def _refresh_after_settings(self) -> None:
                """Re-resolve adb from the saved settings without blocking the UI."""
                settings, problems = load_settings()

                def work() -> None:
                        adb = resolve_adb_path(settings, probe("adb").path, "adb.exe")
                        self._bridge.adb_resolved.emit(adb)

                threading.Thread(target=work, daemon=True).start()
                if problems:
                        self._status.setText(problems[0])

        def _on_adb_resolved(self, adb: str) -> None:
                """Swap the device monitor to the newly resolved adb, if it moved."""
                if adb == self._adb_binary:
                        self._status.setText("设置已保存，新会话生效")
                        return
                self._adb_binary = adb
                self._monitor.stop()
                self._monitor = DeviceMonitor(
                        on_change=self._bridge.devices_changed.emit,
                        query=poll_query(adb),
                        poll_interval_s=2.0,
                )
                self._monitor.poll_now()
                self._monitor.start()
                _resolve_installed(adb, self._bridge.apps_resolved.emit)
                self._status.setText("设置已保存，已切换 adb，新会话生效")

        def resizeEvent(self, event: QResizeEvent | None) -> None:
                """Re-wrap the grids when the panel is resized."""
                super().resizeEvent(event)
                self._relayout_all_grid()
                self._refresh_sessions()

        def closeEvent(self, event: QCloseEvent | None) -> None:
                """Stop polling on close."""
                self._monitor.stop()
                super().closeEvent(event)


def run_app() -> int:
        """Create the panel and run the Qt event loop."""
        app = QApplication(sys.argv)
        # Same resolution as the CLI: settings override > PATH probe > the
        # literal "adb.exe" fallback, so panel and spawned sessions share adb.
        settings, problems = load_settings()
        for problem in problems:
                print(f"settings: {problem}", file=sys.stderr)
        adb = resolve_adb_path(settings, probe("adb").path, "adb.exe")
        window = MainWindow(adb)
        window.show()
        return app.exec()


if __name__ == "__main__":
        sys.exit(run_app())

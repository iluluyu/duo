"""Duo panel: a quiet launcher for mirroring sessions.

Design language: Apple-flavoured restraint — one accent colour, hairline
separators, generous whitespace, no decorative chrome. The panel is a single
column: device status, app list, one option, one status line.

Since the QML migration the widgets panel owns rendering only: devices,
catalog, sessions, portrait prefs and adb resolution live in
:mod:`duo.ui.controller` and reach this window through one PanelController.
"""

from __future__ import annotations

import sys
from string import Template

from PyQt6.QtCore import QSize, Qt
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
        QGraphicsDropShadowEffect,
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

from duo.core.engine import probe
from duo.core.settings import load_settings, resolve_adb_path
from duo.ui.controller import (
        APP_CATALOG,
        DEFAULT_PORTRAIT,
        MIRROR_KEY,
        PanelController,
        build_device_mirror_argv,
        build_launch_argv,
        elide_label,
        load_portrait_prefs,
        package_to_label,
        save_portrait_prefs,
        session_label,
)
from duo.ui.settings_page import SettingsPage
from duo.ui.tokens import INK_3, QSS_TOKENS

#: Names this module used to define itself; they are single-sourced in
#: controller.py now. Re-exported here so widgets-era imports (tests,
#: scripts) keep working unchanged.
__all__ = [
        "APP_CATALOG",
        "DEFAULT_PORTRAIT",
        "MIRROR_KEY",
        "PanelController",
        "build_device_mirror_argv",
        "build_launch_argv",
        "elide_label",
        "load_portrait_prefs",
        "package_to_label",
        "save_portrait_prefs",
        "session_label",
]

_STYLE_TMPL = """
QMainWindow, QWidget#root { background: $bg; }
QLabel#title { color: $ink; font-size: 22px; font-weight: 600; }
QLabel#caption { color: $ink2; font-size: 12px; }
QFrame#card {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                    stop:0 $glassTop, stop:1 $glassBottom);
        border: 1px solid $glassBorder;
        border-radius: ${radiusCard}px;
}
QFrame#hairline { background: $hairline; max-height: 1px; border: none; }
QLabel#dot { border-radius: 5px; background: $ink3; }
QLabel#dot[state="device"] { background: $success; }
QLabel#dot[state="offline"], QLabel#dot[state="unauthorized"] { background: $warn; }
QLabel#device-name { color: $ink; font-size: 14px; font-weight: 600; }
QPushButton#app-icon {
        background: transparent; border: none; border-radius: 14px;
        font-size: 18px; font-weight: 600; color: $ink3;
}
QPushButton#app-icon:hover { background: $hoverWash; }
QPushButton#app-icon:pressed { background: $pressWash; }
QPushButton#app-icon:focus { background: $hoverWash; }
QPushButton#app-icon:disabled { color: $ink3; }
QPushButton#device-mirror {
        background: $accent; border: 1px solid transparent; border-radius: 12px;
        color: #FFFFFF; font-size: 14px; font-weight: 600; padding: 11px 0;
}
QPushButton#device-mirror:hover { background: $accentHover; }
QPushButton#device-mirror:pressed { background: $accentPress; }
QPushButton#device-mirror:focus { border-color: rgba(255, 255, 255, 190); }
QFrame#running-chip {
        background: $glassTop; border: 1px solid $glassBorder;
        border-radius: 18px;
}
QToolButton#chip-close {
        background: transparent; border: 1px solid transparent; border-radius: 16px;
        color: $ink2; font-size: 12px;
}
QToolButton#chip-close:hover { background: $pressWash; color: $ink; }
QToolButton#chip-close:focus { border-color: $accent; }
QScrollArea#all-apps { background: transparent; border: none; }
QWidget#all-apps-host, QWidget#running-zone { background: transparent; }
QToolButton#mini-icon {
        background: transparent; border: 1px solid transparent; border-radius: 12px;
        font-size: 11px; color: $ink2; padding: 2px;
}
QToolButton#mini-icon:hover { background: $hoverWash; }
QToolButton#mini-icon:pressed { background: $pressWash; }
QToolButton#mini-icon:focus { background: $hoverWash; border-color: $accent; }
QLabel#empty-hint { color: $ink3; font-size: 12px; }
QLabel#status { color: $ink2; font-size: 12px; }
QToolButton#gear {
        background: transparent; border: 1px solid transparent; border-radius: 17px;
        color: $ink2; font-size: 17px;
}
QToolButton#gear:hover { background: $hoverWash; color: $ink; }
QToolButton#gear:pressed { background: $pressWash; }
QToolButton#gear:focus { background: $hoverWash; border-color: $accent; }
"""
STYLE = Template(_STYLE_TMPL).substitute(QSS_TOKENS)


def _label(text: str, name: str) -> QLabel:
        """A QLabel with a QSS objectName set explicitly (stub-friendly)."""
        widget = QLabel(text)
        widget.setObjectName(name)
        return widget


class MainWindow(QMainWindow):
        """The Duo launcher panel (rendering only; logic in PanelController)."""

        def __init__(self, adb_binary: str) -> None:
                super().__init__()
                self._controller = PanelController(adb_binary, parent=self)
                self._installed: set[str] | None = None
                self._icon_buttons: dict[str, QPushButton] = {}
                self._build_ui()
                controller = self._controller
                controller.devicesChanged.connect(self._on_devices)
                controller.appsResolved.connect(self._on_apps)
                controller.iconReady.connect(self._on_icon)
                controller.allAppsReady.connect(self._on_all_apps)
                controller.appInfoReady.connect(self._on_all_icon)
                controller.statusChanged.connect(self._notify)
                controller.sessionsChanged.connect(self._refresh_sessions)
                shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
                shortcut.activated.connect(self._open_settings)
                self._settings_shortcut = shortcut
                # The controller started polling in its constructor, before
                # these slots were connected - render the current state once.
                self._on_devices(controller.devices)
                self._refresh_sessions()

        @property
        def _sessions(self):
                """Compat view of the live session map (controller-owned)."""
                return self._controller.sessions

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
                column.setContentsMargins(24, 28, 24, 18)
                column.setSpacing(10)

                title_row = QHBoxLayout()
                title_row.addWidget(_label("Duo", "title"))
                title_row.addStretch(1)
                title_row.addWidget(self._build_gear_button())
                column.addLayout(title_row)
                column.addSpacing(14)

                column.addWidget(self._build_device_card())
                column.addWidget(self._build_apps_card())
                column.addWidget(self._build_running_zone())
                column.addStretch(1)
                column.addWidget(self._build_mirror_action())

                # The status line exists only when something needs saying:
                # no persistent "ready" text (docs/window-experience.md §5).
                self._status = _label("", "status")
                self._status.hide()
                column.addWidget(self._status)

                self.setCentralWidget(root)
                self.setStyleSheet(STYLE)
                font = QFont()
                # Windows first per the design contract, then fallbacks.
                families = ["Segoe UI", "Inter", "SF Pro Text",
                            "Noto Sans CJK SC", "sans-serif"]
                font.setFamilies(families)
                font.setPixelSize(13)
                self.setFont(font)
                # Keyboard focus lands on the primary action at launch, not
                # on the gear (a resting focus ring up there is pure noise).
                self._mirror_button.setFocus()

        @staticmethod
        def _apply_shadow(widget: QWidget) -> None:
                """One soft static shadow; nothing animates (§5)."""
                effect = QGraphicsDropShadowEffect(widget)
                effect.setBlurRadius(18)
                effect.setOffset(0, 5)
                effect.setColor(QColor(0, 0, 0, 26))
                widget.setGraphicsEffect(effect)

        def _card(self) -> tuple[QFrame, QVBoxLayout]:
                """A glass capsule: smoky translucent white, bright hairline,
                light shadow."""
                card = QFrame()
                card.setObjectName("card")
                inner = QVBoxLayout(card)
                inner.setContentsMargins(18, 16, 18, 16)
                inner.setSpacing(12)
                self._apply_shadow(card)
                return card, inner

        @staticmethod
        def _hairline() -> QFrame:
                """A 1px quiet divider for use inside a card."""
                line = QFrame()
                line.setObjectName("hairline")
                line.setFrameShape(QFrame.Shape.NoFrame)
                line.setFixedHeight(1)
                return line

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
                """Device state speaks for itself: dot, serial, hint.
                No container title."""
                card, inner = self._card()
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
                """One container for all launches: the curated row, a
                hairline, then every user-installed app (letter avatars
                first, real icons resolving in the background, cached)."""
                card, inner = self._card()
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
                inner.addWidget(self._hairline())
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
                scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                scroll.setWidget(self._all_grid_host)
                self._all_scroll = scroll
                # Empty state until a device delivers the package list; the
                # scroll grows to fit rows (capped) once apps arrive.
                hint = _label("连接设备后显示全部应用", "empty-hint")
                self._all_hint = hint
                self._all_grid.addWidget(hint, 0, 0)
                scroll.setFixedHeight(40)
                inner.addWidget(scroll)
                return card

        def _build_mirror_action(self) -> QPushButton:
                """Direct whole-device mirroring: the panel's one accent
                moment, standing alone without a wrapper card."""
                button = QPushButton("镜像设备屏幕")
                button.setObjectName("device-mirror")
                button.setAccessibleName("镜像设备屏幕")
                button.clicked.connect(self._launch_device_mirror)
                self._apply_shadow(button)
                self._mirror_button = button
                return button

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
                # The viewport hugs the rows (capped): no tall blank glass.
                rows = -(-len(self._all_packages) // per_row)
                height = 6 + rows * 62 + (rows - 1) * 8
                self._all_scroll.setFixedHeight(min(190, height))

        def _set_app_tooltip(self, button: QPushButton, label: str, package: str) -> None:
                portrait = self._controller.portraitFor(package)
                orientation = "竖屏" if portrait else "横屏"
                button.setToolTip(f"{label} · {orientation}（右键切换）")

        def _build_running_zone(self) -> QFrame:
                """Live session chips as glass pills on the canvas. No
                container title: a pill with a close button explains itself,
                and the empty state stays visible while nothing runs."""
                zone = QFrame()
                zone.setObjectName("running-zone")
                inner = QVBoxLayout(zone)
                inner.setContentsMargins(2, 0, 2, 0)
                inner.setSpacing(6)
                self._running_host = QWidget()
                self._running_host.setObjectName("all-apps-host")
                self._running_grid = QGridLayout(self._running_host)
                self._running_grid.setContentsMargins(0, 0, 0, 0)
                self._running_grid.setSpacing(8)
                inner.addWidget(self._running_host)
                return zone

        # ------------------------------------------------------------ events

        def _on_devices(self, devices: object) -> None:
                assert isinstance(devices, list)
                entries = [entry for entry in devices if isinstance(entry, dict)]
                online = [entry for entry in entries if entry.get("online")]
                if online:
                        head = online[0]
                        state = str(head.get("state", ""))
                        self._dot.setProperty("state", state)
                        self._device_name.setText(str(head.get("serial", "")))
                        self._device_state.setText(str(head.get("stateText", state)))
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

        def _notify(self, text: str) -> None:
                """Show the status line only now that it has something to say."""
                self._status.setText(text)
                self._status.setVisible(True)

        def _on_apps(self, installed: object) -> None:
                assert isinstance(installed, set)
                self._installed = installed
                # Status text and icon resolution are controller-side now;
                # the panel only refreshes button availability.
                self._on_devices(self._controller.devices)

        def _on_all_apps(self, packages: object) -> None:
                """Create letter-avatar buttons (placeholders); layout happens
                in _relayout_all_grid so column count follows the width."""
                assert isinstance(packages, list)
                for button in self._all_buttons.values():
                        button.deleteLater()
                self._all_buttons.clear()
                self._all_packages = list(packages)
                self._all_per_row = 0
                if not packages:
                        # True empty state only; no permanent resolution chatter.
                        self._all_hint.setText("未发现第三方应用")
                        self._all_hint.setVisible(True)
                        self._all_grid.addWidget(self._all_hint, 0, 0)
                        self._all_scroll.setFixedHeight(40)
                        return
                self._all_hint.setVisible(False)
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
                        painter.setPen(QColor(INK_3))
                        painter.drawText(avatar.rect(), Qt.AlignmentFlag.AlignCenter, short)
                        painter.end()
                        button.setIcon(QIcon(avatar))
                        self._all_buttons[package] = button
                self._relayout_all_grid()

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
                """Spawn a session via the controller (labels are derived there)."""
                self._controller.startSession(package)

        def _launch_device_mirror(self) -> None:
                """Start whole-device mirroring via the controller."""
                self._controller.startMirror()

        def _toggle_portrait(self, button: QPushButton, package: str, label: str) -> None:
                """Right-click on an app icon flips its remembered orientation."""
                self._controller.togglePortrait(package)
                self._set_app_tooltip(button, label, package)

        def _refresh_sessions(self, sessions: object = None) -> None:
                """Redraw the running chips from the controller's session list."""
                entries = (
                        sessions if isinstance(sessions, list)
                        else self._controller.runningSessions
                )
                while self._running_grid.count():
                        item = self._running_grid.takeAt(0)
                        if item is None:
                                continue
                        widget = item.widget()
                        if widget is not None:
                                widget.deleteLater()
                if not entries:
                        self._running_grid.addWidget(_label("暂无窗口", "empty-hint"), 0, 0)
                        return
                per_row = self._columns_for(self._running_host.width(), 150, 1, 6)
                for index, entry in enumerate(entries):
                        chip = QFrame()
                        chip.setObjectName("running-chip")
                        chip.setFixedHeight(36)
                        layout = QHBoxLayout(chip)
                        layout.setContentsMargins(14, 0, 4, 0)
                        layout.setSpacing(2)
                        name = str(entry["label"])
                        text = _label(name, "")
                        layout.addWidget(text)
                        key = str(entry["key"])
                        close = QToolButton()
                        close.setObjectName("chip-close")
                        close.setText("✕")
                        close.setFixedSize(32, 32)
                        close.setToolTip(f"关闭 {name}")
                        close.setAccessibleName(f"关闭 {name}")
                        close.clicked.connect(
                                lambda _=False, k=key: self._controller.stopSession(k)
                        )
                        layout.addWidget(close)
                        self._running_grid.addWidget(
                                chip, index // per_row, index % per_row
                        )

        # ---------------------------------------------------------- settings

        def active_session_count(self) -> int:
                """Live mirror sessions; drives the settings page engine lock."""
                return self._controller.activeSessionCount()

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
                """Re-resolve adb from the saved settings (controller owns the swap)."""
                self._controller.resolveAdb()

        def resizeEvent(self, event: QResizeEvent | None) -> None:
                """Re-wrap the grids when the panel is resized."""
                super().resizeEvent(event)
                self._relayout_all_grid()
                self._refresh_sessions()

        def closeEvent(self, event: QCloseEvent | None) -> None:
                """Stop polling on close."""
                self._controller.shutdown()
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

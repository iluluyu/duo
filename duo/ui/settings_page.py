"""Settings page: engine paths, quality numbers, and window appearance.

Two groups only (docs/window-experience.md §4.3): 引擎 and 外观. The page
opens with whatever ``load_settings()`` returns (it must open even when the
engine is missing), shows load problems inline, validates before saving
(problems stay on the page - never raised), and discards everything on
cancel. Saves take effect for the next mirror session; the small preview
refreshes the corner appearance immediately.

Tool "检测" runs in a background thread (the ``_resolve_installed`` pattern
of the panel) and reports back on the UI thread. While a mirror session is
running, the engine path rows are locked with a "close sessions first" hint
so an old adb and a new adb cannot fight over the server.
"""

from __future__ import annotations

import threading
from string import Template

from PyQt6.QtCore import QObject, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPaintEvent, QPen
from PyQt6.QtWidgets import (
        QCheckBox,
        QDialog,
        QFileDialog,
        QFrame,
        QGraphicsDropShadowEffect,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSlider,
        QSpinBox,
        QToolButton,
        QVBoxLayout,
        QWidget,
)

from duo.core.engine import ToolInfo, probe, probe_binary
from duo.core.settings import (
        BITRATE_RANGE,
        CORNER_RANGE,
        DPI_RANGE,
        FPS_RANGE,
        Settings,
        load_settings,
        save_settings,
        validate,
)
from duo.ui.tokens import DANGER, INK_2, QSS_TOKENS, SUCCESS, WARN

#: Corner modes in display order: (settings value, radio label).
CORNER_MODES: tuple[tuple[str, str], ...] = (
        ("system", "system · Windows 系统圆角"),
        ("g2", "g2（实验）"),
        ("none", "none · 直角"),
)

#: Value shown in the DPI box while "自动" is checked (no custom density).
_DPI_PLACEHOLDER = 480

_STYLE_TMPL = """
QDialog { background: $bg; }
QWidget#settings-content { background: transparent; }
QLabel#page-title { color: $ink; font-size: 20px; font-weight: 600; }
QLabel#caption { color: $ink2; font-size: 12px; }
QLabel#problems { color: $danger; font-size: 12px; }
QToolButton#back {
        background: $glassTop; border: 1px solid $glassBorder;
        border-radius: 15px; color: $ink; font-size: 13px; padding: 6px 14px;
}
QToolButton#back:hover { background: #FFFFFF; }
QToolButton#back:pressed { background: $hoverWash; }
QToolButton#back:focus { border-color: $accent; }
QGroupBox {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                    stop:0 $glassTop, stop:1 $glassBottom);
        border: 1px solid $glassBorder; border-radius: ${radiusCard}px;
        margin-top: 0; padding: 34px 14px 14px 14px;
        font-size: 11px; font-weight: 600; color: $ink2; letter-spacing: 1px;
}
QGroupBox::title {
        subcontrol-origin: padding; subcontrol-position: top left;
        left: 2px; top: 12px;
}
QLineEdit, QSpinBox {
        background: #FFFFFF; border: 1px solid $hairline; border-radius: 8px;
        color: $ink; padding: 4px 8px; min-height: 18px; font-size: 13px;
        selection-background-color: $accent; selection-color: #FFFFFF;
}
QLineEdit:focus, QSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QSpinBox:disabled { background: $bg; color: $ink3; }
QPushButton#secondary {
        background: $glassTop; border: 1px solid $hairline;
        border-radius: 8px; color: $ink; padding: 6px 14px;
}
QPushButton#secondary:hover { background: #FFFFFF; }
QPushButton#secondary:pressed { background: $hoverWash; }
QPushButton#secondary:focus { border-color: $accent; }
QPushButton#primary {
        background: $accent; border: 1px solid transparent; border-radius: 8px;
        color: #FFFFFF; padding: 6px 20px; font-weight: 600;
}
QPushButton#primary:hover { background: $accentHover; }
QPushButton#primary:pressed { background: $accentPress; }
QPushButton#primary:focus { border-color: $ink; }
QCheckBox, QRadioButton { color: $ink; spacing: 7px; }
QCheckBox::indicator, QRadioButton::indicator { width: 17px; height: 17px; }
QCheckBox::indicator {
        border: 1px solid $hairline; border-radius: 5px; background: #FFFFFF;
}
QRadioButton::indicator {
        border: 1px solid $hairline; border-radius: 9px; background: #FFFFFF;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: $ink3; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QRadioButton::indicator:checked {
        border-color: $accent;
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                    stop:0 #FFFFFF, stop:0.42 #FFFFFF,
                                    stop:0.52 $accent, stop:1 $accent);
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
        background: $bg; border-color: $hairline;
}
QSlider::groove:horizontal { height: 4px; background: rgba(0, 0, 0, 34); border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal {
        width: 18px; height: 18px; margin: -7px 0; border-radius: 9px;
        background: #FFFFFF; border: 1px solid $hairline;
}
QSlider::handle:horizontal:hover { border-color: $accent; }
QSlider::sub-page:horizontal:disabled { background: $ink3; }
QSlider::handle:horizontal:disabled { background: $bg; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical {
        background: rgba(0, 0, 0, 38); border-radius: 3px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: rgba(0, 0, 0, 64); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
STYLE = Template(_STYLE_TMPL).substitute(QSS_TOKENS)


def _label(text: str, name: str) -> QLabel:
        """A QLabel with a QSS objectName set explicitly (same as the panel)."""
        widget = QLabel(text)
        widget.setObjectName(name)
        return widget


class _ProbeBridge(QObject):
        """Delivers background tool-probe results back to the UI thread."""

        done = pyqtSignal(str, object)       # tool name, ToolInfo


class CornerPreview(QWidget):
        """Paints the current corner appearance; refreshes on every change."""

        def __init__(self, parent: QWidget | None = None) -> None:
                super().__init__(parent)
                self._mode = "system"
                self._size = 48
                self._glass = True
                self.setMinimumSize(170, 108)
                self.setAccessibleName("圆角外观预览")

        def set_appearance(self, mode: str, size_dip: int, glass: bool) -> None:
                """Update the painted sample (cheap enough for every tick)."""
                self._mode = mode
                self._size = size_dip
                self._glass = glass
                self.update()

        def paintEvent(self, event: QPaintEvent | None) -> None:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                body = self.rect().adjusted(16, 10, -16, -30)
                radius = self._corner_radius_px(body.width(), body.height())
                path = QPainterPath()
                path.addRoundedRect(QRectF(body), radius, radius)
                # Same smoky glass the panel capsules use; opaque fallback
                # when the style switch is off (§5 high-contrast story).
                fill = QColor(255, 255, 255, 228) if self._glass else QColor(240, 240, 243)
                painter.fillPath(path, fill)
                painter.setPen(QPen(QColor(0, 0, 0, 24), 1))
                painter.drawPath(path)
                captions = {
                        "system": "system · Windows 系统圆角",
                        "g2": f"g2 · {self._size} DIP",
                        "none": "none · 直角",
                }
                painter.setPen(QColor(INK_2))
                painter.drawText(
                        self.rect().adjusted(0, 0, 0, -10),
                        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                        captions.get(self._mode, self._mode),
                )
                painter.end()

        def _corner_radius_px(self, width: int, height: int) -> int:
                """Preview radius for the mode; g2 dips clamp into the box."""
                if self._mode == "system":
                        return 10           # small hint of Windows' own rounding
                if self._mode == "g2":
                        return min(self._size, max(0, min(width, height) // 2))
                return 0


class SettingsPage(QDialog):
        """Modal settings dialog: load on open, save validates, cancel discards."""

        def __init__(self, engine_locked: bool = False,
                     parent: QWidget | None = None) -> None:
                super().__init__(parent)
                self.setWindowTitle("设置")
                self.setModal(True)
                self.setMinimumSize(420, 520)
                self.engine_locked = engine_locked
                self._edits: dict[str, QLineEdit] = {}
                self._detect_buttons: dict[str, QPushButton] = {}
                self._statuses: dict[str, QLabel] = {}
                self._corner_radios: dict[str, QRadioButton] = {}
                self._bridge = _ProbeBridge()
                self._bridge.done.connect(self._on_probe_done)
                self._build_ui()
                self._load()
                font = QFont()
                # Same family stack as the panel (Segoe UI first on Windows).
                families = ["Segoe UI", "Inter", "SF Pro Text",
                            "Noto Sans CJK SC", "sans-serif"]
                font.setFamilies(families)
                font.setPixelSize(13)
                self.setFont(font)

        @staticmethod
        def _apply_shadow(widget: QWidget) -> None:
                """The one light shadow shared with the panel containers."""
                effect = QGraphicsDropShadowEffect(widget)
                effect.setBlurRadius(18)
                effect.setOffset(0, 5)
                effect.setColor(QColor(0, 0, 0, 26))
                widget.setGraphicsEffect(effect)

        # ---------------------------------------------------------------- UI

        def _build_ui(self) -> None:
                outer = QVBoxLayout(self)
                outer.setContentsMargins(20, 16, 20, 16)
                outer.setSpacing(10)

                top = QHBoxLayout()
                back = QToolButton()
                back.setObjectName("back")
                back.setText("‹ 返回")
                back.setToolTip("返回主面板（不保存）")
                back.setAccessibleName("返回")
                back.clicked.connect(self.reject)
                top.addWidget(back)
                top.addStretch(1)
                top.addWidget(_label("设置", "page-title"))
                top.addStretch(1)
                outer.addLayout(top)

                self._problems = QLabel("")
                self._problems.setObjectName("problems")
                self._problems.setWordWrap(True)
                self._problems.hide()
                outer.addWidget(self._problems)

                # Narrow windows scroll in one column (§4.3); no side nav.
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.Shape.NoFrame)
                content = QWidget()
                content.setObjectName("settings-content")
                column = QVBoxLayout(content)
                column.setContentsMargins(0, 0, 0, 0)
                column.setSpacing(10)
                column.addWidget(self._build_engine_group())
                column.addWidget(self._build_appearance_group())
                column.addStretch(1)
                scroll.setWidget(content)
                outer.addWidget(scroll, 1)

                bottom = QHBoxLayout()
                bottom.addStretch(1)
                cancel = QPushButton("取消")
                cancel.setObjectName("secondary")
                cancel.setAccessibleName("取消")
                cancel.clicked.connect(self.reject)
                bottom.addWidget(cancel)
                save = QPushButton("保存")
                save.setObjectName("primary")
                save.setAccessibleName("保存设置")
                save.setDefault(True)
                save.clicked.connect(self._save)
                bottom.addWidget(save)
                outer.addLayout(bottom)

                self.setStyleSheet(STYLE)

        def _build_engine_group(self) -> QGroupBox:
                group = QGroupBox("引擎")
                inner = QVBoxLayout(group)
                inner.setSpacing(6)
                self._apply_shadow(group)

                self._path_row(inner, "scrcpy")
                self._path_row(inner, "adb")

                # Engine path swaps are forbidden mid-session (server wars).
                self._lock_hint = QLabel("镜像会话运行中，引擎路径暂不可改（先关闭会话）")
                self._lock_hint.setObjectName("caption")
                self._lock_hint.setStyleSheet(f"color: {WARN};")
                self._lock_hint.setVisible(self.engine_locked)
                inner.addWidget(self._lock_hint)

                numbers = QHBoxLayout()
                numbers.setSpacing(14)
                self._fps = self._number_cell(numbers, "FPS", FPS_RANGE, "最大帧率")
                self._bitrate = self._number_cell(numbers, "码率 Mbps",
                                                  BITRATE_RANGE, "视频码率")
                numbers.addLayout(self._dpi_cell())
                inner.addLayout(numbers)
                return group

        def _path_row(self, inner: QVBoxLayout, tool: str) -> None:
                """A path row: text box + browse + detect + inline status."""
                inner.addWidget(_label(f"{tool} 路径", "caption"))
                edit = QLineEdit()
                edit.setAccessibleName(f"{tool} 路径")
                edit.setPlaceholderText("留空自动探测")
                browse = QPushButton("浏览…")
                browse.setObjectName("secondary")
                browse.setAccessibleName(f"浏览 {tool} 路径")
                browse.clicked.connect(
                        lambda _=False, e=edit, t=tool: self._browse(e, t)
                )
                detect = QPushButton("检测")
                detect.setObjectName("secondary")
                detect.setAccessibleName(f"检测 {tool}")
                detect.clicked.connect(lambda _=False, t=tool: self._start_probe(t))
                row = QHBoxLayout()
                row.setSpacing(6)
                row.addWidget(edit, 1)
                row.addWidget(browse)
                row.addWidget(detect)
                inner.addLayout(row)
                status = QLabel("")
                status.setObjectName("caption")
                inner.addWidget(status)
                self._edits[tool] = edit
                self._detect_buttons[tool] = detect
                self._statuses[tool] = status
                if self.engine_locked:
                        for widget in (edit, browse, detect):
                                widget.setEnabled(False)

        def _number_cell(self, row: QHBoxLayout, title: str, bounds: tuple[int, int],
                         accessible: str) -> QSpinBox:
                """A caption-over-spinbox cell with the settings.py range."""
                cell = QVBoxLayout()
                cell.setSpacing(2)
                cell.addWidget(_label(title, "caption"))
                spin = QSpinBox()
                spin.setRange(*bounds)
                spin.setAccessibleName(accessible)
                cell.addWidget(spin)
                row.addLayout(cell)
                return spin

        def _dpi_cell(self) -> QVBoxLayout:
                """DPI is nullable: the 自动 checkbox stands for no custom dpi."""
                cell = QVBoxLayout()
                cell.setSpacing(2)
                head = QHBoxLayout()
                head.setSpacing(6)
                head.addWidget(_label("DPI", "caption"))
                self._dpi_auto = QCheckBox("自动")
                self._dpi_auto.setAccessibleName("DPI 自动")
                self._dpi_auto.setToolTip("勾选时密度由显示推荐决定")
                head.addWidget(self._dpi_auto)
                head.addStretch(1)
                cell.addLayout(head)
                self._dpi = QSpinBox()
                self._dpi.setRange(*DPI_RANGE)
                self._dpi.setAccessibleName("DPI")
                cell.addWidget(self._dpi)
                self._dpi_auto.toggled.connect(self._dpi.setDisabled)
                return cell

        def _build_appearance_group(self) -> QGroupBox:
                group = QGroupBox("外观")
                inner = QVBoxLayout(group)
                inner.setSpacing(8)
                self._apply_shadow(group)

                modes = QHBoxLayout()
                modes.setSpacing(14)
                for value, text in CORNER_MODES:
                        radio = QRadioButton(text)
                        radio.toggled.connect(self._sync_appearance)
                        self._corner_radios[value] = radio
                        modes.addWidget(radio)
                modes.addStretch(1)
                inner.addLayout(modes)

                corner = QHBoxLayout()
                corner.setSpacing(8)
                self._corner_slider = QSlider(Qt.Orientation.Horizontal)
                self._corner_slider.setRange(*CORNER_RANGE)
                self._corner_slider.setAccessibleName("圆角大小")
                self._corner_value = _label("48 DIP", "caption")
                corner.addWidget(self._corner_slider, 1)
                corner.addWidget(self._corner_value)
                inner.addLayout(corner)

                self._glass = QCheckBox("液态玻璃风格")
                self._glass.setAccessibleName("液态玻璃风格")
                inner.addWidget(self._glass)

                self._preview = CornerPreview()
                inner.addWidget(self._preview)

                self._corner_slider.valueChanged.connect(self._sync_appearance)
                self._glass.toggled.connect(self._sync_appearance)
                return group

        # ------------------------------------------------------- load / save

        def _load(self) -> None:
                """Fill every field from load_settings(); surface problems once."""
                settings, problems = load_settings()
                self._edits["scrcpy"].setText(settings.scrcpy_path)
                self._edits["adb"].setText(settings.adb_path)
                self._fps.setValue(settings.fps or 90)
                self._bitrate.setValue(settings.bitrate_mbps or 30)
                self._dpi_auto.setChecked(settings.dpi is None)
                self._dpi.setValue(settings.dpi or _DPI_PLACEHOLDER)
                self._corner_radios[settings.corner_mode].setChecked(True)
                self._corner_slider.setValue(settings.corner_size_dip)
                self._glass.setChecked(settings.glass_enabled)
                self._show_problems(problems)
                self._sync_appearance()

        def _collect(self) -> Settings:
                """Current field values as a Settings instance."""
                return Settings(
                        scrcpy_path=self._edits["scrcpy"].text().strip(),
                        adb_path=self._edits["adb"].text().strip(),
                        fps=self._fps.value(),
                        bitrate_mbps=self._bitrate.value(),
                        dpi=None if self._dpi_auto.isChecked() else self._dpi.value(),
                        corner_mode=self._current_mode(),
                        corner_size_dip=self._corner_slider.value(),
                        glass_enabled=self._glass.isChecked(),
                )

        def _save(self) -> None:
                """Validate then persist; problems stay on the page (no raise)."""
                settings = self._collect()
                problems = validate(settings)
                if problems:
                        self._show_problems(problems)
                        return
                save_settings(settings)
                self.accept()

        def _show_problems(self, problems: list[str]) -> None:
                """One short banner for load/save problems; hidden when clean."""
                self._problems.setText("\n".join(problems))
                self._problems.setVisible(bool(problems))

        # ---------------------------------------------------------- preview

        def _current_mode(self) -> str:
                return next(
                        value for value, radio in self._corner_radios.items()
                        if radio.isChecked()
                )

        def _sync_appearance(self) -> None:
                """Slider/value/preview follow the mode (slider is g2-only)."""
                mode = self._current_mode()
                self._corner_slider.setEnabled(mode == "g2")
                self._corner_value.setText(f"{self._corner_slider.value()} DIP")
                self._preview.set_appearance(
                        mode, self._corner_slider.value(), self._glass.isChecked()
                )

        # -------------------------------------------------------- detection

        def _start_probe(self, tool: str) -> None:
                """Check the configured path off the UI thread (probe pattern)."""
                path = self._edits[tool].text().strip()
                status = self._statuses[tool]
                status.setText("检测中…")
                status.setStyleSheet("")
                bridge = self._bridge

                def work() -> None:
                        info = probe_binary(path, tool) if path else probe(tool)
                        bridge.done.emit(tool, info)

                threading.Thread(target=work, daemon=True).start()

        def _on_probe_done(self, tool: str, info: object) -> None:
                """Short inline result next to the field (runs on the UI thread)."""
                assert isinstance(info, ToolInfo)
                status = self._statuses[tool]
                if info.available:
                        status.setText(f"✓ {info.version}" if info.version else "✓ 可执行")
                        status.setStyleSheet(f"color: {SUCCESS};")
                elif self._edits[tool].text().strip():
                        status.setText("✗ 无法运行，请检查路径")
                        status.setStyleSheet(f"color: {DANGER};")
                else:
                        status.setText("✗ 未在 PATH 找到，可手动填写路径")
                        status.setStyleSheet(f"color: {DANGER};")

        def _browse(self, edit: QLineEdit, tool: str) -> None:
                path, _file_filter = QFileDialog.getOpenFileName(
                        self, f"选择 {tool} 可执行文件", edit.text().strip(),
                )
                if path:
                        edit.setText(path)

"""Panel UI behaviour: event-only status line, empty states, chip safety.

Runs headless via QT_QPA_PLATFORM=offscreen; skipped without PyQt6, like
test_settings_ui. Behaviour only - no pixel or stylesheet assertions.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QToolButton, QWidget  # noqa: E402

from duo.ui.main_window import MainWindow  # noqa: E402


class _FakeProc:
        """Duck-typed Popen: a session that never exits."""

        def poll(self) -> int | None:
                return None


@pytest.fixture()
def qapp():
        """Ensure exactly one QApplication exists (offscreen)."""
        app = QApplication.instance() or QApplication([])
        yield app


@pytest.fixture()
def panel(qapp):
        """A panel against a bogus adb: probes fail quietly, UI stays whole."""
        window = MainWindow("/nonexistent/adb-for-tests")
        yield window
        window.close()


def test_status_line_starts_hidden(panel):
        """No persistent "ready" text: the line exists only for events."""
        assert panel._status.isHidden()
        assert panel._status.text() == ""


def test_notify_surfaces_event_text(panel):
        """_notify shows the line exactly while there is something to say."""
        panel._notify("已启动 微信 · 横屏")
        assert not panel._status.isHidden()
        assert panel._status.text() == "已启动 微信 · 横屏"


def test_all_apps_empty_state_is_explicit(panel):
        """Without packages the grid shows a guidance hint, not blank glass."""
        panel._on_all_apps([])
        assert panel._all_hint.isVisibleTo(panel)
        assert panel._all_hint.text() == "未发现第三方应用"
        assert panel._all_buttons == {}


def test_section_titles_are_gone(panel):
        """The duplicated container titles (设备/应用/全部应用/运行中) are gone."""
        from PyQt6.QtWidgets import QLabel

        labels = [
                w.text()
                for w in panel.findChildren(QLabel)
                if w.objectName() == "section"
        ]
        assert labels == []


def test_running_chip_close_is_reachable(panel):
        """Chip close: tooltip, accessible name, and a >=32px click target."""
        panel._sessions["com.example.chip"] = _FakeProc()  # type: ignore[assignment]
        panel._refresh_sessions()
        host: QWidget = panel._running_host
        closes = [
                b for b in host.findChildren(QToolButton)
                if b.objectName() == "chip-close"
        ]
        assert len(closes) == 1
        close = closes[0]
        assert close.width() >= 32 and close.height() >= 32
        assert "关闭" in (close.toolTip() + close.accessibleName())

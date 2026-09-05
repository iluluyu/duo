"""Settings page UI: defaults load, save round trip, cancel, engine lock.

Runs headless via QT_QPA_PLATFORM=offscreen; skipped entirely when the gui
extra (PyQt6) is not installed, so the plain ``[dev]`` CI job stays green.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from duo.core.settings import Settings, load_settings  # noqa: E402
from duo.ui.settings_page import SettingsPage  # noqa: E402


@pytest.fixture()
def qapp():
        """Ensure exactly one QApplication exists (offscreen)."""
        app = QApplication.instance() or QApplication([])
        yield app


@pytest.fixture()
def settings_file(tmp_path, monkeypatch):
        """Point settings_path at a tmp file so tests never touch real prefs."""
        import duo.core.settings as settings_mod

        path = tmp_path / "s.json"
        monkeypatch.setattr(settings_mod, "settings_path", lambda: path)
        return path


def test_page_loads_defaults(settings_file, qapp):
        """A missing settings file fills the page with the documented defaults."""
        page = SettingsPage()
        assert page._fps.value() == 90
        assert page._bitrate.value() == 30
        assert page._dpi_auto.isChecked()
        assert not page._dpi.isEnabled()
        assert page._edits["scrcpy"].text() == ""
        assert page._corner_radios["system"].isChecked()
        assert page._corner_slider.value() == 48
        assert page._glass.isChecked()
        # system rounding is the default: the experimental slider sits idle.
        assert not page._corner_slider.isEnabled()


def test_save_roundtrip(settings_file, qapp):
        """Modified fields survive validate + atomic save (中文/空格 paths too)."""
        page = SettingsPage()
        page._edits["scrcpy"].setText(r"C:\bin\scrcpy 4.1\scrcpy.exe")
        page._edits["adb"].setText(r"C:\工具\platform-tools\adb.exe")
        page._fps.setValue(120)
        page._bitrate.setValue(8)
        page._dpi_auto.setChecked(False)
        page._dpi.setValue(400)
        page._corner_radios["g2"].setChecked(True)
        page._corner_slider.setValue(64)
        page._glass.setChecked(False)
        page._save()
        assert page.result() == SettingsPage.DialogCode.Accepted
        loaded, problems = load_settings()
        assert problems == []
        assert loaded == Settings(
                scrcpy_path=r"C:\bin\scrcpy 4.1\scrcpy.exe",
                adb_path=r"C:\工具\platform-tools\adb.exe",
                fps=120,
                bitrate_mbps=8,
                dpi=400,
                corner_mode="g2",
                corner_size_dip=64,
                glass_enabled=False,
        )
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["corner_mode"] == "g2"


def test_cancel_discards_changes(settings_file, qapp):
        """Cancel never writes: the file stays untouched on disk."""
        page = SettingsPage()
        page._fps.setValue(200)
        page._edits["adb"].setText(r"C:\nope\adb.exe")
        page.reject()
        assert not Path(settings_file).exists()


def test_corner_slider_enabled_only_in_g2(settings_file, qapp):
        """The size slider is live exactly while the experimental mode runs."""
        page = SettingsPage()
        assert not page._corner_slider.isEnabled()          # system default
        page._corner_radios["g2"].setChecked(True)
        assert page._corner_slider.isEnabled()
        page._corner_radios["none"].setChecked(True)
        assert not page._corner_slider.isEnabled()


def test_dpi_auto_disables_field(settings_file, qapp):
        """自动 (device default) keeps the DPI spinbox inert until unchecked."""
        page = SettingsPage()
        assert page._dpi_auto.isChecked()
        assert not page._dpi.isEnabled()
        page._dpi_auto.setChecked(False)
        assert page._dpi.isEnabled()


def test_engine_lock_disables_path_rows(settings_file, qapp):
        """With a session running the engine rows lock; otherwise they don't."""
        locked = SettingsPage(engine_locked=True)
        assert not locked._edits["scrcpy"].isEnabled()
        assert not locked._edits["adb"].isEnabled()
        assert not locked._detect_buttons["adb"].isEnabled()
        assert "先关闭会话" in locked._lock_hint.text()
        unlocked = SettingsPage()
        assert unlocked._edits["scrcpy"].isEnabled()
        assert not unlocked._lock_hint.isVisibleTo(unlocked)


def test_preview_reflects_appearance_state(settings_file, qapp):
        """The preview widget mirrors mode/size/glass from the page controls."""
        page = SettingsPage()
        assert page._preview._mode == "system"
        page._corner_radios["g2"].setChecked(True)
        page._corner_slider.setValue(72)
        assert page._preview._mode == "g2"
        assert page._preview._size == 72
        assert page._corner_value.text().startswith("72")

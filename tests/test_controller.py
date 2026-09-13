"""PanelController: bindable launcher state (devices, sessions, status).

Runs headless via QT_QPA_PLATFORM=offscreen; adb, threads and process spawns
are faked, so controller behaviour is deterministic. The stub device monitor
never threads, which means the controller's private hop signals deliver
synchronously (direct connection) when tests raise them on the GUI thread.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QUrl  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import duo.core.settings as settings_mod
import duo.ui.controller as controller_mod
from duo.core.apps import AdbError, label_sort_key
from duo.core.settings import Settings
from duo.ui.controller import (  # noqa: E402
        MIRROR_KEY,
        PanelController,
        build_device_mirror_argv,
        build_launch_argv,
)


class _StubMonitor:
        """DeviceMonitor stand-in: no threads, no adb, deterministic state."""

        instances: list[_StubMonitor] = []

        def __init__(self, on_change, query=None, adb_binary=None, poll_interval_s=2.0):
                self.on_change = on_change
                self.adb_binary = adb_binary
                self.started = False
                self.stopped = False
                self.online: list[str] = []
                self._states: dict[str, str] = {}
                _StubMonitor.instances.append(self)

        def set_states(self, states: dict[str, str]) -> None:
                """Pretend a poll happened; ``online`` follows the map."""
                self._states = dict(states)
                self.online = [s for s, state in states.items() if state == "device"]

        @property
        def states(self) -> dict[str, str]:
                return dict(self._states)

        def poll_now(self) -> None:
                self.on_change(self.states)

        def start(self) -> None:
                self.started = True

        def stop(self) -> None:
                self.stopped = True


class _StubPrefsFile:
        """Minimal path duck-type for controller._prefs_path patching."""

        def __init__(self) -> None:
                self.payload: str | None = None
                self.parent = Path(".")

        def read_text(self, encoding: str = "utf-8") -> str:
                if self.payload is None:
                        raise OSError("missing")
                return self.payload

        def write_text(self, text: str, encoding: str = "utf-8") -> None:
                self.payload = text

        def mkdir(self, parents: bool = True, exist_ok: bool = True) -> None:
                pass


class _FakeProc:
        """subprocess.Popen stand-in: never a real process.

        ``pid`` stays 0: the win32 tree-kill branch reads it (and its
        taskkill then fails on pid 0, falling back to terminate - exactly
        the wanted behaviour for a fake).
        """

        def __init__(self, argv: list[str], exit_code: int | None = None) -> None:
                self.argv = argv
                self.pid = 0
                self.terminated = False
                self._exit_code = exit_code

        def poll(self) -> int | None:
                return self._exit_code

        def terminate(self) -> None:
                self.terminated = True

        def wait(self, timeout: float | None = None) -> int | None:
                # The audio-restart path waits for the CLI to release the
                # audio lock; the fake exits immediately.
                return self._exit_code


@pytest.fixture()
def qapp():
        """Ensure exactly one QApplication exists (offscreen)."""
        app = QApplication.instance() or QApplication([])
        yield app


@pytest.fixture()
def prefs_stub(monkeypatch):
        """Portrait prefs live in a stub file (never the real data dir)."""
        stub = _StubPrefsFile()
        monkeypatch.setattr(controller_mod, "_prefs_path", lambda: stub)
        return stub


@pytest.fixture()
def no_adb(monkeypatch, tmp_path_factory):
        """Stub the device monitor and the installed-package lookup.

        Panel session logs are also redirected into a temp dir so tests
        never touch the real data dir (startSession truncates the log file
        the display-id parser reads back). The stub device reports every
        catalog app installed: under the installed-only grid semantics a
        ``set()`` delivery would leave the model empty and every catalog
        expectation below (ordering, pinning, icons) would run vacuous -
        sparser devices re-emit their own set via ``_installedResolved``.
        """
        _StubMonitor.instances = []
        monkeypatch.setattr(controller_mod, "DeviceMonitor", _StubMonitor)
        # Settings live in the real user data dir; controller session starts
        # re-read them (audio policy), so tests pin the defaults.
        monkeypatch.setattr(
                controller_mod, "load_settings", lambda: (Settings(), []))
        checked: list[str] = []

        def fake_resolve_installed(adb_binary, done):
                checked.append(adb_binary)
                done({str(preset.package) for preset in controller_mod.APP_CATALOG})

        monkeypatch.setattr(controller_mod, "_resolve_installed", fake_resolve_installed)
        log_root = tmp_path_factory.mktemp("panel-logs")
        monkeypatch.setattr(
                controller_mod, "panel_log_path", lambda pkg: log_root / f"{pkg}.log"
        )
        return checked


@pytest.fixture()
def settings_file(monkeypatch, tmp_path):
        """Real settings.json under a temp dir for toggle round trips.

        ``no_adb`` pins ``controller_mod.load_settings`` to defaults; this
        fixture re-points it at the REAL loader bound to the temp path, so
        it must be ordered AFTER ``no_adb`` in the test signature (pytest
        sets fixtures up in parameter order).
        """
        path = tmp_path / "settings.json"
        monkeypatch.setattr(settings_mod, "settings_path", lambda: path)
        monkeypatch.setattr(controller_mod, "load_settings", settings_mod.load_settings)
        return path


# While duo.core.catalog is still being built in parallel, the controller's
# guard branch serves an empty APP_CATALOG; this suite then runs against a
# stand-in catalog (the curated set the panel shipped with) so every
# catalog-dependent expectation stays exercised. Once the real module
# lands, the real catalog takes over and the dynamic expectations follow it.
_FALLBACK_CATALOG = [
        SimpleNamespace(label="不背单词", package="cn.com.langeasy.LangEasyLexis"),
        SimpleNamespace(label="哔哩哔哩", package="tv.danmaku.bili"),
        SimpleNamespace(label="微信", package="com.tencent.mm"),
        SimpleNamespace(label="WPS Office", package="cn.wps.moffice_eng"),
        SimpleNamespace(label="微信读书", package="com.tencent.weread"),
]


@pytest.fixture(autouse=True)
def catalog_guard(monkeypatch):
        """Keep the test environment deterministic while modules land.

        duo.core.catalog being absent (guard branch) would leave every
        catalog expectation vacuous, so the stand-in covers it. And once
        duo.core.icon_presets IS present, construction would render real
        SVGs into the user's data dir - stubbed back to None so the suite
        never touches disk (the dedicated preset test patches its own).
        """
        if not controller_mod.APP_CATALOG:
                monkeypatch.setattr(
                        controller_mod, "APP_CATALOG", list(_FALLBACK_CATALOG))
        monkeypatch.setattr(controller_mod, "_PRESETS_READY", True)
        monkeypatch.setattr(
                controller_mod, "preset_icon_path", lambda package: None)


def _catalog_rows() -> list[tuple[str, str]]:
        """(label, package) rows of the active catalog in panel sort order.

        Mirrors the controller's ordering (pinyin key, label, package) so
        tests never hardcode the 28-entry real catalog.
        """
        rows = [
                (str(preset.label), str(preset.package))
                for preset in controller_mod.APP_CATALOG
        ]
        rows.sort(key=lambda row: (label_sort_key(row[0]), row[0], row[1]))
        return rows


# ------------------------------------------------------------- QML app model


def test_apps_model_lists_only_installed_catalog_entries(no_adb, prefs_stub, qapp):
        """网格只显示设备上真实安装的应用（2026-09 真机反馈：不铺预置灰块）。

        无 installed 解析（空集）→ 空网格，空态交给 QML 既有逻辑；目录
        条目仅在包名出现在 installed 里时进模型，顺序按活跃目录动态计算
        （同 label_sort_key），不硬编码 27 项。例：两个已装目录子集 →
        恰好两格。
        """
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit(set())
        assert controller.apps == []
        assert controller.pinnedApps == []

        rows = _catalog_rows()
        picked = {rows[0][1], rows[1][1]}   # 任意两个已装目录子集
        controller._installedResolved.emit(picked)
        assert [entry["package"] for entry in controller.apps] == [
                package for _, package in rows if package in picked
        ]
        assert len(controller.apps) == len(picked) == 2
        assert controller.pinnedApps == []
        assert all(entry["installed"] is True for entry in controller.apps)
        assert all(entry["pinned"] is False for entry in controller.apps)
        assert all(
                entry["key"] == label_sort_key(str(entry["label"]))
                for entry in controller.apps
        )


def test_empty_installed_leaves_grid_empty(no_adb, prefs_stub, qapp):
        """空 installed 集合：网格为空，不铺预置灰块（空态由 QML 显示）。"""
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit(set())
        assert controller.apps == []
        assert controller.pinnedApps == []


def test_uninstalled_entries_leave_the_models(no_adb, prefs_stub, qapp):
        """卸载即消失：目录与第三方条目随 installed 收缩退场，不灰显滞留。"""
        rows = _catalog_rows()
        pkg = rows[0][1]
        controller = PanelController("/fake/adb.exe")
        controller.allAppsReady.emit(["org.foo.bar"])
        assert "org.foo.bar" in {entry["package"] for entry in controller.apps}

        # 设备上只剩 pkg：目录其余项与三方 org.foo.bar 一起退场。
        controller._installedResolved.emit({pkg})
        assert [entry["package"] for entry in controller.apps] == [pkg]
        assert controller.pinnedApps == []


def test_orphan_pin_hidden_until_reinstall(no_adb, prefs_stub, qapp):
        """孤儿 pin：卸载后固定卡消失但 prefs 保留，重装自动回固定卡。

        有意行为（见 _rebuild_apps 注释）：一次卸载不丢用户的固定选择，
        pinnedApps 与网格同步过滤，且固定行被清空时也发信号。
        """
        controller = PanelController("/fake/adb.exe")
        pkg = _catalog_rows()[0][1]
        controller.togglePin(pkg)
        assert controller.pinnedApps[0]["package"] == pkg

        pin_emits: list[int] = []
        controller.pinnedAppsChanged.connect(lambda: pin_emits.append(1))
        controller._installedResolved.emit(set())   # 卸载：固定行清空也要发信号
        assert controller.pinnedApps == []
        assert controller.apps == []
        assert len(pin_emits) == 1
        # 孤儿 pin 保留在 prefs：重装后自动回固定卡。
        assert json.loads(prefs_stub.payload)["pinned"] == [pkg]

        controller._installedResolved.emit({pkg})
        assert controller.pinnedApps[0]["package"] == pkg
        assert controller.pinnedApps[0]["installed"] is True
        assert controller.pinnedApps[0]["pinned"] is True
        assert pkg not in {entry["package"] for entry in controller.apps}


def test_empty_catalog_guard_leaves_empty_models_standing(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """APP_CATALOG 未就绪（护栏分支）时：空模型，不崩，第三方仍可用。"""
        monkeypatch.setattr(controller_mod, "APP_CATALOG", [])
        controller = PanelController("/fake/adb.exe")
        assert controller.apps == []
        assert controller.pinnedApps == []
        # Rebuilds against an empty catalog must not raise.
        controller._installedResolved.emit({"org.foo.bar"})
        assert controller.apps == []   # third-party arrives via allAppsReady
        controller.allAppsReady.emit(["org.foo.bar"])
        assert [entry["package"] for entry in controller.apps] == ["org.foo.bar"]
        # Labels degrade to the package fallback.
        assert controller_mod.session_label("org.foo.bar") == "Bar"


def test_apps_model_sorts_pinyin_across_catalog_and_extras(no_adb, prefs_stub, qapp):
        """Catalog + third-party apps share one pinyin-initial order.

        The pre-sort behavior (catalog first, then third-party apps in
        package order) read as a random grid once real app labels landed.
        """
        controller = PanelController("/fake/adb.exe")
        controller.allAppsReady.emit(["com.foo.zbar", "org.alpha.app"])
        rows = _catalog_rows() + [("App", "org.alpha.app"), ("Zbar", "com.foo.zbar")]
        rows.sort(key=lambda row: (label_sort_key(row[0]), row[0], row[1]))
        assert [entry["package"] for entry in controller.apps] == [
                package for _, package in rows
        ]
        # The real label arrives MID-SWEEP: the entry updates in place -
        # label patched, position frozen (扫描期间顺序冻结：磁贴不流动).
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        controller.appInfoReady.emit([("org.alpha.app", None, "阿里")])
        assert len(rebuilds) == 1
        assert {e["package"]: e["label"] for e in controller.apps}["org.alpha.app"] \
                == "阿里"
        assert [entry["package"] for entry in controller.apps] == [
                package for _, package in rows
        ]   # fallback 标签序未动
        # Sweep end: exactly ONE settle re-sort - 阿里 (al) jumps above
        # 不背单词 (bbdc) - and exactly one more appsChanged for it.
        controller.infoSweepDone.emit()
        assert len(rebuilds) == 2
        rows = [
                ("阿里", package) if package == "org.alpha.app" else (label, package)
                for label, package in rows
        ]
        rows.sort(key=lambda row: (label_sort_key(row[0]), row[0], row[1]))
        assert [entry["package"] for entry in controller.apps] == [
                package for _, package in rows
        ]
        assert rows[0] == ("阿里", "org.alpha.app")   # al < bbdc < ... < zbar


def test_toggle_pin_moves_entry_between_grid_and_pinned_row(no_adb, prefs_stub, qapp):
        """Pin: the entry MOVES to the pinned row; the grid never repeats it.

        Fixed-row semantics (DESIGN.md §3.3): pinned tiles leave the grid
        instead of floating to its top. One toggle = exactly one
        appsChanged + one pinnedAppsChanged emit.
        """
        controller = PanelController("/fake/adb.exe")
        label, package = _catalog_rows()[-1]   # would sit last in the grid
        grid_before = [entry["package"] for entry in controller.apps]
        assert grid_before[-1] == package

        app_emits: list[int] = []
        pin_emits: list[int] = []
        controller.appsChanged.connect(lambda: app_emits.append(1))
        controller.pinnedAppsChanged.connect(lambda: pin_emits.append(1))

        controller.togglePin(package)
        assert len(app_emits) == 1   # one user action, one emit pair
        assert len(pin_emits) == 1
        assert controller.pinnedApps[0]["package"] == package
        assert controller.pinnedApps[0]["pinned"] is True
        assert [entry["package"] for entry in controller.apps] == grid_before[:-1]
        assert json.loads(prefs_stub.payload)["pinned"] == [package]
        assert controller.statusText == f"已置顶 {label}"

        # A fresh controller reads the pin back: the row splits the same way.
        second = PanelController("/fake/adb.exe")
        assert second.pinnedApps[0]["package"] == package
        assert second.pinnedApps[0]["pinned"] is True
        assert package not in [entry["package"] for entry in second.apps]

        # Unpin: back to the pinyin position, persist again, one emit pair.
        second_app_emits: list[int] = []
        second_pin_emits: list[int] = []
        second.appsChanged.connect(lambda: second_app_emits.append(1))
        second.pinnedAppsChanged.connect(lambda: second_pin_emits.append(1))
        second.togglePin(package)
        assert len(second_app_emits) == 1
        assert len(second_pin_emits) == 1
        assert [entry["package"] for entry in second.apps] == grid_before
        assert second.pinnedApps == []
        assert json.loads(prefs_stub.payload)["pinned"] == []
        assert second.statusText == f"已取消置顶 {label}"


def test_pinned_third_party_app_lives_only_in_pinned_row(no_adb, prefs_stub, qapp):
        """A pinned third-party app never appears in the grid.

        The pin can outlive discovery (persisted for a package not yet in
        the model); when the listing arrives the entry goes straight to
        the pinned row, and rebuilds keep it there.
        """
        controller = PanelController("/fake/adb.exe")
        controller.togglePin("org.outside.app")   # not in the model yet
        controller.allAppsReady.emit(["org.outside.app"])
        assert "org.outside.app" not in {
                entry["package"] for entry in controller.apps
        }
        assert controller.pinnedApps[0]["package"] == "org.outside.app"
        assert controller.pinnedApps[0]["pinned"] is True

        # A rebuild (install poll) keeps it in the pinned row only -
        # third-party survival requires the device to still report it.
        controller._installedResolved.emit({"org.outside.app"})
        assert "org.outside.app" not in {
                entry["package"] for entry in controller.apps
        }
        assert controller.pinnedApps[0]["package"] == "org.outside.app"

        # An icon landing on a pinned entry refreshes the pinned row too.
        controller.iconReady.emit("org.outside.app", Path("/tmp/outside.png"))
        QApplication.processEvents()   # let the single-shot flush timer fire
        expected = QUrl.fromLocalFile("/tmp/outside.png").toString()
        assert controller.pinnedApps[0]["icon"] == expected


def test_prefs_sections_do_not_clobber_each_other(no_adb, prefs_stub, qapp):
        """portrait and pinned share one doc; saving one keeps the other."""
        rows = _catalog_rows()
        controller = PanelController("/fake/adb.exe")
        controller.togglePin(rows[-1][1])
        controller.togglePortrait(rows[0][1])
        doc = json.loads(prefs_stub.payload)
        assert doc["pinned"] == [rows[-1][1]]
        assert doc["portrait"][rows[0][1]] is True


def test_apps_model_tracks_installed_icons_and_extras(no_adb, prefs_stub, qapp):
        """Installed-only grid: icons, labels and survivorship reach the model.

        只有 installed 里的包才有格子（目录未装项不进模型）；幸存者保住
        图标与已解析的标签，卸载项退场。
        """
        rows = _catalog_rows()
        pkg, other = rows[0][1], rows[1][1]
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit({pkg})
        assert [entry["package"] for entry in controller.apps] == [pkg]

        # iconReady hops a Path in; the model stores a QML-ready file URL.
        controller.iconReady.emit(pkg, Path("/tmp/bili.png"))
        by_package = {e["package"]: e for e in controller.apps}
        expected = QUrl.fromLocalFile("/tmp/bili.png").toString()
        assert by_package[pkg]["icon"] == expected

        # A third-party listing extends the model; app info fills the label.
        controller.allAppsReady.emit(["org.foo.bar"])
        assert "org.foo.bar" in {e["package"] for e in controller.apps}
        # The batch hop patches the model and rebuilds once (not per app).
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        controller.appInfoReady.emit([("org.foo.bar", None, "Bar 应用")])
        assert len(rebuilds) == 1
        by_package = {e["package"]: e["label"] for e in controller.apps}
        assert by_package["org.foo.bar"] == "Bar 应用"

        # A later install poll keeps survivors + icons; 目录里未装的 other
        # 从未进网格。
        controller._installedResolved.emit({pkg, "org.foo.bar"})
        by_set = {e["package"] for e in controller.apps}
        assert by_set == {pkg, "org.foo.bar"}
        assert other not in by_set
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package[pkg]["icon"] == expected
        assert by_package["org.foo.bar"]["label"] == "Bar 应用"
        assert by_package[pkg]["installed"] is True


def test_entries_carry_pinyin_search_key_and_sync_on_label_patch(
        no_adb, prefs_stub, qapp
):
        """Every entry caches its pinyin search key; label patches sync it.

        Search (DESIGN.md §3.4) matches the raw label OR the pinyin
        initials, and sorting reads the cached key instead of re-running
        the pinyin walk per comparison.
        """
        controller = PanelController("/fake/adb.exe")
        assert all(
                entry["key"] == label_sort_key(str(entry["label"]))
                for entry in controller.apps
        )
        # A third-party entry gets its key at merge time.
        controller.allAppsReady.emit(["org.alpha.app"])
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["org.alpha.app"]["key"] == label_sort_key("App")

        # The real label patch syncs the key inside the same single emit.
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        controller.appInfoReady.emit([("org.alpha.app", None, "阿里")])
        assert len(rebuilds) == 1
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["org.alpha.app"]["key"] == label_sort_key("阿里")
        # Survivors of a rebuild keep the synced key.
        controller._installedResolved.emit({"org.alpha.app"})
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["org.alpha.app"]["key"] == label_sort_key("阿里")


def test_preset_icon_fallback_and_real_icon_override(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """New entries start on preset icons; real icons land permanently.

        The preset module being unbuilt just means icon-less entries; when
        it resolves a path, fresh entries (catalog AND third-party) start
        on that file URL until the icon worker delivers the real one.
        """
        package = _catalog_rows()[0][1]
        monkeypatch.setattr(controller_mod, "_PRESETS_READY", False)
        bare = PanelController("/fake/adb.exe")
        assert {e["package"]: e for e in bare.apps}[package]["icon"] == ""

        monkeypatch.setattr(controller_mod, "_PRESETS_READY", True)
        monkeypatch.setattr(
                controller_mod, "preset_icon_path",
                lambda pkg: Path(f"/tmp/presets/{pkg}.svg"),
        )
        controller = PanelController("/fake/adb.exe")
        expected_preset = QUrl.fromLocalFile(f"/tmp/presets/{package}.svg").toString()
        assert {e["package"]: e for e in controller.apps}[package]["icon"] == \
                expected_preset
        # Third-party fresh entries get the same treatment.
        controller.allAppsReady.emit(["org.preset.app"])
        assert {e["package"]: e for e in controller.apps}["org.preset.app"]["icon"] == \
                QUrl.fromLocalFile("/tmp/presets/org.preset.app.svg").toString()

        # The real icon arrives: it overwrites the preset permanently.
        controller.iconReady.emit(package, Path("/tmp/real.png"))
        QApplication.processEvents()   # let the single-shot flush timer fire
        assert {e["package"]: e for e in controller.apps}[package]["icon"] == \
                QUrl.fromLocalFile("/tmp/real.png").toString()
        # Rebuilds reuse the surviving entry: never re-preset.
        controller._installedResolved.emit({package})
        assert {e["package"]: e for e in controller.apps}[package]["icon"] == \
                QUrl.fromLocalFile("/tmp/real.png").toString()


def test_failed_installed_sweep_keeps_previous_and_retries_once(no_adb, prefs_stub, qapp):
        """A failed installed-sweep must NOT grey out every tile.

        ``done(None)`` = the probe itself failed (adb flake, cold server),
        which is NOT "nothing installed": the previous set stays
        authoritative (tiles stay clickable - the old behavior disabled
        every app on a single timeout and never healed, since this sweep
        has no 2s re-poll). One silent retry is armed; a later success
        re-arms the failure path for the next flake.
        """
        pkg = _catalog_rows()[0][1]
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit({pkg})
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package[pkg]["installed"] is True

        # A flaked sweep: None arrives, the known set rides it out.
        controller._installedResolved.emit(None)
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package[pkg]["installed"] is True
        assert controller._install_retry.isActive()   # one silent retry armed

        # The retry fires exactly once: a second failure must not re-arm.
        controller._install_retry.stop()
        controller._installedResolved.emit(None)
        assert not controller._install_retry.isActive()

        # Recovery disarms the gate; the next flake gets a fresh retry.
        controller._installedResolved.emit({pkg})
        assert controller._install_retried is False
        controller._installedResolved.emit(None)
        assert controller._install_retry.isActive()


def test_toggle_portrait_does_not_rebuild_app_model(no_adb, prefs_stub, qapp):
        """The long-press portrait path never touches the apps model.

        The QML QVariantList grid rebuilds every delegate per
        ``appsChanged`` emit (async icons blank out); togglePortrait only
        flips the pref + status, so no rebuild may ride along.
        """
        controller = PanelController("/fake/adb.exe")
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        controller.togglePortrait("tv.danmaku.bili")
        assert rebuilds == []
        assert controller.portraitFor("tv.danmaku.bili") is True


def test_icon_burst_notifies_once(no_adb, prefs_stub, qapp):
        """An icon sweep flushes as ONE appsChanged emit, not one per icon.

        The old per-icon emits fired a full-grid rebuild storm at every
        startup - tiles visibly vanished under a held press (async images
        blank while delegates recycle). The deferred flush restores the
        batch contract the app-info path already follows.
        """
        rows = _catalog_rows()
        pkg_a, pkg_b, pkg_c = rows[0][1], rows[1][1], rows[2][1]
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit({pkg_a, pkg_b})
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        for package in (pkg_a, pkg_b, pkg_c):
                controller.iconReady.emit(package, Path(f"/tmp/{package}.png"))
        QApplication.processEvents()   # let the single-shot flush timer fire
        assert len(rebuilds) == 1
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package[pkg_a]["icon"] == \
                QUrl.fromLocalFile(f"/tmp/{pkg_a}.png").toString()
        assert by_package[pkg_b]["icon"] == \
                QUrl.fromLocalFile(f"/tmp/{pkg_b}.png").toString()
        # A later, separate burst notifies exactly once more.
        controller.iconReady.emit(pkg_c, None)   # no patch, no emit
        controller.iconReady.emit(pkg_a, Path("/tmp/real-2.png"))
        QApplication.processEvents()
        assert len(rebuilds) == 2
        assert by_package[pkg_a]["icon"] == \
                QUrl.fromLocalFile("/tmp/real-2.png").toString()


def test_load_all_apps_emits_chunked_batches(no_adb, prefs_stub, qapp, monkeypatch):
        """信息扫描分块：每满 8 个解析完的包 emit 一批 appInfoReady。

        首扫 ~100 个三方包要几分钟；单次扫完才 emit 会让标签全程停留在
        包名派生值（观感 = 探索停了）。分块后每批仍是一次 appsChanged
        重建（批处理契约不变），进度可见。"""
        packages = [f"com.example.app{i:02d}" for i in range(20)]
        monkeypatch.setattr(
                controller_mod, "Adb",
                lambda b, s: SimpleNamespace(
                        third_party_packages=lambda: list(packages)),
        )
        monkeypatch.setattr(
                controller_mod, "app_info",
                lambda adb, package: SimpleNamespace(
                        icon_path=None, label=f"应用{package[-2:]}"),
        )
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})

        batches: list[object] = []
        controller.appInfoReady.connect(batches.append)
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        sweeps: list[int] = []
        controller.infoSweepDone.connect(lambda: sweeps.append(1))
        controller._load_all_apps()
        deadline = time.monotonic() + 5.0
        while len(batches) < 3 and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
        # 收尾还要落一次位：merge + 3 批原位 patch + 收尾一次重排。
        while len(rebuilds) < 5 and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)

        assert len(batches) == 3                       # 20 = 8 + 8 + 4
        assert [len(b) for b in batches] == [8, 8, 4]
        assert all(len(b) <= 8 for b in batches)
        flat = [entry for batch in batches for entry in batch]
        assert [e[0] for e in flat] == packages        # 原序不丢不重
        # 每批元素形状不变，且已应用到模型（应用00 → 首批已生效）
        by_package = {e["package"]: e for e in controller.apps}
        assert by_package["com.example.app00"]["label"] == "应用00"
        # worker 的 finally 收尾信号到了（异常/提前返回路径同样发），
        # 且收尾后恰一次重排把全部条目落位真实 label 拼音序。
        assert sweeps
        assert len(rebuilds) == 5
        keys = [str(e["key"]) for e in controller.apps]
        assert keys == sorted(keys)
        assert controller._order_stale is False


def test_info_batches_freeze_order_until_sweep_done(no_adb, prefs_stub, qapp):
        """扫描期间顺序冻结，infoSweepDone 一次落位（2026-10 真机反馈）。

        每 8 个一批 appInfoReady 到达就重排一次 = 磁贴来回「流动」，
        用户正在找的应用一直在漂。批到达只原位 patch（appsChanged 照
        发，新图标/新标签原地弹出，旧图标靠 QML 图像缓存不闪）；扫描
        收尾恰一次重排，网格最终落位真实拼音序，之后的重复收尾不再
        翻动网格。
        """
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit(set())   # 清空目录格，只看三方
        controller.allAppsReady.emit(["org.alpha.app", "com.foo.zbar"])
        frozen = [entry["package"] for entry in controller.apps]
        assert frozen == ["org.alpha.app", "com.foo.zbar"]   # App < Zbar

        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))

        # 两批足以【反转】fallback 序的真实 label：模型原位更新，顺序不动。
        controller.appInfoReady.emit([("org.alpha.app", None, "紫宝")])
        controller.appInfoReady.emit([("com.foo.zbar", None, "阿里")])
        assert len(rebuilds) == 2                       # 每批仍恰一次重建
        assert [entry["package"] for entry in controller.apps] == frozen
        by_label = {e["package"]: str(e["label"]) for e in controller.apps}
        assert by_label["org.alpha.app"] == "紫宝"      # 标签已换，位置未换

        # 收尾：恰一次排序 + 一次 appsChanged，拼音序落位（阿里 al < 紫宝 zb）。
        controller.infoSweepDone.emit()
        assert len(rebuilds) == 3
        assert [entry["package"] for entry in controller.apps] == [
                "com.foo.zbar", "org.alpha.app",
        ]
        # 无新批的重复收尾：不再重排、不再发信号。
        controller.infoSweepDone.emit()
        assert len(rebuilds) == 3
        assert [entry["package"] for entry in controller.apps] == [
                "com.foo.zbar", "org.alpha.app",
        ]


def test_info_sweep_done_without_label_changes_skips_settle(
        no_adb, prefs_stub, qapp
):
        """infoSweepDone 且无 label 变化：不重排、不发 appsChanged。

        空收尾（扫描先于任何批结束/无设备提前返回）与同值 label 的批
        （全缓存命中的重扫）都不标记顺序过期 —— 收尾免付一次网格重建。
        """
        controller = PanelController("/fake/adb.exe")
        rebuilds: list[int] = []
        controller.appsChanged.connect(lambda: rebuilds.append(1))
        before = [entry["package"] for entry in controller.apps]

        controller.infoSweepDone.emit()   # 空扫描收尾
        assert rebuilds == []
        assert [entry["package"] for entry in controller.apps] == before

        # 同值 label 的批：原位 patch 仍发一次重建，但不算 label 变化。
        label = {e["package"]: str(e["label"]) for e in controller.apps}[before[0]]
        controller.appInfoReady.emit([(before[0], None, label)])
        assert len(rebuilds) == 1
        controller.infoSweepDone.emit()
        assert len(rebuilds) == 1
        assert [entry["package"] for entry in controller.apps] == before


def test_info_sweep_settles_pinned_row_once(no_adb, prefs_stub, qapp):
        """被触及的固定行同批落位：收尾补发自己的 pinnedAppsChanged。

        _sort_apps 重排两个模型；固定行若收尾不发信号，QML 的行序会
        一直停在扫描期的冻结序。
        """
        controller = PanelController("/fake/adb.exe")
        controller._installedResolved.emit(set())   # 清空目录格，只看三方
        controller.togglePin("org.zeta.app")       # 孤儿 pin 先落 prefs
        controller.togglePin("org.beta.app")
        controller.allAppsReady.emit(["org.zeta.app", "org.beta.app"])
        frozen_row = [str(e["package"]) for e in controller.pinnedApps]
        assert frozen_row == ["org.beta.app", "org.zeta.app"]   # Beta < Zeta
        assert controller.apps == []   # 两个都在固定行，网格为空

        pin_emits: list[int] = []
        controller.pinnedAppsChanged.connect(lambda: pin_emits.append(1))
        # 真实 label 反转行序：zeta → 阿里(al)，beta → 微宝(wb)；扫描期冻结。
        controller.appInfoReady.emit(
                [("org.zeta.app", None, "阿里"), ("org.beta.app", None, "微宝")]
        )
        assert len(pin_emits) == 1                   # 批原位 patch 时照发
        assert [str(e["package"]) for e in controller.pinnedApps] == frozen_row
        # 收尾：固定行一起落位 + 补发一次自己的信号。
        controller.infoSweepDone.emit()
        assert len(pin_emits) == 2
        assert [str(e["package"]) for e in controller.pinnedApps] == [
                "org.zeta.app", "org.beta.app",
        ]   # 阿里(al) < 微宝(wb)


# ------------------------------------------------------------------ defaults


def test_controller_defaults(no_adb, prefs_stub, qapp):
        """A fresh controller: empty devices/sessions, ready status, unlocked."""
        controller = PanelController("/fake/adb.exe")
        assert controller.devices == []
        assert controller.statusText == "就绪"
        assert controller.runningSessions == []
        assert controller.adbBinary == "/fake/adb.exe"
        assert not controller.engineLocked
        # Polling is up; the install check ran once against our adb.
        monitor = _StubMonitor.instances[-1]
        assert monitor.started
        assert no_adb == ["/fake/adb.exe"]


def test_devices_property_shape_and_signal(no_adb, prefs_stub, qapp):
        """The devices property lists serial + state text per adb state."""
        controller = PanelController("/fake/adb.exe")
        seen: list[list] = []
        controller.devicesChanged.connect(seen.append)
        controller._devicesPolled.emit({"S1": "device", "S2": "unauthorized"})
        assert controller.devices == [
                {
                        "serial": "S1",
                        "state": "device",
                        "stateText": "在线",
                        "online": True,
                },
                {
                        "serial": "S2",
                        "state": "unauthorized",
                        "stateText": "未授权 USB 调试",
                        "online": False,
                },
        ]
        assert seen and seen[-1] == controller.devices


def test_device_arrival_after_startup_triggers_installed_refresh(
        no_adb, prefs_stub, qapp
):
        """冷插设备：面板启动后才上线的，installed 扫描自动重跑。

        回归：refreshInstalled 只在 __init__/setAdb 调用过，设备后连上时
        目录全灰且信息/图标扫描永不运行。首事件只记基线（构造器已扫过
        一次），只有“新增在线设备”才重扫——掉线不重扫。"""
        controller = PanelController("/fake/adb.exe")
        # 构造器扫过一次（poll_now 发空集只记基线）
        assert no_adb == ["/fake/adb.exe"]
        controller._devicesPolled.emit({})   # 空集 → 无变化
        assert no_adb == ["/fake/adb.exe"]

        controller._devicesPolled.emit({"S1": "device"})   # 新设备 → 重扫
        assert no_adb == ["/fake/adb.exe", "/fake/adb.exe"]

        controller._devicesPolled.emit({"S1": "offline"})   # 掉线 → 不重扫
        assert no_adb == ["/fake/adb.exe", "/fake/adb.exe"]
        controller._devicesPolled.emit({})   # 仍无新增
        assert no_adb == ["/fake/adb.exe", "/fake/adb.exe"]

        # 设备换代（拔 A 插 B）同样算“新增”
        controller._devicesPolled.emit({"S2": "device"})
        assert no_adb == ["/fake/adb.exe"] * 3


# ---------------------------------------------------------------- argv reuse


def test_start_session_reuses_build_launch_argv(no_adb, prefs_stub, qapp, monkeypatch):
        """Spawning goes through build_launch_argv (chrome/serial/portrait)."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        monkeypatch.setattr(controller, "_spawn", fake_spawn)
        controller.startSession("tv.danmaku.bili")
        assert spawned == [build_launch_argv("tv.danmaku.bili", "S1", portrait=False)]
        entry = controller.runningSessions[0]
        assert entry["key"] == "tv.danmaku.bili"
        assert entry["label"] == "哔哩哔哩"
        assert entry["running"] is True
        assert entry["portrait"] is False
        assert controller.engineLocked


def test_start_session_without_device_sets_status(no_adb, prefs_stub, qapp):
        """No online serial: a status message, no session, nothing locked."""
        controller = PanelController("/fake/adb.exe")
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startSession("tv.danmaku.bili")
        assert statuses == ["设备未连接"]
        assert controller.runningSessions == []
        assert not controller.engineLocked


def test_device_mirror_session_uses_mirror_argv(no_adb, prefs_stub, qapp, monkeypatch):
        """startMirror spawns build_device_mirror_argv under the mirror key."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        monkeypatch.setattr(controller, "_spawn", fake_spawn)
        controller.startMirror()
        assert spawned == [build_device_mirror_argv("S1")]
        entry = controller.runningSessions[0]
        assert entry["key"] == MIRROR_KEY
        assert entry["label"] == "设备镜像"

        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startMirror()
        assert statuses == ["设备镜像已在运行"]
        assert len(spawned) == 1


# ------------------------------------------------------ start / stop / reap


def _spawn_recorder(controller, procs):
        def fake_spawn(argv):
                proc = _FakeProc(argv)
                procs.append(proc)
                return proc

        controller._spawn = fake_spawn  # type: ignore[method-assign]


def test_start_stop_reap_session(no_adb, prefs_stub, qapp, monkeypatch):
        """stop terminates; the session lingers until the reaper drops it."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.startSession("tv.danmaku.bili")
        controller.stopSession("tv.danmaku.bili")
        assert procs[0].terminated
        # terminate() is asynchronous: still counted while the process lives.
        assert controller.engineLocked
        assert len(controller.runningSessions) == 1

        procs[0]._exit_code = 0
        assert controller.reapSessions() == 1
        assert controller.runningSessions == []
        assert not controller.engineLocked
        # Stopping an unknown key is a quiet no-op.
        controller.stopSession("no.such.package")
        assert controller.reapSessions() == 0


def test_stop_session_routes_through_tree_kill(no_adb, prefs_stub, qapp, monkeypatch):
        """stopSession must tree-kill: a bare terminate orphans scrcpy."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        killed: list[_FakeProc] = []
        monkeypatch.setattr(controller_mod, "terminate_tree", killed.append)

        controller.startSession("tv.danmaku.bili")
        controller.stopSession("tv.danmaku.bili")
        assert killed == [procs[0]]


class _FakeJob:
        """ChildJob stand-in: records adds and the shutdown close."""

        def __init__(self) -> None:
                self.added: list[_FakeProc] = []
                self.closed = False

        def add(self, proc: _FakeProc) -> None:
                self.added.append(proc)

        def close(self) -> None:
                self.closed = True


def test_shutdown_tree_kills_all_sessions_and_closes_job(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """Panel close = every session tree dies and the job handle closes."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        job = _FakeJob()
        controller._job = job   # type: ignore[assignment]
        killed: list[_FakeProc] = []
        monkeypatch.setattr(controller_mod, "terminate_tree", killed.append)

        controller.startSession("tv.danmaku.bili")
        controller.startMirror()
        controller.shutdown()
        assert killed == procs
        assert controller.runningSessions == []
        assert job.closed


def test_spawn_registers_session_in_child_job(no_adb, prefs_stub, qapp, monkeypatch):
        """Every real spawn lands in the kill-on-close job (crash safety)."""
        controller = PanelController("/fake/adb.exe")
        job = _FakeJob()
        controller._job = job   # type: ignore[assignment]
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[_FakeProc] = []

        def fake_popen(argv, **kwargs):
                proc = _FakeProc(argv)
                spawned.append(proc)
                return proc

        monkeypatch.setattr(controller_mod.subprocess, "Popen", fake_popen)

        # First start spawns bili; the second start respawns bili muted
        # (audio arbitration, policy=latest) then spawns the new app -
        # every real Popen must register.
        controller.startSession("tv.danmaku.bili")
        controller.startSession("cn.com.langeasy.LangEasyLexis")
        assert len(spawned) == 3
        assert job.added == spawned


def test_engine_locked_tracks_sessions(no_adb, prefs_stub, qapp, monkeypatch):
        """engineLocked flips with the live session map."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        assert not controller.engineLocked
        controller.startSession("tv.danmaku.bili")
        assert controller.engineLocked
        controller.startMirror()
        assert controller.activeSessionCount() == 2
        for proc in procs:
                proc._exit_code = 0
        controller.reapSessions()
        assert not controller.engineLocked
        assert controller.activeSessionCount() == 0


def test_duplicate_start_routes_to_display_move(no_adb, prefs_stub, qapp, monkeypatch, tmp_path):
        """Starting a live session moves the app onto its display, no 2nd spawn.

        The app may be running on the physical screen (or the user just
        wants it back on the virtual one): the click must not rebuild the
        session, it goes through startAppOnDisplay. Without a session log
        yet the move degrades to a status message.
        """
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        monkeypatch.setattr(
                controller_mod, "panel_log_path", lambda pkg: tmp_path / f"{pkg}.log"
        )

        controller.startSession("tv.danmaku.bili")
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startSession("tv.danmaku.bili")
        assert statuses == ["哔哩哔哩 虚拟屏未就绪，稍后重试"]
        assert len(procs) == 1   # degradation, not a session rebuild


class _FakeAdb:
        """Adb stand-in recording shell commands with canned outputs."""

        def __init__(self, binary: str, serial: str, results: list[str] | None = None):
                self.binary = binary
                self.serial = serial
                self.calls: list[tuple[str, ...]] = []
                self._results = list(results or [])

        def run(self, *args: str, timeout: float = 60.0) -> str:
                self.calls.append(args)
                return self._results.pop(0) if self._results else ""


RESOLVE_OUTPUT = "cn.com.langeasy.LangEasyLexis/cn.com.langeasy.LangEasyLexis.MainActivity\n"


def test_start_app_on_display_moves_running_app(
        no_adb, prefs_stub, qapp, monkeypatch, tmp_path
):
        """Known display id + resolvable component -> am start --display N.

        resolve-activity pre-parses the launchable component, then the app
        is started onto the virtual display read from the session log -
        no session rebuild.
        """
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        log_file = tmp_path / "bili.log"
        monkeypatch.setattr(controller_mod, "panel_log_path", lambda pkg: log_file)
        fake = _FakeAdb(
                "/fake/adb.exe",
                "S1",
                results=[RESOLVE_OUTPUT, "Starting: Intent { cmp=... }\n"],
        )
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)

        controller.startSession("tv.danmaku.bili")
        # The engine writes the announce line after the spawn (startSession
        # truncated the log), so the fixture writes it post-start.
        log_file.write_text(
                "[server] INFO: New display: 1200x1600/280 (id=157)\n", encoding="utf-8"
        )
        controller.startAppOnDisplay("tv.danmaku.bili")
        deadline = time.monotonic() + 5.0
        while controller.statusText != "已在虚拟屏打开 哔哩哔哩" \
                and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
        assert fake.calls == [
                ("shell", "cmd", "package", "resolve-activity", "--brief",
                 "tv.danmaku.bili"),
                ("shell", "am", "start", "--display", "157", "-n",
                 "cn.com.langeasy.LangEasyLexis/cn.com.langeasy.LangEasyLexis.MainActivity"),
        ]
        assert controller.statusText == "已在虚拟屏打开 哔哩哔哩"
        assert len(procs) == 1


def test_start_app_on_display_degradations(no_adb, prefs_stub, qapp, monkeypatch, tmp_path):
        """Every failure path degrades to a status line, never a rebuild."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        log_file = tmp_path / "bili.log"
        monkeypatch.setattr(controller_mod, "panel_log_path", lambda pkg: log_file)

        # Unknown session: chip click for something not tracked.
        controller.startAppOnDisplay("tv.danmaku.bili")
        assert controller.statusText == "哔哩哔哩 会话未运行"
        assert procs == []

        controller.startSession("tv.danmaku.bili")

        # Log without a display line yet (engine still starting).
        log_file.write_text("[server] INFO: connecting\n", encoding="utf-8")
        fake = _FakeAdb("/fake/adb.exe", "S1")
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        controller.startAppOnDisplay("tv.danmaku.bili")
        assert controller.statusText == "哔哩哔哩 虚拟屏未就绪，稍后重试"
        assert fake.calls == []

        def _move_until_status(fake_adb: _FakeAdb, expected: str) -> None:
                controller.startAppOnDisplay("tv.danmaku.bili")
                deadline = time.monotonic() + 5.0
                while controller.statusText != expected and time.monotonic() < deadline:
                        QApplication.processEvents()
                        time.sleep(0.01)
                assert controller.statusText == expected

        # resolve-activity yields nothing usable.
        log_file.write_text("[server] INFO: New display: 1x1/160 (id=9)\n", encoding="utf-8")
        fake = _FakeAdb("/fake/adb.exe", "S1", results=["\n"])
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        _move_until_status(fake, "打开失败：哔哩哔哩（无法解析应用入口）")

        # am start reports an in-band error.
        fake = _FakeAdb(
                "/fake/adb.exe",
                "S1",
                results=[RESOLVE_OUTPUT, "Error: Activity class does not exist\n"],
        )
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        _move_until_status(fake, "打开失败：哔哩哔哩（Error: Activity class does not exist）")

        # adb itself fails (device gone mid-click).
        def boom(*args: str, timeout: float = 60.0) -> str:
                raise AdbError("adb -s S1 shell failed: device offline")

        fake = _FakeAdb("/fake/adb.exe", "S1")
        fake.run = boom  # type: ignore[method-assign]
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        _move_until_status(fake, "打开失败：哔哩哔哩（adb -s S1 shell failed: device offline）")

        # No session rebuild ever happened; no am start succeeded.
        assert len(procs) == 1


def test_session_log_reset_between_sessions(no_adb, prefs_stub, qapp, monkeypatch, tmp_path):
        """A fresh session truncates the panel log: no stale display ids.

        The parser takes the LAST 'New display:' line, so a leftover file
        would hand out the previous run's (dead) display id until the new
        engine rewrites it.
        """
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        log_file = tmp_path / "bili.log"
        monkeypatch.setattr(controller_mod, "panel_log_path", lambda pkg: log_file)
        log_file.write_text("[server] INFO: New display: 1x1/160 (id=9)\n", encoding="utf-8")

        controller.startSession("tv.danmaku.bili")
        assert not log_file.exists()   # stale log removed before the spawn


# -------------------------------------------------------------------- status


def test_status_signal_fires_on_state_changes(no_adb, prefs_stub, qapp, monkeypatch):
        """Launch and portrait changes surface through statusChanged."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startSession("cn.com.langeasy.LangEasyLexis")
        assert statuses[-1] == "已启动 不背单词 · 横屏"   # 无硬编码种子：16:9 开局
        controller.togglePortrait("cn.com.langeasy.LangEasyLexis")
        assert statuses[-1] == "不背单词 将以竖屏启动"   # 从 16:9 默认切到竖屏
        controller.stopSession("cn.com.langeasy.LangEasyLexis")
        assert statuses[-1] == "已关闭 不背单词"


# ---------------------------------------- 镜像时关闭设备屏幕（§3.5 菜单项）


def test_turn_screen_off_initial_flag_persists_and_signals(
        no_adb, prefs_stub, qapp, settings_file
):
        """turnScreenOff 初值来自设置；切换 = 落盘 + 信号 + 状态消息。"""
        settings_mod.save_settings(Settings(turn_screen_off=True))
        controller = PanelController("/fake/adb.exe")
        assert controller.turnScreenOff is True   # 初值 = load_settings

        emitted: list[bool] = []
        controller.turnScreenOffChanged.connect(
                lambda: emitted.append(controller.turnScreenOff))
        controller.toggleTurnScreenOff()
        assert controller.turnScreenOff is False
        assert emitted == [False]
        assert json.loads(Path(settings_file).read_text(encoding="utf-8")) \
                ["turn_screen_off"] is False
        assert controller.statusText == "镜像时保持设备屏幕常亮"

        controller.toggleTurnScreenOff()
        assert controller.turnScreenOff is True
        assert emitted == [False, True]
        assert json.loads(Path(settings_file).read_text(encoding="utf-8")) \
                ["turn_screen_off"] is True
        assert controller.statusText == "镜像时将关闭设备屏幕"

        # 新控制器读回已存选择
        second = PanelController("/fake/adb.exe")
        assert second.turnScreenOff is True


def test_toggle_turn_screen_off_save_failure_keeps_state(
        no_adb, prefs_stub, qapp, settings_file, monkeypatch
):
        """保存失败（validate/IO）：只报状态，内部态不漂移、不发信号。"""
        controller = PanelController("/fake/adb.exe")
        emitted: list[bool] = []
        controller.turnScreenOffChanged.connect(lambda: emitted.append(True))

        def boom(settings: Settings) -> None:
                raise ValueError("fps: 期望整数，实际为 'x'")

        monkeypatch.setattr(controller_mod, "save_settings", boom)
        controller.toggleTurnScreenOff()
        assert controller.turnScreenOff is False   # 未漂移
        assert emitted == []
        assert controller.statusText.startswith("设置保存失败")
        assert not Path(settings_file).exists()   # 什么都没写


# ------------------------------------------------------------------- portrait


def test_portrait_prefs_roundtrip(no_adb, prefs_stub, qapp):
        """togglePortrait persists; a fresh controller reads the choice back."""
        controller = PanelController("/fake/adb.exe")
        assert controller.portraitFor("tv.danmaku.bili") is False
        controller.togglePortrait("tv.danmaku.bili")
        assert controller.portraitFor("tv.danmaku.bili") is True
        assert prefs_stub.payload is not None
        saved = json.loads(prefs_stub.payload)
        assert saved["portrait"]["tv.danmaku.bili"] is True

        second = PanelController("/fake/adb.exe")
        assert second.portraitFor("tv.danmaku.bili") is True
        # 无硬编码种子（防过拟合）：未被用户切换过的 APP 一律 16:9 默认。
        assert second.portraitFor("cn.com.langeasy.LangEasyLexis") is False

        second.togglePortrait("tv.danmaku.bili")   # flips back, persists again
        assert second.portraitFor("tv.danmaku.bili") is False
        assert json.loads(prefs_stub.payload)["portrait"]["tv.danmaku.bili"] is False


# ------------------------------------------------------------------ adb swap


def test_set_adb_rebuilds_monitor(no_adb, prefs_stub, qapp):
        """Same path keeps the monitor; a moved adb stops and rebuilds it."""
        controller = PanelController("/old/adb.exe")
        old_monitor = _StubMonitor.instances[-1]
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.setAdb("/old/adb.exe")
        assert controller.adbBinary == "/old/adb.exe"
        assert statuses == ["设置已保存，新会话生效"]
        assert not old_monitor.stopped

        controller.setAdb("/new/adb.exe")
        assert controller.adbBinary == "/new/adb.exe"
        assert old_monitor.stopped
        new_monitor = _StubMonitor.instances[-1]
        assert new_monitor is not old_monitor
        assert new_monitor.started
        assert statuses[-1] == "设置已保存，已切换 adb，新会话生效"
        # refreshInstalled re-ran against the new binary.
        assert no_adb == ["/old/adb.exe", "/new/adb.exe"]


def test_resolve_adb_uses_settings_probe_fallback(no_adb, prefs_stub, qapp, monkeypatch):
        """resolveAdb resolves off-thread (settings > probe > fallback) and applies."""
        controller = PanelController("/old/adb.exe")
        monkeypatch.setattr(
                controller_mod, "load_settings", lambda: (SimpleNamespace(), ["boom"])
        )
        monkeypatch.setattr(
                controller_mod,
                "probe",
                lambda name: SimpleNamespace(path="/discovered/adb.exe"),
        )
        monkeypatch.setattr(
                controller_mod,
                "resolve_adb_path",
                lambda settings, discovered, fallback: "/resolved/adb.exe",
        )
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.resolveAdb()
        deadline = time.monotonic() + 5.0
        while controller.adbBinary != "/resolved/adb.exe" and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
        assert controller.adbBinary == "/resolved/adb.exe"
        assert "boom" in statuses   # settings problems surfaced once
        assert statuses[-1] == "设置已保存，已切换 adb，新会话生效"


# ------------------------------------------------- audio_policy 三态（面板侧）


def _policy(monkeypatch, value: str) -> None:
        """Pin the settings a session start will re-read."""
        monkeypatch.setattr(
                controller_mod, "load_settings",
                lambda: (Settings(audio_policy=value), []))


def _bars(monkeypatch, top: str, bottom: str) -> None:
        """Pin settings with the given bar modes (audio=all：无交接重启）；
        bars 节钉空——设置默认注入的既有断言不掺环境里的 per-app 覆盖
        （per-app 路径有自己的用例）。"""
        monkeypatch.setattr(
                controller_mod, "load_settings",
                lambda: (Settings(top_bar_mode=top, bottom_bar_mode=bottom,
                                  audio_policy="all"), []))
        monkeypatch.setattr(controller_mod, "load_bar_prefs", lambda: {})


def test_audio_latest_restarts_running_sessions_muted(
        no_adb, prefs_stub, qapp, monkeypatch):
        """latest：新会话带音频启动时，旧音频会话以 --no-audio 重启。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                proc = _FakeProc(argv)
                procs.append(proc)
                return proc

        controller._spawn = fake_spawn  # type: ignore[method-assign]
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.startSession("tv.danmaku.bili")
        controller.startSession("com.tencent.mm")

        # spawns: bili(音频) → bili 静音重启 → mm(音频)
        assert len(spawned) == 3
        assert spawned[1] == build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=False, muted=True)
        assert "--no-audio" in spawned[1]
        assert spawned[2] == build_launch_argv(
                "com.tencent.mm", "S1", portrait=False)  # 无硬编码种子：16:9
        assert "--no-audio" not in spawned[2]
        assert procs[0].terminated          # the old bili CLI got SIGTERM
        assert controller._audio_keys == {"com.tencent.mm"}
        assert "哔哩哔哩 已静音重启" in statuses[-1]


def test_audio_latest_skips_muted_and_dead_sessions(
        no_adb, prefs_stub, qapp, monkeypatch):
        """latest 重启只针对存活的音频会话；静音重启过的不再被翻动。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                proc = _FakeProc(argv)
                procs.append(proc)
                return proc

        controller._spawn = fake_spawn  # type: ignore[method-assign]

        controller.startSession("tv.danmaku.bili")
        controller.startSession("com.tencent.mm")   # bili -> muted restart
        mute_spawn_count = len(spawned)
        mm_first = procs[2]                          # mm's first process
        controller.startSession("cn.wps.moffice_eng")   # third session
        # bili (muted) is not restarted again; only mm is.
        assert len(spawned) == mute_spawn_count + 2   # mm muted + wps
        assert mm_first.terminated is True
        assert not any("--no-audio" in a for a in spawned[mute_spawn_count + 1:])


def test_audio_off_pins_no_audio_in_panel_argv(
        no_adb, prefs_stub, qapp, monkeypatch):
        """off：面板直接在 spawn argv 上钉 --no-audio（CLI 侧同样兜底）。"""
        _policy(monkeypatch, "off")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []
        controller._spawn = lambda argv: (spawned.append(argv), _FakeProc(argv))[1]
        controller.startSession("tv.danmaku.bili")
        assert "--no-audio" in spawned[0]
        assert controller._audio_keys == set()


def test_audio_all_spawns_without_mute_and_without_restart(
        no_adb, prefs_stub, qapp, monkeypatch):
        """all：并行音频是显式要求，不做重启也不静音。"""
        _policy(monkeypatch, "all")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        controller.startSession("tv.danmaku.bili")
        controller.startSession("com.tencent.mm")
        assert len(procs) == 2
        assert not any(p.terminated for p in procs)
        assert "--no-audio" not in procs[0].argv
        assert controller._audio_keys == {"tv.danmaku.bili", "com.tencent.mm"}


def test_device_mirror_latest_restart_uses_mirror_argv(
        no_adb, prefs_stub, qapp, monkeypatch):
        """latest：镜像会话启动时同样把音频会话静音重启（镜像 argv 复用）。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        controller._spawn = fake_spawn  # type: ignore[method-assign]
        controller.startSession("tv.danmaku.bili")
        controller.startMirror()
        # spawns: bili(音频) → bili 静音重启 → mirror(音频，最新者胜)
        assert spawned[2] == build_device_mirror_argv("S1")
        assert "--no-audio" not in spawned[2]
        assert spawned[1][-1] == "--no-audio"   # bili's mute respawn


# ------------------------------------------- 音频独占（右键菜单，audio 节）


def test_audio_prefs_roundtrip_and_corruption(prefs_stub):
        """audio 节读写：勾选落盘、重启读回；坏形状丢弃不硬猜。"""
        assert controller_mod.load_audio_prefs() == {}
        controller_mod.save_audio_prefs({"tv.danmaku.bili": {"exclusive": True}})
        assert json.loads(prefs_stub.payload)["audio"] \
                == {"tv.danmaku.bili": {"exclusive": True}}
        assert controller_mod.load_audio_prefs() \
                == {"tv.danmaku.bili": {"exclusive": True}}

        # 手改坏文件：非 dict 选择 / 非 bool exclusive 均丢弃
        prefs_stub.payload = json.dumps({
                "audio": {
                        "junk": "nope",
                        "ok.pkg": {"exclusive": True},
                        "bad.pkg": {"exclusive": "yes"},
                }
        })
        assert controller_mod.load_audio_prefs() == {"ok.pkg": {"exclusive": True}}
        prefs_stub.payload = json.dumps({"audio": ["not", "a", "dict"]})
        assert controller_mod.load_audio_prefs() == {}


def test_audio_exclusive_slots_roundtrip_and_signal(
        no_adb, prefs_stub, qapp):
        """audioExclusiveFor/setAudioExclusive：勾选持久化+状态消息+信号；
        清除不存空壳节；镜像键永不可独占（菜单只在磁贴上）。"""
        controller = PanelController("/fake/adb.exe")
        assert controller.audioExclusiveFor("tv.danmaku.bili") is False
        emitted: list[str] = []
        controller.audioPrefsChanged.connect(emitted.append)

        controller.setAudioExclusive("tv.danmaku.bili", True)
        assert controller.audioExclusiveFor("tv.danmaku.bili") is True
        assert json.loads(prefs_stub.payload)["audio"] \
                == {"tv.danmaku.bili": {"exclusive": True}}
        assert "独占" in controller.statusText
        assert emitted == ["tv.danmaku.bili"]

        # 新控制器读回同一记忆（持久化，非内存态）
        second = PanelController("/fake/adb.exe")
        assert second.audioExclusiveFor("tv.danmaku.bili") is True

        # 清除：包条目整条退场（同 bars 节纪律），再发一次信号
        second.setAudioExclusive("tv.danmaku.bili", False)
        assert second.audioExclusiveFor("tv.danmaku.bili") is False
        assert json.loads(prefs_stub.payload)["audio"] == {}

        # 镜像键：手写进 prefs 也读作不独占
        prefs_stub.payload = json.dumps({"audio": {MIRROR_KEY: {"exclusive": True}}})
        assert PanelController("/fake/adb.exe").audioExclusiveFor(MIRROR_KEY) is False


def test_exclusive_launch_beats_off_policy(no_adb, prefs_stub, qapp, monkeypatch):
        """独占压过全局 off：独占应用带音频启动（argv 不钉 --no-audio）；
        同场非独占应用照旧走全局 off（静音）。"""
        _policy(monkeypatch, "off")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        controller.setAudioExclusive("com.tencent.mm", True)
        spawned: list[list[str]] = []
        controller._spawn = lambda argv: (spawned.append(argv), _FakeProc(argv))[1]

        controller.startSession("com.tencent.mm")
        assert "--no-audio" not in spawned[0]
        assert controller._audio_keys == {"com.tencent.mm"}

        controller.startSession("tv.danmaku.bili")   # 非独占 → 全局 off
        assert "--no-audio" in spawned[1]
        assert controller._audio_keys == {"com.tencent.mm"}


def test_exclusive_launch_restarts_all_others_including_mirror(
        no_adb, prefs_stub, qapp, monkeypatch):
        """独占启动（policy=all 场景）：其余全部音频会话静音重启（含镜像），
        调用序列 = 逐个静音 respawn + 新会话带音频。"""
        _policy(monkeypatch, "all")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        controller.setAudioExclusive("com.tencent.mm", True)
        procs: list[_FakeProc] = []
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                proc = _FakeProc(argv)
                procs.append(proc)
                return proc

        controller._spawn = fake_spawn  # type: ignore[method-assign]
        controller.startSession("tv.danmaku.bili")   # all：并行音频
        controller.startMirror()                      # all：并行音频
        assert len(spawned) == 2
        spawned.clear()

        controller.startSession("com.tencent.mm")    # 独占启动
        # bili 静音重启 → mirror 静音重启 → mm 带音频（all 也拦不住独占）
        assert spawned == [
                build_launch_argv("tv.danmaku.bili", "S1", portrait=False, muted=True),
                build_device_mirror_argv("S1", muted=True),
                build_launch_argv("com.tencent.mm", "S1", portrait=False),
        ]
        assert controller._audio_keys == {"com.tencent.mm"}
        assert procs[0].terminated and procs[1].terminated   # 旧进程都被 SIGTERM
        assert "已静音重启" in controller.statusText


def test_exclusive_owner_exit_leaves_others_muted(
        no_adb, prefs_stub, qapp, monkeypatch):
        """独占者退出后其余会话保持静音：不自动恢复、零额外 respawn
        （手动重启即回——重启走同一仲裁路）。"""
        _policy(monkeypatch, "all")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        controller.setAudioExclusive("com.tencent.mm", True)
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        controller._spawn = fake_spawn  # type: ignore[method-assign]
        controller.startSession("tv.danmaku.bili")   # 音频
        controller.startSession("com.tencent.mm")    # 独占：bili 静音重启
        assert len(spawned) == 3

        controller.stopSession("com.tencent.mm")     # 独占者退出
        assert len(spawned) == 3                       # 没有任何自动恢复重启
        assert controller._sessions["tv.danmaku.bili"].argv[-1] == "--no-audio"


class _LockedLog:
        """Path duck-type whose unlink hits the Windows handle race.

        真机崩溃日志实锤：旧子进程仍握着会话日志句柄时，unlink 抛
        ``PermissionError: [WinError 32]``——时序相关（旧 CLI 退出途中），
        所以「固定比例启动后 Duo.exe 退出」时有时无。
        """

        parent = Path(".")

        def unlink(self, missing_ok: bool = False) -> None:
                raise PermissionError(32, "The process cannot access the file")


def test_restart_muted_survives_locked_log_handle(
        no_adb, prefs_stub, qapp, monkeypatch):
        """P0 回归：日志 unlink 被旧子进程锁住（WinError 32）不得上抛——
        startSession 与 _restart_others_muted 两处都压住，静音重启照常
        走完（未捕获的 OSError 会从槽里上抛成 PyQt qFatal，整面板退出）。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        controller._spawn = lambda argv: _FakeProc(argv)
        controller.startSession("tv.danmaku.bili")   # 正常启动（真日志路径）

        # 此后所有 panel_log_path().unlink 都抛 WinError 32（旧子进程握着）
        monkeypatch.setattr(controller_mod, "panel_log_path", lambda pkg: _LockedLog())

        # latest：mm 启动 → bili 静音重启（两处 unlink 均被压住，不上抛）
        controller.startSession("com.tencent.mm")
        assert controller._audio_keys == {"com.tencent.mm"}
        assert "已静音重启" in controller.statusText
        respawn = controller._sessions["tv.danmaku.bili"]
        assert isinstance(respawn, _FakeProc) and respawn.argv[-1] == "--no-audio"


# ---------------------------------- 断开保留画面（右键菜单，behavior 节）


def test_behavior_prefs_roundtrip_and_corruption(prefs_stub):
        """behavior 节读写：勾选落盘、重启读回；坏形状丢弃不硬猜。
        独立于 audio 节（audio 语义不动，另开新节）。"""
        assert controller_mod.load_behavior_prefs() == {}
        controller_mod.save_behavior_prefs({"tv.danmaku.bili": {"keep_vd": True}})
        assert json.loads(prefs_stub.payload)["behavior"] \
                == {"tv.danmaku.bili": {"keep_vd": True}}
        assert controller_mod.load_behavior_prefs() \
                == {"tv.danmaku.bili": {"keep_vd": True}}

        # 手改坏文件：非 dict 选择 / 非 bool keep_vd 均丢弃
        prefs_stub.payload = json.dumps({
                "behavior": {
                        "junk": "nope",
                        "ok.pkg": {"keep_vd": True},
                        "bad.pkg": {"keep_vd": "yes"},
                }
        })
        assert controller_mod.load_behavior_prefs() == {"ok.pkg": {"keep_vd": True}}
        prefs_stub.payload = json.dumps({"behavior": ["not", "a", "dict"]})
        assert controller_mod.load_behavior_prefs() == {}


def test_keep_vd_slots_roundtrip_and_signal(no_adb, prefs_stub, qapp):
        """keepVdFor/setKeepVd：勾选持久化+状态消息+信号；清除不存空壳节；
        镜像键永不可保留（设备镜像无自建虚拟屏，菜单只在磁贴上）。"""
        controller = PanelController("/fake/adb.exe")
        assert controller.keepVdFor("tv.danmaku.bili") is False
        emitted: list[str] = []
        controller.behaviorPrefsChanged.connect(emitted.append)

        controller.setKeepVd("tv.danmaku.bili", True)
        assert controller.keepVdFor("tv.danmaku.bili") is True
        assert json.loads(prefs_stub.payload)["behavior"] \
                == {"tv.danmaku.bili": {"keep_vd": True}}
        assert "保留在虚拟屏" in controller.statusText
        assert emitted == ["tv.danmaku.bili"]

        # 新控制器读回同一记忆（持久化，非内存态）
        second = PanelController("/fake/adb.exe")
        assert second.keepVdFor("tv.danmaku.bili") is True

        # 清除：包条目整条退场（同 bars/audio 节纪律），再发一次信号
        second.setKeepVd("tv.danmaku.bili", False)
        assert second.keepVdFor("tv.danmaku.bili") is False
        assert json.loads(prefs_stub.payload)["behavior"] == {}
        assert "退回手机主屏" in second.statusText

        # 镜像键：手写进 prefs 也读作不保留
        prefs_stub.payload = json.dumps({"behavior": {MIRROR_KEY: {"keep_vd": True}}})
        assert PanelController("/fake/adb.exe").keepVdFor(MIRROR_KEY) is False


def test_build_launch_argv_keep_vd_injection(no_adb):
        """argv 注入：默认不带；keep_vd=True 追加 --no-vd-destroy-content
        （--no-audio 仍恒居末位）；display fixed 记忆路径同享；设备镜像
        恒不注入（无自建虚拟屏）。"""
        base = build_launch_argv("tv.danmaku.bili", "S1", portrait=False)
        assert "--no-vd-destroy-content" not in base

        keep = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=False, muted=True, keep_vd=True)
        assert "--no-vd-destroy-content" in keep
        assert keep[-1] == "--no-audio"   # 音频末位不变量保持

        # display fixed 记忆路径（§3.7）与 keep_vd 组合：两注入点共存
        fixed = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=False,
                display={
                        "mode": "fixed", "aspect": "16:9",
                        "width": 1600, "height": 900,
                },
                keep_vd=True)
        assert "--display" in fixed and fixed[fixed.index("--display") + 1] == "fixed"
        assert "--no-vd-destroy-content" in fixed

        # 设备镜像 argv 恒不注入
        assert "--no-vd-destroy-content" not in build_device_mirror_argv("S1")


def test_start_session_injects_keep_vd_per_app(
        no_adb, prefs_stub, qapp, monkeypatch):
        """startSession 按包注入：勾选应用带旗标，未勾选不带；aspect
        一级路径（固定比例启动）同享注入。"""
        _policy(monkeypatch, "all")   # 无音频交接，spawn 序列干净
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []
        controller._spawn = lambda argv: (spawned.append(argv), _FakeProc(argv))[1]

        controller.setKeepVd("com.tencent.mm", True)
        controller.startSession("tv.danmaku.bili")   # 未勾选
        controller.startSession("com.tencent.mm")    # 勾选
        assert "--no-vd-destroy-content" not in spawned[0]
        assert "--no-vd-destroy-content" in spawned[1]

        # 固定比例启动路径同享（第三个包，避开上面的在跑去重）
        controller.setKeepVd("cn.com.langeasy.LangEasyLexis", True)
        controller.startSessionWithAspect("cn.com.langeasy.LangEasyLexis", "16:9")
        assert "--no-vd-destroy-content" in spawned[2]


def test_keep_vd_survives_audio_handover(
        no_adb, prefs_stub, qapp, monkeypatch):
        """音频交接静音重启不丢 keep_vd（同 _session_size 重钉纪律）：
        重启 argv 同时带 --no-vd-destroy-content 与末位 --no-audio。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        controller.setKeepVd("tv.danmaku.bili", True)
        controller.setAudioExclusive("com.tencent.mm", True)
        spawned: list[list[str]] = []
        controller._spawn = lambda argv: (spawned.append(argv), _FakeProc(argv))[1]

        controller.startSession("tv.danmaku.bili")
        controller.startSession("com.tencent.mm")   # 独占启动 → bili 静音重启
        respawn = spawned[1]
        assert "--no-vd-destroy-content" in respawn
        assert respawn[-1] == "--no-audio"


# ------------------------------------------- 设备媒体音量（镜像卡滑杆）


def _wait_status(qapp, statuses: list[str], needle: str, timeout: float = 3.0) -> bool:
        """Spin the loop until a status line containing ``needle`` lands."""
        def landed() -> bool:
                return any(needle in line for line in statuses)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not landed():
                qapp.processEvents()
                time.sleep(0.01)
        return landed()


def test_set_media_volume_runs_command_and_becomes_known(
        no_adb, prefs_stub, qapp, monkeypatch):
        """setMediaVolume：cmd media_session 走绑 serial 的 Adb，属性转已知
        态（mediaVolumeChanged 即时发）；越界值钳进 0..15。"""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        assert controller.mediaVolume == -1.0
        calls: list[tuple[str, int]] = []

        def fake_media_volume(adb, index, timeout: float = 5.0):
                calls.append((adb.serial, index))

        monkeypatch.setattr(controller_mod, "media_volume", fake_media_volume)
        notified: list[bool] = []
        controller.mediaVolumeChanged.connect(lambda: notified.append(True))
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.setMediaVolume(11)
        assert controller.mediaVolume == 11.0
        assert notified == [True]   # 已知态即时通知（滑杆切填充/拇指）
        assert _wait_status(qapp, statuses, "媒体音量 11/15")
        assert calls == [("S1", 11)]

        controller.setMediaVolume(99)   # 越界钳位
        assert controller.mediaVolume == 15.0
        assert _wait_status(qapp, statuses, "媒体音量 15/15")
        assert calls[-1] == ("S1", 15)


def test_set_media_volume_without_device_stays_unknown(
        no_adb, prefs_stub, qapp, monkeypatch):
        """无设备：只报状态，不动已知态、不起命令（滑杆离线即隐藏，防御路）。"""
        controller = PanelController("/fake/adb.exe")
        calls: list[int] = []
        monkeypatch.setattr(
                controller_mod, "media_volume",
                lambda adb, index, timeout=5.0: calls.append(index))
        controller.setMediaVolume(7)
        assert controller.mediaVolume == -1.0
        assert controller.statusText == "设备未连接，音量未调整"
        qapp.processEvents()
        assert calls == []


def test_set_media_volume_reports_adb_failure(
        no_adb, prefs_stub, qapp, monkeypatch):
        """设备侧失败：错误沿 worker hop 回状态行（已知态已是滑杆自持值）。"""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})

        def boom(adb, index, timeout: float = 5.0):
                raise AdbError("device went away")

        monkeypatch.setattr(controller_mod, "media_volume", boom)
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.setMediaVolume(4)
        assert controller.mediaVolume == 4.0
        assert _wait_status(qapp, statuses, "媒体音量调整失败")


# ------------------------------------------- 按比例打开（fixed 虚拟屏，§3.6）


def test_build_launch_argv_pins_fixed_geometry(no_adb):
        """width/height pin --display fixed; --portrait follows the geometry.

        The preset owns the shape: h > w adds --portrait regardless of the
        remembered pref, w > h drops it; without geometry the legacy flex
        argv (and the pref's role in it) stays byte-for-byte unchanged.
        """
        wide = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=False, width=2560, height=1440)
        assert wide[wide.index("--display") + 1] == "fixed"
        assert wide[wide.index("--width") + 1] == "2560"
        assert wide[wide.index("--height") + 1] == "1440"
        assert "--portrait" not in wide

        tall = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=False, width=1440, height=2560)
        assert "--portrait" in tall

        overruled = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=True, width=2560, height=1440)
        assert "--portrait" not in overruled   # geometry beats the pref

        legacy = build_launch_argv("tv.danmaku.bili", "S1", portrait=True)
        assert "--portrait" in legacy
        assert "--width" not in legacy and "--display" not in legacy


def test_start_session_with_aspect_spawns_fixed_argv(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """16:9 / 9:16 spawn with the frozen geometry; status names the ratio."""
        _policy(monkeypatch, "all")   # no audio handover: one spawn per click
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        monkeypatch.setattr(controller, "_spawn", fake_spawn)
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.startSessionWithAspect("com.tencent.mm", "16:9")
        assert spawned == [build_launch_argv(
                "com.tencent.mm", "S1", portrait=False, width=2560, height=1440)]
        assert "--width" in spawned[0]
        assert controller.statusText == "以 16:9 打开 微信"
        assert controller.runningSessions[0]["key"] == "com.tencent.mm"
        assert controller.engineLocked

        # A portrait preset derives --portrait from the geometry alone.
        controller.startSessionWithAspect("tv.danmaku.bili", "9:16")
        assert spawned[1][spawned[1].index("--width") + 1] == "1440"
        assert spawned[1][spawned[1].index("--height") + 1] == "2560"
        assert "--portrait" in spawned[1]
        assert controller.statusText == "以 9:16 打开 哔哩哔哩"


def test_start_session_with_aspect_unknown_and_body_without_device(
        no_adb, prefs_stub, qapp
):
        """Body ids need a device (wm size); unknown ids never spawn."""
        controller = PanelController("/fake/adb.exe")   # monitor: offline
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)

        controller.startSessionWithAspect("com.tencent.mm", "body-l")
        assert statuses == ["机身比例需连接设备后使用"]
        assert controller.runningSessions == []

        controller.startSessionWithAspect("com.tencent.mm", "16:10")
        assert statuses[-1] == "未知比例：16:10"
        assert controller.runningSessions == []


def test_body_aspect_probes_wm_size_and_caches(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """body-l probes wm size once; body-p transposes from the cache."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        fake = _FakeAdb("/fake/adb.exe", "S1", results=["Physical size: 1440x3200\n"])
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        spawned: list[list[str]] = []
        controller._spawn = lambda argv: (spawned.append(argv), _FakeProc(argv))[1]

        controller.startSessionWithAspect("com.tencent.mm", "body-l")
        assert fake.calls == [("shell", "wm", "size")]
        first = spawned[0]
        assert first[first.index("--width") + 1] == "3200"
        assert first[first.index("--height") + 1] == "1440"
        assert "--portrait" not in first
        assert controller.statusText == "以 机身 打开 微信"

        # body-p transposes the CACHED preset: no second wm size probe
        # (mm gets the audio-handover respawn along the way, still fixed).
        controller.startSessionWithAspect("tv.danmaku.bili", "body-p")
        assert fake.calls == [("shell", "wm", "size")]   # cache hit
        assert len(spawned) == 3   # mm fixed → mm 静音重启(同几何) → bili body-p
        last = spawned[-1]
        assert last[last.index("--width") + 1] == "1440"
        assert last[last.index("--height") + 1] == "3200"
        assert "--portrait" in last


def test_body_aspect_probe_failure_cached_then_reprobes(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """A failed probe caches None for the TTL window, then re-probes."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        attempts: list[str] = []

        def boom(*args: str, timeout: float = 60.0) -> str:
                attempts.append("wm size")
                raise AdbError("adb -s S1 shell failed: device offline")

        flaky = _FakeAdb("/fake/adb.exe", "S1")
        flaky.run = boom  # type: ignore[method-assign]
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: flaky)

        controller.startSessionWithAspect("com.tencent.mm", "body-l")
        assert controller.statusText == "机身比例需连接设备后使用"
        controller.startSessionWithAspect("com.tencent.mm", "body-p")
        assert controller.statusText == "机身比例需连接设备后使用"   # cached miss
        assert attempts == ["wm size"]   # one probe served BOTH body clicks
        assert controller.runningSessions == []

        # TTL 过期：下次点击重新探测（这次成功）。
        assert controller._body_probe_at is not None
        controller._body_probe_at -= 601.0
        good = _FakeAdb("/fake/adb.exe", "S1", results=["Override size: 1080x2400\n"])
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: good)
        spawned: list[list[str]] = []
        controller._spawn = lambda argv: (spawned.append(argv), _FakeProc(argv))[1]
        controller.startSessionWithAspect("com.tencent.mm", "body-l")
        assert spawned[0][spawned[0].index("--width") + 1] == "3200"
        assert controller.statusText == "以 机身 打开 微信"


def test_aspect_session_survives_audio_handover_with_geometry(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """latest 音频切换的静音重启重新钉住同一几何，不降级成 flex。"""
        _policy(monkeypatch, "latest")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        controller._spawn = fake_spawn  # type: ignore[method-assign]

        controller.startSessionWithAspect("com.tencent.mm", "16:9")
        controller.startSessionWithAspect("tv.danmaku.bili", "16:9")
        # spawns: mm(16:9 音频) → mm 静音重启（同一 2560×1440，非 flex）→ bili(16:9)
        assert len(spawned) == 3
        assert spawned[1] == build_launch_argv(
                "com.tencent.mm", "S1", portrait=False, muted=True,
                width=2560, height=1440)
        assert "--width" in spawned[1]
        assert spawned[1][-1] == "--no-audio"


def test_plain_restart_after_aspect_session_is_flex(no_adb, prefs_stub, qapp, monkeypatch):
        """An aspect session dying clears its pin: the plain start is flex again."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.startSessionWithAspect("tv.danmaku.bili", "21:9")
        assert "--width" in procs[0].argv
        assert controller._session_size == {"tv.danmaku.bili": (3360, 1440)}

        procs[0]._exit_code = 0
        controller.reapSessions()
        assert controller._session_size == {}

        controller.startSession("tv.danmaku.bili")
        assert "--width" not in procs[1].argv
        assert "--display" not in procs[1].argv


def test_duplicate_aspect_start_routes_to_display_move(
        no_adb, prefs_stub, qapp, monkeypatch, tmp_path
):
        """Live session + 按比例 click = move onto its display, no 2nd spawn."""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)
        monkeypatch.setattr(
                controller_mod, "panel_log_path", lambda pkg: tmp_path / f"{pkg}.log"
        )

        controller.startSession("tv.danmaku.bili")   # flex session lives
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        controller.startSessionWithAspect("tv.danmaku.bili", "9:16")
        assert statuses == ["哔哩哔哩 虚拟屏未就绪，稍后重试"]   # dedupe route
        assert len(procs) == 1   # degradation, not a session rebuild


# ------------------------------------- 显示模式记忆（display prefs，§3.7）


def test_display_prefs_roundtrip_and_default(no_adb, prefs_stub, qapp):
        """display 节读写：缺省 flex；fixed 记忆落盘、重启读回；flex 可改写。"""
        controller = PanelController("/fake/adb.exe")
        assert controller.displayModeFor("tv.danmaku.bili") == {"mode": "flex"}
        assert controller.displayModeFor("com.tencent.mm") == {"mode": "flex"}

        controller.setDisplayFixed("tv.danmaku.bili", "16:9")
        assert controller.displayModeFor("tv.danmaku.bili") == {
                "mode": "fixed", "aspect": "16:9"}
        assert json.loads(prefs_stub.payload)["display"]["tv.danmaku.bili"] \
                == {"mode": "fixed", "aspect": "16:9"}
        assert controller.statusText == "哔哩哔哩 将以 16:9 常驻"

        # 新控制器读回同一记忆（持久化，非内存态）
        second = PanelController("/fake/adb.exe")
        assert second.displayModeFor("tv.danmaku.bili") == {
                "mode": "fixed", "aspect": "16:9"}
        # 改回自适应：写 {"mode":"flex"} 持久化
        second.setDisplayFlex("tv.danmaku.bili")
        assert second.displayModeFor("tv.danmaku.bili") == {"mode": "flex"}
        assert json.loads(prefs_stub.payload)["display"]["tv.danmaku.bili"] \
                == {"mode": "flex"}
        assert second.statusText == "哔哩哔哩 将自适应窗口"


def test_display_prefs_section_shares_doc_with_others(no_adb, prefs_stub, qapp):
        """display 与 portrait/pinned 共享一份文档：存一节不丢其余节。"""
        rows = _catalog_rows()
        controller = PanelController("/fake/adb.exe")
        controller.togglePin(rows[-1][1])
        controller.togglePortrait(rows[0][1])
        controller.setDisplayFixed(rows[1][1], "21:9")
        doc = json.loads(prefs_stub.payload)
        assert doc["pinned"] == [rows[-1][1]]
        assert doc["portrait"][rows[0][1]] is True
        assert doc["display"][rows[1][1]] == {"mode": "fixed", "aspect": "21:9"}


def test_set_display_fixed_rejects_bad_ids_without_persisting(
        no_adb, prefs_stub, qapp
):
        """非法 id / 无设备的机身 id：只报状态，不落库、不发信号。"""
        controller = PanelController("/fake/adb.exe")   # monitor: offline
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        emitted: list[str] = []
        controller.displayModeChanged.connect(emitted.append)

        controller.setDisplayFixed("com.tencent.mm", "16:10")
        assert statuses == ["未知比例：16:10"]
        controller.setDisplayFixed("com.tencent.mm", "body-l")
        assert statuses[-1] == "机身比例需连接设备后使用"
        assert emitted == []                       # 拒绝路径不宣告变化
        assert controller.displayModeFor("com.tencent.mm") == {"mode": "flex"}
        assert prefs_stub.payload is None           # 什么都没写


def test_set_display_fixed_body_persists_with_device(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """有设备时机身 id 经 wm size 探测后落库（label = 机身）。"""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        fake = _FakeAdb("/fake/adb.exe", "S1", results=["Physical size: 1440x3200\n"])
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)

        controller.setDisplayFixed("com.tencent.mm", "body-l")
        assert fake.calls == [("shell", "wm", "size")]
        assert controller.displayModeFor("com.tencent.mm") == {
                "mode": "fixed", "aspect": "body-l"}
        assert controller.statusText == "微信 将以 机身 常驻"
        assert json.loads(prefs_stub.payload)["display"]["com.tencent.mm"] \
                == {"mode": "fixed", "aspect": "body-l"}


def test_display_slots_signal_carries_package(no_adb, prefs_stub, qapp):
        """displayModeChanged(package)：成功写入才发，QML 菜单按包名刷新。"""
        controller = PanelController("/fake/adb.exe")
        emitted: list[str] = []
        controller.displayModeChanged.connect(emitted.append)
        controller.setDisplayFixed("tv.danmaku.bili", "16:9")
        assert emitted == ["tv.danmaku.bili"]
        controller.setDisplayFlex("tv.danmaku.bili")
        assert emitted == ["tv.danmaku.bili", "tv.danmaku.bili"]


def test_build_launch_argv_display_param_pins_fixed(no_adb):
        """display 记忆参数：fixed 冻结 id 注入 fixed argv，方向随几何；
        显式几何优先；flex/None 走老 flex 路径。"""
        remembered = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=True,
                display={"mode": "fixed", "aspect": "16:9"})
        assert remembered[remembered.index("--display") + 1] == "fixed"
        assert remembered[remembered.index("--width") + 1] == "2560"
        assert remembered[remembered.index("--height") + 1] == "1440"
        assert "--portrait" not in remembered   # 几何定方向，pref 让位

        # 控制器解析好的机身几何（body id 带显式 width/height）
        body = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=False,
                display={"mode": "fixed", "aspect": "body-p",
                         "width": 1440, "height": 3200})
        assert "--portrait" in body   # h > w

        # 显式几何仍然优先于 display（一次性按比例启动路径不受影响）
        explicit = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=False,
                width=3360, height=1440,
                display={"mode": "fixed", "aspect": "16:9"})
        assert explicit[explicit.index("--width") + 1] == "3360"

        flex = build_launch_argv(
                "tv.danmaku.bili", "S1", portrait=True,
                display={"mode": "flex"})
        assert "--display" not in flex and "--width" not in flex
        assert "--portrait" in flex
        legacy = build_launch_argv("tv.danmaku.bili", "S1", portrait=True)
        assert flex == legacy   # flex 记忆 = 老 flex 路径逐字节一致


def test_start_session_uses_remembered_fixed_display(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """普通点击按记忆启动：fixed 记忆 → argv 注入 --display fixed
        --width/--height；几何钉进 _session_size（音频切换重启不降级）。"""
        _policy(monkeypatch, "all")   # 无音频交接，一次点击一次 spawn
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.setDisplayFixed("tv.danmaku.bili", "16:9")
        controller.startSession("tv.danmaku.bili")
        argv = procs[0].argv
        assert argv[argv.index("--display") + 1] == "fixed"
        assert argv[argv.index("--width") + 1] == "2560"
        assert argv[argv.index("--height") + 1] == "1440"
        assert controller._session_size == {"tv.danmaku.bili": (2560, 1440)}

        # 竖屏预设记忆：--portrait 随几何注入
        procs[0]._exit_code = 0
        controller.reapSessions()
        controller.setDisplayFixed("tv.danmaku.bili", "9:16")
        controller.startSession("tv.danmaku.bili")
        argv = procs[1].argv
        assert argv[argv.index("--width") + 1] == "1440"
        assert argv[argv.index("--height") + 1] == "2560"
        assert "--portrait" in argv


def test_start_session_flex_again_after_set_display_flex(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """setDisplayFlex 后普通点击回到 flex：无 fixed argv（缺省同样）。"""
        _policy(monkeypatch, "all")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.setDisplayFixed("com.tencent.mm", "16:9")
        controller.setDisplayFlex("com.tencent.mm")
        controller.startSession("com.tencent.mm")
        assert "--display" not in procs[0].argv
        assert "--width" not in procs[0].argv
        assert controller._session_size == {}


def test_start_session_remembered_body_display_probes(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """记忆的机身比例：启动时经缓存探测解析成具体几何（一次 wm size）。"""
        _policy(monkeypatch, "all")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        fake = _FakeAdb("/fake/adb.exe", "S1", results=["Physical size: 1440x3200\n"])
        monkeypatch.setattr(controller_mod, "Adb", lambda b, s: fake)
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.setDisplayFixed("com.tencent.mm", "body-l")   # 探测+落库
        controller.startSession("com.tencent.mm")
        argv = procs[0].argv
        assert argv[argv.index("--width") + 1] == "3200"
        assert argv[argv.index("--height") + 1] == "1440"
        assert "--portrait" not in argv
        assert fake.calls == [("shell", "wm", "size")]   # 缓存命中，不再探测


def test_aspect_one_shot_launch_writes_no_display_prefs(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """一次性按比例启动（startSessionWithAspect）不改持久记忆。"""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.startSessionWithAspect("tv.danmaku.bili", "16:9")
        assert "--width" in procs[0].argv
        assert prefs_stub.payload is None   # 全程未写 prefs
        assert controller.displayModeFor("tv.danmaku.bili") == {"mode": "flex"}


# ------------------------------------ 窗口栏模式注入（--chrome-top/--chrome-bottom）

_BAR_COMBOS = [
        ("immersive", "immersive"),
        ("immersive", "native"),
        ("native", "immersive"),
        ("native", "native"),
        # none（2026-09-09 第三态：该边不建栏）同路注入，无专门分支
        ("immersive", "none"),
        ("none", "immersive"),
        ("none", "none"),
]


@pytest.mark.parametrize("top,bottom", _BAR_COMBOS)
def test_build_launch_argv_injects_bar_modes(monkeypatch, top, bottom):
        """枚举全组合（含 none）：两旗标取自当前 Settings 实例；--no-audio
        仍居末位。"""
        _bars(monkeypatch, top, bottom)
        argv = build_launch_argv("tv.danmaku.bili", "S1", portrait=False, muted=True)
        assert argv[argv.index("--chrome-top") + 1] == top
        assert argv[argv.index("--chrome-bottom") + 1] == bottom
        assert argv[-1] == "--no-audio"


@pytest.mark.parametrize("top,bottom", _BAR_COMBOS)
def test_build_device_mirror_argv_injects_bar_modes(monkeypatch, top, bottom):
        """设备镜像 argv 同样注入两旗标。"""
        _bars(monkeypatch, top, bottom)
        argv = build_device_mirror_argv("S1")
        assert argv[argv.index("--chrome-top") + 1] == top
        assert argv[argv.index("--chrome-bottom") + 1] == bottom


def test_start_session_spawn_carries_settings_bar_modes(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """startSession 的 spawn argv 带上设置里的两旗标（每次启动重读）。"""
        _bars(monkeypatch, "native", "immersive")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        monkeypatch.setattr(controller, "_spawn", fake_spawn)
        controller.startSession("tv.danmaku.bili")
        argv = spawned[0]
        assert argv[argv.index("--chrome-top") + 1] == "native"
        assert argv[argv.index("--chrome-bottom") + 1] == "immersive"


def test_start_mirror_spawn_carries_settings_bar_modes(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """startMirror 的 spawn argv 同样带上两旗标。"""
        _bars(monkeypatch, "immersive", "native")
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        monkeypatch.setattr(controller, "_spawn", fake_spawn)
        controller.startMirror()
        argv = spawned[0]
        assert argv[argv.index("--chrome-top") + 1] == "immersive"
        assert argv[argv.index("--chrome-bottom") + 1] == "native"


def test_bar_modes_reread_per_launch(no_adb, prefs_stub, qapp, monkeypatch):
        """两次启动之间改设置：下一次 spawn 即生效（不缓存旧值）。"""
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        monkeypatch.setattr(controller, "_spawn", fake_spawn)
        _bars(monkeypatch, "immersive", "immersive")
        controller.startSession("tv.danmaku.bili")
        procs = list(controller.sessions.values())
        procs[0]._exit_code = 0
        controller.reapSessions()
        _bars(monkeypatch, "native", "native")
        controller.startSession("tv.danmaku.bili")
        assert spawned[1][spawned[1].index("--chrome-top") + 1] == "native"
        assert spawned[1][spawned[1].index("--chrome-bottom") + 1] == "native"


# ---------------------- 窗口栏按应用设置（gui_prefs.json bars 节）


def test_bar_prefs_roundtrip_and_default(no_adb, prefs_stub, qapp):
        """bars 节读写：缺省跟随设置页默认（下巴新默认 none）；显式 override
        落盘、重启读回；空串清除后包条目整个退场（不留空壳节）。"""
        controller = PanelController("/fake/adb.exe")
        assert controller.barModeFor("tv.danmaku.bili", "top") \
                == {"explicit": False, "mode": "immersive"}
        assert controller.barModeFor("tv.danmaku.bili", "bottom") \
                == {"explicit": False, "mode": "none"}

        controller.setAppBar("tv.danmaku.bili", "top", "native")
        assert controller.barModeFor("tv.danmaku.bili", "top") \
                == {"explicit": True, "mode": "native"}
        # 另一条不受牵连：仍跟默认（下巴 none）
        assert controller.barModeFor("tv.danmaku.bili", "bottom") \
                == {"explicit": False, "mode": "none"}
        assert json.loads(prefs_stub.payload)["bars"]["tv.danmaku.bili"] \
                == {"top": "native", "bottom": None}
        assert controller.statusText == "哔哩哔哩 上巴将使用系统栏"

        # 新控制器读回同一记忆（持久化，非内存态）
        second = PanelController("/fake/adb.exe")
        assert second.barModeFor("tv.danmaku.bili", "top") \
                == {"explicit": True, "mode": "native"}

        # 空串清除 override：explicit 消失、effective 回默认，包条目退场
        second.setAppBar("tv.danmaku.bili", "top", "")
        assert second.barModeFor("tv.danmaku.bili", "top") \
                == {"explicit": False, "mode": "immersive"}
        assert json.loads(prefs_stub.payload)["bars"] == {}
        assert second.statusText == "哔哩哔哩 上巴将跟随默认窗口栏"


def test_set_default_bar_mode_writes_settings_defaults(
        no_adb, prefs_stub, qapp, settings_file
):
        """2026-09-09 用户反馈：镜像卡右键菜单不够完整——窗口栏小节写的是
        设置页默认（设备镜像无应用包）。setDefaultBarMode 落 settings.json
        的 top/bottom_bar_mode、状态文案、barPrefsChanged("") 供镜像菜单
        刷新圆点；同值 no-op；非法 which/mode 只报状态不落库。"""
        controller = PanelController("/fake/adb.exe")
        seen: list[str] = []
        controller.barPrefsChanged.connect(seen.append)

        controller.setDefaultBarMode("top", "native")
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["top_bar_mode"] == "native"
        assert raw["bottom_bar_mode"] == "none"   # 未动的字段原样
        assert controller.statusText == "默认上巴将使用系统栏"
        assert seen == [""]

        # 同值 no-op：不写盘不发信号（圆点已在该项）
        controller.setDefaultBarMode("top", "native")
        assert seen == [""]

        controller.setDefaultBarMode("bottom", "immersive")
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["bottom_bar_mode"] == "immersive"
        assert raw["top_bar_mode"] == "native"
        assert controller.statusText == "默认下巴将使用沉浸栏"
        assert seen == ["", ""]

        # 非法值：只报状态，不落库
        controller.setDefaultBarMode("middle", "immersive")
        controller.setDefaultBarMode("top", "floating")
        assert controller.statusText.startswith("未知窗口栏")
        raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
        assert raw["top_bar_mode"] == "native"


def test_set_app_bar_none_persists_and_reports(no_adb, prefs_stub, qapp):
        """none（不显示）：setAppBar 落库 bars 节、状态文案「将不显示」
        （不套「将使用…栏」句式）、barModeFor 回读 explicit；启动 argv 注入
        none 见 _BAR_COMBOS 用例。"""
        controller = PanelController("/fake/adb.exe")
        controller.setAppBar("tv.danmaku.bili", "bottom", "none")
        assert controller.barModeFor("tv.danmaku.bili", "bottom") \
                == {"explicit": True, "mode": "none"}
        assert json.loads(prefs_stub.payload)["bars"]["tv.danmaku.bili"] \
                == {"top": None, "bottom": "none"}
        assert controller.statusText == "哔哩哔哩 下巴将不显示"

        # 新控制器读回同一记忆（持久化，非内存态）
        second = PanelController("/fake/adb.exe")
        assert second.barModeFor("tv.danmaku.bili", "bottom") \
                == {"explicit": True, "mode": "none"}


@pytest.mark.parametrize("which", ["top", "bottom"])
@pytest.mark.parametrize("default,override,expected", [
        ("immersive", None, "immersive"),
        ("immersive", "immersive", "immersive"),
        ("immersive", "native", "native"),
        ("immersive", "none", "none"),
        ("native", None, "native"),
        ("native", "immersive", "immersive"),
        ("native", "native", "native"),
        ("none", None, "none"),
        ("none", "immersive", "immersive"),
        ("none", "none", "none"),
])
def test_effective_bar_resolution_matrix(
        no_adb, prefs_stub, qapp, monkeypatch, which, default, override, expected
):
        """解析矩阵（含 none）：effective = bars[pkg][which] 若显式，否则
        settings 默认。"""
        monkeypatch.setattr(
                controller_mod, "load_settings",
                lambda: (Settings(top_bar_mode=default, bottom_bar_mode=default), []))
        controller = PanelController("/fake/adb.exe")
        if override is not None:
                controller.setAppBar("com.tencent.mm", which, override)
        assert controller.barModeFor("com.tencent.mm", which) == {
                "explicit": override is not None,
                "mode": expected,
        }


def test_set_app_bar_rejects_bad_values(no_adb, prefs_stub, qapp):
        """非法 which/mode：只报状态，不落库、不发信号。"""
        controller = PanelController("/fake/adb.exe")
        statuses: list[str] = []
        controller.statusChanged.connect(statuses.append)
        emitted: list[str] = []
        controller.barPrefsChanged.connect(emitted.append)

        controller.setAppBar("com.tencent.mm", "left", "native")
        assert statuses == ["未知窗口栏：left"]
        controller.setAppBar("com.tencent.mm", "top", "both")
        assert statuses[-1] == "未知窗口栏模式：both"
        assert emitted == []
        assert prefs_stub.payload is None
        assert controller.barModeFor("com.tencent.mm", "top") \
                == {"explicit": False, "mode": "immersive"}


def test_bar_prefs_changed_signal_carries_package(no_adb, prefs_stub, qapp):
        """barPrefsChanged(package)：成功写入（含清除）才发，QML 按包名刷圆点。"""
        controller = PanelController("/fake/adb.exe")
        emitted: list[str] = []
        controller.barPrefsChanged.connect(emitted.append)
        controller.setAppBar("tv.danmaku.bili", "top", "native")
        assert emitted == ["tv.danmaku.bili"]
        controller.setAppBar("tv.danmaku.bili", "top", "")
        assert emitted == ["tv.danmaku.bili", "tv.danmaku.bili"]


def test_bar_prefs_section_shares_doc_with_others(no_adb, prefs_stub, qapp):
        """bars 与 portrait/pinned/display 共享一份文档：存一节不丢其余节。"""
        rows = _catalog_rows()
        controller = PanelController("/fake/adb.exe")
        controller.togglePin(rows[-1][1])
        controller.togglePortrait(rows[0][1])
        controller.setDisplayFixed(rows[1][1], "21:9")
        controller.setAppBar(rows[0][1], "bottom", "native")
        doc = json.loads(prefs_stub.payload)
        assert doc["pinned"] == [rows[-1][1]]
        assert doc["portrait"][rows[0][1]] is True
        assert doc["display"][rows[1][1]] == {"mode": "fixed", "aspect": "21:9"}
        assert doc["bars"][rows[0][1]] == {"top": None, "bottom": "native"}


def test_build_launch_argv_bar_override_beats_settings(prefs_stub, monkeypatch):
        """argv 注入按包取 effective：override 优先，同文件里其他包仍默认；
        镜像 argv（无包）恒取默认。"""
        monkeypatch.setattr(
                controller_mod, "load_settings",
                lambda: (Settings(top_bar_mode="native", bottom_bar_mode="native"), []))
        controller_mod.save_bar_prefs(
                {"tv.danmaku.bili": {"top": "immersive", "bottom": None}})
        argv = build_launch_argv("tv.danmaku.bili", "S1", portrait=False)
        assert argv[argv.index("--chrome-top") + 1] == "immersive"   # override
        assert argv[argv.index("--chrome-bottom") + 1] == "native"   # 跟默认
        other = build_launch_argv("com.tencent.mm", "S1", portrait=False)
        assert other[other.index("--chrome-top") + 1] == "native"    # 无记忆
        assert other[other.index("--chrome-bottom") + 1] == "native"
        mirror = build_device_mirror_argv("S1")
        assert mirror[mirror.index("--chrome-top") + 1] == "native"   # 镜像恒默认
        assert mirror[mirror.index("--chrome-bottom") + 1] == "native"


def test_start_session_argv_uses_per_app_bar_override(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """startSession 的 spawn argv 按包取 effective；设置包间互不串；
        镜像恒默认。"""
        monkeypatch.setattr(
                controller_mod, "load_settings",
                lambda: (Settings(top_bar_mode="immersive", bottom_bar_mode="native",
                                  audio_policy="all"), []))
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        # bili：只覆盖下巴（默认 native → immersive）；mm：只覆盖上巴
        controller.setAppBar("tv.danmaku.bili", "bottom", "immersive")
        controller.setAppBar("com.tencent.mm", "top", "native")

        controller.startSession("tv.danmaku.bili")
        argv = procs[0].argv
        assert argv[argv.index("--chrome-top") + 1] == "immersive"   # 跟默认
        assert argv[argv.index("--chrome-bottom") + 1] == "immersive"   # override

        controller.startSession("com.tencent.mm")
        argv = procs[1].argv
        assert argv[argv.index("--chrome-top") + 1] == "native"      # override
        assert argv[argv.index("--chrome-bottom") + 1] == "native"   # 跟默认

        # 未单独设置的包 = 默认；镜像 = 默认
        controller.startSession("cn.com.langeasy.LangEasyLexis")
        argv = procs[2].argv
        assert argv[argv.index("--chrome-top") + 1] == "immersive"
        assert argv[argv.index("--chrome-bottom") + 1] == "native"
        controller.startMirror()
        argv = procs[3].argv
        assert argv[argv.index("--chrome-top") + 1] == "immersive"   # 镜像恒默认
        assert argv[argv.index("--chrome-bottom") + 1] == "native"


def test_bar_override_reread_per_launch(no_adb, prefs_stub, qapp, monkeypatch):
        """两次启动之间清掉 override：下一次 spawn 即回默认（每次重读磁盘）。"""
        monkeypatch.setattr(
                controller_mod, "load_settings",
                lambda: (Settings(top_bar_mode="native", bottom_bar_mode="native",
                                  audio_policy="all"), []))
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        procs: list[_FakeProc] = []
        _spawn_recorder(controller, procs)

        controller.setAppBar("tv.danmaku.bili", "top", "immersive")
        controller.startSession("tv.danmaku.bili")
        assert procs[0].argv[procs[0].argv.index("--chrome-top") + 1] == "immersive"
        procs[0]._exit_code = 0
        controller.reapSessions()

        controller.setAppBar("tv.danmaku.bili", "top", "")   # 清 override
        controller.startSession("tv.danmaku.bili")
        assert procs[1].argv[procs[1].argv.index("--chrome-top") + 1] == "native"


def test_audio_restart_respawn_keeps_bar_override(
        no_adb, prefs_stub, qapp, monkeypatch
):
        """latest 音频切换的静音重启同样按包带 override（不丢也不串）。"""
        _policy(monkeypatch, "latest")   # 默认 immersive/immersive
        controller = PanelController("/fake/adb.exe")
        _StubMonitor.instances[-1].set_states({"S1": "device"})
        spawned: list[list[str]] = []

        def fake_spawn(argv):
                spawned.append(argv)
                return _FakeProc(argv)

        controller._spawn = fake_spawn  # type: ignore[method-assign]
        controller.setAppBar("tv.danmaku.bili", "top", "native")

        controller.startSession("tv.danmaku.bili")
        controller.startSession("com.tencent.mm")
        # spawns: bili(音频，override native) → bili 静音重启(同 override) → mm(默认)
        assert len(spawned) == 3
        assert spawned[0][spawned[0].index("--chrome-top") + 1] == "native"
        assert spawned[1][spawned[1].index("--chrome-top") + 1] == "native"
        assert spawned[2][spawned[2].index("--chrome-top") + 1] == "immersive"


# ---------------------- DPI/渲染倍率按应用（gui_prefs density/scale 节）


def test_density_prefs_roundtrip_and_clear(no_adb, prefs_stub, qapp):
        """density 节读写：缺省跟随设置页默认；显式 override 落盘、新控制
        器读回；非法值只报状态；0 清除后包条目整个退场（不留空壳节）。"""
        controller = PanelController("/fake/adb.exe")
        assert controller.densityFor("tv.danmaku.bili") \
                == {"explicit": False, "dpi": None, "default": 160}

        controller.setAppDensity("tv.danmaku.bili", 240)
        assert controller.densityFor("tv.danmaku.bili") \
                == {"explicit": True, "dpi": 240, "default": 160}
        assert json.loads(prefs_stub.payload)["density"]["tv.danmaku.bili"] \
                == {"dpi": 240}
        assert controller.statusText == "哔哩哔哩 将以 DPI 240 建屏（下次启动生效）"

        # 新控制器读回同一记忆（持久化，非内存态）
        second = PanelController("/fake/adb.exe")
        assert second.densityFor("tv.danmaku.bili") \
                == {"explicit": True, "dpi": 240, "default": 160}

        # 非法值：只报状态，不落库不改记忆
        second.setAppDensity("tv.danmaku.bili", 9999)
        assert second.densityFor("tv.danmaku.bili") \
                == {"explicit": True, "dpi": 240, "default": 160}
        assert second.statusText == "DPI 需在 120–640"

        # 0 = 清除 override：explicit 消失、包条目退场
        second.setAppDensity("tv.danmaku.bili", 0)
        assert second.densityFor("tv.danmaku.bili") \
                == {"explicit": False, "dpi": None, "default": 160}
        assert json.loads(prefs_stub.payload)["density"] == {}
        assert second.statusText == "哔哩哔哩 DPI 将跟随默认"


def test_scale_prefs_roundtrip_and_effective_default(no_adb, prefs_stub, qapp,
                                                      monkeypatch):
        """scale 节读写：无 override 时 effective = 设置页默认（fresh read）；
        显式 override 落盘读回；非法值只报状态；0 清除后整节退场。"""
        controller = PanelController("/fake/adb.exe")
        # no_adb 把 load_settings 钉在 Settings() 默认（render_scale 1.0）
        assert controller.scaleFor("tv.danmaku.bili") \
                == {"explicit": False, "scale": 1.0, "default": 1.0}

        # 设置页默认变化能到达下一次菜单读取（fresh read 契约）
        monkeypatch.setattr(
                controller_mod, "load_settings",
                lambda: (Settings(render_scale=3.0), []))
        assert controller.scaleFor("tv.danmaku.bili") \
                == {"explicit": False, "scale": 3.0, "default": 3.0}

        controller.setAppScale("tv.danmaku.bili", 2.0)
        assert controller.scaleFor("tv.danmaku.bili") \
                == {"explicit": True, "scale": 2.0, "default": 3.0}
        assert json.loads(prefs_stub.payload)["scale"]["tv.danmaku.bili"] \
                == {"scale": 2.0}
        assert controller.statusText == "哔哩哔哩 渲染倍率 2×（窗口÷2，下次启动生效）"

        # 非法值：只报状态，不落库
        controller.setAppScale("tv.danmaku.bili", 8.0)
        assert controller.scaleFor("tv.danmaku.bili") \
                == {"explicit": True, "scale": 2.0, "default": 3.0}
        assert controller.statusText == "渲染倍率需在 1.0–3.0"

        # 0 = 清除：effective 回设置页默认（此处 monkeypatch 后为 3.0）
        controller.setAppScale("tv.danmaku.bili", 0)
        assert controller.scaleFor("tv.danmaku.bili") \
                == {"explicit": False, "scale": 3.0, "default": 3.0}
        assert json.loads(prefs_stub.payload)["scale"] == {}


def test_build_launch_argv_injects_density_and_scale(no_adb, prefs_stub):
        """density 节覆盖建屏密度（flex/fixed 均注入 --dpi）；render scale
        节+设置页默认仅 flex 路径注入 --render-scale（固定几何不叠加）。"""
        base = build_launch_argv("tv.danmaku.bili", "S1", portrait=False)
        assert "--dpi" not in base
        assert "--render-scale" not in base

        prefs_stub.payload = json.dumps(
                {"density": {"tv.danmaku.bili": {"dpi": 240}}})
        argv = build_launch_argv("tv.danmaku.bili", "S1", portrait=False)
        assert argv[argv.index("--dpi") + 1] == "240"
        assert "--render-scale" not in argv

        prefs_stub.payload = json.dumps({
                "density": {"tv.danmaku.bili": {"dpi": 240}},
                "scale": {"tv.danmaku.bili": {"scale": 2.0}},
        })
        argv = build_launch_argv("tv.danmaku.bili", "S1", portrait=False)
        assert argv[argv.index("--dpi") + 1] == "240"
        assert argv[argv.index("--render-scale") + 1] == "2"

        # 固定几何（按比例/记忆启动）：倍率不叠加，DPI 仍生效
        argv = build_launch_argv("tv.danmaku.bili", "S1", portrait=False,
                                 width=2560, height=1440)
        assert "--render-scale" not in argv
        assert argv[argv.index("--dpi") + 1] == "240"

        # 设置页默认倍率（无 per-app 覆盖）同样注入 flex 路径
        prefs_stub.payload = None
        argv = build_launch_argv("tv.danmaku.bili", "S1", portrait=False)
        assert "--render-scale" not in argv      # 默认 1.0 = 原生，不注入


def test_theme_state_and_apply_theme_wiring(no_adb, prefs_stub, qapp, settings_file):
        """外观态（2026-09-12）：默认亮色 + 玻璃开；applyTheme 即时切换
        dark/light/system；system 模式接 colorSchemeChanged 实时跟随，
        固定模式拆线（Opus 终审 MUST-FIX #7a 的回归测试）。"""
        from PyQt6.QtCore import QCoreApplication, Qt  # noqa: F401
        from PyQt6.QtGui import QGuiApplication

        controller = PanelController("/fake/adb.exe")
        assert controller.effectiveDark is False
        assert controller.glassMaterial is True

        settings_file.write_text('{"theme": "dark", "glass_enabled": false}',
                                 encoding="utf-8")
        controller.applyTheme()
        assert controller.effectiveDark is True
        assert controller.glassMaterial is False

        # system 模式：接线 + 立即解析一次（offscreen 后端 Unknown → 亮）
        settings_file.write_text('{"theme": "system"}', encoding="utf-8")
        controller.applyTheme()
        assert controller.effectiveDark is False
        app = QCoreApplication.instance()
        hints = app.styleHints() if isinstance(app, QGuiApplication) else None
        if hints is not None:
                # 固定模式回切后不再跟随系统信号（拆线不抛错即过）
                settings_file.write_text('{"theme": "light"}', encoding="utf-8")
                controller.applyTheme()
                assert controller.effectiveDark is False
                hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)   # 拆线后空发不翻转
                assert controller.effectiveDark is False

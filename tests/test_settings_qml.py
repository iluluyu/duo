"""SettingsPage.qml in isolation: the cleaned-up page per DESIGN §3.8.

Offscreen + software rendering like test_qml_app, but the page is
instantiated directly (no Main.qml, no controller) against either the real
SettingsApi (tmp settings.json) or a recording stub - so these tests stay
green while the main panel churns.

Covers the 2026-09 cleanup contract:
  - dpi / corner_mode / corner_size_dip pass invisibly through the page:
    SettingsApi.save builds the whole Settings table, so a missing key
    would silently reset the user's value to its default.
  - DPI/corner controls are gone; footer keeps only 保存.
  - audio row label + option names; probe results are transient.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

pytest.importorskip("PyQt6.QtQml")

from PyQt6.QtCore import (  # noqa: E402
        QEventLoop,
        QObject,
        QTimer,
        pyqtSignal,
        pyqtSlot,
)
from PyQt6.QtQml import QQmlComponent, QQmlEngine  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import duo.core.settings as settings_mod  # noqa: E402
from duo.ui.app import QML_MAIN, SettingsApi  # noqa: E402

SETTINGS_QML = QML_MAIN.with_name("SettingsPage.qml")

SETTINGS_KEYS = {
        "scrcpy_path", "adb_path", "fps", "bitrate_mbps", "dpi",
        "corner_mode", "corner_size_dip", "glass_enabled",
        "audio_policy", "video_codec", "turn_screen_off",
        "top_bar_mode", "bottom_bar_mode",
}


class _RecordingApi(QObject):
    """SettingsApi stand-in that records exactly what save() receives.

    The passthrough contract is about the map collect() hands to save();
    a recording stub pins that seam (the real api is exercised in its
    own test below).
    """

    probeDone = pyqtSignal(str, bool, str)

    def __init__(self, loaded: dict[str, object]) -> None:
        super().__init__()
        self._loaded = loaded
        self.saved: list[dict[str, object]] = []

    @pyqtSlot(result="QVariantMap")
    def load(self) -> dict[str, object]:
        return dict(self._loaded)

    @pyqtSlot(result="QVariantList")
    def loadProblems(self) -> list[str]:
        return []

    @pyqtSlot("QVariantMap", result="QVariantList")
    def save(self, values: dict[str, object]) -> list[str]:
        self.saved.append(dict(values))
        return []

    @pyqtSlot(str, str)
    def probe(self, tool: str, path: str) -> None:
        pass   # tests drive results via probeDone directly


@pytest.fixture()
def qapp():
    """Ensure exactly one QApplication exists (offscreen)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def settings_file(tmp_path, monkeypatch):
    """settings.json in a tmp dir: never touches the real user file."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "settings_path", lambda: path)
    return path


def _pump(ms: int) -> None:
    """Spin the event loop so bindings, animations and timers settle."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _make_page(api: QObject) -> tuple[QObject, QQmlEngine]:
    """Instantiate SettingsPage.qml directly against the given api."""
    engine = QQmlEngine()
    context = engine.rootContext()
    context.setContextProperty("settingsApi", api)
    component = QQmlComponent(engine, str(SETTINGS_QML))
    assert not component.isError(), [str(e) for e in component.errors()]
    page = component.create()
    assert page is not None
    return page, engine


@pytest.fixture()
def make_page(qapp):
    """Page factory over any settingsApi; every page is torn down after."""
    # The api must stay referenced for the test's lifetime: the QML context
    # property only stores a pointer, so an unreferenced Python-side api
    # gets collected and the page sees settingsApi = null.
    made: list[tuple[QObject, QQmlEngine, QObject]] = []

    def _make(api: QObject) -> QObject:
        page, engine = _make_page(api)
        made.append((page, engine, api))
        return page

    yield _make
    for page, engine, _api in made:
        page.deleteLater()
        engine.deleteLater()
        _pump(20)


@pytest.fixture()
def settings_page(make_page):
    """Page over the real SettingsApi (defaults; no settings.json yet)."""
    return make_page(SettingsApi())


def _save_changes(page: QObject) -> None:
    meta = page.metaObject()
    assert meta.indexOfMethod("saveChanges()") >= 0
    meta.invokeMethod(page, "saveChanges")


# ------------------------------------------- ① 隐形透传（DESIGN §3.8）


def test_removed_dpi_and_corner_values_pass_through_to_save(make_page):
    """load 带自定义 dpi/圆角 → collect 原样带回，save 收到的 map 键全值同。

    SettingsApi.save 按整表构造 Settings：map 缺哪一键，那一键就落回默认
    ——丢键等于把用户的 dpi / corner_mode / corner_size_dip 静默重置。
    """
    api = _RecordingApi({
        "scrcpy_path": "", "adb_path": "", "fps": 60, "bitrate_mbps": 30,
        "dpi": 400, "corner_mode": "g2", "corner_size_dip": 72,
        "glass_enabled": True, "audio_policy": "latest",
        "video_codec": "auto", "turn_screen_off": False,
    })
    page = make_page(api)
    accepted: list[bool] = []
    page.accepted.connect(lambda: accepted.append(True))

    _save_changes(page)

    assert accepted == [True]
    assert len(api.saved) == 1
    saved = api.saved[0]
    assert set(saved) == SETTINGS_KEYS          # 整表 13 键一个不少
    assert saved["dpi"] == 400
    assert saved["corner_mode"] == "g2"
    assert saved["corner_size_dip"] == 72


def test_null_dpi_passes_through_as_null(make_page):
    """dpi 为 null（跟随显示密度）时透传仍是 null，不会被占位值顶掉。"""
    api = _RecordingApi({
        "scrcpy_path": "", "adb_path": "", "fps": 60, "bitrate_mbps": 30,
        "dpi": None, "corner_mode": "none", "corner_size_dip": 0,
        "glass_enabled": True, "audio_policy": "off",
        "video_codec": "h264", "turn_screen_off": True,
    })
    page = make_page(api)
    _save_changes(page)
    saved = api.saved[0]
    assert saved["dpi"] is None
    assert saved["corner_mode"] == "none"
    assert saved["corner_size_dip"] == 0


def test_custom_dpi_and_corner_survive_real_api_save(make_page, settings_file):
    """端到端：文件里的自定义值经页面保存后原样落盘（防重置回归）。"""
    api = SettingsApi()
    assert api.save({
        "scrcpy_path": "", "adb_path": "", "fps": 60, "bitrate_mbps": 30,
        "dpi": 400, "corner_mode": "g2", "corner_size_dip": 72,
        "glass_enabled": True, "audio_policy": "latest",
        "video_codec": "auto", "turn_screen_off": False,
    }) == []
    page = make_page(api)   # Component.onCompleted → reloadFromApi 读文件
    accepted: list[bool] = []
    page.accepted.connect(lambda: accepted.append(True))

    _save_changes(page)

    assert accepted == [True]
    raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
    assert raw["dpi"] == 400
    assert raw["corner_mode"] == "g2"
    assert raw["corner_size_dip"] == 72


# ------------------------------------------------- ② 删除即删除


def test_dpi_and_corner_controls_are_gone(settings_page):
    """DPI 数字框/自动开关、圆角三选一/滑块/Canvas 预览不复存在。"""
    for name in ("dpiBox", "dpiAutoSwitch", "cornerSlider", "cornerPreview"):
        assert settings_page.findChild(QObject, name) is None, name


def test_footer_has_only_save_button(settings_page):
    """底部仅「保存」；取消按钮已删（‹ 返回即放弃）。"""
    save = settings_page.findChild(QObject, "saveButton")
    assert save is not None
    assert save.property("text") == "保存"
    texts = [
        str(o.property("text"))
        for o in settings_page.findChildren(QObject)
        if o.property("text") is not None
    ]
    assert "取消" not in texts


# ----------------------------------------------------- ③ 文案


def test_audio_row_label_and_option_texts(settings_page):
    """音频行带行标签「音频」（与 FPS/码率同构）；选项名不再裸写「最新」。"""
    label = settings_page.findChild(QObject, "audioRowLabel")
    assert label is not None
    assert label.property("text") == "音频"
    expected = {
        "audioLatest": "仅最新会话",
        "audioAll": "全部会话",
        "audioMute": "静音",
    }
    for name, text in expected.items():
        btn = settings_page.findChild(QObject, name)
        assert btn is not None, name
        assert btn.property("text") == text
    # 点击仍切页面状态（选中态维持）
    settings_page.findChild(QObject, "audioMute").click()
    _pump(20)
    assert settings_page.property("audioPolicy") == "off"


def test_page_has_no_header_row_or_back_button(settings_page):
    """顶栏胶囊即导航：无 ‹ 返回钮、无 22px 页标题（DESIGN §3.9 无标题行）。"""
    assert settings_page.findChild(QObject, "backBtn") is None
    for item in settings_page.findChildren(QObject):
        font = item.property("font")
        if font is not None and item.property("text") is not None:
            assert font.pixelSize() != 22   # 唯一 22px 是旧页标题字号


# ------------------------------------------- ④ 探测瞬时提示


def test_probe_status_is_transient(make_page, settings_file):
    """probeDone 后胶囊波起可见，约 2.5s 后淡出（不留常驻绿/红字）。"""
    api = SettingsApi()
    page = make_page(api)
    pill = page.findChild(QObject, "adbProbeStatus")
    label = page.findChild(QObject, "adbProbeStatusLabel")
    assert pill is not None and label is not None
    assert pill.property("visible") is False   # 未探测：不出提示

    api.probeDone.emit("adb", True, "1.0.41")
    _pump(200)                                  # 波起（140ms 淡入走完）
    assert label.property("text") == "✓ 1.0.41"
    assert pill.property("visible") is True
    assert pill.property("opacity") == 1.0

    _pump(2500)                                 # 2.36s 计时 + 140ms 淡出收场
    assert pill.property("opacity") < 0.05      # 视觉上已收（透明即不见）


def test_repeated_probe_resets_fade_timer(make_page, settings_file):
    """重复探测重置淡出计时：旧结果没淡出就被新结果接管。"""
    api = SettingsApi()
    page = make_page(api)
    pill = page.findChild(QObject, "adbProbeStatus")

    api.probeDone.emit("adb", True, "1.0.41")
    _pump(2000)                                 # 未到淡出点（约 2.36s）
    api.probeDone.emit("adb", True, "2.0")      # 重探：计时重置
    _pump(1000)                                 # 若未重置，此刻早已淡出
    assert pill.property("opacity") == 1.0
    _pump(1800)                                 # 重置后再走完整个淡出
    assert pill.property("opacity") < 0.05


# ------------------------------------------------- ⑤ 保存出口


def test_save_success_emits_accepted(settings_page, settings_file):
    """保存成功发 accepted()（Main 侧据此 resolveAdb + pop）。"""
    accepted: list[bool] = []
    settings_page.accepted.connect(lambda: accepted.append(True))
    field = settings_page.findChild(QObject, "adbPathField")
    assert field is not None
    field.setProperty("text", r"C:\工具\adb.exe")

    _save_changes(settings_page)

    assert accepted == [True]
    assert Path(settings_file).exists()


# ------------------------------------------- ⑥ 窗口栏（上巴/下巴模式）


def test_window_bar_group_exists_with_defaults(settings_page):
        """窗口栏组：上巴两键（沉浸|系统）、下巴三键（沉浸|系统|不显示）。
        默认（2026-09-09 决策）：上巴沉浸、下巴不显示（scrcpy 右键已是
        返回，下巴对多数用户冗余）。"""
        assert settings_page.findChild(QObject, "windowBarCard") is not None
        for name in ("topBarRowLabel", "bottomBarRowLabel"):
                assert settings_page.findChild(QObject, name) is not None, name
        for name in ("topBarImmersive", "topBarNative",
                     "bottomBarImmersive", "bottomBarNative",
                     "bottomBarNone"):
                assert settings_page.findChild(QObject, name) is not None, name
        assert settings_page.property("topBarMode") == "immersive"
        assert settings_page.property("bottomBarMode") == "none"


def test_window_bar_buttons_switch_modes(settings_page):
    """点击切换页面状态（两侧各来回一次）。"""
    settings_page.findChild(QObject, "topBarNative").click()
    settings_page.findChild(QObject, "bottomBarNative").click()
    _pump(20)
    assert settings_page.property("topBarMode") == "native"
    assert settings_page.property("bottomBarMode") == "native"
    settings_page.findChild(QObject, "topBarImmersive").click()
    _pump(20)
    assert settings_page.property("topBarMode") == "immersive"
    assert settings_page.property("bottomBarMode") == "native"


def test_window_bar_collect_carries_both_modes(make_page):
    """collect 带上两键：save 收到的 map 里上/下各自当前值。"""
    api = _RecordingApi({
        "scrcpy_path": "", "adb_path": "", "fps": 60, "bitrate_mbps": 30,
        "dpi": None, "corner_mode": "system", "corner_size_dip": 48,
        "glass_enabled": True, "audio_policy": "latest",
        "video_codec": "auto", "turn_screen_off": False,
    })
    page = make_page(api)
    page.findChild(QObject, "topBarNative").click()      # 只切上巴
    _pump(20)
    _save_changes(page)
    saved = api.saved[0]
    assert saved["top_bar_mode"] == "native"
    # 下巴未触碰 → 跟随新默认 none（collect 直接读页面属性）
    assert saved["bottom_bar_mode"] == "none"


def test_window_bar_roundtrip_via_real_api(settings_page, settings_file):
    """端到端：控件 → SettingsApi.save → settings.json 两字段落盘。"""
    settings_page.findChild(QObject, "topBarNative").click()
    settings_page.findChild(QObject, "bottomBarNative").click()
    _pump(20)
    _save_changes(settings_page)
    raw = json.loads(Path(settings_file).read_text(encoding="utf-8"))
    assert raw["top_bar_mode"] == "native"
    assert raw["bottom_bar_mode"] == "native"


def test_window_bar_group_is_defaults_semantics(settings_page):
    """窗口栏组 = 默认值语义：组标题带「（默认）」，说明行注明
    「应用未单独设置时生效」（应用级覆盖在右键菜单「窗口栏 ▸」）。"""
    texts = [
        str(o.property("text"))
        for o in settings_page.findChildren(QObject)
        if o.property("text") is not None
    ]
    assert any("窗口栏（默认）" in t for t in texts)
    assert any("应用未单独设置时生效" in t for t in texts)

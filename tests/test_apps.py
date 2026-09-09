"""Tests for app metadata parsing (fixtures captured from the real device)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

from duo.core.apps import (
        label_sort_key,
        parse_badging,
        parse_base_apk_path,
        parse_package_list,
        parse_resolve_activity,
        pinyin_initial,
)

DEVICES_OUTPUT = """List of devices attached
4444bd6b               device product:OPD2409 model:OPD2409 device:OP615CL1 transport_id:1
emulator-5554          offline
ABC123                 unauthorized
"""

PM_PATH_OUTPUT = (
        "package:/data/app/~~pAeeM3oES5guBJhkOYjXAQ==/"
        "cn.com.langeasy.LangEasyLexis-6cGM_YvZ4qvNihlkr_4U7Q==/base.apk\n"
)

PM_LIST_OUTPUT = """package:com.android.chrome
package:cn.com.langeasy.LangEasyLexis
package:tv.danmaku.bili
"""

BADGING_PACKAGE = (
        "package: name='cn.com.langeasy.LangEasyLexis' versionCode='368' "
        "versionName='5.11.1' platformBuildVersionName='14'"
)
BADGING_OUTPUT = f"""{BADGING_PACKAGE}
application-label:'不背单词'
application-label-zh-CN:'不背单词'
application: label='不背单词' icon='res/mipmap-anydpi-v26/ic_launcher_app.xml'
launchable-activity: name='cn.com.langeasy.LangEasyLexis.activity.SplashActivity'  label='' icon=''
"""


def test_parse_package_list_sorted():
        """Package names are extracted without the package: prefix, sorted."""
        assert parse_package_list(PM_LIST_OUTPUT) == [
                "cn.com.langeasy.LangEasyLexis",
                "com.android.chrome",
                "tv.danmaku.bili",
        ]


def test_parse_base_apk_path_strips_prefix():
        """The base.apk device path is extracted from pm path output."""
        path = parse_base_apk_path(PM_PATH_OUTPUT)
        assert path is not None
        assert path.startswith("/data/app/")
        assert path.endswith("/base.apk")
        assert not path.startswith("package:")


def test_parse_base_apk_path_missing():
        """Missing base.apk yields None (e.g. split-only installs)."""
        assert parse_base_apk_path("package:/data/app/x/split_config.arm64_v8a.apk\n") is None
        assert parse_base_apk_path("") is None


def test_parse_badging_extracts_fields():
        """Label, icon, package and version come out of badging output."""
        fields = parse_badging(BADGING_OUTPUT)
        assert fields["package"] == "cn.com.langeasy.LangEasyLexis"
        assert fields["version_name"] == "5.11.1"
        assert fields["label"] == "不背单词"
        assert fields["icon"] == "res/mipmap-anydpi-v26/ic_launcher_app.xml"


def test_parse_badging_empty_output():
        """Unparseable output yields an empty dict, not an exception."""
        assert parse_badging("") == {}
        assert parse_badging("some random stderr noise") == {}


def test_extract_icon_defers_adaptive_xml(tmp_path: Path):
        """Adaptive icon XML references are deferred to the M3 compositing."""
        from duo.core.apps import extract_icon

        apk = tmp_path / "app.apk"
        with zipfile.ZipFile(apk, "w") as zf:
                zf.writestr("res/mipmap-anydpi-v26/ic_launcher_app.xml", "<adaptive-icon/>")
        ref = "res/mipmap-anydpi-v26/ic_launcher_app.xml"
        assert extract_icon(apk, ref, tmp_path / "out.png") is None


def test_extract_icon_missing_entry(tmp_path: Path):
        """A missing icon entry yields None instead of an error."""
        from duo.core.apps import extract_icon

        apk = tmp_path / "app.apk"
        with zipfile.ZipFile(apk, "w") as zf:
                zf.writestr("dummy.txt", "x")
        assert extract_icon(apk, "res/drawable/icon.png", tmp_path / "out.png") is None


def test_extract_icon_raster_gets_rounded_mask(tmp_path: Path):
        """Direct raster refs come out as rounded, corner-transparent PNGs."""
        import io

        from PIL import Image

        from duo.core.apps import extract_icon

        source = Image.new("RGB", (64, 64), (255, 0, 0))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        apk = tmp_path / "app.apk"
        with zipfile.ZipFile(apk, "w") as zf:
                zf.writestr("res/mipmap-xxxhdpi/ic_launcher.png", buffer.getvalue())
        out = tmp_path / "out.png"
        assert extract_icon(apk, "res/mipmap-xxxhdpi/ic_launcher.png", out) == out
        with Image.open(out) as image:
                assert image.mode == "RGBA"
                for corner in [(0, 0), (0, 63), (63, 0), (63, 63)]:
                        assert image.getpixel(corner)[3] == 0
                assert image.getpixel((32, 32))[3] == 255


def test_app_info_icon_cache_name_is_versioned(tmp_path: Path, monkeypatch):
        """Icon caches carry the .r3 suffix; apk/metadata caches do not."""
        import duo.core.apps as apps

        monkeypatch.setattr(apps, "aapt2_ensure", lambda root=None: tmp_path / "aapt2.exe")
        monkeypatch.setattr(
                apps, "subprocess", SimpleNamespace(
                        run=lambda *args, **kwargs: SimpleNamespace(stdout=BADGING_OUTPUT)
                )
        )
        captured: dict[str, Path] = {}

        def fake_extract_icon(
                apk_path: Path, icon_ref: str, out_png: Path, aapt2: Path | None = None
        ) -> Path | None:
                captured["out"] = out_png
                return out_png

        monkeypatch.setattr(apps, "extract_icon", fake_extract_icon)

        class StubAdb:
                serial = "stub"

                def shell(self, command: str) -> str:
                        return PM_PATH_OUTPUT if command.startswith("pm path") else "1024"

                def pull(self, remote: str, local: Path) -> None:
                        local.write_bytes(b"apk")

        info = apps.app_info(StubAdb(), "cn.com.langeasy.LangEasyLexis", tmp_path)
        assert info.icon_path == captured["out"]
        assert captured["out"].name == "cn.com.langeasy.LangEasyLexis.r3.png"
        # The apk and metadata caches keep their unversioned names.
        assert (tmp_path / "apks" / "cn.com.langeasy.LangEasyLexis.apk").exists()
        assert (tmp_path / "apks" / "cn.com.langeasy.LangEasyLexis.json").exists()


def test_parse_resolve_activity_extracts_component():
        """``--brief`` output yields the launchable component."""
        output = (
            "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
            "  cn.com.langeasy.LangEasyLexis/.activity.SplashActivity\n"
            "\n"
            "cn.com.langeasy.LangEasyLexis/cn.com.langeasy.LangEasyLexis.MainActivity\n"
        )
        component = parse_resolve_activity(output)
        assert component == (
            "cn.com.langeasy.LangEasyLexis/cn.com.langeasy.LangEasyLexis.MainActivity"
        )


def test_parse_resolve_activity_single_line():
        """The common one-line form passes through untouched."""
        assert parse_resolve_activity(
            "tv.danmaku.bili/tv.danmaku.bili.MainActivityV2\n"
        ) == "tv.danmaku.bili/tv.danmaku.bili.MainActivityV2"


def test_parse_resolve_activity_missing():
        """Nothing resolvable -> None (caller degrades to a status message)."""
        assert parse_resolve_activity("") is None
        assert parse_resolve_activity("\n\n") is None


# ----------------------------------------------------------- pinyin sorting


def test_pinyin_initial_classifies_common_hanzi():
        """Run-table chars, curated level-2 chars and passthrough all work."""
        assert pinyin_initial("不") == "b"
        assert pinyin_initial("背") == "b"
        assert pinyin_initial("微") == "w"
        assert pinyin_initial("信") == "x"
        assert pinyin_initial("读") == "d"
        assert pinyin_initial("书") == "s"
        # 哔 is GB2312 level-2 (outside the pinyin-sorted run) - curated dict.
        assert pinyin_initial("哔") == "b"
        assert pinyin_initial("咪") == "m"
        # 吧 is GBK-only (贴吧) - same curated dict.
        assert pinyin_initial("吧") == "b"
        # Non-hanzi input passes through lowercased, never raises.
        assert pinyin_initial("W") == "w"
        assert pinyin_initial("1") == "1"
        assert pinyin_initial(" ") == " "
        # Multi-char input is not a single hanzi - returned lowercased as-is.
        assert pinyin_initial("WX") == "wx"


def test_label_sort_key_orders_labels_by_first_letter():
        """Chinese and latin labels order together by pinyin/latin initial."""
        labels = ["微信读书", "WPS Office", "哔哩哔哩", "微信", "不背单词"]
        assert sorted(labels, key=label_sort_key) == [
                "不背单词",
                "哔哩哔哩",
                "WPS Office",
                "微信",
                "微信读书",
        ]
        assert label_sort_key("不背单词") == "bbdc"
        assert label_sort_key("哔哩哔哩") == "blbl"
        assert label_sort_key("WPS Office") == "wps office"

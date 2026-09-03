"""Tests for app metadata parsing (fixtures captured from the real device)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from duo.core.apps import (
        parse_badging,
        parse_base_apk_path,
        parse_package_list,
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

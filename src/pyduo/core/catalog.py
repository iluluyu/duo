"""Frozen catalog of popular Chinese Android apps with preset tile data.

Why this exists: the panel grid is seeded from ``pm list packages -3``,
which lists user-installed packages only - vendor-PREINSTALLED apps never
appear there (QQ is the classic case: Chinese ROMs ship it as a system
package). The catalog closes that gap from the other side: each entry is
existence-checked against the FULL ``pm list packages`` output, so a
preinstalled QQ still earns a tile. The two listings are complementary,
not redundant: discovery covers whatever the user installed, the catalog
restores what the vendor hid.

Every preset also carries its brand color and glyph, so a catalog tile can
show a finished icon immediately - no APK pull, no aapt2 extraction round.
Pure data, Qt-free; see duo.core.icon_presets for turning rows into icons.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppPreset:
        """One catalog app: identity plus the tile's preset look.

        ``glyph_ink`` picks the glyph color on light brand backgrounds:
        True renders the glyph dark (#1D1D1F) where white would fail
        contrast (美团's yellow, 不背单词's amber).
        """

        label: str
        package: str
        color: str
        glyph: str
        glyph_ink: bool


#: The frozen table. Order is part of the contract: the grid is seeded in
#: this exact order (pinyin-sorted at display time), so rows read as a
#: checklist from the top. Do not reorder casually.
APP_CATALOG: list[AppPreset] = [
        AppPreset("微信", "com.tencent.mm", "#07C160", "微", False),
        AppPreset("QQ", "com.tencent.mobileqq", "#12B7F5", "Q", False),
        AppPreset("TIM", "com.tencent.tim", "#12B7F5", "T", False),
        AppPreset("哔哩哔哩", "tv.danmaku.bili", "#FB7299", "哔", False),
        AppPreset("微信读书", "com.tencent.weread", "#3E7BFA", "读", False),
        AppPreset("不背单词", "cn.com.langeasy.LangEasyLexis", "#F5A623", "不", True),
        AppPreset("WPS Office", "cn.wps.moffice_eng", "#D34B2F", "W", False),
        AppPreset("淘宝", "com.taobao.taobao", "#FF6A00", "淘", False),
        AppPreset("支付宝", "com.eg.android.AlipayGphone", "#1677FF", "支", False),
        AppPreset("抖音", "com.ss.android.ugc.aweme", "#1C1C1E", "抖", False),
        AppPreset("网易云音乐", "com.netease.cloudmusic", "#C20C0C", "云", False),
        AppPreset("知乎", "com.zhihu.android", "#0084FF", "知", False),
        AppPreset("微博", "com.sina.weibo", "#E6162D", "微", False),
        AppPreset("京东", "com.jingdong.app.mall", "#E93A32", "京", False),
        AppPreset("拼多多", "com.xunmeng.pinduoduo", "#E02E24", "拼", False),
        AppPreset("小红书", "com.xingin.xhs", "#FF2442", "红", False),
        AppPreset("高德地图", "com.autonavi.minimap", "#118EE9", "高", False),
        AppPreset("百度", "com.baidu.searchbox", "#2932E1", "百", False),
        AppPreset("美团", "com.sankuai.meituan", "#F7B500", "团", True),
        AppPreset("饿了么", "me.ele", "#0095FF", "e", False),
        AppPreset("腾讯视频", "com.tencent.qqlive", "#FF6B00", "腾", False),
        AppPreset("爱奇艺", "com.qiyi.video", "#00BE06", "奇", False),
        AppPreset("优酷", "com.youku.phone", "#1EBEFF", "优", False),
        AppPreset("酷安", "com.coolapk.market", "#3DC34B", "酷", False),
        AppPreset("夸克", "com.quark.browser", "#4E6EF2", "夸", False),
        AppPreset("钉钉", "com.alibaba.android.rimet", "#0089FF", "钉", False),
        AppPreset("飞书", "com.ss.android.lark", "#3370FF", "飞", False),
]

_BY_PACKAGE: dict[str, AppPreset] | None = None


def catalog_by_package() -> dict[str, AppPreset]:
        """Package name -> preset lookup over the catalog.

        Built once per process and cached at module level: existence checks
        and icon lookups query this per package on every device refresh.
        """
        global _BY_PACKAGE
        if _BY_PACKAGE is None:
                _BY_PACKAGE = {preset.package: preset for preset in APP_CATALOG}
        return _BY_PACKAGE

//! 冻结的常用中文安卓应用目录（预设平铺数据）。
//! 对译自 duo/core/catalog.py；合同镜像 tests/test_catalog.py。
//!
//! 存在理由：面板网格由 ``pm list packages -3`` 播种——厂商预装应用不在
//! 其中（QQ 是经典案例）；目录从另一侧补位：每行对全量包列表做存在性
//! 检查，预装 QQ 照样有平铺。顺序是合同的一部分（网格按此序播种）。

/// 一条目录应用：身份 + 平铺的预设外观。``glyph_ink`` 决定亮底品牌色上
/// 用深色字形（#1D1D1F）还是白色。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AppPreset {
    pub label: &'static str,
    pub package: &'static str,
    pub color: &'static str,
    pub glyph: &'static str,
    pub glyph_ink: bool,
}

/// 冻结表。顺序即合同（网格播种序），勿随意重排。
pub const APP_CATALOG: &[AppPreset] = &[
    AppPreset {
        label: "微信",
        package: "com.tencent.mm",
        color: "#07C160",
        glyph: "微",
        glyph_ink: false,
    },
    AppPreset {
        label: "QQ",
        package: "com.tencent.mobileqq",
        color: "#12B7F5",
        glyph: "Q",
        glyph_ink: false,
    },
    AppPreset {
        label: "TIM",
        package: "com.tencent.tim",
        color: "#12B7F5",
        glyph: "T",
        glyph_ink: false,
    },
    AppPreset {
        label: "哔哩哔哩",
        package: "tv.danmaku.bili",
        color: "#FB7299",
        glyph: "哔",
        glyph_ink: false,
    },
    AppPreset {
        label: "微信读书",
        package: "com.tencent.weread",
        color: "#3E7BFA",
        glyph: "读",
        glyph_ink: false,
    },
    AppPreset {
        label: "不背单词",
        package: "cn.com.langeasy.LangEasyLexis",
        color: "#F5A623",
        glyph: "不",
        glyph_ink: true,
    },
    AppPreset {
        label: "WPS Office",
        package: "cn.wps.moffice_eng",
        color: "#D34B2F",
        glyph: "W",
        glyph_ink: false,
    },
    AppPreset {
        label: "淘宝",
        package: "com.taobao.taobao",
        color: "#FF6A00",
        glyph: "淘",
        glyph_ink: false,
    },
    AppPreset {
        label: "支付宝",
        package: "com.eg.android.AlipayGphone",
        color: "#1677FF",
        glyph: "支",
        glyph_ink: false,
    },
    AppPreset {
        label: "抖音",
        package: "com.ss.android.ugc.aweme",
        color: "#1C1C1E",
        glyph: "抖",
        glyph_ink: false,
    },
    AppPreset {
        label: "网易云音乐",
        package: "com.netease.cloudmusic",
        color: "#C20C0C",
        glyph: "云",
        glyph_ink: false,
    },
    AppPreset {
        label: "知乎",
        package: "com.zhihu.android",
        color: "#0084FF",
        glyph: "知",
        glyph_ink: false,
    },
    AppPreset {
        label: "微博",
        package: "com.sina.weibo",
        color: "#E6162D",
        glyph: "微",
        glyph_ink: false,
    },
    AppPreset {
        label: "京东",
        package: "com.jingdong.app.mall",
        color: "#E93A32",
        glyph: "京",
        glyph_ink: false,
    },
    AppPreset {
        label: "拼多多",
        package: "com.xunmeng.pinduoduo",
        color: "#E02E24",
        glyph: "拼",
        glyph_ink: false,
    },
    AppPreset {
        label: "小红书",
        package: "com.xingin.xhs",
        color: "#FF2442",
        glyph: "红",
        glyph_ink: false,
    },
    AppPreset {
        label: "高德地图",
        package: "com.autonavi.minimap",
        color: "#118EE9",
        glyph: "高",
        glyph_ink: false,
    },
    AppPreset {
        label: "百度",
        package: "com.baidu.searchbox",
        color: "#2932E1",
        glyph: "百",
        glyph_ink: false,
    },
    AppPreset {
        label: "美团",
        package: "com.sankuai.meituan",
        color: "#F7B500",
        glyph: "团",
        glyph_ink: true,
    },
    AppPreset {
        label: "饿了么",
        package: "me.ele",
        color: "#0095FF",
        glyph: "e",
        glyph_ink: false,
    },
    AppPreset {
        label: "腾讯视频",
        package: "com.tencent.qqlive",
        color: "#FF6B00",
        glyph: "腾",
        glyph_ink: false,
    },
    AppPreset {
        label: "爱奇艺",
        package: "com.qiyi.video",
        color: "#00BE06",
        glyph: "奇",
        glyph_ink: false,
    },
    AppPreset {
        label: "优酷",
        package: "com.youku.phone",
        color: "#1EBEFF",
        glyph: "优",
        glyph_ink: false,
    },
    AppPreset {
        label: "酷安",
        package: "com.coolapk.market",
        color: "#3DC34B",
        glyph: "酷",
        glyph_ink: false,
    },
    AppPreset {
        label: "夸克",
        package: "com.quark.browser",
        color: "#4E6EF2",
        glyph: "夸",
        glyph_ink: false,
    },
    AppPreset {
        label: "钉钉",
        package: "com.alibaba.android.rimet",
        color: "#0089FF",
        glyph: "钉",
        glyph_ink: false,
    },
    AppPreset {
        label: "飞书",
        package: "com.ss.android.lark",
        color: "#3370FF",
        glyph: "飞",
        glyph_ink: false,
    },
];

/// 包名 → 预设查找；未收录返回 None。
pub fn catalog_by_package(package: &str) -> Option<&'static AppPreset> {
    APP_CATALOG.iter().find(|p| p.package == package)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn catalog_has_27_unique_packages() {
        let packages: Vec<&str> = APP_CATALOG.iter().map(|p| p.package).collect();
        assert_eq!(packages.len(), 27);
        let unique: HashSet<&str> = packages.iter().copied().collect();
        assert_eq!(unique.len(), 27);
    }

    #[test]
    fn qq_present_mobileqq_only() {
        assert!(catalog_by_package("com.tencent.mobileqq").is_some());
        // 投机条目 com.tencent.qq（QQ NT 别名）2026-09-08 已删——Android
        // 上不存在，只会显示为一块死灰平铺。
        assert!(catalog_by_package("com.tencent.qq").is_none());
    }

    #[test]
    fn glyph_ink_true_only_on_light_brand_colors() {
        let dark_ink: HashSet<&str> = APP_CATALOG
            .iter()
            .filter(|p| p.glyph_ink)
            .map(|p| p.package)
            .collect();
        assert_eq!(
            dark_ink,
            ["cn.com.langeasy.LangEasyLexis", "com.sankuai.meituan"]
                .into_iter()
                .collect()
        );
    }

    #[test]
    fn colors_are_legal_rrggbb() {
        for preset in APP_CATALOG {
            let c = preset.color.as_bytes();
            assert_eq!(c.len(), 7, "{}", preset.package);
            assert_eq!(c[0], b'#', "{}", preset.package);
            assert!(
                c[1..].iter().all(|b| b.is_ascii_hexdigit()),
                "{}",
                preset.package
            );
        }
    }

    #[test]
    fn by_package_maps_identity() {
        assert_eq!(catalog_by_package("com.tencent.mm"), Some(&APP_CATALOG[0]));
        assert!(catalog_by_package("no.such.app").is_none());
    }

    #[test]
    fn order_is_the_frozen_seed_order() {
        assert_eq!(APP_CATALOG[0].package, "com.tencent.mm");
        assert_eq!(
            APP_CATALOG[APP_CATALOG.len() - 1].package,
            "com.ss.android.lark"
        );
    }

    #[test]
    fn every_row_has_label_and_glyph() {
        for preset in APP_CATALOG {
            assert!(!preset.label.is_empty(), "{}", preset.package);
            assert!(!preset.glyph.is_empty(), "{}", preset.package);
        }
    }
}

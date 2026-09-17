//! DESIGN.md 色板对译（唯一色板合同：docs/ui/DESIGN.md §2）。
//!
//! 面板默认暗色（DESIGN.md 暗色列）。玻璃开 = 画布带 alpha 透出系统
//! blur；玻璃关 = 不透明画布。卡片 hover 提亮走 `hover_wash`。

use egui::Color32;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ThemeKind {
    Light,
    Dark,
}

impl ThemeKind {
    /// settings.theme（light/dark/system）→ 面板主题。system 在 v0 折叠为
    /// 暗色（eframe 无 OS 跟随钩子，跟随留 TODO）。
    pub fn from_settings(s: &str) -> Self {
        match s {
            "light" => ThemeKind::Light,
            _ => ThemeKind::Dark,
        }
    }
}

/// #RRGGBB → Color32；RGBA(R,G,B,A)。手写解析避免拉依赖。
pub fn hex(s: &str) -> Color32 {
    let b = s.as_bytes();
    let ch =
        |i: usize| u32::from_str_radix(std::str::from_utf8(&b[i..i + 2]).unwrap(), 16).unwrap();
    Color32::from_rgb(ch(1) as u8, ch(3) as u8, ch(5) as u8)
}

/// 半透明色叠在不透明底上（QML/Qt 同式 sRGB 直混；u8 域）。
pub fn over(base: Color32, rgb: Color32, a: f32) -> Color32 {
    let mix = |b: u8, t: u8| (f32::from(b) * (1.0 - a) + f32::from(t) * a).round() as u8;
    Color32::from_rgb(
        mix(base.r(), rgb.r()),
        mix(base.g(), rgb.g()),
        mix(base.b(), rgb.b()),
    )
}

/// 一套主题令牌（DESIGN.md §2 / Style.qml 逐行对译）。
///
/// egui 0.31 Windows 着色管线的 alpha 混合是非线性病态（标定实测
/// out = srgb(a^0.75)，见 docs/validation）；因此**所有静态半透明层
/// 在此预合成为不透明色**（数学上恰为 QML 的 sRGB 直混结果），GPU
/// 只画不透明矩形。设计 alpha 全部保留为字面量（禁发明数值）。
#[derive(Debug, Clone, Copy)]
pub struct Tokens {
    pub kind: ThemeKind,
    pub bg: Color32,     // 画布
    pub ink: Color32,    // 主文字
    pub ink2: Color32,   // 次文字
    pub accent: Color32, // 唯一强调色
    pub accent_hover: Color32,
    pub accent_press: Color32,
    pub running: Color32, // 在线绿
    pub warn: Color32,
    pub danger: Color32,
    /// 卡（cardFill over bg）。
    pub card: Color32,
    /// 卡 hover（+hoverWash）。
    pub card_hover: Color32,
    /// 卡 1px 亮边（cardBorder over card）。
    pub card_border: Color32,
    /// 运行芯片（cardFill over card）。
    pub chip: Color32,
    /// 芯片 1px 边（cardBorder over chip）。
    pub chip_border: Color32,
    /// 滑轨/输入描边（hairline over card）。
    pub hairline_on_card: Color32,
    /// 顶栏胶囊底（flyoutFill over bg）。
    pub capsule: Color32,
    /// 胶囊 hover 段（hoverWash over capsule）。
    pub capsule_hover: Color32,
    /// 胶囊 1px 边（cardBorder over capsule）。
    pub capsule_border: Color32,
    /// 选中段（不透明：亮纯白 / 暗 #48484A）。
    pub segment_fill: Color32,
    /// 搜索胶囊（searchFill；暗色本就不透明 #28282A）。
    pub search: Color32,
    /// 搜索聚焦态（flyoutFill over bg）。
    pub search_focus: Color32,
    /// 菜单浮层不透明回退底（Style.menuFill）。
    pub menu_fill: Color32,
    /// 输入框/次按钮实底槽（Style.controlFill）。
    pub control_fill: Color32,
    /// Toast 深底胶囊（pillFill over bg）。
    pub pill: Color32,
    /// 磁贴/图标区 hover 洗色（hoverWash over bg）。
    pub hover_on_canvas: Color32,
    /// 卡内 hover 洗色（hoverWash over card）。
    pub hover_on_card: Color32,
    /// 卡内 press 洗色（pressWash over card）。
    pub press_on_card: Color32,
    /// 危险 hover 洗底（dangerWash over card）。
    pub danger_on_card: Color32,
    /// 投屏钮禁用态（accent@40% over bg，Main.qml opacity 0.4）。
    pub btn_disabled: Color32,
    pub btn_disabled_text: Color32,
    /// 色斑原色（CPU 合成用：(色, 设计alpha) ×3 层同心）。
    pub spot_blue: [(Color32, f32); 3],
    pub spot_green: [(Color32, f32); 3],
}

impl Tokens {
    pub fn dark() -> Self {
        let bg = hex("#1C1C1E");
        let white = hex("#FFFFFF");
        let card = over(bg, white, 0.10);
        let capsule = over(bg, white, 0.07);
        let chip = over(card, white, 0.10);
        Self {
            kind: ThemeKind::Dark,
            bg,
            ink: hex("#F5F5F7"),
            ink2: hex("#98989D"),
            accent: hex("#0A84FF"),
            accent_hover: hex("#409EFF"),
            accent_press: hex("#0066D6"),
            running: hex("#30D158"),
            warn: hex("#FF9F0A"),
            danger: hex("#FF453A"),
            card,
            card_hover: over(card, white, 0.06),
            card_border: over(card, white, 0.16),
            chip,
            chip_border: over(chip, white, 0.16),
            hairline_on_card: over(card, white, 0.14),
            capsule,
            capsule_hover: over(capsule, white, 0.06),
            capsule_border: over(capsule, white, 0.16),
            segment_fill: hex("#48484A"),
            search: hex("#28282A"),
            search_focus: capsule,
            menu_fill: hex("#2C2C2E"),
            control_fill: hex("#28282A"),
            pill: over(bg, hex("#48484A"), 0.90),
            hover_on_canvas: over(bg, white, 0.06),
            hover_on_card: over(card, white, 0.06),
            press_on_card: over(card, white, 0.12),
            danger_on_card: over(card, hex("#FF3B30"), 0.08),
            btn_disabled: over(bg, hex("#0A84FF"), 0.40),
            btn_disabled_text: over(bg, white, 0.40),
            spot_blue: [
                (hex("#007AFF"), 0.028),
                (hex("#007AFF"), 0.039),
                (hex("#007AFF"), 0.071),
            ],
            spot_green: [
                (hex("#34C759"), 0.020),
                (hex("#34C759"), 0.035),
                (hex("#34C759"), 0.063),
            ],
        }
    }

    pub fn light() -> Self {
        let bg = hex("#F5F5F7");
        let white = hex("#FFFFFF");
        let card = over(bg, white, 0.72);
        let capsule = over(bg, white, 0.60);
        let chip = over(card, white, 0.72);
        Self {
            kind: ThemeKind::Light,
            bg,
            ink: hex("#1D1D1F"),
            ink2: hex("#86868B"),
            accent: hex("#007AFF"),
            accent_hover: hex("#2E90FF"),
            accent_press: hex("#0066D6"),
            running: hex("#34C759"),
            warn: hex("#FF9F0A"),
            danger: hex("#FF3B30"),
            card,
            card_hover: over(card, hex("#000000"), 0.04),
            card_border: over(card, white, 0.65),
            chip,
            chip_border: over(chip, white, 0.65),
            hairline_on_card: over(card, hex("#000000"), 0.12),
            capsule,
            capsule_hover: over(capsule, hex("#000000"), 0.04),
            capsule_border: over(capsule, white, 0.65),
            segment_fill: white,
            search: over(bg, white, 0.72),
            search_focus: capsule,
            menu_fill: hex("#F7F7F9"),
            control_fill: white,
            pill: over(bg, hex("#1D1D1F"), 0.90),
            hover_on_canvas: over(bg, hex("#000000"), 0.04),
            hover_on_card: over(card, hex("#000000"), 0.04),
            press_on_card: over(card, hex("#000000"), 0.08),
            danger_on_card: over(card, hex("#FF3B30"), 0.08),
            btn_disabled: over(bg, hex("#007AFF"), 0.40),
            btn_disabled_text: over(bg, white, 0.40),
            spot_blue: [
                (hex("#007AFF"), 0.035),
                (hex("#007AFF"), 0.055),
                (hex("#007AFF"), 0.086),
            ],
            spot_green: [
                (hex("#34C759"), 0.027),
                (hex("#34C759"), 0.047),
                (hex("#34C759"), 0.078),
            ],
        }
    }

    pub fn of(kind: ThemeKind) -> Self {
        match kind {
            ThemeKind::Dark => Self::dark(),
            ThemeKind::Light => Self::light(),
        }
    }

    /// DWM blur 底染色（保留给未来真毛玻璃实验；当前窗口不透明）。
    pub fn blur_tint(&self) -> [u8; 4] {
        match self.kind {
            ThemeKind::Dark => [28, 28, 30, 255],
            ThemeKind::Light => [245, 245, 247, 255],
        }
    }
}

/// 画布色斑几何（Main.qml bgLayer 逐行照抄）：(x, y, 直径)。
pub const SPOTS_BLUE: [(f32, f32, f32); 3] = [
    (-272.0, -212.0, 504.0),
    (-180.0, -120.0, 320.0),
    (-132.0, -72.0, 224.0),
];
pub const SPOTS_GREEN: [(f32, f32, f32); 3] = [
    (205.0, 385.0, 570.0),
    (310.0, 490.0, 360.0),
    (370.0, 550.0, 240.0),
];

/// 未知应用 fallback 色板（Style.fallbackPalette 顺序照抄；取色 =
/// 包名 charCode 和 % 12）。
pub const FALLBACK_PALETTE: [Color32; 12] = [
    Color32::from_rgb(0x5F, 0x72, 0x92),
    Color32::from_rgb(0x6F, 0x84, 0x68),
    Color32::from_rgb(0x96, 0x72, 0x5D),
    Color32::from_rgb(0x7D, 0x6B, 0x94),
    Color32::from_rgb(0x62, 0x8C, 0x87),
    Color32::from_rgb(0x94, 0x63, 0x63),
    Color32::from_rgb(0x6C, 0x7F, 0xA3),
    Color32::from_rgb(0x82, 0x94, 0x62),
    Color32::from_rgb(0x93, 0x62, 0x80),
    Color32::from_rgb(0x62, 0x81, 0x92),
    Color32::from_rgb(0x8C, 0x71, 0x63),
    Color32::from_rgb(0x74, 0x6A, 0x92),
];

/// 包名 → fallback 色（Main.qml fallbackColor 同构）。
pub fn fallback_color(package: &str) -> Color32 {
    let sum: u32 = package.chars().map(|c| u32::from(c as u32 as u16)).sum();
    FALLBACK_PALETTE[(sum % 12) as usize]
}

/// DESIGN.md §2 圆角（dip）：卡 16 / 控件 10 / 浮层 12；胶囊 = 高度/2。
pub mod rounding {
    pub const CARD: f32 = 16.0;
    pub const CONTROL: f32 = 10.0;
    pub const FLYOUT: f32 = 12.0;
    /// 图标 squircle 的圆弧近似（60px 的 23% ≈ 14）。
    pub const ICON: f32 = 14.0;
}

/// 页面左右留白 20（间距只用 4 的倍数）。
pub const PAGE_MARGIN: f32 = 20.0;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_parses_design_tokens() {
        assert_eq!(hex("#1C1C1E"), Color32::from_rgb(0x1C, 0x1C, 0x1E));
        assert_eq!(hex("#007AFF"), Color32::from_rgb(0x00, 0x7A, 0xFF));
    }

    #[test]
    fn dark_tokens_match_design_table() {
        let t = Tokens::dark();
        assert_eq!(t.bg, hex("#1C1C1E"));
        assert_eq!(t.ink, hex("#F5F5F7"));
        assert_eq!(t.accent, hex("#0A84FF"));
        assert_eq!(t.ink2, hex("#98989D"));
        // 卡 = 10% 白 over bg（CPU 直混 = QML 结果）
        assert_eq!(t.card, hex("#333335"));
        assert_eq!(t.segment_fill, hex("#48484A"));
        // Style.qml 控件底材/搜索/菜单（不透明原值）
        assert_eq!(t.control_fill, hex("#28282A"));
        assert_eq!(t.search, hex("#28282A"));
        assert_eq!(t.menu_fill, hex("#2C2C2E"));
    }

    #[test]
    fn light_tokens_match_style_qml() {
        let t = Tokens::light();
        assert_eq!(t.control_fill, hex("#FFFFFF"));
        assert_eq!(t.menu_fill, hex("#F7F7F9"));
        // 搜索 = 72% 白 over #F5F5F7
        assert_eq!(t.search, hex("#FCFCFD"));
    }

    #[test]
    fn precomputed_layers_are_opaque_and_ordered() {
        let t = Tokens::dark();
        for c in [
            t.card,
            t.card_hover,
            t.capsule,
            t.chip,
            t.search_focus,
            t.pill,
        ] {
            assert_eq!(c.a(), 255, "预合成层必须不透明");
        }
        // hover 在卡之上再提亮：亮于卡、暗于纯白
        assert!(t.card_hover.r() > t.card.r());
        assert!(t.card_hover.r() < 255);
        // 卡边比卡面更亮
        assert!(t.card_border.r() > t.card.r());
        assert!(t.hover_on_card.r() > t.card.r());
        assert!(t.press_on_card.r() > t.hover_on_card.r());
    }

    #[test]
    fn light_spots_and_disabled_button() {
        let t = Tokens::light();
        // 投屏钮禁用 = accent@40% over bg
        assert_eq!(t.btn_disabled, over(t.bg, hex("#007AFF"), 0.40));
    }

    #[test]
    fn theme_from_settings_folds_system_to_dark() {
        assert_eq!(ThemeKind::from_settings("dark"), ThemeKind::Dark);
        assert_eq!(ThemeKind::from_settings("system"), ThemeKind::Dark);
        assert_eq!(ThemeKind::from_settings("light"), ThemeKind::Light);
    }
}

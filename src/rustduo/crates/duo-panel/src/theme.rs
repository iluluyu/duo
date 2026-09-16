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

fn rgba(r: u8, g: u8, b: u8, a: f32) -> Color32 {
    Color32::from_rgba_unmultiplied(r, g, b, (a * 255.0).round() as u8)
}

/// 一套主题令牌（DESIGN.md §2 表格逐行对译）。
#[derive(Debug, Clone, Copy)]
pub struct Tokens {
    pub kind: ThemeKind,
    pub bg: Color32,     // 画布（不含玻璃 alpha；玻璃版见 canvas）
    pub ink: Color32,    // 主文字
    pub ink2: Color32,   // 次文字
    pub accent: Color32, // 唯一强调色
    pub accent_hover: Color32,
    pub accent_press: Color32,
    pub running: Color32, // 在线绿
    pub warn: Color32,
    pub danger: Color32,
    pub card_fill: Color32,    // 轻玻璃卡填充
    pub card_border: Color32,  // 卡 1px 亮边
    pub segment_fill: Color32, // 选中段
    pub flyout_fill: Color32,  // 胶囊浮层
    pub pill_fill: Color32,
    pub hover_wash: Color32, // hover 提亮
    pub press_wash: Color32,
    pub hairline: Color32,
    /// 菜单浮层不透明回退底（Style.menuFill）。
    pub menu_fill: Color32,
    /// 输入框/次按钮实底槽（Style.controlFill）。
    pub control_fill: Color32,
    /// 画布级搜索胶囊（Style.searchFill）。
    pub search_fill: Color32,
    /// 危险动作 hover 洗底（Style.dangerWash）。
    pub danger_wash: Color32,
    /// 画布色斑：蓝/绿各三层同心衰减（几何见 SPOTS_BLUE/GREEN）。
    pub spot_blue: [Color32; 3],
    pub spot_green: [Color32; 3],
}

impl Tokens {
    pub fn dark() -> Self {
        Self {
            kind: ThemeKind::Dark,
            bg: hex("#1C1C1E"),
            ink: hex("#F5F5F7"),
            ink2: hex("#98989D"),
            accent: hex("#0A84FF"),
            accent_hover: hex("#409EFF"),
            accent_press: hex("#0066D6"),
            running: hex("#30D158"),
            warn: hex("#FF9F0A"),
            danger: hex("#FF453A"),
            card_fill: rgba(255, 255, 255, 0.10),
            card_border: rgba(255, 255, 255, 0.16),
            segment_fill: hex("#48484A"),
            flyout_fill: rgba(255, 255, 255, 0.07),
            pill_fill: rgba(72, 72, 74, 0.90),
            hover_wash: rgba(255, 255, 255, 0.06),
            press_wash: rgba(255, 255, 255, 0.12),
            hairline: rgba(255, 255, 255, 0.14),
            menu_fill: hex("#2C2C2E"),
            control_fill: hex("#28282A"),
            search_fill: hex("#28282A"),
            danger_wash: rgba(255, 59, 48, 0.08),
            spot_blue: [
                rgba(0, 122, 255, 0.028),
                rgba(0, 122, 255, 0.039),
                rgba(0, 122, 255, 0.047),
            ],
            spot_green: [
                rgba(52, 199, 89, 0.020),
                rgba(52, 199, 89, 0.035),
                rgba(52, 199, 89, 0.063),
            ],
        }
    }

    pub fn light() -> Self {
        Self {
            kind: ThemeKind::Light,
            bg: hex("#F5F5F7"),
            ink: hex("#1D1D1F"),
            ink2: hex("#86868B"),
            accent: hex("#007AFF"),
            accent_hover: hex("#2E90FF"),
            accent_press: hex("#0066D6"),
            running: hex("#34C759"),
            warn: hex("#FF9F0A"),
            danger: hex("#FF3B30"),
            card_fill: rgba(255, 255, 255, 0.72),
            card_border: rgba(255, 255, 255, 0.65),
            segment_fill: hex("#FFFFFF"),
            flyout_fill: rgba(255, 255, 255, 0.60),
            pill_fill: rgba(29, 29, 31, 0.90),
            hover_wash: rgba(0, 0, 0, 0.04),
            press_wash: rgba(0, 0, 0, 0.08),
            hairline: rgba(0, 0, 0, 0.12),
            menu_fill: hex("#F7F7F9"),
            control_fill: hex("#FFFFFF"),
            search_fill: rgba(255, 255, 255, 0.72),
            danger_wash: rgba(255, 59, 48, 0.08),
            spot_blue: [
                rgba(0, 122, 255, 0.035),
                rgba(0, 122, 255, 0.055),
                rgba(0, 122, 255, 0.086),
            ],
            spot_green: [
                rgba(52, 199, 89, 0.027),
                rgba(52, 199, 89, 0.047),
                rgba(52, 199, 89, 0.078),
            ],
        }
    }

    pub fn of(kind: ThemeKind) -> Self {
        match kind {
            ThemeKind::Dark => Self::dark(),
            ThemeKind::Light => Self::light(),
        }
    }

    /// 画布清屏色：玻璃开 = 画布带 alpha（透出 DWM blur 的系统毛玻璃）；
    /// 玻璃关 = 不透明。alpha 取 DESIGN.md 菜单语义（近不透明底）。
    pub fn canvas(&self, glass: bool) -> Color32 {
        if glass {
            match self.kind {
                ThemeKind::Dark => rgba(28, 28, 30, 0.72),
                ThemeKind::Light => rgba(245, 245, 247, 0.78),
            }
        } else {
            self.bg
        }
    }

    /// DWM blur 底染色（apply_blur 的 tint）：深色底系压暗，浅色提亮。
    pub fn blur_tint(&self) -> [u8; 4] {
        match self.kind {
            ThemeKind::Dark => [28, 28, 30, 255],
            ThemeKind::Light => [245, 245, 247, 255],
        }
    }

    /// 卡 hover 填充 = 卡填充叠 hover_wash（玻璃卡 hover 提亮）。
    pub fn card_hover(&self) -> Color32 {
        let mut c = self.card_fill;
        let wash = self.hover_wash;
        c[0] = c[0].saturating_add(wash[0] / 2);
        c[1] = c[1].saturating_add(wash[1] / 2);
        c[2] = c[2].saturating_add(wash[2] / 2);
        c
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
        assert_eq!(
            t.card_fill,
            Color32::from_rgba_unmultiplied(255, 255, 255, 26)
        );
        assert_eq!(t.segment_fill, hex("#48484A"));
        // Style.qml 控件底材/搜索/菜单回退（像素对齐，禁发明数值）
        assert_eq!(t.control_fill, hex("#28282A"));
        assert_eq!(t.search_fill, hex("#28282A"));
        assert_eq!(t.menu_fill, hex("#2C2C2E"));
    }

    #[test]
    fn light_tokens_match_style_qml() {
        let t = Tokens::light();
        assert_eq!(t.control_fill, hex("#FFFFFF"));
        assert_eq!(t.menu_fill, hex("#F7F7F9"));
        assert_eq!(
            t.search_fill,
            Color32::from_rgba_unmultiplied(255, 255, 255, 184)
        );
    }

    #[test]
    fn canvas_is_transparent_only_with_glass() {
        let t = Tokens::dark();
        assert_eq!(t.canvas(false)[3], 255);
        assert!(t.canvas(true)[3] < 255);
    }

    #[test]
    fn hover_lightens_card() {
        let t = Tokens::dark();
        let h = t.card_hover();
        assert!(h.r() > t.card_fill.r() || h[0] > t.card_fill[0]);
    }

    #[test]
    fn theme_from_settings_folds_system_to_dark() {
        assert_eq!(ThemeKind::from_settings("dark"), ThemeKind::Dark);
        assert_eq!(ThemeKind::from_settings("system"), ThemeKind::Dark);
        assert_eq!(ThemeKind::from_settings("light"), ThemeKind::Light);
    }
}

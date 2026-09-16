//! PC 显示器几何与 DPI 推荐。对译自 duo/core/monitor.py（纯几何部分）；
//! 合同镜像 tests/test_monitor.py。
//!
//! scrcpy 在建屏时一次性设定虚拟屏密度，竖屏体验取决于按目标窗口几何
//! 选密度：横屏 = flex 屏（自由跟随窗口）；竖屏 = 定尺寸固定屏（贴右缘
//! 的塔形窗口）。PowerShell 工作区探测属进程层（winproc 移植，0.3），
//! 此处只落几何推导。

use crate::aspects::scaled_size;
use crate::engine::{DisplayMode, DisplaySpec, WindowGeometry};

/// 探测失败时的回退工作区（4K 减一条任务栏）。
pub const FALLBACK_WORK_AREA: (i64, i64) = (3840, 2054);

/// dp 目标宽度（历史参数：现两预设均为固定值，保留对齐 Python 签名）。
pub const LANDSCAPE_TARGET_DP: i64 = 1280;
pub const PORTRAIT_TARGET_DP: i64 = 640;

/// 竖屏窗口宽相对工作区高的比例（文档口径；实际按 1080 预设 min 钳制）。
pub const PORTRAIT_WIDTH_RATIO: f64 = 0.6;

/// 主显示器工作区（物理像素）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkArea {
    pub width: i64,
    pub height: i64,
}

/// 单一方向的显示模式推荐参数。
///
/// display_width/height 只给竖屏（固定 WxH 屏）；横屏走 flex，无需显示
/// 尺寸，只要 dpi。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct DisplayRecommendation {
    pub dpi: i64,
    pub display_width: Option<i64>,
    pub display_height: Option<i64>,
    pub window: Option<WindowGeometry>,
}

/// 固定 16:9 横屏预设：flex 跟随窗口，无窗口几何（初始窗=显示预设）。
///
/// 密度不再由显示器推导（2026-09-06 定稿）：CLI 注入设备自身有效密度
/// （wm density，Override 优先），此处 dpi 只是惰性兜底，跟随设置页
/// 默认（2026-09-11 起 160）。
pub fn recommend_landscape(_area: WorkArea, _target_dp: i64) -> DisplayRecommendation {
    DisplayRecommendation {
        dpi: 160,
        ..Default::default()
    }
}

/// 固定 9:16 竖屏预设：1080x1920 固定屏 + 贴工作区右缘的窗口；密度
/// 来自设备探测（CLI），此处 dpi 是惰性兜底。窗口被钳进工作区，显示
/// 预设恒为 1080x1920（flex 会重新跟随实际窗口）。
pub fn recommend_portrait(area: WorkArea, _target_dp: i64) -> DisplayRecommendation {
    let height = 1920.min(area.height);
    let width = 1080.min(area.width);
    let window = WindowGeometry {
        x: 0.max(area.width - width),
        y: 0.max((area.height - height) / 2),
        width,
        height,
    };
    DisplayRecommendation {
        dpi: 160,
        display_width: Some(1080),
        display_height: Some(1920),
        window: Some(window),
    }
}

/// flex DisplaySpec × 倍率 → fixed DisplaySpec（窗口÷k 渲染）。
///
/// 倍率 > 1.0 时 flex 会话换成固定屏：基准 = 意图窗口（横屏 flex 无显式
/// 几何 → 主屏工作区，即最大化窗口；竖屏 flex 已带 1080x1920 初始形状
/// 则按它缩），render = 基准 ÷ k（偶数取整见 aspects::scaled_size）。
/// 固定屏 + 窗口比例锁 = 零失真放大（“窗口 4K、安卓 1K 渲染”）——
/// scrcpy flex+--max-size 是逐维钳制（比例被拉歪），不可用，论证见
/// docs/mirroring-quality.md §5。fixed/mirror 原样返回：固定比例会话
/// 已有自己的几何（倍率不叠加），物理屏无法缩。
pub fn apply_render_scale(display: &DisplaySpec, area: WorkArea, scale: f64) -> DisplaySpec {
    if scale <= 1.0 || display.mode != DisplayMode::Flex {
        return display.clone();
    }
    let base_w = display.width.map(i64::from).unwrap_or(area.width);
    let base_h = display.height.map(i64::from).unwrap_or(area.height);
    match scaled_size(base_w, base_h, scale) {
        Ok((width, height)) => DisplaySpec {
            mode: DisplayMode::Fixed,
            width: Some(width as u32),
            height: Some(height as u32),
            dpi: display.dpi,
        },
        // 正常输入（正尺寸、倍率>1）不会走到这里；防御性原样返回。
        Err(_) => display.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const AREA_4K: WorkArea = WorkArea {
        width: 3840,
        height: 2054,
    };
    const AREA_1080P: WorkArea = WorkArea {
        width: 1920,
        height: 1040,
    };

    #[test]
    fn landscape_4k_recommends_inert_fallback() {
        // 横屏 = 固定 16:9 预设：dpi 只是惰性兜底（真密度来自 CLI 的
        // 设备探测），无窗口几何（初始窗=显示预设）。
        let rec = recommend_landscape(AREA_4K, LANDSCAPE_TARGET_DP);
        assert_eq!(rec.dpi, 160);
        assert_eq!(rec.window, None);
        assert_eq!(rec.display_width, None);
        assert_eq!(rec.display_height, None);
    }

    #[test]
    fn landscape_1080p_is_monitor_independent() {
        // 预设与显示器无关（flex 之后跟随窗口）。
        let rec = recommend_landscape(AREA_1080P, LANDSCAPE_TARGET_DP);
        assert_eq!(rec.dpi, 160);
    }

    #[test]
    fn portrait_window_geometry() {
        // 竖屏 = 9:16 预设 + 贴右缘窗口。
        let rec = recommend_portrait(AREA_4K, PORTRAIT_TARGET_DP);
        assert_eq!(rec.display_width, Some(1080));
        assert_eq!(rec.display_height, Some(1920));
        let window = rec.window.unwrap();
        assert_eq!(window.width, 1080);
        assert_eq!(window.height, 1920);
        assert_eq!(window.x, AREA_4K.width - window.width);
        assert_eq!(window.y, (AREA_4K.height - window.height) / 2);
        // 密度来自设备探测；回退值 160 跟随新设置页默认（2026-09-11）。
        assert_eq!(rec.dpi, 160);
    }

    #[test]
    fn portrait_dpi_equals_landscape_fallback() {
        // 两预设共享设备密度兜底（密度由设备推导，与方向无关）。
        let portrait = recommend_portrait(AREA_4K, PORTRAIT_TARGET_DP);
        let landscape = recommend_landscape(AREA_4K, LANDSCAPE_TARGET_DP);
        assert_eq!(portrait.dpi, 160);
        assert_eq!(landscape.dpi, 160);
    }

    #[test]
    fn portrait_on_narrow_monitor_clamps_window() {
        // 窄工作区把窗口钳进去（显示预设不变，flex 重新跟随实际窗口）。
        let rec = recommend_portrait(
            WorkArea {
                width: 1000,
                height: 800,
            },
            PORTRAIT_TARGET_DP,
        );
        let window = rec.window.unwrap();
        assert!(window.width <= 1000);
        assert!(window.height <= 800);
        assert_eq!((window.width, window.height), (1000, 800));
        assert_eq!(window.x, 0);
        assert_eq!(window.y, 0);
        assert_eq!(rec.display_width, Some(1080));
        assert_eq!(rec.dpi, 160);
    }

    #[test]
    fn scale_one_returns_display_unchanged() {
        // 渲染倍率：flex DisplaySpec × k → 窗口÷k 的固定屏；1.0 原样。
        let flex = DisplaySpec::default();
        assert_eq!(apply_render_scale(&flex, AREA_4K, 1.0), flex);
    }

    #[test]
    fn fixed_and_mirror_untouched() {
        // 固定比例会话自带几何，倍率不叠加；物理屏无法缩。
        let fixed = DisplaySpec {
            mode: DisplayMode::Fixed,
            width: Some(2560),
            height: Some(1440),
            dpi: Some(160),
        };
        assert_eq!(apply_render_scale(&fixed, AREA_4K, 2.0), fixed);
        let mirror = DisplaySpec {
            mode: DisplayMode::Mirror,
            ..Default::default()
        };
        assert_eq!(apply_render_scale(&mirror, AREA_4K, 2.0), mirror);
    }

    #[test]
    fn landscape_flex_scales_from_work_area() {
        // 横屏 flex 无显式几何 → 基准 = 主屏工作区（最大化窗口）。
        // 4K 工作区 ÷2 = 1K 渲染（用户定稿的倍率语义）。
        let flex = DisplaySpec {
            dpi: Some(160),
            ..Default::default()
        };
        let scaled = apply_render_scale(&flex, AREA_4K, 2.0);
        assert_eq!(scaled.mode, DisplayMode::Fixed);
        assert_eq!((scaled.width, scaled.height), (Some(1920), Some(1028)));
        assert_eq!(scaled.dpi, Some(160));
    }

    #[test]
    fn portrait_flex_scales_from_initial_shape() {
        // 竖屏 flex 已带 1080x1920 初始形状 → 按它缩。
        let flex = DisplaySpec {
            width: Some(1080),
            height: Some(1920),
            dpi: Some(240),
            ..Default::default()
        };
        let scaled = apply_render_scale(&flex, AREA_4K, 2.0);
        assert_eq!(scaled.mode, DisplayMode::Fixed);
        assert_eq!((scaled.width, scaled.height), (Some(540), Some(960)));
        assert_eq!(scaled.dpi, Some(240));
    }

    #[test]
    fn odd_results_snap_to_even() {
        // 奇数取整后上调到偶（编码器/窗口整数配置）。
        let flex = DisplaySpec::default();
        let scaled = apply_render_scale(
            &flex,
            WorkArea {
                width: 3841,
                height: 2055,
            },
            1.75,
        );
        assert_eq!(scaled.width.unwrap() % 2, 0);
        assert_eq!(scaled.height.unwrap() % 2, 0);
        assert_eq!((scaled.width, scaled.height), (Some(2196), Some(1174)));
    }
}

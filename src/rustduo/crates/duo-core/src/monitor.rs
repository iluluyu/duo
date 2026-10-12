//! PC 显示器几何与 DPI 推荐。对译自 duo/core/monitor.py（纯几何部分）；
//! 合同镜像 tests/test_monitor.py。
//!
//! scrcpy 在建屏时一次性设定虚拟屏密度，竖屏体验取决于按目标窗口几何
//! 选密度：横屏 = flex 屏（自由跟随窗口）；竖屏 = 定尺寸固定屏（贴右缘
//! 的塔形窗口）。目标屏工作区探测（PMv2 物理像素 + origin + 有效 DPI，
//! 2026-11）见 launch_monitor；多显示器开窗/密度语义论证见
//! docs/mirroring-quality.md §5b。

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

/// 启动目标屏：origin 为工作区左上角的全局（虚拟桌面）坐标——副屏/负
/// 坐标屏非零；area 为物理像素工作区；effective_dpi 为宿主缩放（144 =
/// 150%）；primary 标记主屏（flex 主屏沿用 OS 默认摆窗，见 §5b）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TargetMonitor {
    pub origin_x: i64,
    pub origin_y: i64,
    pub area: WorkArea,
    pub effective_dpi: u32,
    pub primary: bool,
}

/// native-canvas 初窗宽度上限（DIP，96 基准）：≈ 手机伴随窗宽度。
pub const HOST_INITIAL_DIP_WIDTH: i64 = 600;

/// DIP → 目标屏物理像素（600 DIP @144dpi → 900px）。
pub fn dip_px(dip: i64, effective_dpi: u32) -> i64 {
    dip * i64::from(effective_dpi.max(1)) / 96
}

/// 面板 Sessions 默认 spawner 启动时钉入的光标点（DUO_HOST_X/DUO_HOST_Y，
/// 避免 adb 探测后光标已移屏）；两值都解析成功才生效。
pub fn env_pinned_point() -> Option<(i64, i64)> {
    let x = std::env::var("DUO_HOST_X").ok()?.parse::<i64>().ok()?;
    let y = std::env::var("DUO_HOST_Y").ok()?.parse::<i64>().ok()?;
    Some((x, y))
}

/// 启动目标屏：env 钉点 > 光标 > 主屏回退（探测失败回退常量主屏）。
pub fn launch_monitor() -> TargetMonitor {
    resolve_target_monitor(env_pinned_point().or_else(cursor_position))
}

/// 目标屏裁决：给点取所在屏，无点回主屏；非 Windows 恒回退。
pub fn resolve_target_monitor(point: Option<(i64, i64)>) -> TargetMonitor {
    match point {
        Some((x, y)) => probe_physical(x, y, false),
        None => probe_physical(0, 0, true),
    }
    .unwrap_or_else(fallback_monitor)
}

fn fallback_monitor() -> TargetMonitor {
    TargetMonitor {
        origin_x: 0,
        origin_y: 0,
        area: WorkArea {
            width: FALLBACK_WORK_AREA.0,
            height: FALLBACK_WORK_AREA.1,
        },
        effective_dpi: 96,
        primary: true,
    }
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

/// 主显示器工作区（物理像素）。Windows：临时线程 PMv2 探测主屏 rcWork
///（真机物理像素）；其余平台/探测失败回退 4K 减任务栏常量。
pub fn primary_work_area() -> WorkArea {
    resolve_target_monitor(None).area
}

/// 光标位置（全局虚拟桌面坐标）；CLI 无 env 钉点时按光标选屏。
#[cfg(windows)]
pub fn cursor_position() -> Option<(i64, i64)> {
    use windows::Win32::Foundation::POINT;
    use windows::Win32::UI::HiDpi::{
        SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
    };
    use windows::Win32::UI::WindowsAndMessaging::GetCursorPos;
    let mut pt = POINT::default();
    let result = unsafe {
        let previous = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let result = GetCursorPos(&mut pt);
        if !previous.is_invalid() {
            SetThreadDpiAwarenessContext(previous);
        }
        result
    };
    result.ok()?;
    Some((i64::from(pt.x), i64::from(pt.y)))
}

#[cfg(not(windows))]
pub fn cursor_position() -> Option<(i64, i64)> {
    None
}

/// 点 → 所在物理屏。临时线程 PMv2（用后恢复）保证 GetMonitorInfoW/
/// GetDpiForMonitor 返回物理像素；default_to_primary 时无点回主屏。
#[cfg(windows)]
fn probe_physical(x: i64, y: i64, default_to_primary: bool) -> Option<TargetMonitor> {
    use windows::Win32::Foundation::POINT;
    use windows::Win32::Graphics::Gdi::{
        GetMonitorInfoW, MonitorFromPoint, MONITORINFO, MONITOR_DEFAULTTONEAREST,
        MONITOR_DEFAULTTOPRIMARY,
    };
    use windows::Win32::UI::HiDpi::{
        GetDpiForMonitor, SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        MDT_EFFECTIVE_DPI,
    };
    unsafe {
        let prev = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let flags = if default_to_primary {
            MONITOR_DEFAULTTOPRIMARY
        } else {
            MONITOR_DEFAULTTONEAREST
        };
        let monitor = MonitorFromPoint(
            POINT {
                x: x as i32,
                y: y as i32,
            },
            flags,
        );
        let mut target = None;
        if !monitor.is_invalid() {
            let mut info = MONITORINFO {
                cbSize: std::mem::size_of::<MONITORINFO>() as u32,
                ..Default::default()
            };
            if GetMonitorInfoW(monitor, &mut info).as_bool() {
                let (w, h) = (
                    i64::from(info.rcWork.right - info.rcWork.left),
                    i64::from(info.rcWork.bottom - info.rcWork.top),
                );
                if w >= 640 && h >= 480 {
                    let (mut dpi_x, mut dpi_y) = (0u32, 0u32);
                    let dpi =
                        if GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut dpi_x, &mut dpi_y)
                            .is_ok()
                        {
                            dpi_x.max(1)
                        } else {
                            96
                        };
                    target = Some(TargetMonitor {
                        origin_x: i64::from(info.rcWork.left),
                        origin_y: i64::from(info.rcWork.top),
                        area: WorkArea {
                            width: w,
                            height: h,
                        },
                        effective_dpi: dpi,
                        primary: info.dwFlags & 1 != 0,
                    });
                }
            }
        }
        if !prev.is_invalid() {
            SetThreadDpiAwarenessContext(prev);
        }
        target
    }
}

#[cfg(not(windows))]
fn probe_physical(_x: i64, _y: i64, _default_to_primary: bool) -> Option<TargetMonitor> {
    None
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

    #[test]
    fn dip_px_scales_with_effective_dpi() {
        // DIP 按 96 基准换算：4K@144%、副屏@125%、无缩放。
        assert_eq!(dip_px(600, 96), 600);
        assert_eq!(dip_px(600, 120), 750);
        assert_eq!(dip_px(600, 144), 900);
    }

    #[test]
    fn fallback_monitor_is_primary_at_origin() {
        // 探测失败回退：主屏、origin 0、4K-任务栏、96dpi。
        let m = fallback_monitor();
        assert!(m.primary);
        assert_eq!((m.origin_x, m.origin_y), (0, 0));
        assert_eq!(
            (m.area.width, m.area.height),
            (FALLBACK_WORK_AREA.0, FALLBACK_WORK_AREA.1)
        );
        assert_eq!(m.effective_dpi, 96);
    }

    #[cfg(not(windows))]
    #[test]
    fn resolve_target_monitor_falls_back_off_windows() {
        // 非 Windows 恒回退：无点与有点同形（主屏常量几何）。
        assert_eq!(resolve_target_monitor(None), fallback_monitor());
        assert_eq!(
            resolve_target_monitor(Some((-9999, -9999))),
            fallback_monitor()
        );
    }
}

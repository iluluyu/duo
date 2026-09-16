//! 沉浸式宿主窗口（TODO 0.3，对译 chrome_overlay.cs `EmbedHost`/`EmbedBand`；
//! 设计合同 docs/window-experience.md §14）。同进程 = 引擎监督线程 + Win32
//! 宿主窗口主线程；scrcpy 窗口 SetParent 嵌入为 WS_CHILD 铺满客户区。
//!
//! 本文件只放可跨平台测试的纯逻辑（样式手术位运算、Glue 比对、嵌入接管
//! 数学、band 几何/命中/双击判定、band 软件渲染管线）与线程编排；Win32
//! 部分住 `win` 子模块（`#[cfg(windows)]`，非 Windows 编译零影响）。

#[cfg(windows)]
pub(crate) mod win;

use crate::session::SessionEvent;
#[cfg(windows)]
use crate::session::{run_session_abortable, SessionSpec};
use std::sync::atomic::AtomicBool;
#[cfg(windows)]
use std::sync::atomic::Ordering;
#[cfg(windows)]
use std::sync::Arc;
use std::sync::Mutex;
#[cfg(windows)]
use std::thread;

/// tick 周期与首次等窗预算（对译 EmbedHost.TickMs/FirstWaitMs）。
pub const TICK_MS: i32 = 100;
pub const FIRST_WAIT_MS: i32 = 12_000;
/// 宿主 ✕ 后等子窗口自行退出的宽限，超时强拆（防 scrcpy 挂死不退）。
pub const CLOSE_GRACE_MS: i64 = 5_000;
/// 宿主最小拖拽尺寸（对译 MinimumSize，AutoScaleMode.None = 原始像素）。
pub const MIN_TRACK_W: i32 = 360;
pub const MIN_TRACK_H: i32 = 300;

// Win32 样式位（ABI 稳定值，纯数学层与 win 层共用）。
pub const WS_CHILD: i32 = 0x4000_0000u32 as i32;
pub const WS_POPUP: i32 = 0x8000_0000u32 as i32;
pub const WS_CAPTION: i32 = 0x00C0_0000;
pub const WS_THICKFRAME: i32 = 0x0004_0000;
pub const WS_SYSMENU: i32 = 0x0008_0000;
pub const WS_MINIMIZEBOX: i32 = 0x0002_0000;
pub const WS_MAXIMIZEBOX: i32 = 0x0001_0000;
pub const WS_EX_APPWINDOW: i32 = 0x0004_0000;
pub const WS_VISIBLE: i32 = 0x1000_0000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PxRect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

impl PxRect {
    pub fn width(&self) -> i32 {
        self.right - self.left
    }

    pub fn height(&self) -> i32 {
        self.bottom - self.top
    }
}

/// 子窗口样式手术：剥 WS_POPUP|WS_CAPTION|WS_THICKFRAME、加 WS_CHILD。
pub fn child_style_after_surgery(style: i32) -> i32 {
    (style & !(WS_POPUP | WS_CAPTION | WS_THICKFRAME)) | WS_CHILD
}

/// SDL 渲染器重建可能自我复辟样式 → tick 漂移检测（幂等时为 false）。
pub fn child_style_needs_surgery(style: i32) -> bool {
    style != child_style_after_surgery(style)
}

/// 子窗口 ex 样式：清 WS_EX_APPWINDOW（任务栏只有宿主一个条目）。
pub fn child_ex_after_surgery(ex: i32) -> i32 {
    ex & !WS_EX_APPWINDOW
}

/// 嵌入目标客户区：scrcpy 原窗口尺寸做下限钳制（对译 Embed 防御）。
pub fn embed_client_target(child: PxRect) -> (i32, i32) {
    (child.width().max(200), child.height().max(160))
}

/// AdjustWindowRectEx 语义的纯数学版：客户区矩形 + 四边框架增量 → 外框。
pub fn adjust_rect(client: PxRect, ml: i32, mt: i32, mr: i32, mb: i32) -> PxRect {
    PxRect {
        left: client.left + ml,
        top: client.top + mt,
        right: client.right + mr,
        bottom: client.bottom + mb,
    }
}

/// 像素级接管：以 scrcpy 原窗口矩形为客户区目标反推宿主外框——宿主亮相
/// 瞬间视频零跳变。
pub fn embed_host_outer(child: PxRect, ml: i32, mt: i32, mr: i32, mb: i32) -> PxRect {
    let (cw, ch) = embed_client_target(child);
    adjust_rect(
        PxRect {
            left: child.left,
            top: child.top,
            right: child.left + cw,
            bottom: child.top + ch,
        },
        ml,
        mt,
        mr,
        mb,
    )
}

/// Glue 比对：子窗原点（经 ScreenToClient 折算）非 0 或尺寸不等于宿主
/// 客户区 = 漂移（镜像转屏时 scrcpy 自改窗尺寸）。
pub fn glue_drifted(
    client_w: i32,
    client_h: i32,
    child_origin: (i32, i32),
    child_w: i32,
    child_h: i32,
) -> bool {
    child_origin != (0, 0) || child_w != client_w || child_h != client_h
}

/// 每 tick 的决策核心（对译 EmbedHost.OnTick 分支；engine_alive 是同进程
/// 监督带来的扩展：引擎重启中时子窗消亡不拆宿主，回头等新窗口）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TickAction {
    KeepWaiting,
    GiveUp,
    Embed,
    FollowChildDeath,
    Glue,
}

pub fn tick_action(
    embedded: bool,
    child_alive: bool,
    found_now: bool,
    waited_ms: i32,
    engine_alive: bool,
) -> TickAction {
    if !embedded {
        if found_now {
            return TickAction::Embed;
        }
        if !engine_alive || waited_ms >= FIRST_WAIT_MS {
            return TickAction::GiveUp;
        }
        return TickAction::KeepWaiting;
    }
    if !child_alive {
        return if engine_alive {
            TickAction::KeepWaiting
        } else {
            TickAction::FollowChildDeath
        };
    }
    TickAction::Glue
}

// ------------------------------------------------------------- band geometry

pub const BAND_H_DIP: f32 = 40.0;
pub const BAND_BTN_DIP: f32 = 30.0;
pub const BAND_PAD_DIP: f32 = 5.0;
pub const BAND_GAP_DIP: f32 = 6.0;
pub const BAND_MARGIN_DIP: f32 = 8.0;

/// band 像素几何（DIP × dpi 后取整，对译 C# `(int)(x * _dpi)` 截断）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BandLayout {
    pub band_h: i32,
    pub btn: i32,
    pub pad: i32,
    pub gap: i32,
    pub margin: i32,
}

impl BandLayout {
    pub fn from_dpi(dpi: f32) -> Self {
        Self {
            band_h: (BAND_H_DIP * dpi) as i32,
            btn: (BAND_BTN_DIP * dpi) as i32,
            pad: (BAND_PAD_DIP * dpi) as i32,
            gap: (BAND_GAP_DIP * dpi) as i32,
            margin: (BAND_MARGIN_DIP * dpi) as i32,
        }
    }
}

/// 悬停胶囊 ─ □ ✕ 的外框（对译 EmbedBand.CapsuleRect）。
pub fn capsule_rect(width: i32, height: i32, l: &BandLayout) -> PxRect {
    let w = 2 * l.pad + 3 * l.btn + 2 * l.gap;
    let h = l.btn + 2 * l.pad;
    PxRect {
        left: width - w - l.margin,
        top: (height - h) / 2,
        right: width - w - l.margin + w,
        bottom: (height - h) / 2 + h,
    }
}

/// i ∈ 0..3：─ □ ✕（对译 EmbedBand.ButtonRect）。
pub fn button_rect(i: i32, cap: PxRect, l: &BandLayout) -> PxRect {
    PxRect {
        left: cap.left + l.pad + i * (l.btn + l.gap),
        top: cap.top + l.pad,
        right: cap.left + l.pad + i * (l.btn + l.gap) + l.btn,
        bottom: cap.top + l.pad + l.btn,
    }
}

pub fn hit_button(x: i32, y: i32, width: i32, height: i32, l: &BandLayout) -> i32 {
    let cap = capsule_rect(width, height, l);
    (0..3)
        .find(|&i| {
            let b = button_rect(i, cap, l);
            x >= b.left && x < b.right && y >= b.top && y < b.bottom
        })
        .unwrap_or(-1)
}

/// 双击空带判定（模态拖动会吃掉系统双击消息，只能自己计时；对译
/// EmbedBand.MouseDown：`<=` 时间窗 + 严格 `<` 距离窗）。
#[allow(clippy::too_many_arguments)]
pub fn is_double_click(
    now_ms: i64,
    at: (i32, i32),
    last_ms: i64,
    last_at: (i32, i32),
    dbl_time_ms: i64,
    dbl_w: i32,
    dbl_h: i32,
) -> bool {
    (now_ms - last_ms) <= dbl_time_ms
        && (at.0 - last_at.0).abs() < dbl_w
        && (at.1 - last_at.1).abs() < dbl_h
}

// --------------------------------------------------------------- band paint

/// 一个字形的 8bit 覆盖掩码（Windows 侧 GDI 光栅化注入；测试注入假掩码）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GlyphMask {
    pub width: usize,
    pub height: usize,
    pub coverage: Vec<u8>,
}

impl GlyphMask {
    pub fn solid(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            coverage: vec![255; width * height],
        }
    }
}

// DryGlass v0 干底材质与字形色（对译 EmbedBand 颜色表）。
pub const GLASS_RGB: (u8, u8, u8) = (28, 28, 30);
pub const GLASS_A: u8 = 235;
pub const CLOSE_RED_RGB: (u8, u8, u8) = (232, 17, 35);
pub const INK_A: u8 = 230;
pub const INK_HOT_A: u8 = 255;
pub const WASH_A: u8 = 10;
pub const GHOST_A: u8 = 1;

pub const GLYPH_MINIMIZE: u16 = 0xE921;
pub const GLYPH_MAXIMIZE: u16 = 0xE922;
pub const GLYPH_RESTORE: u16 = 0xE923;
pub const GLYPH_CLOSE: u16 = 0xE8BB;

/// 预乘 alpha 的 source-over 合成（BGR 通道序由调用方缓冲布局决定）。
fn over_premul(px: &mut [u8], src: (u8, u8, u8), sa: u32) {
    let inv = 255 - sa;
    let dst_a = u32::from(px[3]);
    px[0] = ((u32::from(src.2) * sa + u32::from(px[0]) * inv) / 255) as u8;
    px[1] = ((u32::from(src.1) * sa + u32::from(px[1]) * inv) / 255) as u8;
    px[2] = ((u32::from(src.0) * sa + u32::from(px[2]) * inv) / 255) as u8;
    px[3] = (sa + dst_a * inv / 255).min(255) as u8;
}

/// 覆盖率乘进 alpha。
fn cov_alpha(a: u8, cov: f32) -> u32 {
    (f32::from(a) * cov).round().clamp(0.0, 255.0) as u32
}

/// 圆角矩形 SDF（半径 = 高/2 时即胶囊），1px 反走样边。
fn rounded_cov(px: f32, py: f32, r: PxRect) -> f32 {
    let radius = r.height() as f32 / 2.0;
    let cx = (r.left + r.right) as f32 / 2.0;
    let cy = (r.top + r.bottom) as f32 / 2.0;
    let dx = (px - cx).abs() - (r.width() as f32 / 2.0 - radius);
    let dy = (py - cy).abs() - (r.height() as f32 / 2.0 - radius);
    let ox = dx.max(0.0);
    let oy = dy.max(0.0);
    let d = (ox * ox + oy * oy).sqrt() + dx.max(dy).min(0.0) - radius;
    (0.5 - d).clamp(0.0, 1.0)
}

fn circle_cov(px: f32, py: f32, cx: f32, cy: f32, radius: f32) -> f32 {
    let d = ((px - cx) * (px - cx) + (py - cy) * (py - cy)).sqrt() - radius;
    (0.5 - d).clamp(0.0, 1.0)
}

/// 渲染整条 band：静止 alpha=1 纯热区；悬停露胶囊（玻璃底 + 悬停洗 +
/// Segoe Fluent Icons 字形）。输出 BGRA 预乘缓冲（UpdateLayeredWindow
/// AC_SRC_ALPHA 要求）。字形选择（max/restore）由调用方在 glyphs[1] 处
/// 注入。对译 EmbedBand.Render/PaintCapsule。
pub fn paint_band(
    width: i32,
    height: i32,
    hover: bool,
    hover_btn: i32,
    glyphs: [&GlyphMask; 3],
    l: &BandLayout,
) -> Vec<u8> {
    let mut buf = vec![0u8; (width * height * 4) as usize];
    for px in buf.chunks_exact_mut(4) {
        px[3] = GHOST_A;
    }
    if !hover || width <= 0 || height <= 0 {
        return buf;
    }
    let cap = capsule_rect(width, height, l);
    for y in cap.top..cap.bottom {
        for x in cap.left..cap.right {
            let cov = rounded_cov(x as f32 + 0.5, y as f32 + 0.5, cap);
            if cov <= 0.0 {
                continue;
            }
            let idx = ((y * width + x) * 4) as usize;
            over_premul(&mut buf[idx..idx + 4], GLASS_RGB, cov_alpha(GLASS_A, cov));
        }
    }
    if (0..3).contains(&hover_btn) {
        let b = button_rect(hover_btn, cap, l);
        let (col, a) = if hover_btn == 2 {
            (CLOSE_RED_RGB, INK_HOT_A)
        } else {
            ((0, 0, 0), WASH_A)
        };
        let (cx, cy, radius) = (
            (b.left + b.right) as f32 / 2.0,
            (b.top + b.bottom) as f32 / 2.0,
            b.width() as f32 / 2.0,
        );
        for y in b.top..b.bottom {
            for x in b.left..b.right {
                let cov = circle_cov(x as f32 + 0.5, y as f32 + 0.5, cx, cy, radius);
                if cov <= 0.0 {
                    continue;
                }
                let idx = ((y * width + x) * 4) as usize;
                over_premul(&mut buf[idx..idx + 4], col, cov_alpha(a, cov));
            }
        }
    }
    for (i, m) in glyphs.iter().enumerate() {
        let b = button_rect(i as i32, cap, l);
        let gx = b.left + (b.width() - m.width as i32) / 2;
        let gy = b.top + (b.height() - m.height as i32) / 2;
        let a = if i == 2 || i as i32 == hover_btn {
            INK_HOT_A
        } else {
            INK_A
        };
        for my in 0..m.height {
            for mx in 0..m.width {
                let c = m.coverage[my * m.width + mx];
                if c == 0 {
                    continue;
                }
                let x = gx + mx as i32;
                let y = gy + my as i32;
                if x < 0 || y < 0 || x >= width || y >= height {
                    continue;
                }
                let idx = ((y * width + x) * 4) as usize;
                let cov = f32::from(c) / 255.0;
                over_premul(&mut buf[idx..idx + 4], (255, 255, 255), cov_alpha(a, cov));
            }
        }
    }
    buf
}

// ------------------------------------------------------------ orchestration

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostStyle {
    Immersive,
    Native,
}

impl HostStyle {
    /// 对译 C# `"native".Equals(style) ? native : immersive`：非 native 一律沉浸。
    pub fn parse(s: &Option<&str>) -> Self {
        if matches!(s, Some("native")) {
            HostStyle::Native
        } else {
            HostStyle::Immersive
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostOptions {
    pub title: String,
    pub style: HostStyle,
    pub serial: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum HostEvent {
    Session(SessionEvent),
    Embedded,
    GiveUp,
    ChildGone,
}

/// 引擎线程与宿主窗口线程的共享态。
#[derive(Default)]
pub struct EngineState {
    pub done: AtomicBool,
    pub abort: AtomicBool,
    pub code: Mutex<Option<i32>>,
    events: Mutex<Vec<HostEvent>>,
}

impl EngineState {
    pub fn push(&self, event: HostEvent) {
        self.events.lock().expect("host events lock").push(event);
    }

    /// tick 上排空积压事件（引擎线程只生产，宿主线程串行消费）。
    pub fn drain_into(&self, sink: &mut dyn FnMut(&HostEvent)) {
        let mut pending = self.events.lock().expect("host events lock");
        for event in pending.drain(..) {
            sink(&event);
        }
    }
}

#[cfg(windows)]
fn engine_thread(spec: SessionSpec, st: Arc<EngineState>) {
    let code = run_session_abortable(&spec, &st.abort, &mut |ev| {
        st.push(HostEvent::Session(ev.clone()));
    });
    *st.code.lock().expect("host code lock") = Some(code);
    st.done.store(true, Ordering::Release);
}

/// 同进程编排：引擎监督线程 + 宿主窗口主线程。返回引擎最终退出码
/// （透传）。宿主退出后置 abort 收编引擎，不留孤儿。
#[cfg(windows)]
pub fn run_host(
    spec: &SessionSpec,
    opts: &HostOptions,
    on_event: &mut dyn FnMut(&HostEvent),
) -> i32 {
    let st = Arc::new(EngineState::default());
    let engine = {
        let st = st.clone();
        let spec = spec.clone();
        thread::spawn(move || engine_thread(spec, st))
    };
    // 安全性：window_main 内全是 Win32 调用，指针仅在单 UI 线程流转。
    unsafe { win::window_main(opts, &st, on_event) };
    st.abort.store(true, Ordering::Release);
    let _ = engine.join();
    let code = st.code.lock().expect("host code lock").unwrap_or(1);
    code
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(l: i32, t: i32, r: i32, b: i32) -> PxRect {
        PxRect {
            left: l,
            top: t,
            right: r,
            bottom: b,
        }
    }

    #[test]
    fn style_surgery_strips_popup_caption_thickframe_adds_child() {
        let popup = WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_VISIBLE;
        let s = child_style_after_surgery(popup);
        assert_eq!(s & WS_POPUP, 0);
        assert_eq!(s & WS_CAPTION, 0);
        assert_eq!(s & WS_THICKFRAME, 0);
        assert_eq!(s & WS_CHILD, WS_CHILD);
        assert_eq!(s & WS_SYSMENU, WS_SYSMENU, "无关样式位保留");
        // 幂等：术后样式再手术不变，也不再报告漂移。
        assert_eq!(child_style_after_surgery(s), s);
        assert!(!child_style_needs_surgery(s));
        assert!(child_style_needs_surgery(popup));
    }

    #[test]
    fn ex_surgery_clears_appwindow_only() {
        let ex = WS_EX_APPWINDOW | 0x0001_0000;
        let out = child_ex_after_surgery(ex);
        assert_eq!(out & WS_EX_APPWINDOW, 0);
        assert_eq!(out & 0x0001_0000, 0x0001_0000);
        assert_eq!(child_ex_after_surgery(0), 0);
    }

    #[test]
    fn embed_client_target_clamps_tiny_windows() {
        assert_eq!(embed_client_target(rect(0, 0, 500, 400)), (500, 400));
        assert_eq!(embed_client_target(rect(0, 0, 10, 10)), (200, 160));
        assert_eq!(embed_client_target(rect(0, 0, 500, 40)), (500, 160));
        assert_eq!(embed_client_target(rect(0, 0, 120, 400)), (200, 400));
    }

    #[test]
    fn embed_host_outer_reproduces_adjust_window_rect_semantics() {
        // AdjustWindowRectEx 对典型带框窗给出负左/上增量：外框比客户区大。
        let child = rect(100, 200, 1100, 840); // 1000x640
        let outer = embed_host_outer(child, -8, -31, 8, 8);
        assert_eq!(
            outer,
            rect(100 - 8, 200 - 31, 1100 + 8, 840 + 8),
            "宿主客户区必须恰好等于 scrcpy 原窗口矩形"
        );
        // 钳制路径：子窗退化到 10x10 时目标客户区仍是 200x160。
        let tiny = embed_host_outer(rect(0, 0, 10, 10), -8, -31, 8, 8);
        assert_eq!(tiny, rect(-8, -31, 200 + 8, 160 + 8));
        // adjust_rect 纯增量语义。
        assert_eq!(
            adjust_rect(rect(0, 0, 100, 50), -1, -2, 3, 4),
            rect(-1, -2, 103, 54)
        );
    }

    #[test]
    fn glue_drifted_detects_origin_and_size_mismatch() {
        assert!(!glue_drifted(900, 640, (0, 0), 900, 640));
        assert!(glue_drifted(900, 640, (1, 0), 900, 640), "1px 原点漂移");
        assert!(glue_drifted(900, 640, (0, 0), 899, 640));
        assert!(glue_drifted(900, 640, (0, 0), 900, 641));
        // 负原点（子窗移到客户区左上方）也算漂移。
        assert!(glue_drifted(900, 640, (-4, -4), 900, 640));
    }

    #[test]
    fn tick_action_wait_embed_giveup() {
        use TickAction::*;
        assert_eq!(tick_action(false, false, true, 0, true), Embed);
        assert_eq!(tick_action(false, false, false, 11_900, true), KeepWaiting);
        assert_eq!(tick_action(false, false, false, 12_000, true), GiveUp);
        // 引擎先死：不等满 12s，立刻放弃。
        assert_eq!(tick_action(false, false, false, 500, false), GiveUp);
        assert_eq!(tick_action(false, false, true, 12_000, false), Embed);
    }

    #[test]
    fn tick_action_child_death_follows_engine_state() {
        use TickAction::*;
        assert_eq!(tick_action(true, true, false, 0, true), Glue);
        assert_eq!(tick_action(true, true, false, 0, false), Glue);
        // 子窗消亡：引擎活着（重启中）→ 等新窗口；引擎已终 → 宿主自杀。
        assert_eq!(tick_action(true, false, false, 0, true), KeepWaiting);
        assert_eq!(tick_action(true, false, false, 0, false), FollowChildDeath);
    }

    #[test]
    fn band_layout_from_dpi_truncates_like_csharp() {
        let l = BandLayout::from_dpi(1.0);
        assert_eq!((l.band_h, l.btn, l.pad, l.gap, l.margin), (40, 30, 5, 6, 8));
        let l = BandLayout::from_dpi(1.5);
        assert_eq!(
            (l.band_h, l.btn, l.pad, l.gap, l.margin),
            (60, 45, 7, 9, 12)
        );
        let l = BandLayout::from_dpi(2.0);
        assert_eq!(
            (l.band_h, l.btn, l.pad, l.gap, l.margin),
            (80, 60, 10, 12, 16)
        );
    }

    #[test]
    fn capsule_and_button_geometry_at_1x() {
        let l = BandLayout::from_dpi(1.0);
        let cap = capsule_rect(900, 40, &l);
        assert_eq!(cap, rect(900 - 112 - 8, 0, 900 - 8, 40));
        assert_eq!(button_rect(0, cap, &l), rect(785, 5, 815, 35));
        assert_eq!(button_rect(1, cap, &l), rect(821, 5, 851, 35));
        assert_eq!(button_rect(2, cap, &l), rect(857, 5, 887, 35));
    }

    #[test]
    fn hit_button_finds_all_three_and_misses_empty_band() {
        let l = BandLayout::from_dpi(1.0);
        assert_eq!(hit_button(800, 20, 900, 40, &l), 0);
        assert_eq!(hit_button(836, 20, 900, 40, &l), 1);
        assert_eq!(hit_button(872, 20, 900, 40, &l), 2);
        assert_eq!(hit_button(10, 20, 900, 40, &l), -1, "空带 = 拖动区");
        assert_eq!(hit_button(889, 20, 900, 40, &l), -1, "右边界外开区间");
        // 窄窗口也不越界（胶囊按宽度贴右缘收缩）。
        let _ = hit_button(5, 5, 200, 40, &l);
    }

    #[test]
    fn double_click_time_window_inclusive_distance_strict() {
        let last = (1_000i64, (100, 10));
        assert!(is_double_click(1_500, (102, 12), last.0, last.1, 500, 4, 4));
        assert!(is_double_click(1_500, (100, 10), last.0, last.1, 500, 4, 4));
        assert!(
            !is_double_click(1_501, (100, 10), last.0, last.1, 500, 4, 4),
            "时间窗含端点"
        );
        assert!(
            !is_double_click(1_000, (104, 10), last.0, last.1, 500, 4, 4),
            "dx=4 严格小于"
        );
        assert!(
            !is_double_click(1_000, (100, 14), last.0, last.1, 500, 4, 4),
            "dy=4 严格小于"
        );
        assert!(is_double_click(1_000, (103, 13), last.0, last.1, 500, 4, 4));
    }

    fn dot(buf: &[u8], width: i32, x: i32, y: i32) -> (u8, u8, u8, u8) {
        let i = ((y * width + x) * 4) as usize;
        (buf[i], buf[i + 1], buf[i + 2], buf[i + 3])
    }

    #[test]
    fn paint_band_rest_state_is_invisible_hotzone() {
        let l = BandLayout::from_dpi(1.0);
        let glyphs = [
            GlyphMask::solid(8, 8),
            GlyphMask::solid(8, 8),
            GlyphMask::solid(8, 8),
        ];
        let buf = paint_band(900, 40, false, -1, [&glyphs[0], &glyphs[1], &glyphs[2]], &l);
        assert_eq!(buf.len(), 900 * 40 * 4);
        for px in buf.chunks_exact(4) {
            assert_eq!(px[3], 1, "静止态 alpha=1 纯热区");
            assert_eq!(&px[0..3], &[0, 0, 0]);
        }
    }

    #[test]
    fn paint_band_hover_reveals_glass_capsule() {
        let l = BandLayout::from_dpi(1.0);
        let glyphs = [
            GlyphMask::solid(8, 8),
            GlyphMask::solid(8, 8),
            GlyphMask::solid(8, 8),
        ];
        let buf = paint_band(900, 40, true, -1, [&glyphs[0], &glyphs[1], &glyphs[2]], &l);
        // 胶囊中心：玻璃底 alpha 接近 235、RGB 预乘近似 (28,28,30)。
        let (_, _, _, a) = dot(&buf, 900, 841, 20);
        assert!(a > 220, "capsule interior alpha, got {a}");
        // 远离胶囊的空带：仍是 alpha=1 热区。
        assert_eq!(dot(&buf, 900, 10, 20).3, 1);
        // 胶囊左端圆头外侧 2px：回落到热区。
        let cap = capsule_rect(900, 40, &l);
        assert_eq!(dot(&buf, 900, cap.left - 3, 20).3, 1);
        // 预乘不变量：每个像素 RGB ≤ alpha。
        for px in buf.chunks_exact(4) {
            assert!(px[0] <= px[3] && px[1] <= px[3] && px[2] <= px[3]);
        }
        // 字形（白色覆盖）落在按钮中心：预乘白色，alpha 接近 INK_A、三通道中性。
        let b = button_rect(0, cap, &l);
        let (bl, gr, rd, a) = dot(&buf, 900, (b.left + b.right) / 2, (b.top + b.bottom) / 2);
        assert!(a > 220);
        assert!(
            rd == gr && gr == bl,
            "ink is neutral white, got {bl},{gr},{rd}"
        );
        assert!(rd > 215, "premultiplied white at 230 alpha, got {rd}");
    }

    #[test]
    fn paint_band_close_hover_washes_red() {
        let l = BandLayout::from_dpi(1.0);
        let glyphs = [
            GlyphMask::solid(8, 8),
            GlyphMask::solid(8, 8),
            GlyphMask::solid(8, 8),
        ];
        let buf = paint_band(900, 40, true, 2, [&glyphs[0], &glyphs[1], &glyphs[2]], &l);
        let cap = capsule_rect(900, 40, &l);
        let b = button_rect(2, cap, &l);
        let cy = (b.top + b.bottom) / 2;
        let (bl, gr, rd, _) = dot(&buf, 900, (b.left + b.right) / 2, cy);
        assert!(
            rd > 250 && gr > 250,
            "glyph center over wash is white, got {bl},{gr},{rd}"
        );
        // 悬停洗在字形外：红色圆洗主导（预乘后 r 远大于 g/b）。
        let (blw, grw, rdw, _) = dot(&buf, 900, b.left + 3, cy);
        assert!(
            rdw > blw && blw >= grw,
            "close wash is red-dominant (R>B>G), got B={blw},G={grw},R={rdw}"
        );
        // 非悬停按钮只有玻璃底：红通道远低于悬停洗。
        let b0 = button_rect(0, cap, &l);
        let (_, _, rd0, _) = dot(&buf, 900, b0.left + 3, cy);
        assert!(rd0 < rdw);
    }

    #[test]
    fn host_style_parse_defaults_to_immersive() {
        assert_eq!(HostStyle::parse(&None), HostStyle::Immersive);
        assert_eq!(HostStyle::parse(&Some("immersive")), HostStyle::Immersive);
        assert_eq!(HostStyle::parse(&Some("bogus")), HostStyle::Immersive);
        assert_eq!(HostStyle::parse(&Some("native")), HostStyle::Native);
    }

    #[test]
    fn engine_state_queue_roundtrip() {
        let st = EngineState::default();
        st.push(HostEvent::Embedded);
        st.push(HostEvent::Session(SessionEvent::Started));
        let mut seen = Vec::new();
        st.drain_into(&mut |e| seen.push(e.clone()));
        assert_eq!(
            seen,
            vec![
                HostEvent::Embedded,
                HostEvent::Session(SessionEvent::Started)
            ]
        );
        st.drain_into(&mut |e| seen.push(e.clone()));
        assert_eq!(seen.len(), 2, "drain 清空队列");
    }
}

//! 设置页像素渲染（SettingsPage.qml 逐组照抄；模型在 settings_view.rs）。
//!
//! 页面骨架：bg 实底；滚动区 x16..w-16 / y64..页底；卡间距
//! 12；GlassCard = r14 cardFill + cardBorder + 内边 12 + 内距 9 + 标题
//! 13px DemiBold。底部保存主按钮（w76 h32 r10 accent，右缘 16 底 12）。

use std::time::{Duration, Instant};

use egui::{Pos2, Rect, Sense, Ui, Vec2};

use crate::app::PanelApp;
use crate::paint;
use crate::theme::{over, ThemeKind, Tokens};

/// 左对齐、垂直居中于 pos.y 的文字（QML anchors.verticalCenter 对译；
/// egui/Qt 字体度量差由居中锚定消化）。
fn text_at(painter: &egui::Painter, pos: Pos2, text: &str, px: f32, color: egui::Color32) {
    paint::text_left_at_center(painter, pos, text, px, color);
}

/// 控件标签（CaptionText：12px ink2）。pos = 行盒垂直中心。
fn caption(painter: &egui::Painter, t: &Tokens, pos: Pos2, text: &str) {
    text_at(painter, pos, text, 12.0, t.ink2);
}

fn group_label(painter: &egui::Painter, t: &Tokens, pos: Pos2, text: &str) {
    let galley = painter.ctx().fonts(|f| {
        f.layout_job(egui::text::LayoutJob::simple(
            text.to_owned(),
            paint::font_id(13.0, false),
            t.ink,
            f32::INFINITY,
        ))
    });
    painter.galley(
        Pos2::new(pos.x, pos.y - galley.size().y / 2.0),
        galley,
        egui::Color32::WHITE,
    );
}

/// 卡标题（13px DemiBold，QML letterSpacing 1 不做——字体度量差 1px 级）。
/// pos = 标题盒（h19）垂直中心。
fn card_title(painter: &egui::Painter, t: &Tokens, pos: Pos2, text: &str) {
    let galley = painter.ctx().fonts(|f| {
        f.layout_job(egui::text::LayoutJob::simple(
            text.to_owned(),
            paint::font_id(15.0, true),
            t.ink,
            f32::INFINITY,
        ))
    });
    painter.galley(
        Pos2::new(pos.x, pos.y - galley.size().y / 2.0),
        galley,
        egui::Color32::WHITE,
    );
}

/// 正文行文字（13px ink）。pos = 行盒垂直中心。
fn row_label(painter: &egui::Painter, t: &Tokens, pos: Pos2, text: &str) {
    text_at(painter, pos, text, 13.0, t.ink);
}

/// ModeButton（分段单选）：h32 r10；选中 = accent 14% 底 + accent 45%
/// 边 + accent DemiBold；未选 = 透明 + hover wash + ink2。
#[must_use]
fn mode_button(
    ui: &mut Ui,
    t: &Tokens,
    id: egui::Id,
    rect: Rect,
    text: &str,
    selected: bool,
) -> bool {
    let resp = ui.interact(rect, id, Sense::click());
    let fill = if selected {
        over(t.bg, t.accent, 0.14)
    } else if resp.hovered() {
        t.hover_on_card
    } else {
        t.card
    };
    paint::rounded_fill(ui.painter(), rect, 10.0, fill);
    if selected {
        let border = over(t.bg, t.accent, 0.45);
        paint::rounded_stroke(ui.painter(), rect, 10.0, border);
    }
    paint::text_centered(
        ui.painter(),
        rect.center(),
        text,
        13.0,
        selected,
        if selected { t.accent } else { t.ink2 },
    );
    resp.clicked()
}

/// GlassSwitch（纯开关，40×24 轨 r12 + 白圆 thumb 20 居中；点击整轨切换）。
#[must_use]
fn glass_switch(ui: &mut Ui, t: &Tokens, id: egui::Id, center: Pos2, checked: bool) -> bool {
    let track = Rect::from_center_size(center, Vec2::new(40.0, 24.0));
    let resp = ui.interact(track, id, Sense::click());
    let track_color = if checked {
        t.accent
    } else {
        // QML：白 24% / 黑 16% 叠在卡上（非 bg）
        match t.kind {
            ThemeKind::Dark => over(t.card, egui::Color32::WHITE, 0.24),
            ThemeKind::Light => over(t.card, egui::Color32::BLACK, 0.16),
        }
    };
    paint::rounded_fill(ui.painter(), track, 12.0, track_color);
    let knob_radius = (track.height() - 4.0) / 2.0;
    let knob_center_x = if checked {
        track.right() - 2.0 - knob_radius
    } else {
        track.left() + 2.0 + knob_radius
    };
    ui.painter().circle_filled(
        Pos2::new(knob_center_x, track.center().y),
        knob_radius,
        egui::Color32::WHITE,
    );
    resp.clicked()
}

/// 文字行 + 右侧开关（h32；QML 开关行：文字 y6 h19 垂直中心 ≈ 行中心）。
#[must_use]
fn switch_row(
    ui: &mut Ui,
    t: &Tokens,
    id: egui::Id,
    area: Rect,
    label: &str,
    checked: bool,
) -> bool {
    row_label(
        ui.painter(),
        t,
        Pos2::new(area.left(), area.center().y),
        label,
    );
    glass_switch(
        ui,
        t,
        id,
        Pos2::new(area.right() - 20.0, area.center().y),
        checked,
    )
}

/// NumberBox（−/+ 步进 28px 点击区 + 中央可键入数字；h32 r10
/// controlFill + hairline/accent(focus) 边）。返回 Some(新值)。
#[must_use]
fn number_box(
    ui: &mut Ui,
    t: &Tokens,
    id: egui::Id,
    rect: Rect,
    value: i64,
    (lo, hi): (i64, i64),
) -> Option<i64> {
    let resp = ui.interact(rect, id, Sense::click());
    let down = Rect::from_min_size(rect.min, Vec2::new(28.0, rect.height()));
    let up = Rect::from_min_size(
        Pos2::new(rect.right() - 28.0, rect.top()),
        Vec2::new(28.0, rect.height()),
    );
    let dresp = ui.interact(down, id.with("d"), Sense::click());
    let uresp = ui.interact(up, id.with("u"), Sense::click());
    let focused = ui.ctx().memory(|m| m.has_focus(id.with("edit")));

    paint::rounded_fill(ui.painter(), rect, 10.0, t.control_fill);
    let border = if focused {
        t.accent
    } else {
        t.hairline_on_card
    };
    paint::rounded_stroke(ui.painter(), rect, 10.0, border);
    let glyph_color = t.ink2;
    paint::text_centered(ui.painter(), down.center(), "−", 13.0, false, glyph_color);
    paint::text_centered(ui.painter(), up.center(), "+", 13.0, false, glyph_color);

    // 中央可键入：失焦提交，回车提交
    let mut buf = value.to_string();
    let edit = egui::TextEdit::singleline(&mut buf)
        .id(id.with("edit"))
        .font(egui::FontId::proportional(13.0))
        .text_color(t.ink)
        .frame(false)
        .desired_width(rect.width() - 56.0)
        .horizontal_align(egui::Align::Center)
        .vertical_align(egui::Align::Center);
    let mut inner = ui.new_child(
        egui::UiBuilder::new()
            .max_rect(Rect::from_min_size(
                Pos2::new(rect.left() + 28.0, rect.top()),
                Vec2::new(rect.width() - 56.0, rect.height()),
            ))
            .layout(egui::Layout::left_to_right(egui::Align::Center)),
    );
    let eresp = inner.add(edit);
    let _ = eresp;
    if focused && ui.input(|i| i.key_pressed(egui::Key::Enter)) {
        ui.ctx().memory_mut(|m| m.surrender_focus(id.with("edit")));
    }
    if dresp.clicked() {
        return Some((value - 1).max(lo));
    }
    if uresp.clicked() {
        return Some((value + 1).min(hi));
    }
    if resp.clicked() && !dresp.clicked() && !uresp.clicked() {
        ui.ctx().memory_mut(|m| m.request_focus(id.with("edit")));
    }
    // 键入值：合法即提交（每帧 parse，非法忽略）
    if let Ok(parsed) = buf.trim().parse::<i64>() {
        if parsed != value && (lo..=hi).contains(&parsed) {
            return Some(parsed);
        }
    }
    None
}

/// NumberCell：标签行 h20 + NumberBox。
#[must_use]
#[allow(clippy::too_many_arguments)]
fn number_cell(
    ui: &mut Ui,
    t: &Tokens,
    id: egui::Id,
    rect: Rect,
    title: &str,
    value: i64,
    range: (i64, i64),
    enabled: bool,
) -> Option<i64> {
    caption(
        ui.painter(),
        t,
        Pos2::new(rect.left(), rect.top() + 10.0),
        title,
    );
    let box_rect = Rect::from_min_size(
        Pos2::new(rect.left(), rect.top() + 26.0),
        Vec2::new(rect.width(), 32.0),
    );
    if !enabled {
        // QML opacity 0.45（合成底 = 卡，非 bg）：控件色向卡色收 45%
        let faded = |c: egui::Color32| over(t.card, c, 0.45);
        let tt = fade_tokens(t, faded);
        number_box(ui, &tt, id, box_rect, value, range)
    } else {
        number_box(ui, t, id, box_rect, value, range)
    }
}

/// 禁用态令牌近似（DPI 框跟随设备时）：颜色向 bg 收 45%。
fn fade_tokens(t: &Tokens, f: impl Fn(egui::Color32) -> egui::Color32 + Copy) -> Tokens {
    let mut tt = *t;
    tt.ink = f(t.ink);
    tt.ink2 = f(t.ink2);
    tt.control_fill = f(t.control_fill);
    tt.hairline_on_card = f(t.hairline_on_card);
    tt.accent = f(t.accent);
    tt
}

/// 路径行（标题 h26 + [TextField 撑开 | 浏览 | 检测] h32）。返回
/// (路径改动, 点了浏览, 点了检测)。
#[must_use]
fn path_row(
    ui: &mut Ui,
    t: &Tokens,
    tool: &str,
    rect: Rect,
    text: &mut String,
    probe_pill: Option<(&str, Instant)>,
    locked: bool,
) -> (Option<String>, bool, bool) {
    let mut changed = None;
    let mut browse = false;
    let mut detect = false;
    caption(
        ui.painter(),
        t,
        Pos2::new(rect.left(), rect.top() + 13.0),
        &format!("{tool} 路径"),
    );
    // 检测结果胶囊（右侧，2.5s 淡出语义：这里只在时限内显示）
    if let Some((label, at)) = probe_pill {
        if at.elapsed() < Duration::from_millis(2500) {
            let w = label.chars().count() as f32 * 6.5 + 20.0;
            let pill =
                Rect::from_min_size(Pos2::new(rect.right() - w, rect.top()), Vec2::new(w, 26.0));
            paint::rounded_fill(ui.painter(), pill, 13.0, t.pill);
            paint::text_centered(
                ui.painter(),
                pill.center(),
                label,
                12.0,
                false,
                egui::Color32::WHITE,
            );
        }
    }
    let row = Rect::from_min_size(
        Pos2::new(rect.left(), rect.top() + 32.0),
        Vec2::new(rect.width(), 32.0),
    );
    let sec_w = 64.0;
    let field_w = row.width() - (sec_w + 8.0) * 2.0;
    let field = Rect::from_min_size(row.min, Vec2::new(field_w, 32.0));
    let browse_r = Rect::from_min_size(
        Pos2::new(field.right() + 8.0, row.top()),
        Vec2::new(sec_w, 32.0),
    );
    let detect_r = Rect::from_min_size(
        Pos2::new(browse_r.right() + 8.0, row.top()),
        Vec2::new(sec_w, 32.0),
    );

    // TextField：controlFill + hairline/accent(focus)；占位「留空自动探测」
    let fid = egui::Id::new(("settings-path", tool));
    let focused = ui.ctx().memory(|m| m.has_focus(fid));
    paint::rounded_fill(ui.painter(), field, 10.0, t.control_fill);
    let border = if focused {
        t.accent
    } else {
        t.hairline_on_card
    };
    paint::rounded_stroke(ui.painter(), field, 10.0, border);
    let mut edit = egui::TextEdit::singleline(text)
        .id(fid)
        .font(egui::FontId::proportional(13.0))
        .text_color(if locked { t.ink2 } else { t.ink })
        .frame(false)
        .desired_width(field_w - 20.0)
        .vertical_align(egui::Align::Center)
        .hint_text("留空自动探测");
    if locked {
        edit = edit.interactive(false);
    }
    let mut inner = ui.new_child(
        egui::UiBuilder::new()
            .max_rect(Rect::from_min_size(
                Pos2::new(field.left() + 10.0, field.top()),
                Vec2::new(field_w - 20.0, 32.0),
            ))
            .layout(egui::Layout::left_to_right(egui::Align::Center)),
    );
    let eresp = inner.add(edit);
    if eresp.changed() {
        changed = Some(text.trim().to_string());
    }

    // SecButton ×2（浏览/检测）
    let sec = |ui: &mut Ui, r: Rect, id: egui::Id, label: &str, enabled: bool, out: &mut bool| {
        let resp = ui.interact(r, id, Sense::click());
        let fill = if !enabled {
            t.control_fill
        } else if resp.is_pointer_button_down_on() {
            t.press_on_card
        } else if resp.hovered() {
            t.hover_on_card
        } else {
            t.control_fill
        };
        paint::rounded_fill(ui.painter(), r, 10.0, fill);
        let border = if enabled {
            t.hairline_on_card
        } else {
            t.control_fill
        };
        paint::rounded_stroke(ui.painter(), r, 10.0, border);
        paint::text_centered(
            ui.painter(),
            r.center(),
            label,
            13.0,
            false,
            if enabled { t.ink } else { t.ink2 },
        );
        if enabled && resp.clicked() {
            *out = true;
        }
    };
    sec(
        ui,
        browse_r,
        fid.with("browse"),
        "浏览",
        !locked,
        &mut browse,
    );
    sec(
        ui,
        detect_r,
        fid.with("detect"),
        "检测",
        !locked,
        &mut detect,
    );
    (changed, browse, detect)
}

// ---------------------------------------------------------------- 布局

/// 布局常量（SettingsPage.qml 逐值照抄；史前几何取证脚本 geom_probe.py
/// 随 pyduo 退役，取值冻结于此并由单测守护：标题 13px 盒高 19、行标签
/// Item h20、CaptionText 盒高 18、开关行 Item h32、NumberCell 20+6+32=58、
/// Slider h24）。
/// 保存钮已移除（返回首页自动保存）：scroller 直达页底。
mod geom {
    /// 卡内容横 padding = shadowHost 8 + innerCol 12（对齐 QML 卡内区域）。
    pub const PAD: f32 = 20.0;
    pub const SP: f32 = 9.0; // 卡内 Column spacing
    pub const CARD_SP: f32 = 12.0; // 卡间距
    pub const PATH_ROW_H: f32 = 56.0; // 标题 18 + 6 + 输入 32
    pub const CELL_H: f32 = 56.0; // 标签 18 + 6 + 数字框 32
    pub const LABEL_H: f32 = 18.0;
    pub const ROW_H: f32 = 32.0; // 按钮/开关行
    pub const TITLE_H: f32 = 22.0; // 卡标题 15px 字盒高
    pub const LOCK_H: f32 = 36.0; // 引擎锁提示条
    pub const MARGIN: f32 = 16.0; // 滚动区左右边距
    /// GlassCard.implicitHeight = 3 + pad*2 + content + 10（阴影宿主上下边）。
    pub const CARD_EXTRA: f32 = 3.0 + 12.0 * 2.0 + 10.0;
    pub const TOP: f32 = 64.0; // 胶囊下让位
}

/// 拖拽诊断计数（selfcheck 取证用，静态零开销）。
pub(crate) static DRAG_HITS: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
/// 滚轮事件命中计数。
pub(crate) static WHEEL_HITS: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
/// 指针按住帧数 / 按下事件数。
pub(crate) static PTR_DOWN_FRAMES: std::sync::atomic::AtomicU32 =
    std::sync::atomic::AtomicU32::new(0);
pub(crate) static PTR_PRESS: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

/// 设置页布局（两遍绘制解耦：先算全部 rect，再画卡底，再画内容）。
pub struct SettingsLayout {
    /// 滚动视口（内容 clip 区）。
    pub vp: Rect,
    pub problem: Option<(Rect, String)>,
    /// 设备卡（在线设备行 + 无线地址历史；无内容时 None）。
    pub devices: Option<Card>,
    pub dev_rows: Vec<Rect>,
    pub wifi_label: Option<Pos2>,
    pub wifi_rows: Vec<Rect>,
    pub engine: Card,
    pub scrcpy_row: Rect,
    pub adb_row: Rect,
    pub lock_hint: Option<Rect>,
    pub fps_cell: Rect,
    pub bitrate_cell: Rect,
    pub quality: Card,
    pub codec_row: [Rect; 4],
    pub hwdec_label: Pos2,
    pub hwdec_row: [Rect; 3],
    pub audio_label: Pos2,
    pub audio_row: [Rect; 3],
    pub tso_row: Rect,
    pub dpi_switch: Rect,
    pub dpi_cell: Rect,
    pub rs_label: Pos2,
    pub rs_value: Pos2,
    pub rs_row: [Rect; 4],
    pub windowbar: Card,
    pub top_label: Pos2,
    pub top_row: [Rect; 2],
    pub bottom_label: Pos2,
    pub bottom_row: [Rect; 3],
    pub appearance: Card,
    pub theme_label: Pos2,
    pub theme_row: [Rect; 3],
    pub glass_row: Rect,
    /// 动画效果开关行（玻璃之下；关 = 全静态回退原始观感）。
    pub anim_row: Rect,
    pub content_h: f32,
}

/// 一张卡：底板 rect + 标题位置 + 内容横向范围。
pub struct Card {
    pub bg: Rect,
    pub title: Pos2,
    pub inner: Rect, // 内容排布区（含左右 PAD 内缩）
}

impl SettingsLayout {
    pub fn compute(
        w: f32,
        h: f32,
        problems: &str,
        engine_locked: bool,
        full_bleed: bool,
        dev_count: usize,
        wifi_count: usize,
    ) -> Self {
        use geom::*;
        // QML 卡本体在 shadowHost 内左右各缩 8（阴影宿主），卡缘 = 16+8。
        // 宽屏限宽居中（KISS）：列宽封顶 560，窗口更宽时两侧留白，
        // 避免路径行/模式钮/滑条拉伸成横幅（2026-09-19）。
        // 六修 full_bleed：玻璃开时视口顶到 0（内容从胶囊玻璃岛下滚
        // 过），内容起始 y 不变；关玻璃回 64 硬让位。
        let cw = (w - (MARGIN + 8.0) * 2.0).min(560.0);
        let left = ((w - cw) / 2.0).max(MARGIN + 8.0);
        let vp_top = if full_bleed { 0.0 } else { TOP };
        let vp = Rect::from_min_max(Pos2::new(left, vp_top), Pos2::new(left + cw, h));
        let inner_w = cw - PAD * 2.0;
        let x = left + PAD;
        let mut y = TOP;

        let problem = (!problems.is_empty()).then(|| {
            let lines = problems.lines().count().max(1) as f32;
            let bar = Rect::from_min_size(Pos2::new(left, y), Vec2::new(cw, 20.0 + lines * 18.0));
            (bar, problems.to_string())
        });
        if let Some((bar, _)) = &problem {
            y += bar.height() + CARD_SP;
        }

        // 设备卡（首卡：在线设备一行一枚 + 无线地址历史；空则整卡隐藏）
        let mut dev_rows = Vec::new();
        let mut wifi_label = None;
        let mut wifi_rows = Vec::new();
        let mut dev_items = TITLE_H + SP;
        dev_items += dev_count as f32 * (ROW_H + SP);
        if wifi_count > 0 {
            dev_items += LABEL_H + SP + wifi_count as f32 * (ROW_H + SP);
        }
        let devices = (dev_count > 0 || wifi_count > 0)
            .then(|| card_frame(Pos2::new(left, y), cw, CARD_EXTRA + dev_items));
        if let Some(dev) = &devices {
            let mut cy = dev.title.y + TITLE_H + SP;
            for _ in 0..dev_count {
                dev_rows.push(Rect::from_min_size(
                    Pos2::new(x, cy),
                    Vec2::new(inner_w, ROW_H),
                ));
                cy += ROW_H + SP;
            }
            if wifi_count > 0 {
                wifi_label = Some(Pos2::new(x, cy + 10.0));
                cy += LABEL_H + SP;
                for _ in 0..wifi_count {
                    wifi_rows.push(Rect::from_min_size(
                        Pos2::new(x, cy),
                        Vec2::new(inner_w, ROW_H),
                    ));
                    cy += ROW_H + SP;
                }
            }
            y += CARD_EXTRA + dev_items + CARD_SP;
        }

        // 引擎卡
        let lock = if engine_locked { LOCK_H + SP } else { 0.0 };
        let engine_h =
            CARD_EXTRA + TITLE_H + SP + PATH_ROW_H + SP + PATH_ROW_H + SP + lock + CELL_H;
        let engine = card_frame(Pos2::new(left, y), cw, engine_h);
        let mut cy = engine.title.y + TITLE_H + SP;
        let scrcpy_row = Rect::from_min_size(Pos2::new(x, cy), Vec2::new(inner_w, PATH_ROW_H));
        cy += PATH_ROW_H + SP;
        let adb_row = Rect::from_min_size(Pos2::new(x, cy), Vec2::new(inner_w, PATH_ROW_H));
        cy += PATH_ROW_H + SP;
        let lock_hint = (engine_locked)
            .then(|| Rect::from_min_size(Pos2::new(x, cy), Vec2::new(inner_w, LOCK_H)));
        if lock_hint.is_some() {
            cy += LOCK_H + SP;
        }
        let half = (inner_w - 12.0) / 2.0;
        let fps_cell = Rect::from_min_size(Pos2::new(x, cy), Vec2::new(half, CELL_H));
        let bitrate_cell =
            Rect::from_min_size(Pos2::new(x + half + 12.0, cy), Vec2::new(half, CELL_H));
        y += engine_h + CARD_SP;

        // 投屏质量卡（解释性小字已取消，分组标签 13px 正文字号）
        let q_items = TITLE_H
            + SP
            + ROW_H
            + SP
            + LABEL_H
            + SP
            + ROW_H
            + SP
            + LABEL_H
            + SP
            + ROW_H
            + SP
            + ROW_H
            + SP
            + ROW_H
            + SP
            + CELL_H
            + SP
            + LABEL_H
            + SP
            + geom::ROW_H;
        let quality_h = CARD_EXTRA + q_items;
        let quality = card_frame(Pos2::new(left, y), cw, quality_h);
        let mut cy = quality.title.y + TITLE_H + SP;
        let codec_row: [Rect; 4] = seg_row(x, cy, inner_w, 4).try_into().unwrap();
        cy += ROW_H + SP;
        let hwdec_label = Pos2::new(x, cy + 10.0);
        cy += LABEL_H + SP;
        let hwdec_row: [Rect; 3] = seg_row(x, cy, inner_w, 3).try_into().unwrap();
        cy += ROW_H + SP;
        let audio_label = Pos2::new(x, cy + 10.0);
        cy += LABEL_H + SP;
        let audio_row: [Rect; 3] = seg_row(x, cy, inner_w, 3).try_into().unwrap();
        cy += ROW_H + SP;
        let tso_row = Rect::from_min_size(Pos2::new(x, cy), Vec2::new(inner_w, ROW_H));
        cy += ROW_H + SP;
        let dpi_switch = Rect::from_min_size(Pos2::new(x, cy), Vec2::new(inner_w, ROW_H));
        cy += ROW_H + SP;
        let dpi_cell = Rect::from_min_size(Pos2::new(x, cy), Vec2::new(inner_w, CELL_H));
        cy += CELL_H + SP;
        let rs_label = Pos2::new(x, cy + 10.0);
        let rs_value = Pos2::new(x + inner_w - 20.0, cy + 10.0);
        cy += LABEL_H + SP;
        let rs_row: [Rect; 4] = seg_row(x, cy, inner_w, 4).try_into().unwrap();
        y += quality_h + CARD_SP;

        // 窗口栏（默认）卡
        let wb_items = TITLE_H + SP + LABEL_H + SP + ROW_H + SP + LABEL_H + SP + ROW_H;
        let wb_h = CARD_EXTRA + wb_items;
        let windowbar = card_frame(Pos2::new(left, y), cw, wb_h);
        let mut cy = windowbar.title.y + TITLE_H + SP;
        let top_label = Pos2::new(x, cy + 10.0);
        cy += LABEL_H + SP;
        let top_row: [Rect; 2] = seg_row(x, cy, inner_w, 2).try_into().unwrap();
        cy += ROW_H + SP;
        let bottom_label = Pos2::new(x, cy + 10.0);
        cy += LABEL_H + SP;
        let bottom_row: [Rect; 3] = seg_row(x, cy, inner_w, 3).try_into().unwrap();
        y += wb_h + CARD_SP;

        // 外观卡
        let ap_items = TITLE_H + SP + LABEL_H + SP + ROW_H + SP + ROW_H + SP + ROW_H;
        let ap_h = CARD_EXTRA + ap_items;
        let appearance = card_frame(Pos2::new(left, y), cw, ap_h);
        let mut cy = appearance.title.y + TITLE_H + SP;
        let theme_label = Pos2::new(x, cy + 10.0);
        cy += LABEL_H + SP;
        let theme_row: [Rect; 3] = seg_row(x, cy, inner_w, 3).try_into().unwrap();
        cy += ROW_H + SP;
        let glass_row = Rect::from_min_size(Pos2::new(x, cy), Vec2::new(inner_w, ROW_H));
        cy += ROW_H + SP;
        let anim_row = Rect::from_min_size(Pos2::new(x, cy), Vec2::new(inner_w, ROW_H));

        Self {
            vp,
            problem,
            devices,
            dev_rows,
            wifi_label,
            wifi_rows,
            engine,
            scrcpy_row,
            adb_row,
            lock_hint,
            fps_cell,
            bitrate_cell,
            quality,
            codec_row,
            hwdec_label,
            hwdec_row,
            audio_label,
            audio_row,
            tso_row,
            dpi_switch,
            dpi_cell,
            rs_label,
            rs_value,
            rs_row,
            windowbar,
            top_label,
            top_row,
            bottom_label,
            bottom_row,
            appearance,
            theme_label,
            theme_row,
            glass_row,
            anim_row,
            content_h: y + ap_h,
        }
    }
}

fn card_frame(pos: Pos2, w: f32, h: f32) -> Card {
    // 可见卡底 = shadowHost 内容（上缩 3 / 下缩 10，QML 阴影宿主边）；
    // 内容坐标仍从 GlassCard 顶起（title = pos+3+12）
    Card {
        bg: Rect::from_min_size(Pos2::new(pos.x, pos.y + 3.0), Vec2::new(w, h - 13.0)),
        // 纵向内边 = shadowHost 3 + cardPad 12（PAD=20 只是横向：8+12）
        title: Pos2::new(pos.x + geom::PAD, pos.y + 15.0),
        inner: Rect::from_min_size(
            Pos2::new(pos.x + geom::PAD, pos.y + geom::PAD),
            Vec2::new(w - geom::PAD * 2.0, h - geom::PAD * 2.0),
        ),
    }
}

/// n 等分按钮行（间距 8）。
fn seg_row(x: f32, y: f32, w: f32, n: usize) -> Vec<Rect> {
    let bw = (w - 8.0 * (n - 1) as f32) / n as f32;
    (0..n)
        .map(|i| {
            Rect::from_min_size(
                Pos2::new(x + i as f32 * (bw + 8.0), y),
                Vec2::new(bw, geom::ROW_H),
            )
        })
        .collect()
}

/// 设置页主体（app.rs Page::Settings 分发；布局 + 双遍绘制 + 滚动）。
pub fn show(app: &mut PanelApp, ui: &mut Ui) {
    let (down, press) = ui.input(|i| {
        (
            i.pointer.primary_down(),
            i.events.iter().any(|e| {
                matches!(
                    e,
                    egui::Event::PointerButton {
                        button: egui::PointerButton::Primary,
                        pressed: true,
                        ..
                    }
                )
            }),
        )
    });
    if down {
        PTR_DOWN_FRAMES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
    if press {
        PTR_PRESS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
    let t = app.tokens;
    let full = ui.max_rect();
    // 页面底色：不透明 Style.bg（设置页背景必须干净）
    ui.painter().rect_filled(full, 0, t.bg);

    let problems = app.settings_problems();
    let engine_locked = app.engine_locked();
    let full_bleed = app.settings.draft.glass_enabled && cfg!(target_os = "windows");
    let device_rows = app.device_rows();
    // 设备卡降权为只读：仅当前活动设备（排障看全文 serial），切换职责
    // 在首页设备卡浮层（2026-10-06 Kiro Opus 重设计）。
    let active_device = device_rows.iter().find(|(_, _, sel)| *sel).cloned();
    let dev_count = if active_device.is_some() { 1 } else { 0 };
    let recent: Vec<String> = app
        .wireless_recent
        .iter()
        .filter(|a| !a.is_empty())
        .cloned()
        .collect();
    let layout = SettingsLayout::compute(
        full.width(),
        full.height(),
        &problems,
        engine_locked,
        full_bleed,
        dev_count,
        recent.len(),
    );

    // 整页滚动（2026-09-20 修「设置滚不动」）：滚轮不再局限内容列 vp——
    // 宽屏留白是死区；另补拖拽/触摸平移（触摸屏滑动没有滚轮事件）。
    // 胶囊带不参与；拖拽捕获层垫底，控件（滑杆/文本框）优先拿走拖拽。
    let max_scroll = (layout.content_h - layout.vp.height()).max(0.0);
    let pan_zone = Rect::from_min_max(
        Pos2::new(full.left(), full.top() + 52.0),
        Pos2::new(full.right(), full.bottom()),
    );
    let pan = ui.allocate_rect(pan_zone, Sense::drag());
    // 无条件 clamp：先滑到底再放大窗口（max_scroll 变小/归零）时旧
    // scroll 残留会把内容顶出视口且滚动分支被闸关死（真机卡死根因）
    app.settings_scroll = app.settings_scroll.min(max_scroll);
    // DUO_SHOT_SET_SCROLL=px：出图模式预滚设置页（下半区控件入镜验证）
    if app.shot.is_some() {
        if let Ok(v) = std::env::var("DUO_SHOT_SET_SCROLL") {
            if let Ok(px) = v.parse::<f32>() {
                app.settings_scroll = px.min(max_scroll);
            }
        }
    }
    let menu_open = app.menu_effectively_open(ui.ctx());
    if max_scroll > 0.0 && !menu_open {
        if ui.rect_contains_pointer(pan_zone) {
            let dy = ui.input(|i| i.raw_scroll_delta.y);
            if dy != 0.0 {
                WHEEL_HITS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            }
            app.settings_scroll = (app.settings_scroll - dy).clamp(0.0, max_scroll);
        }
        if pan.dragged() {
            app.settings_scroll = (app.settings_scroll - pan.drag_delta().y).clamp(0.0, max_scroll);
        }
    }
    let scroll = app.settings_scroll;
    // （十修）内容放得下时垂直光学居中（0.38 偏上），消「顶死+底部
    // 空洞」读作滚不动的错觉；off = 统一内容坐标 → 页面坐标的位移
    let pad = ((layout.vp.height() - layout.content_h) * 0.38).max(0.0);
    let off = scroll - pad;

    // 岛背板剖面（内容坐标）：问题条 + 各卡底板，缺省 bg
    // （胶囊岛跨这些色带滚动时透出颜色变化——活模糊）
    app.island_bands.push((
        layout.vp.top() + pad,
        layout.vp.top() + layout.content_h + pad + 200.0,
        t.bg,
    ));
    if let Some((bar, _)) = &layout.problem {
        app.island_bands
            .push((bar.top() + pad, bar.bottom() + pad, t.warn));
    }
    for card in layout.devices.iter().chain([
        &layout.engine,
        &layout.quality,
        &layout.windowbar,
        &layout.appearance,
    ]) {
        app.island_bands
            .push((card.bg.top() + pad, card.bg.bottom() + pad, t.card));
    }

    // 视口内裁剪（QML ScrollView clip：滚动底之下的控件——如 DPI 数字
    // 框——不得溢出）
    let mut ui = ui.new_child(egui::UiBuilder::new().max_rect(full));
    ui.set_clip_rect(layout.vp);
    let painter = ui.painter().with_clip_rect(layout.vp);

    // 卡底（先画，内容后画盖其上）
    for c in layout.devices.iter().chain([
        &layout.engine,
        &layout.quality,
        &layout.windowbar,
        &layout.appearance,
    ]) {
        let shifted = Rect::from_min_size(Pos2::new(c.bg.min.x, c.bg.min.y - off), c.bg.size());
        paint::rounded_fill(&painter, shifted, 14.0, t.card);
        paint::rounded_stroke(&painter, shifted, 14.0, t.card_border);
        card_title(
            &painter,
            &t,
            Pos2::new(shifted.left() + geom::PAD, shifted.top() + 14.0),
            card_name(c, &layout),
        );
    }

    // 问题红条
    if let Some((bar, text)) = &layout.problem {
        let bar = Rect::from_min_size(Pos2::new(bar.min.x, bar.min.y - off), bar.size());
        paint::rounded_fill(&painter, bar, 10.0, over(t.bg, t.danger, 0.10));
        paint::rounded_stroke(&painter, bar, 10.0, over(t.bg, t.danger, 0.35));
        paint::text_left_weight(
            &painter,
            Pos2::new(bar.left() + 10.0, bar.top() + 10.0),
            text,
            13.0,
            t.danger,
            false,
        );
    }

    // 引擎卡内容
    let sy = |r: Rect| Rect::from_min_size(Pos2::new(r.min.x, r.min.y - off), r.size());
    let py = |p: Pos2| Pos2::new(p.x, p.y - off);

    // 设备卡内容（只读当前设备行 + 历史地址 连接/✕）
    if layout.devices.is_some() {
        if let Some((is_wifi, serial, _)) = &active_device {
            if let Some(row) = layout.dev_rows.first() {
                let row = sy(*row);
                app.paint_device_row(&painter, row, *is_wifi, serial, false, true);
            }
        }
        if let Some(label) = layout.wifi_label {
            paint::text_left(&painter, py(label), "无线地址历史", 12.0, t.ink2);
        }
        for (row, addr) in layout.wifi_rows.iter().zip(recent.iter()) {
            let row = sy(*row);
            let del = Rect::from_min_size(
                Pos2::new(row.right() - 8.0 - 28.0, row.center().y - 12.0),
                Vec2::new(28.0, 24.0),
            );
            let conn = Rect::from_min_size(
                Pos2::new(del.left() - 8.0 - 48.0, row.center().y - 12.0),
                Vec2::new(48.0, 24.0),
            );
            let resp = ui.allocate_rect(row, Sense::click());
            let wash = if resp.hovered() {
                t.hover_on_canvas
            } else {
                egui::Color32::TRANSPARENT
            };
            paint::rounded_fill(&painter, row, 10.0, wash);
            let max_w = conn.left() - 16.0 - row.left();
            let label = paint::elide_to_width(addr, max_w.max(24.0), &|c| {
                if c.is_ascii() {
                    13.0 * 0.55
                } else {
                    13.0
                }
            });
            paint::text_left(
                &painter,
                Pos2::new(row.left() + 8.0, row.center().y),
                &label,
                13.0,
                t.ink,
            );
            let conn_resp = ui.allocate_rect(conn, Sense::click());
            paint::rounded_stroke(&painter, conn, 12.0, t.card_border);
            paint::text_centered(&painter, conn.center(), "连接", 11.0, false, t.ink);
            let del_resp = ui.allocate_rect(del, Sense::click());
            paint::rounded_stroke(&painter, del, 12.0, t.card_border);
            paint::text_centered(&painter, del.center(), "✕", 11.0, false, t.ink2);
            if conn_resp.clicked() {
                app.connect_wireless_addr(addr);
            }
            if del_resp.clicked() {
                app.forget_wireless(addr);
            }
        }
    }

    let mut scrcpy = app.settings.draft.scrcpy_path.clone();
    let pill_scrcpy = app.probe_pill_for("scrcpy");
    let (chg, browse, detect) = path_row(
        &mut ui,
        &t,
        "scrcpy",
        sy(layout.scrcpy_row),
        &mut scrcpy,
        pill_scrcpy
            .as_ref()
            .map(|(_, text, at)| (text.as_str(), *at)),
        engine_locked,
    );
    if let Some(p) = chg {
        app.settings.set_scrcpy_path(&p);
    }
    if browse {
        app.browse_engine("scrcpy");
    }
    if detect {
        app.start_probe("scrcpy", &app.settings.draft.scrcpy_path.clone());
    }

    let mut adb = app.settings.draft.adb_path.clone();
    let pill_adb = app.probe_pill_for("adb");
    let (chg, browse, detect) = path_row(
        &mut ui,
        &t,
        "adb",
        sy(layout.adb_row),
        &mut adb,
        pill_adb.as_ref().map(|(_, text, at)| (text.as_str(), *at)),
        engine_locked,
    );
    if let Some(p) = chg {
        app.settings.set_adb_path(&p);
    }
    if browse {
        app.browse_engine("adb");
    }
    if detect {
        app.start_probe("adb", &app.settings.draft.adb_path.clone());
    }

    if let Some(hint) = layout.lock_hint {
        let hint = sy(hint);
        paint::rounded_fill(&painter, hint, 10.0, over(t.bg, t.warn, 0.14));
        paint::text_left_weight(
            &painter,
            Pos2::new(hint.left() + 8.0, hint.top() + 10.0),
            "会话运行中，不可修改引擎路径",
            12.0,
            t.warn,
            false,
        );
    }

    let fps = app.settings.draft.fps.unwrap_or(60);
    if let Some(v) = number_cell(
        &mut ui,
        &t,
        egui::Id::new("fps"),
        sy(layout.fps_cell),
        "FPS",
        fps,
        (1, 240),
        true,
    ) {
        app.settings.set_fps(v);
    }
    let bitrate = app.settings.draft.bitrate_mbps.unwrap_or(30);
    if let Some(v) = number_cell(
        &mut ui,
        &t,
        egui::Id::new("bitrate"),
        sy(layout.bitrate_cell),
        "码率 Mbps",
        bitrate,
        (1, 200),
        true,
    ) {
        app.settings.set_bitrate(v);
    }

    // 投屏质量卡内容
    const CODECS: [(&str, &str); 4] = [
        ("auto", "自动（推荐）"),
        ("h264", "H.264"),
        ("h265", "H.265"),
        ("av1", "AV1"),
    ];
    for (i, (value, label)) in CODECS.iter().enumerate() {
        let r = sy(layout.codec_row[i]);
        if mode_button(
            &mut ui,
            &t,
            egui::Id::new(("codec", i)),
            r,
            label,
            app.settings.draft.video_codec == *value,
        ) {
            app.settings.set_video_codec(value);
        }
    }
    group_label(&painter, &t, py(layout.hwdec_label), "硬件解码");
    const HWDECS: [(&str, &str); 3] = [
        ("auto", "自动（推荐）"),
        ("disabled", "软件解码"),
        ("d3d11va", "硬解"),
    ];
    for (i, (value, label)) in HWDECS.iter().enumerate() {
        let r = sy(layout.hwdec_row[i]);
        if mode_button(
            &mut ui,
            &t,
            egui::Id::new(("hwdec", i)),
            r,
            label,
            app.settings.draft.hwdec == *value,
        ) {
            app.settings.set_hwdec(value);
        }
    }
    group_label(&painter, &t, py(layout.audio_label), "音频");
    const AUDIOS: [(&str, &str); 3] = [
        ("latest", "仅最新会话"),
        ("all", "全部会话"),
        ("off", "静音"),
    ];
    for (i, (value, label)) in AUDIOS.iter().enumerate() {
        let r = sy(layout.audio_row[i]);
        if mode_button(
            &mut ui,
            &t,
            egui::Id::new(("audio", i)),
            r,
            label,
            app.settings.draft.audio_policy == *value,
        ) {
            app.settings.set_audio_policy(value);
        }
    }
    if switch_row(
        &mut ui,
        &t,
        egui::Id::new("tso"),
        sy(layout.tso_row),
        "整机镜像时关闭设备屏幕",
        app.settings.draft.turn_screen_off,
    ) {
        app.settings
            .set_turn_screen_off(!app.settings.draft.turn_screen_off);
    }

    let dpi_auto = app.settings.draft.dpi.is_none();
    if switch_row(
        &mut ui,
        &t,
        egui::Id::new("dpiauto"),
        sy(layout.dpi_switch),
        "DPI 跟随设备",
        dpi_auto,
    ) {
        app.settings
            .set_dpi(if dpi_auto { Some(160) } else { None });
    }
    let dpi_val = app.settings.draft.dpi.unwrap_or(160);
    if let Some(v) = number_cell(
        &mut ui,
        &t,
        egui::Id::new("dpi"),
        sy(layout.dpi_cell),
        "自定义 DPI",
        dpi_val,
        (120, 640),
        !dpi_auto,
    ) {
        app.settings.set_dpi(Some(v));
    }
    group_label(&painter, &t, py(layout.rs_label), "渲染倍率");
    let scale_now = app.settings.draft.render_scale;
    let presets = [1.0f64, 1.4, 2.0, 3.0];
    let on_preset = presets.iter().any(|v| (v - scale_now).abs() < 1e-9);
    if !on_preset {
        // 非档位自由值（右键菜单微调所得）：胶囊不亮，右端提示现值
        paint::text_left(
            &painter,
            layout.rs_value,
            &format!("{scale_now:.1}×"),
            13.0,
            t.accent,
        );
    }
    for (i, v) in presets.iter().enumerate() {
        if mode_button(
            &mut ui,
            &t,
            egui::Id::new(("rscale", i)),
            sy(layout.rs_row[i]),
            &format!("{v:.1}×").replace(".0×", "×"),
            on_preset && (v - scale_now).abs() < 1e-9,
        ) {
            app.settings.set_render_scale(*v);
        }
    }

    // 窗口栏（默认）卡内容
    group_label(&painter, &t, py(layout.top_label), "顶部栏");
    const TOPS: [(&str, &str); 2] = [("immersive", "沉浸"), ("native", "系统")];
    for (i, (value, label)) in TOPS.iter().enumerate() {
        let r = sy(layout.top_row[i]);
        if mode_button(
            &mut ui,
            &t,
            egui::Id::new(("topbar", i)),
            r,
            label,
            app.settings.draft.top_bar_mode == *value,
        ) {
            app.settings.set_bar_mode(true, value);
        }
    }
    group_label(&painter, &t, py(layout.bottom_label), "底部栏");
    const BOTTOMS: [(&str, &str); 3] = [
        ("immersive", "沉浸"),
        ("native", "系统"),
        ("none", "不显示"),
    ];
    for (i, (value, label)) in BOTTOMS.iter().enumerate() {
        let r = sy(layout.bottom_row[i]);
        if mode_button(
            &mut ui,
            &t,
            egui::Id::new(("botbar", i)),
            r,
            label,
            app.settings.draft.bottom_bar_mode == *value,
        ) {
            app.settings.set_bar_mode(false, value);
        }
    }

    // 外观卡内容
    group_label(&painter, &t, py(layout.theme_label), "主题");
    const THEMES: [(&str, &str); 3] = [("light", "亮色"), ("dark", "暗色"), ("system", "跟随系统")];
    for (i, (value, label)) in THEMES.iter().enumerate() {
        let r = sy(layout.theme_row[i]);
        if mode_button(
            &mut ui,
            &t,
            egui::Id::new(("theme", i)),
            r,
            label,
            app.settings.draft.theme == *value,
        ) {
            app.settings.set_theme(value);
        }
    }
    if switch_row(
        &mut ui,
        &t,
        egui::Id::new("glass"),
        sy(layout.glass_row),
        "玻璃材质",
        app.settings.draft.glass_enabled,
    ) {
        app.settings.set_glass(!app.settings.draft.glass_enabled);
    }
    if switch_row(
        &mut ui,
        &t,
        egui::Id::new("animations"),
        sy(layout.anim_row),
        "动画效果",
        app.settings.draft.animations_enabled,
    ) {
        app.settings
            .set_animations(!app.settings.draft.animations_enabled);
    }

    // （十修）顶层拖拽间隙层：卡底/标题/页边在控件之上可拖动平移——
    // 旧底层 pan 层被满列控件遮死（真实拖拽 0.6% 无效，取证见
    // docs/ui/DESIGN.md 十修）。控件矩形不并入（滑杆/文本框要拖拽）。
    if max_scroll > 0.0 && !menu_open {
        let mut widgets = vec![
            layout.scrcpy_row,
            layout.adb_row,
            layout.fps_cell,
            layout.bitrate_cell,
            layout.rs_row[0],
            layout.rs_row[1],
            layout.rs_row[2],
            layout.rs_row[3],
            layout.dpi_switch,
            layout.dpi_cell,
            layout.tso_row,
            layout.glass_row,
            layout.anim_row,
        ];
        widgets.extend(layout.codec_row);
        widgets.extend(layout.hwdec_row);
        widgets.extend(layout.audio_row);
        widgets.extend(layout.top_row);
        widgets.extend(layout.bottom_row);
        widgets.extend(layout.theme_row);
        widgets.sort_by_key(|r| r.top() as i32);
        let col = layout.vp;
        let mut gaps: Vec<Rect> = Vec::new();
        let mut cur = col.top() + pad;
        for w in &widgets {
            let top = w.top() - off;
            if top > cur + 1.0 {
                gaps.push(Rect::from_min_max(
                    Pos2::new(col.left(), cur),
                    Pos2::new(col.right(), top),
                ));
            }
            cur = cur.max(w.bottom() - off);
        }
        if col.bottom() > cur + 1.0 {
            gaps.push(Rect::from_min_max(
                Pos2::new(col.left(), cur),
                Pos2::new(col.right(), col.bottom()),
            ));
        }
        if full.left() < col.left() - 1.0 {
            gaps.push(Rect::from_min_max(
                Pos2::new(full.left(), full.top() + 52.0),
                Pos2::new(col.left(), full.bottom()),
            ));
        }
        if full.right() > col.right() + 1.0 {
            gaps.push(Rect::from_min_max(
                Pos2::new(col.right(), full.top() + 52.0),
                Pos2::new(full.right(), full.bottom()),
            ));
        }
        for (i, g) in gaps.iter().enumerate() {
            let resp = ui.interact(*g, egui::Id::new("settings-pan-gap").with(i), Sense::drag());
            if resp.dragged() {
                DRAG_HITS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                app.settings_scroll =
                    (app.settings_scroll - resp.drag_delta().y).clamp(0.0, max_scroll);
            }
        }
    }
}

/// 画卡标题时从引用反查名字（绘制循环需要；四次调用对应四卡）。
fn card_name<'a>(c: &Card, layout: &'a SettingsLayout) -> &'a str {
    if layout.devices.as_ref().is_some_and(|d| std::ptr::eq(c, d)) {
        "设备"
    } else if std::ptr::eq(c, &layout.engine) {
        "引擎"
    } else if std::ptr::eq(c, &layout.quality) {
        "投屏质量"
    } else if std::ptr::eq(c, &layout.windowbar) {
        "默认窗口栏"
    } else {
        "外观"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use geom::ROW_H;
    use geom::SP;

    #[test]
    fn settings_layout_devices_card_geometry() {
        // 双设备 + 一条历史：首卡出现，行数/标签对齐，引擎卡下移；
        // 全空时整卡隐藏。
        let layout = SettingsLayout::compute(420.0, 760.0, "", false, false, 2, 1);
        let dev = layout.devices.expect("设备卡应存在");
        assert_eq!(layout.dev_rows.len(), 2);
        assert_eq!(layout.wifi_rows.len(), 1);
        assert!(layout.wifi_label.is_some());
        assert!(dev.bg.top() < layout.engine.bg.top());
        assert_eq!(layout.dev_rows[0].height(), ROW_H, "设备行高与按钮行一致");
        let empty = SettingsLayout::compute(420.0, 760.0, "", false, false, 0, 0);
        assert!(empty.devices.is_none());
        assert!(empty.dev_rows.is_empty());
    }

    #[test]
    fn settings_layout_y_chain_and_grouping() {
        let layout = SettingsLayout::compute(420.0, 660.0, "", false, false, 0, 0);
        assert!(layout.engine.bg.top() < layout.quality.bg.top());
        assert!(layout.quality.bg.top() < layout.windowbar.bg.top());
        assert!(layout.windowbar.bg.top() < layout.appearance.bg.top());
        // 保存钮已移除：无 footer，视口直达页底
        assert!((layout.vp.bottom() - 660.0).abs() < 0.01);

        // 解释性小字取消后：黑屏开关行与 DPI 开关行标准间距相接
        let gap = layout.dpi_switch.center().y - layout.tso_row.center().y;
        assert!((gap - (ROW_H + SP)).abs() < 0.01);
    }
}

#[cfg(test)]
mod scroll_tests {
    //! 整页滚动的无头回归（kittest 泵帧 + 合成事件）。「设置滚不动」
    //! 2026-09-20 报障后建立：滚轮不局限内容列、触摸/鼠标拖拽平移、
    //! 顶到上下限钳位。
    use crate::app::{Page, PanelApp};

    fn new_app(ctx: &egui::Context, page: Page) -> PanelApp {
        std::env::set_var("DUO_SKIP_SWEEP", "1");
        crate::fonts::install_fonts(ctx);
        let cc = eframe::CreationContext::_new_kittest(ctx.clone());
        let mut app = PanelApp::new(&cc);
        app.page = page;
        app
    }

    fn pump(app: &mut PanelApp, ctx: &egui::Context, events: Vec<egui::Event>) {
        pump_at(app, ctx, egui::vec2(420.0, 660.0), events);
    }

    fn pump_at(
        app: &mut PanelApp,
        ctx: &egui::Context,
        screen: egui::Vec2,
        events: Vec<egui::Event>,
    ) {
        let raw = egui::RawInput {
            screen_rect: Some(egui::Rect::from_min_size(egui::pos2(0.0, 0.0), screen)),
            events,
            ..Default::default()
        };
        let _ = ctx.run(raw, |ctx| {
            egui::CentralPanel::default()
                .frame(egui::Frame::NONE)
                .show(ctx, |ui| match app.page {
                    Page::Home => crate::home::show(app, ui),
                    Page::Settings => crate::settings::show(app, ui),
                });
        });
    }

    #[test]
    #[ignore]
    fn settings_probe_fullscreen_drag() {
        let ctx = egui::Context::default();
        let mut app = new_app(&ctx, Page::Settings);
        pump_at(
            &mut app,
            &ctx,
            egui::vec2(1536.0, 864.0),
            vec![
                egui::Event::PointerMoved(egui::pos2(768.0, 500.0)),
                wheel(-80.0),
            ],
        );
        assert!(
            app.settings_scroll > 0.0,
            "最大化（内容超出视口）应能滚动，实得 {}",
            app.settings_scroll
        );
        let ctx = egui::Context::default();
        let mut app = new_app(&ctx, Page::Settings);
        // 1080p @125% 最大化（1536x864 逻辑）：内容 ~1206 > 视口，必须能滚
        pump_at(
            &mut app,
            &ctx,
            egui::vec2(1536.0, 864.0),
            vec![
                egui::Event::PointerMoved(egui::pos2(768.0, 500.0)),
                wheel(-80.0),
            ],
        );
        assert!(
            app.settings_scroll > 0.0,
            "最大化（内容超出视口）应能滚动，实得 {}",
            app.settings_scroll
        );
        // 触摸/拖拽平移在最大化下同样有效（探测各点）
        for pt in [
            egui::pos2(768.0, 500.0),
            egui::pos2(768.0, 550.0),
            egui::pos2(768.0, 600.0),
            egui::pos2(768.0, 650.0),
            egui::pos2(150.0, 600.0),
        ] {
            let before = app.settings_scroll;
            pump_at(
                &mut app,
                &ctx,
                egui::vec2(1536.0, 864.0),
                vec![
                    egui::Event::PointerMoved(pt),
                    egui::Event::PointerButton {
                        pos: pt,
                        button: egui::PointerButton::Primary,
                        pressed: true,
                        modifiers: Default::default(),
                    },
                ],
            );
            pump_at(
                &mut app,
                &ctx,
                egui::vec2(1536.0, 864.0),
                vec![egui::Event::PointerMoved(pt - egui::vec2(0.0, 60.0))],
            );
            pump_at(
                &mut app,
                &ctx,
                egui::vec2(1536.0, 864.0),
                vec![egui::Event::PointerButton {
                    pos: pt - egui::vec2(0.0, 60.0),
                    button: egui::PointerButton::Primary,
                    pressed: false,
                    modifiers: Default::default(),
                }],
            );
            eprintln!(
                "probe {:?} scroll {} -> {}",
                pt, before, app.settings_scroll
            );
        }
    }

    fn wheel(delta: f32) -> egui::Event {
        egui::Event::MouseWheel {
            unit: egui::MouseWheelUnit::Point,
            delta: egui::vec2(0.0, delta),
            modifiers: Default::default(),
        }
    }

    fn press(at: egui::Pos2) -> Vec<egui::Event> {
        vec![egui::Event::PointerButton {
            pos: at,
            button: egui::PointerButton::Primary,
            pressed: true,
            modifiers: Default::default(),
        }]
    }

    fn move_to(at: egui::Pos2) -> Vec<egui::Event> {
        vec![egui::Event::PointerMoved(at)]
    }

    fn release(at: egui::Pos2) -> Vec<egui::Event> {
        vec![egui::Event::PointerButton {
            pos: at,
            button: egui::PointerButton::Primary,
            pressed: false,
            modifiers: Default::default(),
        }]
    }

    #[test]
    fn settings_wheel_scrolls_anywhere_below_capsule() {
        let ctx = egui::Context::default();
        let mut app = new_app(&ctx, Page::Settings);
        pump(&mut app, &ctx, vec![]);
        // 指针在内容列左外侧（宽屏留白死区）也应滚动
        pump(
            &mut app,
            &ctx,
            vec![
                egui::Event::PointerMoved(egui::pos2(5.0, 300.0)),
                wheel(-50.0),
            ],
        );
        assert!(app.settings_scroll > 0.0, "页面空白处滚轮应滚动设置页");
        // 胶囊带内不滚
        let at = app.settings_scroll;
        pump(
            &mut app,
            &ctx,
            vec![
                egui::Event::PointerMoved(egui::pos2(210.0, 30.0)),
                wheel(-50.0),
            ],
        );
        assert_eq!(app.settings_scroll, at, "胶囊带不是滚动区");
    }

    #[test]
    fn settings_drag_pans_and_clamps() {
        let ctx = egui::Context::default();
        let mut app = new_app(&ctx, Page::Settings);
        pump(&mut app, &ctx, vec![]);
        // 按下与位移分帧送入（真实输入即逐帧到达）
        pump(&mut app, &ctx, press(egui::pos2(210.0, 400.0)));
        pump(&mut app, &ctx, move_to(egui::pos2(210.0, 340.0)));
        assert!(app.settings_scroll > 0.0, "上拖应滚出下方内容");
        // 疯狂上拖钳在 max（1206 高内容 vs 596 视口 ≈ 610）
        pump(&mut app, &ctx, move_to(egui::pos2(210.0, 60.0)));
        pump(&mut app, &ctx, release(egui::pos2(210.0, 60.0)));
        assert!(app.settings_scroll < 2000.0);
        let maxed = app.settings_scroll;
        pump(&mut app, &ctx, press(egui::pos2(210.0, 100.0)));
        pump(&mut app, &ctx, move_to(egui::pos2(210.0, 650.0)));
        pump(&mut app, &ctx, release(egui::pos2(210.0, 650.0)));
        assert_eq!(app.settings_scroll, 0.0, "下拖回顶");
        assert!(maxed > 0.0);
    }

    #[test]
    fn home_wheel_scrolls_grid_from_page_margin() {
        let ctx = egui::Context::default();
        let mut app = new_app(&ctx, Page::Home);
        let seeded: Vec<String> = duo_core::catalog::APP_CATALOG
            .iter()
            .take(20)
            .map(|p| p.package.to_string())
            .collect();
        app.apps.rebuild(&seeded, &Default::default());
        pump(&mut app, &ctx, vec![]);
        // 指针在搜索胶囊（网格外）滚轮也应驱动网格
        pump(
            &mut app,
            &ctx,
            vec![
                egui::Event::PointerMoved(egui::pos2(210.0, 246.0)),
                wheel(-50.0),
            ],
        );
        assert!(app.grid_scroll > 0.0, "页面任意处滚轮应驱动网格");
        pump(&mut app, &ctx, move_to(egui::pos2(210.0, 400.0)));
        pump(&mut app, &ctx, press(egui::pos2(210.0, 400.0)));
        pump(&mut app, &ctx, move_to(egui::pos2(210.0, 350.0)));
        pump(&mut app, &ctx, release(egui::pos2(210.0, 350.0)));
        assert!(app.grid_scroll >= 90.0, "拖拽平移网格（滚轮50+拖拽50）");
    }

    #[test]
    fn settings_scroll_resized_to_fit_snaps_back() {
        // 真机卡死回归：小窗滑到底（scroll=max），放大窗口后内容放得下
        // （max_scroll=0）——旧 scroll 残留把内容顶出视口且滚动分支被
        // 闸关死。修复：无条件 clamp。
        let ctx = egui::Context::default();
        let mut app = new_app(&ctx, Page::Settings);
        pump_at(
            &mut app,
            &ctx,
            egui::vec2(420.0, 660.0),
            vec![
                egui::Event::PointerMoved(egui::pos2(210.0, 400.0)),
                wheel(-2000.0),
            ],
        );
        assert!(app.settings_scroll > 0.0, "小窗应已滚到底");
        // 放大到内容放得下（2261×1529 逻辑 > content_h）
        pump_at(&mut app, &ctx, egui::vec2(2261.0, 1529.0), vec![]);
        assert_eq!(
            app.settings_scroll, 0.0,
            "放得下时 scroll 必须钳回 0（顶部可见）"
        );
        // 再缩回小窗：仍可正常滚动（不残留死状态）
        pump_at(
            &mut app,
            &ctx,
            egui::vec2(420.0, 660.0),
            vec![
                egui::Event::PointerMoved(egui::pos2(210.0, 400.0)),
                wheel(-80.0),
            ],
        );
        assert!(app.settings_scroll > 0.0, "缩回后滚动应恢复");
    }
}

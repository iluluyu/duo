//! 主面板像素移植（Main.qml panelComp ↔ 本文件，P2）。
//!
//! 布局链（QML 绝对坐标照抄）：胶囊(16..48) → 设备卡(y64 h76) →
//! 固定卡(+12 h68，有置顶才出现) → 镜像卡(+12 h64) → 搜索(+12 h36)
//! → 网格(+16) → 运行卡(bottom 56) → Toast(bottom 16)。
//! 每个绘制函数头部注明 QML 出处；数值不发明（总则 1）。

use std::time::Duration;

use egui::{Pos2, Rect, Sense, Vec2};

use crate::paint;
use crate::theme::ThemeKind;
use crate::{app::PanelApp, model::AppEntry};

/// 页面左右留白（Main.qml x: 20）。
pub const MARGIN: f32 = 20.0;

/// 布局 y 链（纯函数，可测）：从面板宽高推出各卡矩形。
#[derive(Debug, Clone, Copy)]
pub struct HomeLayout {
    pub device: Rect,
    pub pinned: Rect,
    pub mirror: Rect,
    pub search: Rect,
    pub grid: Rect,
}

impl HomeLayout {
    pub fn compute_with_chips_height(w: f32, h: f32, has_pinned: bool, chips_h: f32) -> Self {
        let inner = w - 2.0 * MARGIN;
        let mut y = 64.0; // 胶囊（16+32）下 16 间距（Main.qml deviceCard y:64）
        let device = Rect::from_min_size(Pos2::new(MARGIN, y), Vec2::new(inner, 76.0));
        y += 76.0 + 12.0;
        let pinned = Rect::from_min_size(Pos2::new(MARGIN, y), Vec2::new(inner, 68.0));
        if has_pinned {
            y += 68.0 + 12.0;
        } // 固定卡折叠时零高占位不多间距
        let mirror = Rect::from_min_size(Pos2::new(MARGIN, y), Vec2::new(inner, 64.0));
        y += 64.0 + 12.0;
        let search = Rect::from_min_size(Pos2::new(MARGIN, y), Vec2::new(inner, 36.0));
        y += 36.0 + 16.0;
        let grid_bottom = if chips_h > 0.0 {
            (h - 56.0 - chips_h - 14.0).max(y)
        } else {
            (h - 40.0).max(y)
        };
        let grid = Rect::from_min_max(Pos2::new(MARGIN, y), Pos2::new(w - MARGIN, grid_bottom));
        Self {
            device,
            pinned,
            mirror,
            search,
            grid,
        }
    }

    pub fn compute(w: f32, h: f32, has_pinned: bool, has_chips: bool) -> Self {
        Self::compute_with_chips_height(w, h, has_pinned, if has_chips { 56.0 } else { 0.0 })
    }
}

/// 主页面（app.rs update 调度；胶囊在 app.rs 顶部已画）。
/// 网格可滚时，页面任意处滚轮/拖拽都驱动网格（触摸屏无滚轮事件，
/// 拖拽平移补位；2026-09-20 与设置页同构）。
pub fn show(app: &mut PanelApp, ui: &mut egui::Ui) {
    let chips = app.running_chips();
    let chips_h = running_card_height(ui.max_rect().width(), &chips, ui);
    let entries: Vec<AppEntry> = app.grid_entries();
    // 过渡状态按全量应用列表清理（不能用搜索结果：搜索隐藏/恢复会重播入场）。
    app.icon_fades
        .borrow_mut()
        .retain(|pkg, _| app.apps.apps.iter().any(|e| &e.package == pkg));
    let layout = HomeLayout::compute_with_chips_height(
        ui.max_rect().width(),
        ui.max_rect().height(),
        app.has_pinned(),
        chips_h,
    );
    let full = ui.max_rect();
    let pan_zone = Rect::from_min_max(
        Pos2::new(full.left(), full.top() + 52.0),
        Pos2::new(full.right(), full.bottom()),
    );
    let pan = ui.allocate_rect(pan_zone, Sense::drag());
    let menu_open = app.menu_effectively_open(ui.ctx());
    let max_scroll = grid_metrics(layout.grid, &entries).2;
    if max_scroll > 0.0 && !menu_open {
        if ui.rect_contains_pointer(pan_zone) {
            let dy = ui.input(|i| i.raw_scroll_delta.y);
            app.grid_scroll = (app.grid_scroll - dy).clamp(0.0, max_scroll);
        }
        if pan.dragged() {
            app.grid_scroll = (app.grid_scroll - pan.drag_delta().y).clamp(0.0, max_scroll);
        }
    }
    app.island_bands.push((
        full.top(),
        full.bottom(),
        if matches!(app.tokens.kind, crate::theme::ThemeKind::Dark) {
            crate::theme::hex("#1C1C1E")
        } else {
            crate::theme::hex("#F2F2F7")
        },
    ));
    device_card(app, ui, layout.device);
    app.island_bands
        .push((layout.device.top(), layout.device.bottom(), app.tokens.card));
    if app.has_pinned() {
        app.island_bands
            .push((layout.pinned.top(), layout.pinned.bottom(), app.tokens.card));
    }
    app.island_bands
        .push((layout.mirror.top(), layout.mirror.bottom(), app.tokens.card));
    if app.has_pinned() {
        pinned_card(app, ui, layout.pinned);
    }
    mirror_card(app, ui, layout.mirror);
    // 六修：网格先画（磁贴从搜索岛底下滚过），搜索岛+胶囊后画盖其上
    let glass_on = app.settings.draft.glass_enabled && cfg!(target_os = "windows");
    let grid_clip = if glass_on {
        Rect::from_min_max(
            Pos2::new(layout.grid.left(), layout.search.top()),
            Pos2::new(layout.grid.right(), layout.grid.bottom()),
        )
    } else {
        layout.grid
    };
    grid(app, ui, layout.grid, grid_clip, &entries);

    search_island(app, ui, layout.search, glass_on);
    running_card(app, ui, ui.max_rect(), chips_h);
    ui.allocate_rect(ui.max_rect(), Sense::hover());
}

/// (cols, content_h, max_scroll)：show 的输入路由与 grid 的裁剪共用。
fn grid_metrics(rect: Rect, entries: &[AppEntry]) -> (usize, f32, f32) {
    let cols = ((rect.width() / 92.0).floor() as usize).max(2);
    let content_h = entries.len().div_ceil(cols) as f32 * 102.0;
    (cols, content_h, (content_h - rect.height()).max(0.0))
}

fn device_card(app: &mut PanelApp, ui: &mut egui::Ui, rect: Rect) {
    // Main.qml deviceCard：Dot 8+1 白环；在线绿/有设备琥珀/无设备灰；
    // 15px DemiBold 状态 + 12px ink2 serial
    let t = app.tokens;
    paint::card(ui.painter(), rect, &t);
    let (state, serial, online_count, any_device) = app.device_summary();
    let dot_color = if online_count > 0 {
        t.running
    } else if any_device {
        t.warn
    } else {
        crate::theme::hex("#C7C7CC")
    };
    let dot_center = Pos2::new(rect.left() + 14.0 + 5.0, rect.center().y);
    paint::dot(ui.painter(), dot_center, 8.0, 1.0, dot_color);
    let text_x = rect.left() + 14.0 + 10.0 + 10.0;
    paint::text_left(
        ui.painter(),
        Pos2::new(text_x, rect.center().y - 13.0),
        &state,
        15.0,
        t.ink,
    );
    // DemiBold 档（字体栈 duo-bold）另画——text_left 是常规档
    let sub = if serial.is_some() {
        serial.unwrap_or_default()
    } else {
        "连接设备后可启动应用与投屏".into()
    };
    paint::text_left(
        ui.painter(),
        Pos2::new(text_x, rect.center().y + 5.0),
        &sub,
        12.0,
        t.ink2,
    );
    ui.allocate_rect(rect, Sense::hover());
}

fn pinned_card(app: &mut PanelApp, ui: &mut egui::Ui, rect: Rect) {
    // Main.qml pinnedCard：Flow x12 y12 间距 12 的 44px 小图标
    let t = app.tokens;
    paint::card(ui.painter(), rect, &t);
    let entries: Vec<AppEntry> = app.pinned_entries();
    let mut x = rect.left() + 12.0;
    let y = rect.top() + 12.0;
    for entry in &entries {
        let cell = Rect::from_min_size(Pos2::new(x, y), Vec2::splat(44.0));
        pinned_icon(app, ui, entry, cell);
        x += 44.0 + 12.0;
    }
    ui.allocate_rect(rect, Sense::hover());
}

fn pinned_icon(app: &mut PanelApp, ui: &mut egui::Ui, entry: &AppEntry, rect: Rect) {
    // Main.qml PinnedIcon：44px、r10 洗色、未装 40%
    let t = app.tokens;
    let resp = ui.allocate_rect(rect, Sense::click());
    let fill = if resp.hovered() {
        t.hover_on_canvas
    } else {
        egui::Color32::TRANSPARENT
    };
    paint::rounded_fill(ui.painter(), rect, 10.0, fill);
    let alpha = if entry.installed { 1.0 } else { 0.4 };
    paint_glyph(
        app,
        ui,
        &ui.painter().with_clip_rect(ui.max_rect()),
        entry,
        rect,
        44.0,
        alpha,
    );
    if entry.installed {
        if resp.clicked() {
            app.launch(&entry.package, None);
        }
        app.context_menu(&resp, |app, ui| app.tile_menu(ui, entry));
    }
}

fn mirror_card(app: &mut PanelApp, ui: &mut egui::Ui, rect: Rect) {
    let t = app.tokens;
    let card_resp = ui.allocate_rect(rect, Sense::click());
    app.context_menu(&card_resp, |app, ui| app.mirror_menu(ui));
    paint::card(ui.painter(), rect, &t);
    paint::text_left_weight(
        ui.painter(),
        Pos2::new(rect.left() + 12.0, rect.center().y - 7.5),
        "设备镜像",
        15.0,
        t.ink,
        true,
    );
    let online = app.serial().is_some();
    let btn = Rect::from_min_size(
        Pos2::new(rect.right() - 12.0 - 68.0, rect.center().y - 16.0),
        Vec2::new(68.0, 32.0),
    );
    if online {
        volume_slider(
            app,
            ui,
            Rect::from_min_max(
                Pos2::new(rect.left() + 100.0, rect.top()),
                Pos2::new(btn.left() - 14.0, rect.bottom()),
            ),
        );
        paint::speaker(
            ui.painter(),
            Pos2::new(rect.left() + 78.0, rect.center().y - 7.0),
            t.ink2,
        );
        let resp = ui.allocate_rect(btn, Sense::click());
        let alpha = if resp.hovered() { 0.9 } else { 1.0 };
        paint::rounded_fill(ui.painter(), btn, 16.0, mul_alpha(t.accent, alpha));
        paint::text_centered(
            ui.painter(),
            btn.center(),
            "投屏",
            13.0,
            true,
            egui::Color32::WHITE,
        );
        if resp.clicked() {
            app.start_mirror();
        }
    } else {
        paint::rounded_fill(ui.painter(), btn, 16.0, mul_alpha(t.accent, 0.4));
        paint::text_centered(
            ui.painter(),
            btn.center(),
            "投屏",
            13.0,
            true,
            mul_alpha(egui::Color32::WHITE, 0.4),
        );
        ui.allocate_rect(btn, Sense::hover());
    }
}

/// 音量条（Main.qml mediaVolumeSlider 直译：4px 轨、未知中性、12px 拇指、
/// 拖动视觉先行 + 200ms 防抖落命令）。
fn volume_slider(app: &mut PanelApp, ui: &mut egui::Ui, zone: Rect) {
    let t = app.tokens;
    let track = Rect::from_min_size(
        Pos2::new(zone.left(), zone.center().y - 2.0),
        Vec2::new(zone.width(), 4.0),
    );
    paint::rounded_fill(ui.painter(), track, 2.0, t.hairline_on_card);
    let known = app.media_volume >= 0;
    let resp = ui.allocate_rect(track.expand(6.0), Sense::click_and_drag());
    let mut visual = app.media_volume as f32;
    if resp.is_pointer_button_down_on() && resp.dragged() {
        if let Some(pos) = resp.interact_pointer_pos() {
            let frac = ((pos.x - track.left()) / track.width()).clamp(0.0, 1.0);
            visual = (frac * 15.0).round();
            app.volume_dragged(visual as i64);
        }
    } else if known {
        visual = app.media_volume as f32;
    }
    let filled = known || app.volume_pending.is_some();
    if filled {
        let w = track.width() * visual / 15.0;
        paint::rounded_fill(
            ui.painter(),
            Rect::from_min_size(track.left_top(), Vec2::new(w, 4.0)),
            2.0,
            t.accent,
        );
        let cx = track.left() + w;
        ui.painter()
            .circle_filled(Pos2::new(cx, track.center().y), 6.0, t.accent);
    }
}

/// 搜索悬浮玻璃岛（七修：活合成背板——磁贴色块全知，滚动即重建，
/// 模糊跟手零闪烁；染层烤入=单层）。
fn search_island(app: &mut PanelApp, ui: &mut egui::Ui, rect: Rect, glass_on: bool) {
    if glass_on {
        // 八修：岛=搜索框同矩形（旧外扩 10/6 环=「外面一层透明」双层
        // 读取的根源）
        let pill = rect;
        let bands: Vec<(f32, f32, egui::Color32)> = Vec::new();
        let blocks = app.island_blocks.clone();
        let grid_scroll = app.grid_scroll;
        let tex = app.island_glass(ui.ctx(), pill, 1, 0, grid_scroll, &bands, &blocks);
        app.island_drawn[1] = tex.is_some();
        if let Some(tex) = tex {
            // 浮岛软投影 + 液态贴图（rim 已烤入，不再描边）；聚焦与
            // 静止同材质（十修：聚焦不再换材质/压暗）
            let shadow = egui::Shadow {
                offset: [0, 2],
                blur: 8,
                spread: 0,
                color: crate::theme::srgba(0, 0, 0, 20),
            };
            ui.painter()
                .add(shadow.as_shape(pill, egui::CornerRadius::same(18)));
            ui.painter().add(egui::Shape::image(
                tex.id(),
                pill.expand(8.0),
                egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
                crate::theme::srgba(255, 255, 255, 255),
            ));
        }
    }
    search_capsule(app, ui, rect);
}

fn search_capsule(app: &mut PanelApp, ui: &mut egui::Ui, rect: Rect) {
    // Main.qml searchCapsule：h36 r18、searchFill↔聚焦 flyoutFill、
    // 放大镜 16px@12、TextField 13px、清空钮 28×28
    let t = app.tokens;
    let focused = ui
        .ctx()
        .memory(|m| m.has_focus(egui::Id::new("duo-search")));
    // 六修：玻璃开时胶囊透染（底下是玻璃岛，实心填充会盖死玻璃感）
    let glassy = app.settings.draft.glass_enabled && cfg!(target_os = "windows");
    // 十修：聚焦与静止同材质——玻璃岛画成即无填充，非玻璃路径才区分
    // 静止/聚焦底色
    let fill = if glassy && app.island_drawn[1] {
        egui::Color32::TRANSPARENT
    } else if focused {
        t.search_focus
    } else if glassy {
        let translucent = matches!(t.kind, crate::theme::ThemeKind::Dark);
        if translucent {
            crate::theme::srgba(44, 44, 46, 210)
        } else {
            crate::theme::srgba(255, 255, 255, 225)
        }
    } else {
        t.search
    };
    if fill != egui::Color32::TRANSPARENT {
        paint::rounded_fill(ui.painter(), rect, 18.0, fill);
    }
    if !glassy {
        ui.painter().rect_stroke(
            rect,
            egui::CornerRadius::same(18),
            egui::Stroke::new(1.0_f32, t.card_border),
            egui::StrokeKind::Inside,
        );
    }
    let glass_center = Pos2::new(rect.left() + 12.0 + 8.0, rect.center().y);
    paint::magnifier(ui.painter(), glass_center, t.ink2);
    let field = Rect::from_min_max(
        Pos2::new(rect.left() + 12.0 + 16.0 + 8.0, rect.top() + 4.0),
        Pos2::new(rect.right() - 36.0, rect.bottom() - 4.0),
    );
    let mut child = ui.new_child(
        egui::UiBuilder::new()
            .max_rect(field)
            .layout(egui::Layout::left_to_right(egui::Align::Center)),
    );
    let edit = egui::TextEdit::singleline(&mut app.search)
        .id(egui::Id::new("duo-search"))
        .font(egui::FontId::proportional(13.0))
        .frame(false)
        .desired_width(field.width());
    let resp = child.add(edit);
    // 手绘提示字：egui weak_text_color 在玻璃体上无对比（亮档白上白
    // Δ1），显式取主题对比色
    if app.search.is_empty() {
        let hint = if matches!(t.kind, crate::theme::ThemeKind::Dark) {
            crate::theme::srgba(168, 168, 176, 210)
        } else {
            crate::theme::srgba(108, 108, 116, 210)
        };
        child.painter().text(
            Pos2::new(field.left() + 2.0, field.center().y),
            egui::Align2::LEFT_CENTER,
            "搜索",
            egui::FontId::proportional(13.0),
            hint,
        );
    }
    // Esc 清空失焦（QML Keys.onEscapePressed）
    if resp.has_focus() && ui.input(|i| i.key_pressed(egui::Key::Escape)) && !app.search.is_empty()
    {
        app.search.clear();
        resp.surrender_focus();
    }
    // Ctrl+F 聚焦（Shortcut Ctrl+F）
    if ui.input(|i| i.key_pressed(egui::Key::F) && i.modifiers.ctrl) {
        resp.request_focus();
    }
    // 清空钮
    if !app.search.is_empty() {
        let clear = Rect::from_min_size(
            Pos2::new(rect.right() - 4.0 - 28.0, rect.center().y - 14.0),
            Vec2::splat(28.0),
        );
        let cresp = ui.allocate_rect(clear, Sense::click());
        let wash = if cresp.hovered() {
            t.hover_on_canvas
        } else {
            egui::Color32::TRANSPARENT
        };
        paint::rounded_fill(ui.painter(), clear, 14.0, wash);
        paint::x_mark(ui.painter(), clear.center(), t.ink2);
        if cresp.clicked() {
            app.search.clear();
        }
    }
}

/// 应用网格（Main.qml grid：cellW = w/max(2,floor(w/92))、cellH 102）。
fn grid(app: &mut PanelApp, ui: &mut egui::Ui, rect: Rect, clip: Rect, entries: &[AppEntry]) {
    let installed_count = entries.iter().filter(|e| e.installed).count();
    if installed_count == 0 {
        // 空态（Main.qml 无已装应用 Column）
        let t = app.tokens;
        let c = Pos2::new(rect.center().x, rect.top() + 24.0 + 8.0);
        paint::text_centered(ui.painter(), c, "没有已安装的应用", 15.0, true, t.ink);
        paint::text_centered(
            ui.painter(),
            Pos2::new(c.x, c.y + 22.0),
            "在设备上安装应用后，点击刷新检查",
            12.0,
            false,
            t.ink2,
        );
        let btn = Rect::from_min_size(Pos2::new(c.x - 60.0, c.y + 40.0), Vec2::new(120.0, 32.0));
        let resp = ui.allocate_rect(btn, Sense::click());
        let t2 = app.tokens;
        let wash = if resp.hovered() {
            t2.hover_on_canvas
        } else {
            egui::Color32::TRANSPARENT
        };
        paint::rounded_fill(ui.painter(), btn, 16.0, wash);
        paint::text_centered(
            ui.painter(),
            btn.center(),
            "刷新已装应用",
            13.0,
            false,
            t2.accent,
        );
        if resp.clicked() {
            app.refresh_installed();
        }
        return;
    }
    if entries.is_empty() {
        let t = app.tokens;
        paint::text_centered(
            ui.painter(),
            Pos2::new(rect.center().x, rect.top() + 24.0),
            "无匹配应用",
            13.0,
            false,
            t.ink2,
        );
        return;
    }
    let cols = ((rect.width() / 92.0).floor() as usize).max(2);
    let cell_w = rect.width() / cols as f32;
    let cell_h = 102.0;
    let rows = entries.len().div_ceil(cols);
    let content_h = rows as f32 * cell_h;
    // 网格区内部裁剪（QML clip:true）；滚轮/拖拽路由在 show 顶部，
    // 绘制走 with_clip_rect（绝对坐标在 UI 光标之外，ScrollArea 的光标
    // 式裁剪裁不到，实测会截断——改显式裁剪）。
    let max_scroll = (content_h - rect.height()).max(0.0);
    app.grid_scroll = app.grid_scroll.clamp(0.0, max_scroll);
    let scroll = app.grid_scroll;
    for (i, entry) in entries.iter().enumerate() {
        let col = i % cols;
        let row = i / cols;
        let cell = Rect::from_min_size(
            Pos2::new(
                rect.left() + col as f32 * cell_w,
                rect.top() + row as f32 * cell_h - scroll,
            ),
            Vec2::new(cell_w, cell_h),
        );
        if cell.bottom() < clip.top() || cell.top() > clip.bottom() {
            continue; // 视口外跳过
        }
        tile(app, ui, entry, cell, clip);
        // 岛背板剖面：图标主色块 + 标签墨色块（内容坐标=视口坐标+scroll）
        let t = app.tokens;
        let icon = Rect::from_min_size(
            Pos2::new(cell.center().x - 30.0, cell.top() + 10.0 + scroll),
            Vec2::splat(60.0),
        );
        let c = crate::theme::fallback_color(&entry.package);
        app.island_blocks.push((icon, c));
        let label = Rect::from_min_size(
            Pos2::new(cell.left() + 4.0, cell.top() + 76.0 + scroll),
            Vec2::new(cell.width() - 8.0, 16.0),
        );
        app.island_blocks.push((label, t.ink2));
    }
    // 六修：底缘 12px 小气垫（bg 色、α55、随到底距离归零）——托住
    // 运行卡上沿，替代五修的重霜带（用户：通透，不要屏障）。
    if max_scroll > 0.0 && app.settings.draft.glass_enabled {
        let t = app.tokens;
        let painter = ui.painter().with_clip_rect(rect);
        let k_bottom = ((max_scroll - scroll) / 24.0).clamp(0.0, 1.0);
        if k_bottom > 0.0 {
            let band = Rect::from_min_max(
                Pos2::new(rect.left(), rect.bottom() - 12.0),
                Pos2::new(rect.right(), rect.bottom()),
            );
            crate::paint::v_fade_grad(&painter, band, t.bg, 0, (55.0 * k_bottom) as u8);
        }
    }
}

/// 磁贴（Main.qml AppTile：60px 图标 r14 洗色、标签 12px@top76、6 字截断）。
fn tile(app: &mut PanelApp, ui: &mut egui::Ui, entry: &AppEntry, cell: Rect, viewport: Rect) {
    let t = app.tokens;
    // QML GridView clip:true——图标/标签超出网格区即裁（egui 绝对绘制
    // 不会自动裁，须显式 clip painter）
    let painter = ui.painter().with_clip_rect(viewport);
    let resp = ui.allocate_rect(cell, Sense::click());
    let icon = Rect::from_min_size(
        Pos2::new(
            (cell.center().x - 30.0).round(),
            (cell.top() + 10.0).round(),
        ),
        Vec2::splat(60.0),
    );
    let wash = if resp.hovered() && entry.installed {
        t.hover_on_canvas
    } else {
        egui::Color32::TRANSPARENT
    };
    paint::rounded_fill(&painter, icon, 14.0, wash);
    let alpha = if entry.installed { 1.0 } else { 0.4 };
    let arrival = paint_glyph(app, ui, &painter, entry, icon, 60.0, alpha);
    // 标签槽宽 = QML Text width: tile.width - 8（现有几何，不另设常量）；
    // 运行时逐字量宽（字体栈内 CJK 兜底，中西混排按各自字形宽计），
    // 塞不下才截断并补 "…"（elide_to_width 内含 "…" 预算）。
    let label_font = paint::font_id(12.0, false);
    let slot_w = cell.width() - 8.0;
    let label = ui.ctx().fonts(|f| {
        paint::elide_to_width(&entry.label, slot_w, &|ch| f.glyph_width(&label_font, ch))
    });
    paint::text_centered(
        &painter,
        Pos2::new(cell.center().x.round(), (cell.top() + 76.0 + 7.0).round()),
        &label,
        12.0,
        false,
        mul_alpha(t.ink, arrival),
    );
    if entry.installed {
        if resp.clicked() {
            app.launch(&entry.package, None);
        }
        app.context_menu(&resp, |app, ui| app.tile_menu(ui, entry));
    }
}

/// 图标：真图标（sweep 缓存/预设）优先，空则 G2 squircle + 首字白字
/// （Main.qml AppGlyph；fallback 色 = 包名 charCode 和 % 12）。
/// 切换动画（2026-09-29 gpt-6-sol 方案）：按包名键控 0.18s ease-out-cubic；
/// 新包入场淡入，fallback→真图标交叉淡化 + 0.92→1 缩放；动画开关关=瞬切。
/// 返回入场进度供标签同步淡化。
fn paint_glyph(
    app: &PanelApp,
    ui: &mut egui::Ui,
    painter: &egui::Painter,
    entry: &AppEntry,
    rect: Rect,
    _size: f32,
    alpha: f32,
) -> f32 {
    let visual = resolve_glyph(app, ui, entry, rect.width());
    let ready = visual.as_ref().and_then(|_| entry.icon.clone());
    let (arrival, reveal) = glyph_fade(app, entry, ready.as_deref());
    if arrival < 1.0 || reveal < 1.0 {
        ui.ctx().request_repaint();
    }
    match visual {
        None => draw_fallback_glyph(painter, entry, rect, alpha * arrival),
        Some(GlyphVisual::Preset(preset)) => {
            if reveal < 1.0 {
                draw_fallback_glyph(painter, entry, rect, alpha * arrival * (1.0 - reveal));
            }
            let r = scale_about_center(rect, 0.92 + 0.08 * reveal);
            draw_preset_glyph(painter, preset, r, alpha * arrival * reveal);
        }
        Some(GlyphVisual::Texture(id)) => {
            if reveal < 1.0 {
                draw_fallback_glyph(painter, entry, rect, alpha * arrival * (1.0 - reveal));
            }
            let r = scale_about_center(rect, 0.92 + 0.08 * reveal);
            // painter.image 而非 Image widget：网格区必须裁剪溢出
            // （QML clip:true）——widget 不吃 painter 的 clip rect
            painter.image(
                id,
                r,
                egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
                white_alpha(alpha * arrival * reveal),
            );
        }
    }
    arrival
}

enum GlyphVisual {
    Preset(&'static duo_core::catalog::AppPreset),
    Texture(egui::TextureId),
}

/// 当前帧可用视觉：preset 直绘 / 已就绪贴图 / None=fallback。
fn resolve_glyph(
    app: &PanelApp,
    ui: &mut egui::Ui,
    entry: &AppEntry,
    width: f32,
) -> Option<GlyphVisual> {
    if matches!(&entry.icon, Some(p) if p.to_string_lossy().contains("presets")) {
        if let Some(preset) = duo_core::catalog::catalog_by_package(&entry.package) {
            return Some(GlyphVisual::Preset(preset));
        }
    }
    if let Some(path) = &entry.icon {
        if path
            .extension()
            .is_some_and(|e| e.eq_ignore_ascii_case("png"))
        {
            if let Some(texture) = load_icon_texture(app, ui.ctx(), path, width) {
                return Some(GlyphVisual::Texture(texture.id()));
            }
        } else if path.exists() {
            // Windows 反斜杠不是合法 URL；需正斜杠 + file:/// 前缀。
            // Pending 时轮询重绘，Ready 才上屏。
            let uri = format!("file:///{}", path.display().to_string().replace('\\', "/"));
            let poll = ui.ctx().try_load_texture(
                &uri,
                egui::TextureOptions::LINEAR,
                egui::load::SizeHint::Size(240, 240),
            );
            match poll {
                Ok(egui::load::TexturePoll::Ready { texture }) => {
                    return Some(GlyphVisual::Texture(texture.id));
                }
                Ok(egui::load::TexturePoll::Pending { .. }) => {
                    ui.ctx().request_repaint_after(Duration::from_millis(50));
                }
                Err(_) => {}
            }
        }
    }
    None
}

/// 按包名键控的过渡状态，由 PanelApp::icon_fades 持有；sweep 重写
/// 同路径图标时外部重置为 replaced() 以触发交叉淡化。
pub(crate) struct IconFade {
    ready_path: Option<std::path::PathBuf>,
    since: std::time::Instant,
    entering: bool,
}

impl IconFade {
    pub(crate) fn replaced() -> Self {
        Self {
            ready_path: None,
            since: std::time::Instant::now(),
            entering: false,
        }
    }
}

fn glyph_fade(app: &PanelApp, entry: &AppEntry, ready: Option<&std::path::Path>) -> (f32, f32) {
    let now = std::time::Instant::now();
    let enabled = app.settings.draft.animations_enabled;
    let mut fades = app.icon_fades.borrow_mut();
    let fade = fades.entry(entry.package.clone()).or_insert(IconFade {
        ready_path: None,
        since: now,
        entering: true,
    });
    if fade.ready_path.as_deref() != ready {
        fade.ready_path = ready.map(ToOwned::to_owned);
        fade.since = now;
    }
    let reveal = if enabled {
        crate::app::ease_out_cubic(fade.since.elapsed().as_secs_f32() / 0.18)
    } else {
        1.0
    };
    let arrival = if fade.entering { reveal } else { 1.0 };
    if reveal >= 1.0 {
        fade.entering = false;
    }
    (arrival, reveal)
}

/// 目录预设（品牌渐变 squircle + 叠字）矢量直绘：不走 SVG 光栅缩采样。
fn draw_preset_glyph(
    painter: &egui::Painter,
    preset: &duo_core::catalog::AppPreset,
    rect: Rect,
    alpha: f32,
) {
    let bottom = mul_alpha(crate::theme::hex(preset.color), alpha);
    let top = mul_alpha(
        crate::theme::hex(&duo_core::icons::lighten(preset.color, 0.08)),
        alpha,
    );
    let feather = (painter.ctx().pixels_per_point() * 0.8).clamp(0.5, 2.0);
    painter.add(paint::g2_gradient_feathered(
        rect,
        rect.width() / 2.0,
        bottom,
        top,
        feather,
    ));
    let ink = if preset.glyph_ink {
        egui::Color32::from_rgb(0x1D, 0x1D, 0x1F)
    } else {
        egui::Color32::WHITE
    };
    let ch: String = preset
        .glyph
        .chars()
        .next()
        .map(String::from)
        .unwrap_or_default();
    paint::text_centered(
        painter,
        rect.center(),
        &ch,
        rect.width() * 0.467,
        true,
        mul_alpha(ink, alpha),
    );
}

/// G2 squircle + 首字白字兜底（fallback 色 = 包名 charCode 和 % 12）。
fn draw_fallback_glyph(painter: &egui::Painter, entry: &AppEntry, rect: Rect, alpha: f32) {
    let color = mul_alpha(crate::theme::fallback_color(&entry.package), alpha);
    painter.add(paint::g2_squircle(rect, color));
    let ch: String = entry
        .label
        .chars()
        .next()
        .map(String::from)
        .unwrap_or_default();
    let ink = mul_alpha(egui::Color32::WHITE, alpha);
    paint::text_centered(painter, rect.center(), &ch, rect.width() * 0.32, true, ink);
}

fn scale_about_center(rect: Rect, k: f32) -> Rect {
    Rect::from_center_size(rect.center(), rect.size() * k)
}

fn white_alpha(a: f32) -> egui::Color32 {
    let b = (a.clamp(0.0, 1.0) * 255.0).round() as u8;
    egui::Color32::from_rgba_unmultiplied(255, 255, 255, b)
}

/// 光栅图标贴图（panel 侧缓存；圆角一致性由 icongen::panel_icon_rgba
/// 保证）。失败缓存 None 不重试。贴图尺寸按物理像素（显示尺寸 ×
/// pixels_per_point）生成，GPU 1:1 采样——逻辑像素贴图在 125%/150%
/// 缩放下会被 GPU 双线性放大发虚。
fn load_icon_texture(
    app: &PanelApp,
    ctx: &egui::Context,
    path: &std::path::Path,
    size: f32,
) -> Option<egui::TextureHandle> {
    let ppp = ctx.pixels_per_point();
    let px = ((size * ppp).round() as u32).max(1);
    let key = (path.to_path_buf(), px);
    if let Some(cached) = app.icon_tex.borrow().get(&key) {
        return cached.clone();
    }
    let loaded = std::fs::read(path).ok().and_then(|bytes| {
        duo_core::icongen::panel_icon_rgba(&bytes, px)
            .map_err(|err| eprintln!("duo-panel: 图标加载失败 {path:?}: {err}"))
            .ok()
    });
    let tex = loaded.map(|rgba| {
        let image =
            egui::ColorImage::from_rgba_unmultiplied([px as usize, px as usize], rgba.as_raw());
        ctx.load_texture(
            format!("duo-icon:{}#{px}", path.display()),
            image,
            egui::TextureOptions::LINEAR,
        )
    });
    app.icon_tex.borrow_mut().insert(key, tex.clone());
    tex
}

pub fn running_card_height(w: f32, chips: &[(String, String, bool)], ui: &egui::Ui) -> f32 {
    if chips.is_empty() {
        return 0.0;
    }
    let inner_w = w - 2.0 * MARGIN - 24.0;
    let mut rows: Vec<Vec<f32>> = vec![vec![]];
    for (label, _, _) in chips {
        let w = chip_width(ui, label);
        let last = rows.last().unwrap();
        let used: f32 = last.iter().sum::<f32>() + 8.0 * last.len().max(1) as f32;
        if !last.is_empty() && used + w > inner_w {
            rows.push(vec![w]);
        } else {
            rows.last_mut().unwrap().push(w);
        }
    }
    rows.len() as f32 * 32.0 + (rows.len().saturating_sub(1)) as f32 * 8.0 + 24.0
}

/// 运行卡（Main.qml chipsZone：bottom 56、卡内 Flow 间距 8 芯片）。
fn running_card(app: &mut PanelApp, ui: &mut egui::Ui, page: Rect, flow_h: f32) {
    if flow_h <= 0.0 {
        return;
    }
    let chips: Vec<(String, String, bool)> = app.running_chips();
    if chips.is_empty() {
        return;
    }
    let t = app.tokens;
    let card = Rect::from_min_size(
        Pos2::new(MARGIN, page.bottom() - 56.0 - flow_h),
        Vec2::new(page.width() - 2.0 * MARGIN, flow_h),
    );
    ui.allocate_rect(card, Sense::hover());
    if app.settings.draft.glass_enabled {
        let glass_bg = crate::theme::srgba(
            t.card.r(),
            t.card.g(),
            t.card.b(),
            if t.kind == ThemeKind::Dark { 200 } else { 220 },
        );
        paint::rounded_fill(ui.painter(), card, 12.0, glass_bg);
        ui.painter().rect_stroke(
            card,
            egui::CornerRadius::same(12),
            egui::Stroke::new(1.0_f32, t.card_border),
            egui::StrokeKind::Inside,
        );
    } else {
        paint::card(ui.painter(), card, &t);
    }
    let mut x = card.left() + 12.0;
    let mut y = card.top() + 12.0;
    for (key, label, clickable) in chips.iter() {
        let w = chip_width(ui, label);
        if x + w > card.right() - 12.0 {
            x = card.left() + 12.0;
            y += 32.0 + 8.0;
        }
        let chip = Rect::from_min_size(Pos2::new(x, y), Vec2::new(w, 32.0));
        chip_ui(app, ui, chip, key, label, *clickable);
        x += w + 8.0;
    }
}

fn chip_width(ui: &egui::Ui, label: &str) -> f32 {
    let galley = ui.ctx().fonts(|f| {
        f.layout_job(egui::text::LayoutJob::simple(
            label.to_owned(),
            egui::FontId::proportional(12.0),
            egui::Color32::WHITE,
            f32::INFINITY,
        ))
    });
    // Row: dot 8 + 间距 8 + 文本 + ✕ 24 + 左右 pad 12+12 + 间距 8
    galley.size().x + 8.0 + 8.0 + 24.0 + 8.0 + 24.0
}

/// 芯片（Main.qml SessionChip：h32 r16、点+标签可点、✕ hover 露出）。
fn chip_ui(
    app: &mut PanelApp,
    ui: &mut egui::Ui,
    rect: Rect,
    key: &str,
    label: &str,
    clickable: bool,
) {
    let t = app.tokens;
    let resp = ui.allocate_rect(rect, Sense::click());
    let chip_bg = if app.settings.draft.glass_enabled {
        crate::theme::srgba(
            t.card.r(),
            t.card.g(),
            t.card.b(),
            if t.kind == ThemeKind::Dark { 210 } else { 230 },
        )
    } else {
        t.card
    };
    paint::rounded_fill(ui.painter(), rect, 16.0, chip_bg);
    ui.painter().rect_stroke(
        rect,
        egui::CornerRadius::same(16),
        egui::Stroke::new(1.0_f32, t.card_border),
        egui::StrokeKind::Inside,
    );
    paint::dot(
        ui.painter(),
        Pos2::new(rect.left() + 12.0 + 4.0, rect.center().y),
        8.0,
        1.0,
        t.running,
    );
    paint::text_left(
        ui.painter(),
        Pos2::new(rect.left() + 12.0 + 8.0 + 8.0, rect.center().y - 6.0),
        label,
        12.0,
        t.ink,
    );
    if resp.hovered() {
        let stop = Rect::from_min_size(
            Pos2::new(rect.right() - 12.0 - 24.0, rect.center().y - 12.0),
            Vec2::splat(24.0),
        );
        let sresp = ui.allocate_rect(stop, Sense::click());
        let wash = if sresp.hovered() {
            t.danger_on_card
        } else {
            egui::Color32::TRANSPARENT
        };
        paint::rounded_fill(ui.painter(), stop, 12.0, wash);
        let x_color = if sresp.hovered() { t.danger } else { t.ink2 };
        paint::x_mark(ui.painter(), stop.center(), x_color);
        if sresp.clicked() {
            app.stop_session(key);
        }
    }
    if clickable && resp.clicked() && !resp.hovered() {
        // 绿点+标签整体可点 = 拉回该会话虚拟屏（镜像会话禁点）
        app.move_app_to_display(key);
    }
}

/// Toast（app.rs update 调用；Main.qml statusToast：bottom16 h36 r18 pillFill）。
pub fn toast(app: &PanelApp, ctx: &egui::Context) {
    let Some((text, at)) = &app.toast else {
        return;
    };
    if at.elapsed() > Duration::from_millis(2500) {
        return;
    }
    let screen = ctx.screen_rect();
    let galley = ctx.fonts(|f| {
        f.layout_job(egui::text::LayoutJob::simple(
            text.clone(),
            egui::FontId::proportional(13.0),
            egui::Color32::WHITE,
            f32::INFINITY,
        ))
    });
    let w = galley.size().x + 32.0;
    let rect = Rect::from_min_size(
        Pos2::new(screen.center().x - w / 2.0, screen.bottom() - 16.0 - 36.0),
        Vec2::new(w, 36.0),
    );
    egui::Area::new(egui::Id::new("duo-toast"))
        .fixed_pos(rect.left_top())
        .order(egui::Order::Foreground)
        .show(ctx, |ui| {
            ui.set_min_size(rect.size());
            let painter = ui.painter();
            let t = app.tokens;
            paint::rounded_fill(painter, rect, 18.0, t.pill);
            painter.galley(
                Pos2::new(rect.left() + 16.0, rect.center().y - galley.size().y / 2.0),
                galley.clone(),
                egui::Color32::WHITE,
            );
        });
}

fn mul_alpha(c: egui::Color32, a: f32) -> egui::Color32 {
    let alpha = (f32::from(c.a()) * a).round() as u8;
    crate::theme::srgba(c.r(), c.g(), c.b(), alpha)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_y_chain_matches_qml() {
        // 无置顶：64 → 140 → 152(镜像) → 188(搜索) → 216+16=232 网格顶
        let l = HomeLayout::compute(420.0, 660.0, false, false);
        assert_eq!(l.device.top(), 64.0);
        assert_eq!(l.device.height(), 76.0);
        assert_eq!(l.mirror.top(), 152.0);
        assert_eq!(l.search.top(), 228.0);
        assert_eq!(l.grid.top(), 280.0);
        // 有置顶：固定卡占位 → 镜像 +80
        let l2 = HomeLayout::compute(420.0, 660.0, true, true);
        assert_eq!(l2.pinned.top(), 152.0);
        assert_eq!(l2.mirror.top(), 232.0);
        assert_eq!(l2.grid.top(), 360.0);
    }

    #[test]
    fn grid_bottom_stops_before_running_zone() {
        let with_chips = HomeLayout::compute(420.0, 660.0, false, true);
        assert!(
            (with_chips.grid.bottom() - (660.0 - 56.0 - 56.0 - 14.0)).abs() < 0.01,
            "有芯片时网格底让位 56 + chips_h + 14"
        );
        let custom_chips = HomeLayout::compute_with_chips_height(420.0, 660.0, false, 80.0);
        assert!(
            (custom_chips.grid.bottom() - (660.0 - 56.0 - 80.0 - 14.0)).abs() < 0.01,
            "自定义芯片高时按实际高度让位"
        );
        let no_chips = HomeLayout::compute(420.0, 660.0, false, false);
        assert!(
            (no_chips.grid.bottom() - (660.0 - 40.0)).abs() < 0.01,
            "无芯片时网格底到页面底-40"
        );
    }
}

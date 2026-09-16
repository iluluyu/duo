//! 面板应用（egui）：渲染层。UI 逻辑都在 tiles / settings_view / launch
//! （可测纯逻辑），本文件只做 immediate-mode 绘制与事件转发。
//!
//! 结构对齐 DESIGN.md §3：顶栏胶囊（两页常驻）→ 首页（serial 输入 +
//! 应用网格）或设置页（分组表单 + 单保存钮）→ Toast。

use eframe::egui;
use eframe::egui::{Color32, Sense, Vec2};

use crate::blur;
use crate::launch::{self, LaunchRequest};
use crate::settings_view::{SettingsPageModel, AUDIO_CHOICES, BAR_CHOICES, CODEC_CHOICES, THEME_CHOICES};
use crate::theme::{rounding, ThemeKind, Tokens, PAGE_MARGIN};
use crate::tiles::{tiles, TileData};

/// 两页常驻（胶囊即导航）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Page {
    Home,
    Settings,
}

pub struct PanelApp {
    pub page: Page,
    pub settings: SettingsPageModel,
    pub tile_list: Vec<TileData>,
    pub tokens: Tokens,
    /// 设备 serial（v0 手填，DUO_SERIAL 预填；设备监控是 0.2.3 watch 的事）。
    pub serial: String,
    /// 首页一次性提示（spawn 结果）。
    pub home_flash: Option<String>,
    blur_applied: bool,
}

impl PanelApp {
    pub fn new(cc: &eframe::CreationContext) -> Self {
        egui_extras::install_image_loaders(&cc.egui_ctx);
        crate::fonts::install_cjk_font(&cc.egui_ctx);
        let settings = SettingsPageModel::load(None);
        let tokens = Tokens::of(ThemeKind::from_settings(&settings.draft.theme));
        let serial = std::env::var("DUO_SERIAL").unwrap_or_default();
        Self {
            page: Page::Home,
            settings,
            tile_list: tiles(None),
            tokens,
            serial,
            home_flash: None,
            blur_applied: false,
        }
    }

    fn glass_on(&self) -> bool {
        self.settings.draft.glass_enabled
    }

    fn sync_visuals(&self, ctx: &egui::Context) {
        let mut visuals = if self.tokens.kind == ThemeKind::Light {
            egui::Visuals::light()
        } else {
            egui::Visuals::dark()
        };
        visuals.panel_fill = self.tokens.canvas(false);
        visuals.window_fill = self.tokens.canvas(false);
        ctx.set_visuals(visuals);
    }

    fn launch(&mut self, tile: &TileData) {
        if self.serial.trim().is_empty() {
            self.home_flash = Some("先填设备 serial（或 DUO_SERIAL）".into());
            return;
        }
        let req = LaunchRequest {
            package: tile.package.clone(),
            label: tile.label.clone(),
            serial: self.serial.trim().to_string(),
            scrcpy_binary: "scrcpy".into(),
            adb_binary: "adb".into(),
        };
        match launch::session_spec(&req, &self.settings.draft, self.settings.data_dir()) {
            Ok(spec) => match launch::spawn_session(&spec) {
                Ok(_) => self.home_flash = Some(format!("已启动 {}", tile.label)),
                Err(err) => self.home_flash = Some(err),
            },
            Err(err) => self.home_flash = Some(err),
        }
    }

    // ------------------------------------------------ 渲染

    fn top_capsule(&mut self, ui: &mut egui::Ui) {
        let t = self.tokens;
        let height = 32.0;
        let row = ui.available_rect_before_wrap();
        let pill = egui::Rect::from_min_size(row.left_top(), Vec2::new(row.width(), height));
        ui.painter().rect_filled(pill, egui::CornerRadius::same((height/2.0) as u8), t.flyout_fill);
        for (page, label) in [(Page::Home, "首页"), (Page::Settings, "设置")] {
            let left = pill.left() + if page == Page::Home { 0.0 } else { pill.width() / 2.0 };
            let seg = egui::Rect::from_min_size(egui::pos2(left, pill.top()), Vec2::new(pill.width() / 2.0, height));
            let response = ui.allocate_rect(seg, Sense::click());
            let selected = self.page == page;
            if selected {
                let fill = if t.kind == ThemeKind::Light { Color32::WHITE } else { t.segment_fill };
                ui.painter().rect_filled(egui::Rect::from_min_max(egui::pos2(seg.left(), seg.top()+2.0), egui::pos2(seg.right(), seg.bottom()-2.0)), rounding::FLYOUT, fill);
            } else if response.hovered() {
                ui.painter().rect_filled(egui::Rect::from_min_max(egui::pos2(seg.left(), seg.top()+2.0), egui::pos2(seg.right(), seg.bottom()-2.0)), rounding::FLYOUT, t.hover_wash);
            }
            let ink = if selected { t.ink } else { t.ink2 };
            ui.painter().text(
                seg.center(),
                egui::Align2::CENTER_CENTER,
                label,
                egui::FontId::proportional(13.0),
                ink,
            );
            if response.clicked() {
                self.page = page;
            }
        }
        ui.allocate_space(Vec2::new(0.0, height + 8.0));
    }

    fn home_page(&mut self, ui: &mut egui::Ui) {
        let t = self.tokens;
        ui.horizontal(|ui| {
            ui.label(egui::RichText::new("设备").size(13.0).color(t.ink2));
            let edit = egui::TextEdit::singleline(&mut self.serial)
                .hint_text("serial（DUO_SERIAL 预填）")
                .desired_width(ui.available_width());
            ui.add(edit);
        });
        ui.add_space(4.0);
        if let Some(msg) = self.home_flash.clone() {
            self.toast(ui, &msg);
            ui.add_space(4.0);
        }
        ui.add_space(4.0);

        // 应用网格（裸排，目录序；92×102 磁贴 = 图标 60 + 单行标签）。
        let gap = 8.0;
        let tile_list = self.tile_list.clone();
        egui::ScrollArea::vertical().show(ui, |ui| {
            ui.with_layout(
                egui::Layout::left_to_right(egui::Align::TOP).with_main_wrap(true),
                |ui| {
                    for (i, tile) in tile_list.iter().enumerate() {
                        if i > 0 {
                            ui.add_space(gap);
                        }
                        self.tile(ui, tile);
                    }
                },
            );
        });
        let _ = gap;
    }

    fn tile(&mut self, ui: &mut egui::Ui, tile: &TileData) {
        let t = self.tokens;
        let size = Vec2::new(92.0, 102.0);
        let (rect, response) = ui.allocate_exact_size(size, Sense::click());
        let fill = if response.hovered() { t.card_hover() } else { t.card_fill };
        ui.painter().rect_filled(rect, egui::CornerRadius::same(rounding::CARD as u8), fill);
        ui.painter().rect_stroke(rect, egui::CornerRadius::same(rounding::CARD as u8), egui::Stroke::new(1.0_f32, t.card_border), egui::StrokeKind::Outside);
        if response.clicked() {
            self.launch(tile);
        }

        // 图标 60×60：预设 SVG（后端纹理）；加载失败回退品牌色块。
        let icon_rect = egui::Rect::from_center_size(
            egui::pos2(rect.center().x, rect.top() + 8.0 + 30.0),
            Vec2::splat(60.0),
        );
        let mut svg_ready = false;
        if let Some(uri) = &tile.icon_uri {
            let result = ui.ctx().try_load_image(uri, egui::load::SizeHint::Width(120));
            if matches!(result, Ok(egui::load::ImagePoll::Ready { .. })) {
                svg_ready = true;
                ui.put(
                    icon_rect,
                    egui::Image::from_uri(uri.clone())
                        .fit_to_exact_size(Vec2::splat(60.0))
                        .corner_radius(rounding::ICON),
                );
            }
        }
        if !svg_ready {
            let color = crate::theme::hex(&tile.color_hex);
            ui.painter().rect_filled(icon_rect, egui::CornerRadius::same(rounding::ICON as u8), color);
        }
        // 单字（SVG 文本层无系统字体，恒由 egui 叠画，保证字形一致）。
        let ink = if tile.glyph_ink {
            crate::theme::hex("#1D1D1F")
        } else {
            Color32::WHITE
        };
        ui.painter().text(
            icon_rect.center(),
            egui::Align2::CENTER_CENTER,
            &tile.glyph,
            egui::FontId::proportional(24.0),
            ink,
        );
        ui.painter().text(
            egui::pos2(rect.center().x, rect.bottom() - 14.0),
            egui::Align2::CENTER_CENTER,
            &tile.label,
            egui::FontId::proportional(13.0),
            t.ink,
        );
    }

    fn toast(&mut self, ui: &mut egui::Ui, msg: &str) {
        let t = self.tokens;
        egui::Frame::NONE
            .fill(t.pill_fill)
            .corner_radius(14.0)
            .inner_margin(egui::Margin::symmetric(12, 6))
            .show(ui, |ui| {
                ui.label(egui::RichText::new(msg).size(13.0).color(t.ink));
            });
    }

    fn settings_page(&mut self, ui: &mut egui::Ui) {
        if let Some(msg) = self.settings.flash.clone() {
            self.toast(ui, &msg);
            ui.add_space(4.0);
        }
        egui::ScrollArea::vertical().show(ui, |ui| {
            // —— 投屏质量：视频编码 / 帧率 / 码率
            self.settings_group(ui, "投屏质量", |ui, app| {
                let t = app.tokens;
                let codec_now = app.settings.draft.video_codec.clone();
                ui.label(egui::RichText::new("视频编码").size(13.0).color(t.ink2));
                let codec = combo(ui, "duo-codec", &codec_now, &CODEC_CHOICES, |v| v.to_string());
                if codec != codec_now {
                    app.settings.set_video_codec(&codec);
                }
                ui.add_space(8.0);
                let fps_now = app.settings.draft.fps.unwrap_or(60);
                let mut fps = fps_now;
                ui.add(egui::Slider::new(&mut fps, 1..=240).text("帧率"));
                if fps != fps_now {
                    app.settings.set_fps(fps);
                }
                let bitrate_now = app.settings.draft.bitrate_mbps.unwrap_or(30);
                let mut bitrate = bitrate_now;
                ui.add(egui::Slider::new(&mut bitrate, 1..=200).text("码率 Mbps"));
                if bitrate != bitrate_now {
                    app.settings.set_bitrate(bitrate);
                }
            });
            // —— 音频（三态）
            self.settings_group(ui, "音频", |ui, app| {
                let policy_now = app.settings.draft.audio_policy.clone();
                let policy = combo(
                    ui,
                    "duo-audio",
                    &policy_now,
                    &AUDIO_CHOICES,
                    audio_label,
                );
                if policy != policy_now {
                    app.settings.set_audio_policy(&policy);
                }
            });
            // —— 窗口栏：上巴/下巴三态（immersive/native/none）
            self.settings_group(ui, "窗口栏", |ui, app| {
                let top_now = app.settings.draft.top_bar_mode.clone();
                let top = bar_combo(ui, "duo-top-bar", &top_now, "上巴");
                if top != top_now {
                    app.settings.set_bar_mode(true, &top);
                }
                let bottom_now = app.settings.draft.bottom_bar_mode.clone();
                let bottom = bar_combo(ui, "duo-bottom-bar", &bottom_now, "下巴");
                if bottom != bottom_now {
                    app.settings.set_bar_mode(false, &bottom);
                }
            });
            // —— 外观：玻璃开关 / 主题
            self.settings_group(ui, "外观", |ui, app| {
                let mut glass = app.settings.draft.glass_enabled;
                if ui.checkbox(&mut glass, "玻璃（系统 blur）").changed() {
                    app.settings.set_glass(glass);
                }
                let theme_now = app.settings.draft.theme.clone();
                let theme = combo(ui, "duo-theme", &theme_now, &THEME_CHOICES, |v| v.to_string());
                if theme != theme_now {
                    app.settings.set_theme(&theme);
                }
            });
            // 单保存钮（强调色 = 唯一主操作）。
            let t = self.tokens;
            let label = if self.settings.dirty { "保存" } else { "已保存" };
            let button = egui::Button::new(egui::RichText::new(label).size(13.0))
                .fill(if self.settings.dirty { t.accent } else { t.segment_fill });
            if ui.add_sized([ui.available_width(), 28.0], button).clicked() {
                self.settings.save();
            }
        });
    }

    fn settings_group(
        &mut self,
        ui: &mut egui::Ui,
        title: &str,
        body: impl FnOnce(&mut egui::Ui, &mut Self),
    ) {
        let t = self.tokens;
        egui::Frame::NONE
            .fill(t.card_fill)
            .stroke(egui::Stroke::new(1.0_f32, t.card_border))
            .corner_radius(rounding::CARD)
            .inner_margin(egui::Margin::same(12))
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.label(egui::RichText::new(title).size(13.0).strong().color(t.ink));
                ui.add_space(4.0);
                body(ui, self);
            });
        ui.add_space(8.0);
    }
}

/// 下拉选择：返回选中的值（未动 = 传入的当前值）。
fn combo(
    ui: &mut egui::Ui,
    id: &str,
    current: &str,
    choices: &[&str],
    label: impl Fn(&str) -> String,
) -> String {
    let mut value = current.to_string();
    egui::ComboBox::from_id_salt(id)
        .selected_text(label(current))
        .show_ui(ui, |ui| {
            for c in choices {
                ui.selectable_value(&mut value, c.to_string(), label(c));
            }
        });
    value
}

fn audio_label(policy: &str) -> String {
    match policy {
        "latest" => "仅最新会话".into(),
        "all" => "全部会话".into(),
        _ => "静音".into(),
    }
}

fn bar_combo(ui: &mut egui::Ui, id: &str, current: &str, label: &str) -> String {
    ui.horizontal(|ui| {
        ui.label(egui::RichText::new(label).size(13.0));
        combo(ui, id, current, &BAR_CHOICES, bar_label)
    })
    .inner
}

fn bar_label(mode: &str) -> String {
    match mode {
        "immersive" => "沉浸".into(),
        "native" => "系统标题栏".into(),
        _ => "不显示".into(),
    }
}

impl eframe::App for PanelApp {
    /// 画布清屏色：玻璃开 = 半透明（透出 DWM blur），关 = 不透明。
    fn clear_color(&self, _visuals: &egui::Visuals) -> [f32; 4] {
        let c = self.tokens.canvas(self.glass_on());
        [
            c.r() as f32 / 255.0,
            c.g() as f32 / 255.0,
            c.b() as f32 / 255.0,
            c.a() as f32 / 255.0,
        ]
    }

    fn update(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        if !self.blur_applied && self.glass_on() {
            blur::apply_glass(frame, [28u8, 28, 30, 255]);
            self.blur_applied = true;
        }
        // 主题令牌即时跟随草稿（保存前先看效果）。
        let kind = ThemeKind::from_settings(&self.settings.draft.theme);
        if self.tokens.kind != kind {
            self.tokens = Tokens::of(kind);
        }
        self.sync_visuals(ctx);

        egui::CentralPanel::default()
            .frame(egui::Frame::NONE.inner_margin(egui::Margin::symmetric(PAGE_MARGIN as i8, 16)))
            .show(ctx, |ui| {
                self.top_capsule(ui);
                match self.page {
                    Page::Home => self.home_page(ui),
                    Page::Settings => self.settings_page(ui),
                }
            });
        // flash 短驻：任意点击即清（v0 无定时器 Toast）。
        if ctx.input(|i| i.pointer.any_click()) {
            self.settings.dismiss_flash();
        }
        ctx.request_repaint_after(std::time::Duration::from_millis(500));
    }
}

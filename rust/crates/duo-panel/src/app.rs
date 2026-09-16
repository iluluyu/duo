//! 面板应用（egui）：渲染层。UI 逻辑住在 model / sessions / prefs /
//! settings_view / backend（可测纯逻辑或进程封装），本文件只做
//! immediate-mode 绘制与事件转发。结构对齐 DESIGN.md §3：顶栏胶囊 →
//! 首页（设备卡 + 固定卡 + 搜索 + 网格 + 运行卡）或设置页 → Toast。

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use eframe::egui;
use eframe::egui::{Color32, RichText, Sense, Vec2};

use duo_core::aspects::{
    aspect_presets, body_aspect_from_wm_size, preset_by_id, transposed, AspectPreset,
    BODY_LANDSCAPE_ID, BODY_PORTRAIT_ID,
};
use duo_core::catalog::catalog_by_package;
use duo_core::settings::resolve_adb_path;

use crate::backend::{self, Background, DeviceWatch};
use crate::blur;
use crate::model::{AppEntry, AppsModel};
use crate::prefs::{
    load_audio_prefs, load_bar_prefs, load_behavior_prefs, load_density_prefs, load_display_prefs,
    load_pinned_prefs, load_scale_prefs, save_audio_prefs, save_bar_prefs, save_behavior_prefs,
    save_density_prefs, save_display_prefs, save_pinned_prefs, save_scale_prefs, BarChoice,
    DisplayChoice,
};
use crate::sessions::{panel_log_path, session_label, Sessions, MIRROR_KEY};
use crate::settings_view::{
    SettingsPageModel, AUDIO_CHOICES, BAR_CHOICES, CODEC_CHOICES, CORNER_CHOICES, THEME_CHOICES,
};
use crate::theme::{rounding, ThemeKind, Tokens, PAGE_MARGIN};

/// 两页常驻（胶囊即导航）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Page {
    Home,
    Settings,
}

/// 已装探测后台任务结果（已装包名全集 + duo-core apps 行）。
type InstalledResult = Result<(Vec<String>, Vec<backend::AppRow>), String>;

/// 移动应用到虚拟屏的后台任务结果。
struct MoveResult {
    package: String,
    ok: bool,
    detail: String,
}

pub struct PanelApp {
    pub page: Page,
    pub settings: SettingsPageModel,
    pub tokens: Tokens,

    // 后端
    duo_core: Option<PathBuf>,
    adb: String,
    watch: Option<DeviceWatch>,
    watch_adb: String,
    installed_bg: Option<Background<InstalledResult>>,
    sweep_bg: Option<Background<Result<backend::SweepResult, String>>>,
    move_bg: Option<Background<MoveResult>>,
    volume_bg: Option<Background<()>>,
    known_online: Option<Vec<String>>,

    // 模型
    pub apps: AppsModel,
    sessions: Sessions,
    pinned: BTreeMap<String, bool>,
    display_prefs: BTreeMap<String, DisplayChoice>,
    bar_prefs: BTreeMap<String, BarChoice>,
    audio_prefs: BTreeMap<String, bool>,
    behavior_prefs: BTreeMap<String, bool>,
    density_prefs: BTreeMap<String, i64>,
    scale_prefs: BTreeMap<String, f64>,
    body_preset: Option<AspectPreset>,
    body_probed_at: Option<Instant>,

    // UI 态
    pub search: String,
    toast: Option<(String, Instant)>,
    media_volume: i64,
    blur_applied: bool,
}

impl PanelApp {
    pub fn new(cc: &eframe::CreationContext) -> Self {
        egui_extras::install_image_loaders(&cc.egui_ctx);
        crate::fonts::install_cjk_font(&cc.egui_ctx);
        let settings = SettingsPageModel::load(None);
        let tokens = Tokens::of(ThemeKind::from_settings(&settings.draft.theme));
        let adb = resolve_adb_path(&settings.draft, None, "adb");
        let mut app = Self {
            page: Page::Home,
            settings,
            tokens,
            duo_core: backend::find_duo_core(),
            adb: adb.clone(),
            watch: None,
            watch_adb: adb,
            installed_bg: None,
            sweep_bg: None,
            move_bg: None,
            volume_bg: None,
            known_online: None,
            apps: AppsModel::default(),
            sessions: Sessions::new(),
            pinned: load_pinned_prefs().into_iter().map(|p| (p, true)).collect(),
            display_prefs: load_display_prefs(),
            bar_prefs: load_bar_prefs(),
            audio_prefs: load_audio_prefs(),
            behavior_prefs: load_behavior_prefs(),
            density_prefs: load_density_prefs(),
            scale_prefs: load_scale_prefs(),
            body_preset: None,
            body_probed_at: None,
            search: String::new(),
            toast: None,
            media_volume: 9,
            blur_applied: false,
        };
        app.ensure_watch();
        app.refresh_installed();
        app
    }

    fn ensure_watch(&mut self) {
        if self.watch.is_some() && self.watch_adb == self.adb {
            return;
        }
        let Some(binary) = self.duo_core.clone() else {
            return;
        };
        self.watch = Some(DeviceWatch::start(
            &binary.display().to_string(),
            &self.adb,
            2.0,
        ));
        self.watch_adb = self.adb.clone();
    }

    fn toast_now(&mut self, text: impl Into<String>) {
        self.toast = Some((text.into(), Instant::now()));
    }

    fn state_text(state: &str) -> &str {
        match state {
            "device" => "在线",
            "offline" => "离线",
            "unauthorized" => "未授权 USB 调试",
            "recovery" => "recovery 模式",
            _ => state,
        }
    }

    fn device_status(&self) -> String {
        let Some(watch) = &self.watch else {
            return "设备监控未启动（缺 duo-core）".into();
        };
        let states = watch.states();
        if states.is_empty() {
            return "等待设备…".into();
        }
        let parts: Vec<String> = states
            .iter()
            .map(|(serial, state)| format!("{serial} · {}", Self::state_text(state)))
            .collect();
        parts.join("  ")
    }

    fn serial(&self) -> Option<String> {
        self.watch.as_ref().and_then(|w| w.online_serial())
    }

    fn refresh_installed(&mut self) {
        let Some(binary) = self.duo_core.clone() else {
            return;
        };
        let Some(serial) = self.serial() else {
            return;
        };
        let adb = self.adb.clone();
        self.installed_bg = Some(Background::spawn(move || {
            backend::query_apps(&binary.display().to_string(), &adb, &serial).map(|rows| {
                let installed = rows.iter().map(|r| r.package.clone()).collect();
                (installed, rows)
            })
        }));
    }

    fn start_sweep(&mut self) {
        let Some(binary) = self.duo_core.clone() else {
            return;
        };
        let Some(serial) = self.serial() else {
            return;
        };
        let adb = self.adb.clone();
        self.sweep_bg = Some(Background::spawn(move || {
            backend::run_sweep(&binary.display().to_string(), &adb, &serial)
        }));
    }

    fn adopt_installed(&mut self, result: Result<(Vec<String>, Vec<backend::AppRow>), String>) {
        match result {
            Ok((installed, rows)) => {
                self.apps.rebuild(&installed, &self.pinned);
                let extras: Vec<String> = rows
                    .iter()
                    .filter(|r| !r.catalog)
                    .map(|r| r.package.clone())
                    .collect();
                self.apps.merge_third_party(&extras, &self.pinned);
                self.start_sweep();
            }
            Err(err) => {
                self.toast_now(format!("已装应用探测失败：{err}"));
            }
        }
    }

    // ------------------------------------------------------------- launch

    fn launch(&mut self, package: &str, size: Option<(i64, i64)>) {
        let Some(serial) = self.serial() else {
            self.toast_now("设备未连接");
            return;
        };
        let Some(binary) = self.duo_core.clone() else {
            self.toast_now("找不到 duo-core 二进制");
            return;
        };
        self.sessions.reap();
        if self.sessions.is_running(package) {
            self.move_app_to_display(package);
            return;
        }
        let portrait = self.sessions.portrait_of(package);
        let display = self.display_prefs.get(package).cloned();
        // 机身 id 解析成具体几何后作为一次性 size 启动。
        let size = size.or_else(|| self.remembered_size(package));
        let keep_vd = self.behavior_prefs.get(package).copied().unwrap_or(false);
        let exclusive = self.audio_prefs.get(package).copied().unwrap_or(false);
        let params = crate::sessions::LaunchParams {
            package,
            serial: &serial,
            portrait,
            muted: false,
            size,
            display: display.as_ref(),
            keep_vd,
        };
        match self
            .sessions
            .start(&binary.display().to_string(), &params, exclusive)
        {
            Ok(restarted) => {
                let orientation = if portrait { "竖屏" } else { "横屏" };
                let suffix = if restarted.is_empty() {
                    String::new()
                } else {
                    format!("（{} 已静音重启）", restarted.join("、"))
                };
                self.toast_now(format!(
                    "已启动 {} · {orientation}{suffix}",
                    session_label(package)
                ));
            }
            Err(err) => self.toast_now(format!("启动失败：{}（{err}）", session_label(package))),
        }
    }

    /// 记忆的 fixed 几何（body id 经 wm size 探测解析）。
    fn remembered_size(&mut self, package: &str) -> Option<(i64, i64)> {
        let choice = self.display_prefs.get(package)?.clone();
        let DisplayChoice::Fixed { aspect } = choice else {
            return None;
        };
        if let Some(preset) = preset_by_id(&aspect) {
            return Some((preset.width, preset.height));
        }
        if aspect == BODY_LANDSCAPE_ID || aspect == BODY_PORTRAIT_ID {
            let body = self.body_preset()?;
            let preset = if aspect == BODY_LANDSCAPE_ID {
                body
            } else {
                transposed(&body)
            };
            return Some((preset.width, preset.height));
        }
        None
    }

    fn body_preset(&mut self) -> Option<AspectPreset> {
        if let Some(at) = self.body_probed_at {
            if at.elapsed() < Duration::from_secs(600) {
                return self.body_preset.clone();
            }
        }
        let serial = self.serial()?;
        let output = std::process::Command::new(&self.adb)
            .args(["-s", &serial, "shell", "wm", "size"])
            .output()
            .ok()?;
        let text = String::from_utf8_lossy(&output.stdout).into_owned();
        let preset = body_aspect_from_wm_size(&text);
        self.body_probed_at = Some(Instant::now());
        self.body_preset = preset.clone();
        preset
    }

    fn move_app_to_display(&mut self, package: &str) {
        let Some(serial) = self.serial() else {
            self.toast_now("设备未连接");
            return;
        };
        let adb = self.adb.clone();
        let package = package.to_string();
        let log = panel_log_path(&package);
        self.move_bg = Some(Background::spawn(move || {
            let display_id = duo_core::session::display_id_from_log(&log);
            let Some(display_id) = display_id else {
                return MoveResult {
                    package,
                    ok: false,
                    detail: "虚拟屏未就绪，稍后重试".into(),
                };
            };
            let output = std::process::Command::new(&adb)
                .args([
                    "-s",
                    &serial,
                    "shell",
                    "cmd",
                    "package",
                    "resolve-activity",
                    "--brief",
                    &package,
                ])
                .output();
            let Ok(output) = output else {
                return MoveResult {
                    package,
                    ok: false,
                    detail: "adb 失败".into(),
                };
            };
            let text = String::from_utf8_lossy(&output.stdout).into_owned();
            let Some(component) = duo_core::apps::parse_resolve_activity(&text) else {
                return MoveResult {
                    package,
                    ok: false,
                    detail: "无法解析应用入口".into(),
                };
            };
            let output = std::process::Command::new(&adb)
                .args([
                    "-s",
                    &serial,
                    "shell",
                    "am",
                    "start",
                    "--display",
                    &display_id.to_string(),
                    "-n",
                    component,
                ])
                .output();
            let Ok(output) = output else {
                return MoveResult {
                    package,
                    ok: false,
                    detail: "adb 失败".into(),
                };
            };
            let text = String::from_utf8_lossy(&output.stdout).into_owned();
            let failed = text.contains("Error");
            MoveResult {
                package,
                ok: !failed,
                detail: text.lines().next().unwrap_or("").to_string(),
            }
        }));
    }

    fn start_mirror(&mut self) {
        let Some(serial) = self.serial() else {
            self.toast_now("设备未连接");
            return;
        };
        let Some(binary) = self.duo_core.clone() else {
            self.toast_now("找不到 duo-core 二进制");
            return;
        };
        self.sessions.reap();
        if self.sessions.is_running(MIRROR_KEY) {
            self.toast_now("设备镜像已在运行");
            return;
        }
        match self
            .sessions
            .start_mirror(&binary.display().to_string(), &serial)
        {
            Ok(restarted) => {
                let suffix = if restarted.is_empty() {
                    String::new()
                } else {
                    format!("（{} 已静音重启）", restarted.join("、"))
                };
                self.toast_now(format!("已启动 设备镜像{suffix}"));
            }
            Err(err) => self.toast_now(format!("启动失败：设备镜像（{err}）")),
        }
    }

    // --------------------------------------------------------------- prefs

    fn toggle_pin(&mut self, package: &str) {
        let now = !self.pinned.get(package).copied().unwrap_or(false);
        if now {
            self.pinned.insert(package.to_string(), true);
            self.toast_now(format!("已置顶 {}", session_label(package)));
        } else {
            self.pinned.remove(package);
            self.toast_now(format!("已取消置顶 {}", session_label(package)));
        }
        for entry in &mut self.apps.apps {
            entry.pinned = self.pinned.contains_key(&entry.package);
        }
        save_pinned_prefs(&self.pinned.keys().cloned().collect::<Vec<_>>());
    }

    fn toggle_portrait(&mut self, package: &str) {
        let now = !self.sessions.portrait_of(package);
        self.sessions.set_portrait(package, now);
        self.toast_now(format!(
            "{} 将以{}启动",
            session_label(package),
            if now { "竖屏" } else { "横屏" }
        ));
    }

    fn set_display_flex(&mut self, package: &str) {
        self.display_prefs
            .insert(package.to_string(), DisplayChoice::Flex);
        save_display_prefs(&self.display_prefs);
        self.toast_now(format!("{} 将自适应窗口", session_label(package)));
    }

    fn set_display_fixed(&mut self, package: &str, aspect: &str) {
        self.display_prefs.insert(
            package.to_string(),
            DisplayChoice::Fixed {
                aspect: aspect.into(),
            },
        );
        save_display_prefs(&self.display_prefs);
        self.toast_now(format!("{} 将固定比例启动", session_label(package)));
    }

    fn set_bar(&mut self, package: &str, which: bool, mode: Option<&'static str>) {
        let choice = self.bar_prefs.get(package).cloned().unwrap_or_default();
        let entry = self.bar_prefs.entry(package.to_string()).or_insert(choice);
        if which {
            entry.top = mode;
        } else {
            entry.bottom = mode;
        }
        save_bar_prefs(&self.bar_prefs);
    }

    fn toggle_audio_exclusive(&mut self, package: &str) {
        let now = !self.audio_prefs.get(package).copied().unwrap_or(false);
        if now {
            self.audio_prefs.insert(package.to_string(), true);
        } else {
            self.audio_prefs.remove(package);
        }
        save_audio_prefs(&self.audio_prefs);
    }

    fn toggle_keep_vd(&mut self, package: &str) {
        let now = !self.behavior_prefs.get(package).copied().unwrap_or(false);
        if now {
            self.behavior_prefs.insert(package.to_string(), true);
        } else {
            self.behavior_prefs.remove(package);
        }
        save_behavior_prefs(&self.behavior_prefs);
    }

    fn set_density(&mut self, package: &str, dpi: Option<i64>) {
        match dpi {
            Some(dpi) => {
                self.density_prefs.insert(package.to_string(), dpi);
            }
            None => {
                self.density_prefs.remove(package);
            }
        }
        save_density_prefs(&self.density_prefs);
    }

    fn set_scale(&mut self, package: &str, scale: Option<f64>) {
        match scale {
            Some(scale) => {
                self.scale_prefs.insert(package.to_string(), scale);
            }
            None => {
                self.scale_prefs.remove(package);
            }
        }
        save_scale_prefs(&self.scale_prefs);
    }

    // ------------------------------------------------------------- 渲染

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

    fn top_capsule(&mut self, ui: &mut egui::Ui) {
        let t = self.tokens;
        let height = 32.0;
        let row = ui.available_rect_before_wrap();
        let pill = egui::Rect::from_min_size(row.left_top(), Vec2::new(row.width(), height));
        ui.painter().rect_filled(
            pill,
            egui::CornerRadius::same((height / 2.0) as u8),
            t.flyout_fill,
        );
        for (page, label) in [(Page::Home, "首页"), (Page::Settings, "设置")] {
            let left = pill.left()
                + if page == Page::Home {
                    0.0
                } else {
                    pill.width() / 2.0
                };
            let seg = egui::Rect::from_min_size(
                egui::pos2(left, pill.top()),
                Vec2::new(pill.width() / 2.0, height),
            );
            let response = ui.allocate_rect(seg, Sense::click());
            let selected = self.page == page;
            if selected {
                let fill = if t.kind == ThemeKind::Light {
                    Color32::WHITE
                } else {
                    t.segment_fill
                };
                ui.painter().rect_filled(
                    egui::Rect::from_min_max(
                        egui::pos2(seg.left(), seg.top() + 2.0),
                        egui::pos2(seg.right(), seg.bottom() - 2.0),
                    ),
                    rounding::FLYOUT,
                    fill,
                );
            } else if response.hovered() {
                ui.painter().rect_filled(
                    egui::Rect::from_min_max(
                        egui::pos2(seg.left(), seg.top() + 2.0),
                        egui::pos2(seg.right(), seg.bottom() - 2.0),
                    ),
                    rounding::FLYOUT,
                    t.hover_wash,
                );
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

    fn device_card(&mut self, ui: &mut egui::Ui) {
        let t = self.tokens;
        egui::Frame::NONE
            .fill(t.card_fill)
            .stroke(egui::Stroke::new(1.0_f32, t.card_border))
            .corner_radius(rounding::CARD)
            .inner_margin(egui::Margin::same(10))
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.horizontal(|ui| {
                    ui.label(RichText::new("设备").size(13.0).color(t.ink2));
                    ui.label(RichText::new(self.device_status()).size(13.0).color(t.ink));
                    let online = self.serial().is_some();
                    let dot = if online { t.accent } else { t.ink2 };
                    let (rect, _) = ui.allocate_exact_size(Vec2::new(8.0, 8.0), Sense::hover());
                    ui.painter().circle_filled(rect.center(), 4.0, dot);
                    if online && ui.button(RichText::new("设备镜像").size(12.0)).clicked() {
                        self.start_mirror();
                    }
                });
            });
        ui.add_space(8.0);
    }

    fn pinned_row(&mut self, ui: &mut egui::Ui) {
        let pinned: Vec<String> = self
            .apps
            .apps
            .iter()
            .filter(|e| e.pinned)
            .map(|e| e.package.clone())
            .collect();
        if pinned.is_empty() {
            return;
        }
        let t = self.tokens;
        egui::Frame::NONE
            .fill(t.card_fill)
            .stroke(egui::Stroke::new(1.0_f32, t.card_border))
            .corner_radius(rounding::CARD)
            .inner_margin(egui::Margin::same(8))
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    let entries: Vec<AppEntry> = self
                        .apps
                        .apps
                        .iter()
                        .filter(|e| e.pinned)
                        .cloned()
                        .collect();
                    for entry in entries {
                        self.tile(ui, &entry, 44.0);
                    }
                });
            });
        ui.add_space(8.0);
    }

    fn home_page(&mut self, ui: &mut egui::Ui) {
        let t = self.tokens;
        self.device_card(ui);

        let search = egui::TextEdit::singleline(&mut self.search)
            .hint_text("搜索（拼音首字母 / 标签 / 包名）")
            .desired_width(ui.available_width());
        ui.add(search);
        ui.add_space(8.0);

        self.pinned_row(ui);

        let entries: Vec<AppEntry> = self
            .apps
            .search(&self.search)
            .into_iter()
            .cloned()
            .collect();
        if entries.is_empty() {
            ui.label(
                RichText::new(if self.search.is_empty() {
                    "未发现已装应用"
                } else {
                    "无匹配应用"
                })
                .size(13.0)
                .color(t.ink2),
            );
        }
        let gap = 8.0;
        egui::ScrollArea::vertical().show(ui, |ui| {
            ui.with_layout(
                egui::Layout::left_to_right(egui::Align::TOP).with_main_wrap(true),
                |ui| {
                    for (i, entry) in entries.iter().enumerate() {
                        if i > 0 {
                            ui.add_space(gap);
                        }
                        self.tile(ui, entry, 92.0);
                    }
                },
            );
        });

        self.running_card(ui);
    }

    fn tile(&mut self, ui: &mut egui::Ui, entry: &AppEntry, size: f32) {
        let t = self.tokens;
        let label_size = if size >= 92.0 { 13.0 } else { 10.0 };
        let icon = if size >= 92.0 { 60.0 } else { 28.0 };
        let (rect, response) = ui.allocate_exact_size(Vec2::new(size, size + 20.0), Sense::click());
        let fill = if response.hovered() {
            t.card_hover()
        } else {
            t.card_fill
        };
        ui.painter()
            .rect_filled(rect, egui::CornerRadius::same(rounding::CARD as u8), fill);
        if response.clicked() {
            self.launch(&entry.package, None);
        }
        response.context_menu(|ui| {
            self.tile_menu(ui, entry);
        });

        let icon_rect = egui::Rect::from_center_size(
            egui::pos2(rect.center().x, rect.top() + 8.0 + icon / 2.0),
            Vec2::splat(icon),
        );
        let mut painted = false;
        if let Some(path) = &entry.icon {
            if path.exists() {
                let uri = format!("file://{}", path.display());
                let loaded = ui
                    .ctx()
                    .try_load_image(&uri, egui::load::SizeHint::Width(120));
                if matches!(loaded, Ok(egui::load::ImagePoll::Ready { .. })) {
                    ui.put(
                        icon_rect,
                        egui::Image::from_uri(uri)
                            .fit_to_exact_size(Vec2::splat(icon))
                            .corner_radius(rounding::ICON),
                    );
                    painted = true;
                }
            }
        }
        if !painted {
            let preset = catalog_by_package(&entry.package);
            let color = preset
                .map(|p| crate::theme::hex(p.color))
                .unwrap_or_else(|| t.accent);
            ui.painter().rect_filled(
                icon_rect,
                egui::CornerRadius::same(rounding::ICON as u8),
                color,
            );
            let glyph = preset.map(|p| p.glyph.to_string()).unwrap_or_else(|| {
                entry
                    .label
                    .chars()
                    .next()
                    .map(String::from)
                    .unwrap_or_default()
            });
            let ink = preset
                .map(|p| {
                    if p.glyph_ink {
                        crate::theme::hex("#1D1D1F")
                    } else {
                        Color32::WHITE
                    }
                })
                .unwrap_or(Color32::WHITE);
            ui.painter().text(
                icon_rect.center(),
                egui::Align2::CENTER_CENTER,
                &glyph,
                egui::FontId::proportional(if size >= 92.0 { 24.0 } else { 14.0 }),
                ink,
            );
        }
        ui.painter().text(
            egui::pos2(rect.center().x, rect.bottom() - 10.0),
            egui::Align2::CENTER_CENTER,
            &entry.label,
            egui::FontId::proportional(label_size),
            t.ink,
        );
    }

    fn tile_menu(&mut self, ui: &mut egui::Ui, entry: &AppEntry) {
        let package = entry.package.clone();
        let label = session_label(&package);
        if ui.button(format!("打开 {label}")).clicked() {
            self.launch(&package, None);
            ui.close_menu();
        }
        let pin_text = if self.pinned.contains_key(&package) {
            "取消置顶"
        } else {
            "置顶"
        };
        if ui.button(pin_text).clicked() {
            self.toggle_pin(&package);
            ui.close_menu();
        }
        let portrait = self.sessions.portrait_of(&package);
        if ui
            .button(if portrait {
                "改为横屏启动"
            } else {
                "改为竖屏启动"
            })
            .clicked()
        {
            self.toggle_portrait(&package);
            ui.close_menu();
        }
        ui.menu_button("按比例打开", |ui| {
            self.aspect_menu(ui, &package, false);
        });
        ui.menu_button("固定比例", |ui| {
            self.aspect_menu(ui, &package, true);
        });
        ui.menu_button("窗口栏", |ui| {
            let bars = self.bar_prefs.get(&package).cloned().unwrap_or_default();
            for mode in BAR_CHOICES {
                let top_mark = bars.top == Some(mode);
                if ui
                    .button(format!("上巴 {}{}", mode, if top_mark { " ✓" } else { "" }))
                    .clicked()
                {
                    self.set_bar(&package, true, Some(mode));
                    ui.close_menu();
                }
            }
            ui.separator();
            for mode in BAR_CHOICES {
                let bottom_mark = bars.bottom == Some(mode);
                if ui
                    .button(format!(
                        "下巴 {}{}",
                        mode,
                        if bottom_mark { " ✓" } else { "" }
                    ))
                    .clicked()
                {
                    self.set_bar(&package, false, Some(mode));
                    ui.close_menu();
                }
            }
        });
        let exclusive = self.audio_prefs.get(&package).copied().unwrap_or(false);
        if ui
            .button(format!("音频独占 {}", if exclusive { "✓" } else { "" }))
            .clicked()
        {
            self.toggle_audio_exclusive(&package);
            ui.close_menu();
        }
        let keep_vd = self.behavior_prefs.get(&package).copied().unwrap_or(false);
        if ui
            .button(format!("断开保留画面 {}", if keep_vd { "✓" } else { "" }))
            .clicked()
        {
            self.toggle_keep_vd(&package);
            ui.close_menu();
        }
        ui.menu_button("DPI", |ui| {
            if ui.button("跟随设置").clicked() {
                self.set_density(&package, None);
                ui.close_menu();
            }
            for dpi in [160i64, 240, 320, 356, 480] {
                let mark = self.density_prefs.get(&package) == Some(&dpi);
                if ui
                    .button(format!("{dpi}{}", if mark { " ✓" } else { "" }))
                    .clicked()
                {
                    self.set_density(&package, Some(dpi));
                    ui.close_menu();
                }
            }
        });
        ui.menu_button("渲染倍率", |ui| {
            if ui.button("跟随设置").clicked() {
                self.set_scale(&package, None);
                ui.close_menu();
            }
            for scale in [1.5f64, 2.0, 2.5, 3.0] {
                let mark = self.scale_prefs.get(&package) == Some(&scale);
                if ui
                    .button(format!("÷{scale}{}", if mark { " ✓" } else { "" }))
                    .clicked()
                {
                    self.set_scale(&package, Some(scale));
                    ui.close_menu();
                }
            }
        });
    }

    fn aspect_menu(&mut self, ui: &mut egui::Ui, package: &str, remember: bool) {
        let presets = aspect_presets().to_vec();
        let (landscape, portrait): (Vec<&AspectPreset>, Vec<&AspectPreset>) =
            presets.iter().partition(|p| p.landscape);
        for group in [landscape, portrait] {
            for preset in group {
                let button = if remember {
                    let current = self.display_prefs.get(package);
                    let mark = matches!(current,
                        Some(DisplayChoice::Fixed { aspect }) if *aspect == preset.id);
                    format!("{}{}", preset.id, if mark { " ✓" } else { "" })
                } else {
                    preset.id.clone()
                };
                if ui.button(button).clicked() {
                    if remember {
                        self.set_display_fixed(package, &preset.id);
                    } else {
                        self.launch(package, Some((preset.width, preset.height)));
                    }
                    ui.close_menu();
                }
            }
        }
        for (id, text) in [(BODY_LANDSCAPE_ID, "机身横"), (BODY_PORTRAIT_ID, "机身竖")] {
            if ui.button(text).clicked() {
                if let Some(body) = self.body_preset() {
                    let preset = if id == BODY_LANDSCAPE_ID {
                        body
                    } else {
                        transposed(&body)
                    };
                    if remember {
                        self.set_display_fixed(package, id);
                    } else {
                        self.launch(package, Some((preset.width, preset.height)));
                    }
                } else {
                    self.toast_now("机身比例需连接设备后使用");
                }
                ui.close_menu();
            }
        }
        if remember && ui.button("自适应窗口").clicked() {
            self.set_display_flex(package);
            ui.close_menu();
        }
    }

    fn running_card(&mut self, ui: &mut egui::Ui) {
        self.sessions.reap();
        let running = self.sessions.running();
        if running.is_empty() {
            return;
        }
        let t = self.tokens;
        egui::Frame::NONE
            .fill(t.card_fill)
            .stroke(egui::Stroke::new(1.0_f32, t.card_border))
            .corner_radius(rounding::CARD)
            .inner_margin(egui::Margin::same(10))
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                for (key, label) in running {
                    ui.horizontal(|ui| {
                        let dot_rect = ui
                            .allocate_exact_size(Vec2::new(8.0, 8.0), Sense::hover())
                            .0;
                        ui.painter().circle_filled(dot_rect.center(), 3.0, t.accent);
                        ui.label(RichText::new(&label).size(13.0).color(t.ink));
                        if ui.button(RichText::new("✕").size(12.0)).clicked() {
                            self.sessions.stop(&key);
                            self.toast_now(format!("已关闭 {label}"));
                        }
                    });
                }
            });
    }

    fn toast(&mut self, ui: &mut egui::Ui, msg: &str) {
        let t = self.tokens;
        egui::Frame::NONE
            .fill(t.pill_fill)
            .corner_radius(14.0)
            .inner_margin(egui::Margin::symmetric(12, 6))
            .show(ui, |ui| {
                ui.label(RichText::new(msg).size(13.0).color(t.ink));
            });
    }

    // -------------------------------------------------------------- 设置页

    fn settings_page(&mut self, ui: &mut egui::Ui) {
        if let Some(msg) = self.settings.flash.clone() {
            self.toast(ui, &msg);
            ui.add_space(4.0);
            self.settings.dismiss_flash();
        }
        egui::ScrollArea::vertical().show(ui, |ui| {
            self.settings_group(ui, "投屏质量", |ui, app| {
                let t = app.tokens;
                let codec_now = app.settings.draft.video_codec.clone();
                ui.label(RichText::new("视频编码").size(13.0).color(t.ink2));
                let codec = combo(ui, "duo-codec", &codec_now, &CODEC_CHOICES, |v| {
                    v.to_string()
                });
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
            self.settings_group(ui, "音频", |ui, app| {
                let policy_now = app.settings.draft.audio_policy.clone();
                let policy = combo(ui, "duo-audio", &policy_now, &AUDIO_CHOICES, audio_label);
                if policy != policy_now {
                    app.settings.set_audio_policy(&policy);
                }
                ui.add_space(6.0);
                let serial = app.serial();
                let mut volume = app.media_volume;
                ui.add_enabled(
                    serial.is_some(),
                    egui::Slider::new(&mut volume, 0..=15).text("媒体音量"),
                );
                if volume != app.media_volume {
                    app.media_volume = volume;
                    if let (Some(binary), Some(serial)) = (app.duo_core.clone(), serial) {
                        let adb = app.adb.clone();
                        app.volume_bg = Some(Background::spawn(move || {
                            let _ = backend::set_volume(
                                &binary.display().to_string(),
                                &adb,
                                &serial,
                                volume,
                            );
                        }));
                    }
                }
            });
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
            self.settings_group(ui, "外观", |ui, app| {
                let mut glass = app.settings.draft.glass_enabled;
                if ui.checkbox(&mut glass, "玻璃（系统 blur）").changed() {
                    app.settings.set_glass(glass);
                }
                let theme_now = app.settings.draft.theme.clone();
                let theme = combo(ui, "duo-theme", &theme_now, &THEME_CHOICES, |v| {
                    v.to_string()
                });
                if theme != theme_now {
                    app.settings.set_theme(&theme);
                }
                ui.add_space(4.0);
                let corner_now = app.settings.draft.corner_mode.clone();
                let corner = combo(ui, "duo-corner", &corner_now, &CORNER_CHOICES, |v| {
                    v.to_string()
                });
                if corner != corner_now {
                    app.settings.set_corner_mode(&corner);
                }
                if app.settings.draft.corner_mode == "g2" {
                    let size_now = app.settings.draft.corner_size_dip;
                    let mut size = size_now;
                    ui.add(egui::Slider::new(&mut size, 0..=96).text("圆角 DIP"));
                    if size != size_now {
                        app.settings.set_corner_size(size);
                    }
                }
            });
            self.settings_group(ui, "显示", |ui, app| {
                let dpi_follow = app.settings.draft.dpi.is_none();
                if ui.checkbox(&mut { dpi_follow }, "密度跟随设备").changed() {
                    if dpi_follow {
                        app.settings.set_dpi(None);
                    } else {
                        app.settings.set_dpi(Some(160));
                    }
                }
                if let Some(dpi) = app.settings.draft.dpi {
                    let mut value = dpi;
                    ui.add(egui::Slider::new(&mut value, 120..=640).text("密度 dpi"));
                    if value != dpi {
                        app.settings.set_dpi(Some(value));
                    }
                }
                let scale_now = app.settings.draft.render_scale;
                let mut scale = scale_now;
                ui.add(egui::Slider::new(&mut scale, 1.0..=3.0).text("渲染倍率"));
                if scale != scale_now {
                    app.settings.set_render_scale(scale);
                }
                let mut screen_off = app.settings.draft.turn_screen_off;
                if ui.checkbox(&mut screen_off, "镜像时关闭设备屏幕").changed() {
                    app.settings.set_turn_screen_off(screen_off);
                }
            });
            self.settings_group(ui, "工具路径", |ui, app| {
                let mut scrcpy = app.settings.draft.scrcpy_path.clone();
                ui.label(RichText::new("scrcpy").size(13.0));
                let edit = egui::TextEdit::singleline(&mut scrcpy)
                    .hint_text("PATH 探测")
                    .desired_width(ui.available_width());
                if ui.add(edit).changed() {
                    app.settings.set_scrcpy_path(&scrcpy);
                }
                let mut adb = app.settings.draft.adb_path.clone();
                ui.label(RichText::new("adb").size(13.0));
                let edit = egui::TextEdit::singleline(&mut adb)
                    .hint_text("PATH 探测")
                    .desired_width(ui.available_width());
                if ui.add(edit).changed() {
                    app.settings.set_adb_path(&adb);
                }
            });
            let t = self.tokens;
            let label = if self.settings.dirty {
                "保存"
            } else {
                "已保存"
            };
            let button =
                egui::Button::new(RichText::new(label).size(13.0)).fill(if self.settings.dirty {
                    t.accent
                } else {
                    t.segment_fill
                });
            if ui.add_sized([ui.available_width(), 28.0], button).clicked() {
                self.settings.save();
                self.adb = resolve_adb_path(&self.settings.draft, None, "adb");
                self.duo_core = backend::find_duo_core();
                self.ensure_watch();
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
                ui.label(RichText::new(title).size(13.0).strong().color(t.ink));
                ui.add_space(4.0);
                body(ui, self);
            });
        ui.add_space(8.0);
    }

    /// 后台结果收编（每帧轮询，非阻塞）。
    fn pump_background(&mut self) {
        if let Some(bg) = self.installed_bg.take() {
            match bg.take() {
                Some(result) => self.adopt_installed(result),
                None => self.installed_bg = Some(bg),
            }
        }
        if let Some(bg) = self.sweep_bg.take() {
            if let Some(result) = bg.take() {
                match result {
                    Ok(sweep) => {
                        if sweep.rendered {
                            let patched = self.apps.apply_sweep(&sweep.labels);
                            if !patched.is_empty() {
                                self.toast_now("应用图标已更新");
                            }
                        } else {
                            self.toast_now("图标渲染不可用，沿用预设图标");
                        }
                    }
                    Err(err) => self.toast_now(format!("图标渲染失败：{err}")),
                }
            } else {
                self.sweep_bg = Some(bg);
            }
        }
        if let Some(bg) = self.move_bg.take() {
            match bg.take() {
                Some(result) => {
                    let label = session_label(&result.package);
                    if result.ok {
                        self.toast_now(format!("已在虚拟屏打开 {label}"));
                    } else {
                        self.toast_now(format!("打开失败：{label}（{}）", result.detail));
                    }
                }
                None => self.move_bg = Some(bg),
            }
        }
        if let Some(bg) = self.volume_bg.take() {
            if bg.take().is_none() {
                self.volume_bg = Some(bg);
            }
        }
        // 设备晚插 → 重跑已装探测（仅新增触发，掉线不动）。
        if let Some(watch) = &self.watch {
            let online: Vec<String> = watch
                .states()
                .into_iter()
                .filter(|(_, state)| state == "device")
                .map(|(serial, _)| serial)
                .collect();
            if let Some(known) = &self.known_online {
                if online.len() > known.len() && self.installed_bg.is_none() {
                    self.refresh_installed();
                }
            }
            self.known_online = Some(online);
        }
    }
}

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
        ui.label(RichText::new(label).size(13.0));
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
        let kind = ThemeKind::from_settings(&self.settings.draft.theme);
        if self.tokens.kind != kind {
            self.tokens = Tokens::of(kind);
        }
        self.sync_visuals(ctx);
        self.pump_background();

        egui::CentralPanel::default()
            .frame(egui::Frame::NONE.inner_margin(egui::Margin::symmetric(PAGE_MARGIN as i8, 16)))
            .show(ctx, |ui| {
                self.top_capsule(ui);
                match self.page {
                    Page::Home => self.home_page(ui),
                    Page::Settings => self.settings_page(ui),
                }
            });
        // Toast 驻留 3s 自清。
        if let Some((_, at)) = &self.toast {
            if at.elapsed() > Duration::from_secs(3) {
                self.toast = None;
            }
        }
        if let Some((text, _)) = self.toast.clone() {
            egui::Area::new(egui::Id::new("duo-toast"))
                .anchor(egui::Align2::CENTER_BOTTOM, egui::vec2(0.0, -16.0))
                .show(ctx, |ui| {
                    let t = self.tokens;
                    egui::Frame::NONE
                        .fill(t.pill_fill)
                        .corner_radius(14.0)
                        .inner_margin(egui::Margin::symmetric(14, 7))
                        .show(ui, |ui| {
                            ui.label(RichText::new(&text).size(13.0).color(t.ink));
                        });
                });
        }
        ctx.request_repaint_after(Duration::from_millis(500));
    }
}

impl Drop for PanelApp {
    fn drop(&mut self) {
        self.sessions.shutdown();
    }
}

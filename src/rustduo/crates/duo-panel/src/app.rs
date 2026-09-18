//! 面板应用（egui）：渲染层。UI 逻辑住在 model / sessions / prefs /
//! settings_view / backend（可测纯逻辑或进程封装），本文件只做
//! immediate-mode 绘制与事件转发。结构对齐 DESIGN.md §3：顶栏胶囊 →
//! 首页（设备卡 + 固定卡 + 搜索 + 网格 + 运行卡）或设置页 → Toast。

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use eframe::egui;
use eframe::egui::{Sense, TextureHandle, Vec2};

use crate::sessions;
use duo_core::aspects::{
    body_aspect_from_wm_size, preset_by_id, transposed, AspectPreset, BODY_LANDSCAPE_ID,
    BODY_PORTRAIT_ID,
};
use duo_core::settings::resolve_adb_path;

use crate::backend::{self, Background, DeviceWatch};
use crate::model::{AppEntry, AppsModel};
use crate::prefs::{
    load_audio_prefs, load_bar_prefs, load_behavior_prefs, load_density_prefs, load_display_prefs,
    load_pinned_prefs, load_scale_prefs, save_audio_prefs, save_bar_prefs, save_behavior_prefs,
    save_density_prefs, save_display_prefs, save_pinned_prefs, save_scale_prefs, BarChoice,
    DisplayChoice,
};
use crate::sessions::{panel_log_path, session_label, Sessions, MIRROR_KEY};
use crate::settings_view::SettingsPageModel;
use crate::theme::{ThemeKind, Tokens};
use crate::winproc;

/// 两页常驻（胶囊即导航）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Page {
    Home,
    Settings,
}

/// 「固定比例」记忆的校验与文案决策（pyduo setDisplayFixed 同构）：
/// Ok(比例名) = 可落库；Err = toast 文案（不落库）。
fn resolve_fixed_aspect(
    aspect: &str,
    body: Option<&duo_core::aspects::AspectPreset>,
) -> Result<String, &'static str> {
    if duo_core::aspects::preset_by_id(aspect).is_some() {
        Ok(aspect.to_string())
    } else if aspect == BODY_LANDSCAPE_ID || aspect == BODY_PORTRAIT_ID {
        if body.is_some() {
            Ok("机身".to_string())
        } else {
            Err("机身比例需连接设备后使用")
        }
    } else {
        Err("未知比例")
    }
}

/// 窗口栏记忆合并（pyduo setAppBar 同构）：两键全 None → 整条退场。
fn bar_entry_after(
    existing: Option<crate::prefs::BarChoice>,
    which: bool,
    mode: Option<&'static str>,
) -> Option<crate::prefs::BarChoice> {
    let mut choice = existing.unwrap_or_default();
    if which {
        choice.top = mode;
    } else {
        choice.bottom = mode;
    }
    (choice.top.is_some() || choice.bottom.is_some()).then_some(choice)
}

/// 已装探测后台任务结果（已装包名全集 + duo-core apps 行）。
type InstalledResult = Result<(Vec<String>, Vec<backend::AppRow>), String>;

/// 菜单毛玻璃截图请求的归属标签（与 --shot 出图泵的回包区分，防互吞）。
const GLASS_SHOT_TAG: &str = "duo-menu-glass-shot";
/// --shot 出图泵的截图归属标签。
const SHOT_TAG: &str = "duo-shot";

pub(crate) const MENU_WIDTH: f32 = 128.0;
pub(crate) const MENU_MARGIN: f32 = 4.0;
pub(crate) const MENU_INNER_WIDTH: f32 = MENU_WIDTH - 2.0 * MENU_MARGIN;

fn user_data_eq(ud: &egui::UserData, tag: &str) -> bool {
    ud.data
        .as_ref()
        .and_then(|a| a.downcast_ref::<String>())
        .is_some_and(|s| s == tag)
}

/// 引擎路径检测结果：。*/
pub(crate) type ProbeResult = Result<(String, bool, String), String>;

/// 移动应用到虚拟屏的后台任务结果。
struct MoveResult {
    package: String,
    ok: bool,
    detail: String,
}

/// 菜单毛玻璃状态机（生命周期 = 一次菜单打开；配方见 docs/ui/glass-recipe.md
/// §8）：跳变帧跳画菜单并发截图命令 → 下一帧
/// Event::Screenshot 落地 → 裁剪/模糊/贴图 → 菜单闭包开头垫贴图。
pub(crate) struct MenuGlass {
    /// 截图命令已发出（一次打开只发一次）。
    pub(crate) requested: bool,
    /// 菜单外沿矩形（逻辑 px，frame 边距含）。
    pub(crate) menu_rect: Option<egui::Rect>,
    /// 全窗设备像素快照 + 拍摄时 pixels_per_point。
    pub(crate) snapshot: Option<std::sync::Arc<egui::ColorImage>>,
    pub(crate) snapshot_ppp: f32,
    pub(crate) main_tex: Option<TextureHandle>,
    /// 主贴图的屏幕矩形（设备像素网格对齐后）。
    pub(crate) main_rect: Option<egui::Rect>,
    pub(crate) sub_tex: Option<TextureHandle>,
    pub(crate) sub_rect: Option<egui::Rect>,
    /// 贴图屏幕覆盖矩形（= 裁剪区，非菜单矩形；主侧为 main_rect）。
    pub(crate) sub_draw: Option<egui::Rect>,
    /// 贴图对应的拍摄矩形（区域被屏幕钳位后矩形会变，须重拍）。
    pub(crate) sub_tex_rect: Option<egui::Rect>,
    pub(crate) main_shape: Option<egui::layers::ShapeIdx>,
    pub(crate) sub_shape: Option<egui::layers::ShapeIdx>,
}

impl Default for MenuGlass {
    fn default() -> Self {
        Self {
            requested: false,
            menu_rect: None,
            snapshot: None,
            snapshot_ppp: 1.0,
            main_tex: None,
            main_rect: None,
            sub_tex: None,
            sub_rect: None,
            sub_draw: None,
            sub_tex_rect: None,
            main_shape: None,
            sub_shape: None,
        }
    }
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
    pub(crate) toast: Option<(String, Instant)>,
    pub(crate) media_volume: i64,
    /// 网格滚动偏移（像素；QML interactive 网格的 egui 对应物）。
    pub(crate) grid_scroll: f32,
    /// 设置页滚动偏移（像素）。
    pub(crate) settings_scroll: f32,
    /// 引擎路径检测后台任务：(tool, ok, detail)。
    pub(crate) probe_bg: Option<Background<ProbeResult>>,
    /// 检测结果瞬时胶囊：(tool, 文案, 落地时刻)；2.5s 淡出（QML 同款）。
    pub(crate) probe_pill: Option<(String, String, Instant)>,
    pub(crate) volume_pending: Option<(i64, Instant)>,
    /// 出图模式：(path, 已渲染帧数, 启动时刻)。帧数 ≥40 且满 1.6s（桩
    /// duo-core 的 watch/apps 首行落位）才请求截图，收到即存盘退出。
    pub(crate) shot: Option<(String, u32, Instant)>,
    /// 出图泵自己的截图请求已发出（毛玻璃也发截图命令，须区分归属）。
    pub(crate) shot_capture: bool,
    pub(crate) shot_capture_frame: u32,
    #[allow(dead_code)]
    shot_clicked: bool,
    #[allow(dead_code)]
    shot_sub_moved: bool,
    /// DUO_SKIP_SWEEP=1：零子进程出图（不探测/不 sweep，图标吃缓存）。
    skip_sweep: bool,
    /// Wayland 出图：无 XTEST/点击注入，改由 harness 托管菜单 Area。
    shot_use_harness: bool,
    /// DUO_SHOT_MENU=tile|tile-sub|mirror：--shot 模式注入合成右键自动
    /// 开菜单（tile-sub 再悬停「固定比例」展开二级）。对拍回路常备开关
    /// （菜单只能走 Windows exe 验：WSLg 下 popup 行为不同）。
    pub(crate) shot_menu: Option<String>,
    /// 菜单毛玻璃状态（None = 菜单关/玻璃关）。
    pub(crate) menu_glass: Option<MenuGlass>,
    /// 菜单外沿矩形记忆（换目标时重裁切）。
    pub(crate) menu_rect_hint: Option<egui::Rect>,
    /// 菜单连续"未开"帧计数（宽容清空去抖，见 pump_menu_glass）。
    pub(crate) menu_glass_closed_frames: u32,
    /// 全窗干净背景快照（菜单未开时捕获，供菜单打开时即时切图）。
    pub(crate) menu_snapshot: Option<std::sync::Arc<egui::ColorImage>>,
    pub(crate) menu_snapshot_ppp: f32,
    pub(crate) last_screen_size: Option<egui::Vec2>,
    /// 图标贴图缓存：(路径, 显示尺寸)。None = 加载失败不重试。
    pub(crate) icon_tex: RefCell<BTreeMap<(PathBuf, u32), Option<TextureHandle>>>,
}

impl PanelApp {
    pub fn new(cc: &eframe::CreationContext) -> Self {
        egui_extras::install_image_loaders(&cc.egui_ctx);
        crate::fonts::install_fonts(&cc.egui_ctx);
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
            volume_pending: None,
            shot: None,
            search: String::new(),
            toast: None,
            media_volume: -1,
            grid_scroll: 0.0,
            settings_scroll: 0.0,
            probe_bg: None,
            probe_pill: None,
            shot_capture: false,
            shot_capture_frame: 0,
            shot_clicked: false,
            shot_sub_moved: false,
            shot_use_harness: false,
            skip_sweep: std::env::var("DUO_SKIP_SWEEP").is_ok_and(|v| v == "1"),
            shot_menu: std::env::var("DUO_SHOT_MENU").ok(),
            menu_glass: None,
            menu_rect_hint: None,
            menu_glass_closed_frames: 0,
            menu_snapshot: None,
            menu_snapshot_ppp: 1.0,
            last_screen_size: None,
            icon_tex: RefCell::new(BTreeMap::new()),
        };
        // QML _status_text 初始「就绪」→ 启动即挂状态 toast
        app.toast_now("就绪");
        if app.skip_sweep {
            // 出图回路：零子进程、不受 adb 抖动影响
            app.load_apps_from_icon_cache();
        } else {
            app.ensure_watch();
            app.refresh_installed();
        }
        app
    }

    /// icons 缓存即已装集合（r20 优先，裸 png 兜底），标签退包名末段。
    fn load_apps_from_icon_cache(&mut self) {
        let dir = duo_core::paths::icons_dir(None);
        let Ok(files) = std::fs::read_dir(&dir) else {
            return;
        };
        let mut icons: BTreeMap<String, Option<PathBuf>> = BTreeMap::new();
        for file in files.flatten() {
            let path = file.path();
            if !path
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("png"))
            {
                continue;
            }
            let Some(name) = path.file_name().map(|s| s.to_string_lossy().into_owned()) else {
                continue;
            };
            let (package, is_r20) = match name.strip_suffix(duo_core::sweep::ICON_CACHE_SUFFIX) {
                Some(pkg) => (pkg.to_string(), true),
                None => match name.strip_suffix(".png") {
                    Some(pkg) => (pkg.to_string(), false),
                    None => continue,
                },
            };
            let slot = icons.entry(package).or_default();
            if is_r20 || slot.is_none() {
                *slot = Some(path);
            }
        }
        let mut entries: Vec<AppEntry> = icons
            .into_iter()
            .map(|(package, icon)| {
                let label = crate::sessions::package_to_label(&package);
                let mut entry = AppEntry::fresh(&package, &label, false);
                entry.icon = icon.or(entry.icon);
                entry
            })
            .collect();
        entries.sort_by(|a, b| (&a.key, &a.label, &a.package).cmp(&(&b.key, &b.label, &b.package)));
        self.apps.apps = entries;
        // 出图种子：无图标缓存的机器（WSL/新机）也能拍磁贴菜单——
        // DUO_SHOT_SEED_APPS=N 视目录前 N 项为已装（仅出图回路，不影响
        // 真实启动路径）。
        if self.apps.apps.is_empty() {
            let seed = std::env::var("DUO_SHOT_SEED_APPS")
                .ok()
                .and_then(|v| v.parse::<usize>().ok());
            if let Some(n) = seed {
                let seeded: Vec<String> = duo_core::catalog::APP_CATALOG
                    .iter()
                    .take(n)
                    .map(|p| p.package.to_string())
                    .collect();
                self.apps.rebuild(&seeded, &self.pinned);
            }
        }
    }

    fn ensure_watch(&mut self) {
        if self.watch.is_some() && self.watch_adb == self.adb {
            return;
        }
        self.watch = Some(DeviceWatch::start(&self.adb, 2.0));
        self.watch_adb = self.adb.clone();
    }

    /// 设置页离页守卫（2026-09-18 拍板：返回首页即自动保存，保存钮已删）：
    /// 脏才写盘；校验失败留在设置页看红字，成功则刷新 adb 守护并回首页。
    fn save_settings_and_leave(&mut self) {
        self.settings.save_if_dirty();
        if !self.settings.dirty {
            let adb = resolve_adb_path(&self.settings.draft, None, "adb");
            if adb != self.adb {
                self.adb = adb;
                self.ensure_watch();
            }
            self.settings.dismiss_flash();
            self.page = Page::Home;
        }
    }

    // ---- home.rs 桥接（数据合同，全部薄转发） ----

    pub(crate) fn has_pinned(&self) -> bool {
        self.apps.pinned().iter().any(|e| e.installed)
    }

    pub(crate) fn pinned_entries(&self) -> Vec<AppEntry> {
        self.apps
            .pinned()
            .into_iter()
            .filter(|e| e.installed)
            .cloned()
            .collect()
    }

    pub(crate) fn grid_entries(&self) -> Vec<AppEntry> {
        self.apps
            .search(&self.search)
            .into_iter()
            .cloned()
            .collect()
    }

    /// QML device/fallbackDevice 语义：首个在线设备，回退首个任意设备。
    pub(crate) fn device_summary(&self) -> (String, Option<String>, usize, bool) {
        let Some(watch) = &self.watch else {
            return ("设备监控未启动（缺 duo-core）".into(), None, 0, false);
        };
        let states = watch.states();
        if states.is_empty() {
            return ("未连接设备".into(), None, 0, false);
        }
        let online: Vec<&String> = states
            .iter()
            .filter(|(_, st)| st.as_str() == "device")
            .map(|(k, _)| k)
            .collect();
        let (serial, state) = if let Some(first) = online.first() {
            (*first, "在线")
        } else {
            states
                .iter()
                .next()
                .map(|(k, v)| (k, Self::state_text(v)))
                .unwrap()
        };
        (
            state.to_string(),
            Some(serial.to_string()),
            online.len(),
            !states.is_empty(),
        )
    }

    pub(crate) fn running_chips(&self) -> Vec<(String, String, bool)> {
        self.sessions
            .running()
            .into_iter()
            .map(|(key, label)| {
                let clickable = key != sessions::MIRROR_KEY;
                (key, label, clickable)
            })
            .collect()
    }

    pub(crate) fn stop_session(&mut self, key: &str) {
        self.sessions.stop(key);
    }

    pub(crate) fn volume_dragged(&mut self, index: i64) {
        self.media_volume = index;
        self.volume_pending = Some((index, Instant::now()));
    }

    /// 镜像卡右键菜单（Main.qml mirrorMenu：打开投屏 / 关屏 / 默认窗口栏）。
    /// 菜单行（QML MenuRow/MenuCheckRow：x4 w-8 h32 r10、文字 13px 左
    /// 12 / 勾选行文字 x24 + 4px accent 点 x14、hover 洗色走 skin_menus 的
    /// weak_bg_fill——不留显式 fill，hover 才能上洗色）。返回 clicked。
    fn menu_item(&self, ui: &mut egui::Ui, label: &str, marked: Option<bool>) -> bool {
        let t = self.tokens;
        menu_row_style(ui, &t);
        let pad_x = if marked.is_some() { 20.0 } else { 8.0 };
        let prev_pad = ui.spacing().button_padding;
        ui.style_mut().spacing.button_padding = egui::vec2(pad_x, 0.0);
        let text_color = if matches!(t.kind, ThemeKind::Dark) {
            egui::Color32::WHITE
        } else {
            t.ink
        };
        let btn = egui::Button::new(egui::RichText::new(label).size(13.0).color(text_color))
            .min_size(egui::vec2(MENU_INNER_WIDTH, 32.0))
            .stroke(egui::Stroke::NONE);
        let resp = ui.add(btn);
        ui.style_mut().spacing.button_padding = prev_pad;
        if marked == Some(true) {
            let dot = egui::pos2(resp.rect.min.x + 9.0, resp.rect.center().y);
            ui.painter().circle_filled(dot, 2.0, t.accent);
        }
        resp.clicked()
    }

    fn menu_aspect_item(
        &self,
        ui: &mut egui::Ui,
        label: &str,
        marked: bool,
        gw: f32,
        gh: f32,
    ) -> bool {
        let t = self.tokens;
        menu_row_style(ui, &t);
        ui.style_mut().spacing.interact_size.y = 28.0;
        let prev_pad = ui.spacing().button_padding;
        ui.style_mut().spacing.button_padding = egui::vec2(20.0, 0.0);
        let is_dark = matches!(t.kind, ThemeKind::Dark);
        let text_color = if is_dark { egui::Color32::WHITE } else { t.ink };
        let btn = egui::Button::new(egui::RichText::new(label).size(13.0).color(text_color))
            .min_size(egui::vec2(MENU_INNER_WIDTH, 28.0))
            .stroke(egui::Stroke::NONE);
        let resp = ui.add(btn);
        ui.style_mut().spacing.button_padding = prev_pad;
        if marked {
            let dot = egui::pos2(resp.rect.min.x + 9.0, resp.rect.center().y);
            ui.painter().circle_filled(dot, 2.0, t.accent);
        }
        let cy = resp.rect.center().y;
        let glyph = egui::Rect::from_min_max(
            egui::pos2(resp.rect.right() - 8.0 - gw, cy - gh / 2.0),
            egui::pos2(resp.rect.right() - 8.0, cy + gh / 2.0),
        );
        let glyph_stroke = if is_dark {
            egui::Color32::from_rgba_unmultiplied(255, 255, 255, 200)
        } else {
            t.ink2
        };
        ui.painter().rect_stroke(
            glyph,
            egui::CornerRadius::same(2),
            egui::Stroke::new(1.5_f32, glyph_stroke),
            egui::StrokeKind::Middle,
        );
        resp.clicked()
    }

    fn menu_sub_button(
        &mut self,
        ui: &mut egui::Ui,
        label: &str,
        add_contents: impl FnOnce(&mut Self, &mut egui::Ui),
    ) {
        let t = self.tokens;
        menu_row_style(ui, &t);
        let prev_pad = ui.spacing().button_padding;
        ui.style_mut().spacing.button_padding = egui::vec2(20.0, 0.0);
        let text_color = if matches!(t.kind, ThemeKind::Dark) {
            egui::Color32::WHITE
        } else {
            t.ink
        };
        ui.menu_button(
            egui::RichText::new(label).size(13.0).color(text_color),
            |ui| {
                ui.set_width(MENU_INNER_WIDTH);
                menu_row_style(ui, &t);
                ui.style_mut().spacing.button_padding = egui::vec2(20.0, 0.0);
                self.glass_underlay(ui, true);
                add_contents(self, ui);
                self.glass_record_sub(ui);
            },
        );
        ui.style_mut().spacing.button_padding = prev_pad;
    }

    pub(crate) fn mirror_menu(&mut self, ui: &mut egui::Ui) {
        if self.menu_item(ui, "打开投屏", None) {
            self.start_mirror();
            ui.close_menu();
        }
        menu_hairline(ui, &self.tokens);
        let top = self.settings.draft.top_bar_mode.clone();
        let bottom = self.settings.draft.bottom_bar_mode.clone();
        menu_caption(ui, &self.tokens, "上巴");
        for (mode, label) in [("immersive", "沉浸"), ("native", "系统")] {
            if self.menu_item(ui, label, Some(top == mode)) {
                self.settings.set_bar_mode(true, mode);
                ui.close_menu();
            }
        }
        menu_caption(ui, &self.tokens, "下巴");
        for (mode, label) in [
            ("immersive", "沉浸"),
            ("native", "系统"),
            ("none", "不显示"),
        ] {
            if self.menu_item(ui, label, Some(bottom == mode)) {
                self.settings.set_bar_mode(false, mode);
                ui.close_menu();
            }
        }
    }

    pub(crate) fn context_menu(
        &mut self,
        resp: &egui::Response,
        add_contents: impl FnOnce(&mut Self, &mut egui::Ui),
    ) {
        if self.menu_glass.is_none() && self.settings.draft.glass_enabled {
            self.menu_glass = Some(MenuGlass {
                menu_rect: self.menu_rect_hint,
                snapshot: self.menu_snapshot.clone(),
                snapshot_ppp: self.menu_snapshot_ppp,
                requested: self.menu_snapshot.is_some(),
                ..Default::default()
            });
            if self.menu_snapshot.is_none() {
                resp.ctx
                    .send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::new(
                        GLASS_SHOT_TAG.to_string(),
                    )));
                resp.ctx.request_repaint();
            }
        }
        resp.context_menu(|ui| {
            ui.set_width(MENU_INNER_WIDTH);
            self.glass_underlay(ui, false);
            add_contents(self, ui);
            self.glass_record_main(ui);
        });
    }

    /// 菜单闭包开头：在内容下方占位并垫毛玻璃贴图（ShapeIdx 预占底位，
    /// 确保即使首帧刚建出贴图也能插在内容控件下方）。
    pub(crate) fn glass_underlay(&mut self, ui: &mut egui::Ui, sub: bool) {
        let Some(g) = &mut self.menu_glass else {
            return;
        };
        let (tex, rect) = if sub {
            (&g.sub_tex, g.sub_draw)
        } else {
            (&g.main_tex, g.main_rect)
        };
        let shape = if let (Some(tex), Some(rect)) = (tex, rect) {
            egui::Shape::image(
                tex.id(),
                rect,
                egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
                egui::Color32::WHITE,
            )
        } else {
            egui::Shape::Noop
        };
        let idx = ui.painter().add(shape);
        if sub {
            g.sub_shape = Some(idx);
        } else {
            g.main_shape = Some(idx);
        }
    }

    /// 一级菜单闭包末尾：记录外沿矩形；换目标（右键到另一磁贴）时即时
    /// 重构贴图，并通过预占的 main_shape 底位立即更新图元。
    pub(crate) fn glass_record_main(&mut self, ui: &mut egui::Ui) {
        let rect = crate::glass::snap_rect_device_px(
            ui.min_rect().expand2(egui::vec2(MENU_MARGIN, MENU_MARGIN)),
            ui.ctx().pixels_per_point(),
        );
        let effective = match self.menu_glass.as_ref().and_then(|g| g.menu_rect) {
            Some(old) if crate::glass::rect_within_tol(old, rect, 1.0) => old,
            _ => {
                let is_dark = matches!(self.tokens.kind, ThemeKind::Dark);
                if let Some(g) = &mut self.menu_glass {
                    g.menu_rect = Some(rect);
                    g.sub_tex = None;
                    g.sub_rect = None;
                    g.sub_draw = None;
                    g.sub_tex_rect = None;
                    if let Some(snapshot) = &g.snapshot {
                        let built = crate::glass::build_texture(
                            ui.ctx(),
                            snapshot,
                            g.snapshot_ppp,
                            rect,
                            &crate::glass::main_params(is_dark),
                            "duo-menu-glass",
                        );
                        if let Some((tex, draw)) = built {
                            g.main_tex = Some(tex);
                            g.main_rect = Some(draw);
                        } else {
                            g.main_tex = None;
                            g.main_rect = None;
                        }
                    } else {
                        g.main_tex = None;
                        g.main_rect = None;
                    }
                }
                rect
            }
        };
        self.menu_rect_hint = Some(effective);
        if let Some(g) = &mut self.menu_glass {
            if let (Some(idx), Some(tex), Some(draw)) = (g.main_shape, &g.main_tex, g.main_rect) {
                ui.painter().set(
                    idx,
                    egui::Shape::image(
                        tex.id(),
                        draw,
                        egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
                        egui::Color32::WHITE,
                    ),
                );
            }
        }
        if self
            .menu_glass
            .as_ref()
            .is_some_and(|g| g.main_tex.is_some())
        {
            ui.painter().rect_stroke(
                effective,
                egui::CornerRadius::same(12),
                egui::Stroke::new(1.0_f32, self.tokens.menu_glass_border),
                egui::StrokeKind::Inside,
            );
        }
    }

    pub(crate) fn glass_record_sub(&mut self, ui: &mut egui::Ui) {
        if let Some(g) = &mut self.menu_glass {
            let rect = crate::glass::snap_rect_device_px(
                ui.min_rect().expand2(egui::vec2(MENU_MARGIN, MENU_MARGIN)),
                ui.ctx().pixels_per_point(),
            );
            let effective = match g.sub_rect {
                Some(old) if crate::glass::rect_within_tol(old, rect, 1.0) => old,
                _ => {
                    let is_dark = matches!(self.tokens.kind, ThemeKind::Dark);
                    if let Some(snapshot) = &g.snapshot {
                        let built = crate::glass::build_texture(
                            ui.ctx(),
                            snapshot,
                            g.snapshot_ppp,
                            rect,
                            &crate::glass::main_params(is_dark),
                            "duo-menu-glass-sub",
                        );
                        if let Some((tex, draw)) = built {
                            g.sub_tex = Some(tex);
                            g.sub_draw = Some(draw);
                            g.sub_tex_rect = Some(rect);
                        } else {
                            g.sub_tex = None;
                            g.sub_draw = None;
                            g.sub_tex_rect = None;
                        }
                    } else {
                        g.sub_tex = None;
                        g.sub_draw = None;
                        g.sub_tex_rect = None;
                    }
                    rect
                }
            };
            g.sub_rect = Some(effective);
            if let (Some(idx), Some(tex), Some(draw)) = (g.sub_shape, &g.sub_tex, g.sub_draw) {
                ui.painter().set(
                    idx,
                    egui::Shape::image(
                        tex.id(),
                        draw,
                        egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
                        egui::Color32::WHITE,
                    ),
                );
            }
            if g.sub_tex.is_some() {
                ui.painter().rect_stroke(
                    effective,
                    egui::CornerRadius::same(12),
                    egui::Stroke::new(1.0_f32, self.tokens.menu_glass_border),
                    egui::StrokeKind::Inside,
                );
            }
        }
    }

    /// 菜单可视状态：真右键链路 + Wayland 出图 harness 托管菜单。
    pub(crate) fn menu_effectively_open(&self, ctx: &egui::Context) -> bool {
        ctx.is_context_menu_open() || self.harness_menu_open()
    }

    fn harness_menu_open(&self) -> bool {
        self.shot_use_harness
            && self.shot_menu.is_some()
            && self
                .shot
                .as_ref()
                .is_some_and(|(_, frames, _)| *frames >= 46)
    }

    /// 毛玻璃泵：开/关跳变、截图命令与 Event::Screenshot 消费、贴图懒
    /// 构建。须在 pump_shot / pump_shot_menu 之前跑（后者会重跑
    /// begin_pass 吞掉未读事件）。
    pub(crate) fn pump_menu_glass(&mut self, ctx: &egui::Context) {
        if !self.settings.draft.glass_enabled {
            self.menu_glass = None;
            self.menu_snapshot = None;
            self.menu_glass_closed_frames = 0;
            return;
        }

        let shot = ctx.input(|i| {
            i.events.iter().find_map(|e| match e {
                egui::Event::Screenshot {
                    image, user_data, ..
                } if user_data_eq(user_data, GLASS_SHOT_TAG) => Some(image.clone()),
                _ => None,
            })
        });
        if let Some(image) = shot {
            let ppp = ctx.pixels_per_point();
            self.menu_snapshot = Some(image.clone());
            self.menu_snapshot_ppp = ppp;
            if let Some(g) = self.menu_glass.as_mut() {
                g.snapshot = Some(image);
                g.snapshot_ppp = ppp;
            }
            ctx.request_repaint();
        }

        if !self.menu_effectively_open(ctx) {
            self.menu_glass_closed_frames += 1;
            if self.menu_glass_closed_frames >= 2 {
                self.menu_glass = None;
            }
            if self.menu_snapshot.is_none() && (self.shot.is_none() || self.shot_menu.is_some()) {
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::new(
                    GLASS_SHOT_TAG.to_string(),
                )));
                ctx.request_repaint();
            }
            return;
        }
        self.menu_glass_closed_frames = 0;
        if self.menu_glass.is_none() {
            self.menu_glass = Some(MenuGlass {
                menu_rect: self.menu_rect_hint,
                snapshot: self.menu_snapshot.clone(),
                snapshot_ppp: self.menu_snapshot_ppp,
                requested: self.menu_snapshot.is_some(),
                ..Default::default()
            });
        }
        let is_dark = matches!(self.tokens.kind, ThemeKind::Dark);
        let Some(g) = self.menu_glass.as_mut() else {
            return;
        };
        if !g.requested {
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::new(
                GLASS_SHOT_TAG.to_string(),
            )));
            g.requested = true;
        }
        let (snapshot, ppp, rect, sub_rect) = match self.menu_glass.as_ref() {
            Some(g) => match (&g.snapshot, g.menu_rect, g.sub_rect) {
                (Some(s), Some(r), sub) => (s.clone(), g.snapshot_ppp, r, sub),
                _ => return,
            },
            None => return,
        };
        if self
            .menu_glass
            .as_ref()
            .is_some_and(|g| g.main_tex.is_none())
        {
            if let Some((tex, draw)) = crate::glass::build_texture(
                ctx,
                &snapshot,
                ppp,
                rect,
                &crate::glass::main_params(is_dark),
                "duo-menu-glass",
            ) {
                if let Some(g) = self.menu_glass.as_mut() {
                    g.main_tex = Some(tex);
                    g.main_rect = Some(draw);
                }
            }
        }
        let want_sub = self.menu_glass.as_ref().is_some_and(|g| {
            g.sub_tex.is_none() || (g.sub_rect.is_some() && g.sub_tex_rect != g.sub_rect)
        });
        if let (true, Some(sub_rect)) = (want_sub, sub_rect) {
            if let Some((tex, draw)) = crate::glass::build_texture(
                ctx,
                &snapshot,
                ppp,
                sub_rect,
                &crate::glass::main_params(is_dark),
                "duo-menu-glass-sub",
            ) {
                if let Some(g) = self.menu_glass.as_mut() {
                    g.sub_tex = Some(tex);
                    g.sub_draw = Some(draw);
                    g.sub_tex_rect = Some(sub_rect);
                }
            }
        }
    }

    /// 引擎锁（会话运行中不可改路径）。
    pub(crate) fn engine_locked(&self) -> bool {
        !self.sessions.running().is_empty()
    }

    /// 设置读取问题清单（红条；空 = 无）。
    pub(crate) fn settings_problems(&self) -> String {
        self.settings
            .flash
            .clone()
            .filter(|f| f.starts_with("未保存：") || !f.contains("已保存"))
            .unwrap_or_default()
    }

    /// 引擎路径检测结果胶囊。
    pub(crate) fn probe_pill_for(&self, tool: &str) -> Option<(String, String, Instant)> {
        self.probe_pill
            .as_ref()
            .filter(|(t, _, _)| t == tool)
            .cloned()
    }

    /// 异步检测引擎路径（pyduo SettingsApi.probe：--version 可执行性）。
    pub(crate) fn start_probe(&mut self, tool: &str, path: &str) {
        let tool = tool.to_string();
        let path = path.trim().to_string();
        self.probe_pill = Some((tool.clone(), "检测中…".into(), Instant::now()));
        self.probe_bg = Some(Background::spawn(move || {
            let bin = if path.is_empty() {
                // PATH 查找（which 语义）
                let ext = if cfg!(windows) { ".exe" } else { "" };
                let candidates: Vec<PathBuf> = std::env::var_os("PATH")
                    .map(|p| std::env::split_paths(&p).collect())
                    .unwrap_or_default();
                candidates
                    .iter()
                    .map(|d| d.join(format!("{tool}{ext}")))
                    .find(|p| p.is_file())
                    .ok_or_else(|| format!("{tool} 不在 PATH"))?
                    .display()
                    .to_string()
            } else {
                path.clone()
            };
            let out = winproc::quiet_command(&bin)
                .arg("--version")
                .output()
                .map_err(|_| "无法运行".to_string())?;
            let ok = out.status.success();
            let detail = if ok {
                let first = String::from_utf8_lossy(&out.stdout);
                first.lines().next().unwrap_or("").trim().to_string()
            } else {
                String::new()
            };
            Ok((tool, ok, detail))
        }));
    }

    /// 后台检测结果落地（update 泵）。
    pub(crate) fn pump_probe(&mut self) {
        let Some(bg) = self.probe_bg.take() else {
            return;
        };
        match bg.take() {
            Some(Ok((tool, ok, detail))) => {
                let path_set = match tool.as_str() {
                    "scrcpy" => !self.settings.draft.scrcpy_path.trim().is_empty(),
                    _ => !self.settings.draft.adb_path.trim().is_empty(),
                };
                let text = if ok {
                    if detail.is_empty() {
                        "✓ 可执行".to_string()
                    } else {
                        format!("✓ {detail}")
                    }
                } else if path_set {
                    "✗ 无法运行，请检查路径".to_string()
                } else {
                    "✗ 未在 PATH 找到，可手动填写路径".to_string()
                };
                self.probe_pill = Some((tool, text, Instant::now()));
            }
            Some(Err(_)) => {}
            None => self.probe_bg = Some(bg),
        }
    }

    /// 浏览按钮（native 文件对话框；shot/无交互环境无害）。
    pub(crate) fn browse_engine(&mut self, _tool: &str) {
        if let Some(picked) = rfd::FileDialog::new()
            .set_title("选择可执行文件")
            .pick_file()
        {
            let text = picked.display().to_string();
            if _tool == "scrcpy" {
                self.settings.set_scrcpy_path(&text);
            } else {
                self.settings.set_adb_path(&text);
            }
        }
    }

    /// 菜单浮层皮肤（MenuGlassPlate 在 egui 约束下的忠实近似）：popup 是
    /// 独立 Area 层、打开期间整层重绘，半透明 window_fill 只做一次 GPU
    /// 合成——「假玻璃」= 高 alpha menuFill（背后内容 10% 透出）+ 1px
    /// 亮边 + r12；玻璃关 = QML 软件回退路径（不透明 menuFill +
    /// menuFillBorder）。行皮肤：行高 32 / r10 / hover 洗色（QML
    /// hoverWash/pressWash 字面半透明）。egui popup frame 在内容闭包外
    /// 构造，须在菜单打开期间改 ctx 级 style（二级菜单同根 BarState，
    /// is_context_menu_open 覆盖全链）。
    pub(crate) fn skin_menus(&self, ctx: &egui::Context) {
        if !self.menu_effectively_open(ctx) {
            return;
        }
        let t = self.tokens;
        let is_dark = matches!(t.kind, ThemeKind::Dark);
        // QML hoverWash/pressWash 令牌字面（rgba），叠在玻璃填充上
        let (hover, press) = if is_dark {
            (
                egui::Color32::from_rgba_premultiplied(12, 12, 12, 20),
                egui::Color32::from_rgba_premultiplied(24, 24, 24, 36),
            )
        } else {
            (
                egui::Color32::from_rgba_unmultiplied(0, 0, 0, 15),
                egui::Color32::from_rgba_unmultiplied(0, 0, 0, 26),
            )
        };
        let glass = self.settings.draft.glass_enabled;
        ctx.style_mut(|st| {
            st.visuals.window_fill = if glass {
                egui::Color32::TRANSPARENT
            } else {
                t.menu_fill
            };
            st.visuals.window_stroke = if glass {
                egui::Stroke::NONE
            } else {
                egui::Stroke::new(
                    1.0_f32,
                    if is_dark {
                        egui::Color32::from_rgba_unmultiplied(255, 255, 255, 26)
                    } else {
                        egui::Color32::from_rgba_unmultiplied(0, 0, 0, 26)
                    },
                )
            };
            st.visuals.window_corner_radius = egui::CornerRadius::same(12);
            st.visuals.menu_corner_radius = egui::CornerRadius::same(12);
            st.visuals.popup_shadow = if glass {
                egui::epaint::Shadow::NONE
            } else {
                egui::epaint::Shadow {
                    offset: [0, 8],
                    blur: 32,
                    spread: 3,
                    color: egui::Color32::from_black_alpha(if is_dark { 110 } else { 50 }),
                }
            };
            st.spacing.menu_margin = egui::Margin::same(4);
            st.spacing.menu_width = MENU_WIDTH;
            let text_color = if is_dark { egui::Color32::WHITE } else { t.ink };
            for state in [
                &mut st.visuals.widgets.hovered,
                &mut st.visuals.widgets.open,
                &mut st.visuals.widgets.active,
                &mut st.visuals.widgets.inactive,
            ] {
                state.expansion = 0.0;
                state.corner_radius = 8.into();
                state.bg_stroke = egui::Stroke::NONE;
                state.fg_stroke = egui::Stroke::new(1.0_f32, text_color);
            }
            st.visuals.widgets.hovered.weak_bg_fill = hover;
            st.visuals.widgets.hovered.bg_fill = hover;
            st.visuals.widgets.active.weak_bg_fill = press;
            st.visuals.widgets.active.bg_fill = press;
            st.visuals.widgets.open.weak_bg_fill = hover;
            st.visuals.widgets.open.bg_fill = hover;
            st.visuals.widgets.inactive.weak_bg_fill = egui::Color32::TRANSPARENT;
            st.visuals.widgets.inactive.bg_fill = egui::Color32::TRANSPARENT;
        });
    }

    pub(crate) fn toast_now(&mut self, text: impl Into<String>) {
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

    pub(crate) fn serial(&self) -> Option<String> {
        self.watch.as_ref().and_then(|w| w.online_serial())
    }

    pub(crate) fn refresh_installed(&mut self) {
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

    pub(crate) fn launch(&mut self, package: &str, size: Option<(i64, i64)>) {
        let Some(serial) = self.serial() else {
            self.toast_now("设备未连接");
            return;
        };
        let Some(binary) = self.duo_core.clone() else {
            self.toast_now("找不到 duo-core 二进制");
            return;
        };
        self.sessions.reap();
        let adb_for_init = self.adb.clone();
        let serial_for_init = serial.clone();
        std::thread::spawn(move || {
            let mut cmd = winproc::quiet_command(&adb_for_init);
            cmd.args([
                "-s",
                &serial_for_init,
                "shell",
                "settings",
                "put",
                "global",
                "force_resizable_activities",
                "1",
            ]);
            let _ = cmd.output();
            let mut cmd = winproc::quiet_command(&adb_for_init);
            cmd.args([
                "-s",
                &serial_for_init,
                "shell",
                "settings",
                "put",
                "global",
                "enable_freeform_support",
                "1",
            ]);
            let _ = cmd.output();
        });
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
        let output = winproc::quiet_command(&self.adb)
            .args(["-s", &serial, "shell", "wm", "size"])
            .output()
            .ok()?;
        let text = String::from_utf8_lossy(&output.stdout).into_owned();
        let preset = body_aspect_from_wm_size(&text);
        self.body_probed_at = Some(Instant::now());
        self.body_preset = preset.clone();
        preset
    }

    pub(crate) fn move_app_to_display(&mut self, package: &str) {
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
            let output = winproc::quiet_command(&adb)
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
            let output = winproc::quiet_command(&adb)
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

    pub(crate) fn start_mirror(&mut self) {
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

    fn set_display_flex(&mut self, package: &str) {
        self.display_prefs
            .insert(package.to_string(), DisplayChoice::Flex);
        save_display_prefs(&self.display_prefs);
        self.toast_now(format!("{} 将自适应窗口", session_label(package)));
    }

    fn set_display_fixed(&mut self, package: &str, aspect: &str) {
        let body = self.body_preset();
        let label = match resolve_fixed_aspect(aspect, body.as_ref()) {
            Ok(label) => label,
            Err(msg) => {
                self.toast_now(msg);
                return;
            }
        };
        self.display_prefs.insert(
            package.to_string(),
            DisplayChoice::Fixed {
                aspect: aspect.into(),
            },
        );
        save_display_prefs(&self.display_prefs);
        self.toast_now(format!("{} 将以 {label} 常驻", session_label(package)));
    }

    fn set_bar(&mut self, package: &str, which: bool, mode: Option<&'static str>) {
        let merged = bar_entry_after(self.bar_prefs.get(package).copied(), which, mode);
        match merged {
            Some(choice) => {
                self.bar_prefs.insert(package.to_string(), choice);
            }
            None => {
                self.bar_prefs.remove(package);
            }
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

    fn sync_visuals(&self, ctx: &egui::Context) {
        let t = self.tokens;
        let is_dark = matches!(t.kind, ThemeKind::Dark);
        let mut visuals = if is_dark {
            egui::Visuals::dark()
        } else {
            egui::Visuals::light()
        };
        visuals.panel_fill = t.bg;
        let glass = self.settings.draft.glass_enabled;
        visuals.window_fill = if glass {
            egui::Color32::TRANSPARENT
        } else {
            t.menu_fill
        };
        visuals.window_stroke = if glass {
            egui::Stroke::NONE
        } else {
            egui::Stroke::new(
                1.0_f32,
                if is_dark {
                    egui::Color32::from_rgba_unmultiplied(255, 255, 255, 26)
                } else {
                    egui::Color32::from_rgba_unmultiplied(0, 0, 0, 26)
                },
            )
        };
        visuals.window_corner_radius = egui::CornerRadius::same(12);
        visuals.menu_corner_radius = egui::CornerRadius::same(12);
        visuals.popup_shadow = if glass {
            egui::epaint::Shadow::NONE
        } else {
            egui::epaint::Shadow {
                offset: [0, 8],
                blur: 32,
                spread: 3,
                color: egui::Color32::from_black_alpha(if is_dark { 110 } else { 50 }),
            }
        };
        visuals.extreme_bg_color = t.bg;
        visuals.selection.bg_fill = t.accent;
        visuals.selection.stroke = egui::Stroke::new(1.0_f32, t.ink);
        visuals.widgets.noninteractive.fg_stroke = egui::Stroke::new(1.0_f32, t.ink2);
        visuals.widgets.inactive.bg_stroke = egui::Stroke::new(1.0_f32, t.card_border);
        visuals.widgets.inactive.fg_stroke = egui::Stroke::new(1.0_f32, t.ink);
        visuals.widgets.hovered.bg_fill = t.hover_on_card;
        visuals.widgets.hovered.bg_stroke = egui::Stroke::new(1.0_f32, t.card_border);
        visuals.widgets.hovered.fg_stroke = egui::Stroke::new(1.0_f32, t.ink);
        visuals.widgets.active.bg_fill = t.press_on_card;
        visuals.widgets.active.fg_stroke = egui::Stroke::new(1.0_f32, t.ink);
        visuals.widgets.open.bg_fill = t.capsule;
        ctx.set_visuals(visuals);
        ctx.style_mut(|st| {
            st.spacing.menu_margin = egui::Margin::same(4);
            st.spacing.menu_width = MENU_WIDTH;
        });
    }

    /// 顶栏分段导航（2026-09-19 修订，学 iOS UISegmentedControl /
    /// COLOROS 17 分段控件）：紧凑居中胶囊轨道（不再通栏对半），拇指
    /// 滑动动画 + 轻投影，亮色 = 灰轨道 vs 纯白拇指的强对比（旧版
    /// 白上白零对比）。段内文字交给 egui 居中，坐标仅定段矩形。
    fn top_capsule(&mut self, ui: &mut egui::Ui) {
        let t = self.tokens;
        let full = ui.max_rect();
        // 液态玻璃胶囊（iOS 26 segmented + ColorOS 17 流体亚克力，2026-09-19
        // 视觉升级，配方详见 docs/ui/DESIGN.md §3.2）：宽度随文案收缩，
        // 悬浮居中；拇指 = 玻璃透镜（顶部高光渐变 + 双层阴影）。
        let label_w = 28.0; // 「首页」「设置」两字 13.5px 实测宽度上限
        let seg_target = ((label_w + 46.0_f32).max(72.0)).max(72.0);
        let track_h = 32.0_f32;
        let inset = 3.0;
        let thumb_h = track_h - 2.0 * inset;
        let cap_w = (seg_target * 2.0 + 2.0 * inset)
            .min(full.width() - 40.0)
            .max(0.0);
        let rect = egui::Rect::from_min_size(
            egui::pos2(full.center().x - cap_w / 2.0, full.top() + 16.0),
            Vec2::new(cap_w, track_h),
        );
        ui.allocate_rect(rect, Sense::hover());
        let is_dark = t.kind == ThemeKind::Dark;
        let track = if self.settings.draft.glass_enabled {
            egui::Color32::from_rgba_unmultiplied(
                t.segment_track.r(),
                t.segment_track.g(),
                t.segment_track.b(),
                if is_dark { 200 } else { 220 },
            )
        } else {
            t.segment_track
        };
        crate::paint::rounded_fill(ui.painter(), rect, 15.0, track);
        // 发光边缘（ColorOS 17 luminous edge）：轨道内侧 1px 亮线圈，
        // 上半强下半弱——玻璃截面高光。
        let inner = egui::Rect::from_min_size(
            egui::pos2(rect.left() + 0.5, rect.top() + 0.5),
            Vec2::new(rect.width() - 1.0, rect.height() - 1.0),
        );
        let glow = if is_dark { 6 } else { 110 };
        ui.painter().rect_stroke(
            inner,
            egui::CornerRadius::same(15),
            egui::Stroke::new(1.0_f32, egui::Color32::from_white_alpha(glow)),
            egui::StrokeKind::Inside,
        );
        let seg_w = ((rect.width() - 2.0 * inset) / 2.0).floor();
        let target_x = if self.page == Page::Home {
            rect.left() + inset
        } else {
            rect.right() - inset - seg_w
        };
        let x = ui
            .ctx()
            .animate_value_with_time(egui::Id::new("duo-tab-thumb"), target_x, 0.22);
        let thumb = egui::Rect::from_min_size(
            egui::pos2(x, rect.top() + inset),
            Vec2::new(seg_w, thumb_h),
        );
        // 双层阴影：贴底接触阴影（锐、深）+ 环境投影（软、浅）——
        // 「搁在轨道上」而非「悬浮」。
        let contact = egui::Shadow {
            offset: [0, 1],
            blur: 1,
            spread: 0,
            color: egui::Color32::from_black_alpha(if is_dark { 80 } else { 48 }),
        };
        let ambient = egui::Shadow {
            offset: [0, 3],
            blur: 8,
            spread: 0,
            color: egui::Color32::from_black_alpha(if is_dark { 55 } else { 20 }),
        };
        for sh in [&ambient, &contact] {
            ui.painter()
                .add(sh.as_shape(thumb, egui::CornerRadius::same(13)));
        }
        // 玻璃透镜拇指：顶部亮、底部微沉的垂直渐变（非纯白方块）。
        let (lens_top, lens_bot) = if is_dark {
            (
                egui::Color32::from_rgb(94, 94, 98),
                egui::Color32::from_rgb(60, 60, 63),
            )
        } else {
            (
                egui::Color32::from_rgb(255, 255, 255),
                egui::Color32::from_rgb(242, 242, 245),
            )
        };
        crate::paint::rounded_fill_v(
            ui.painter(),
            thumb,
            13.0,
            lens_top,
            lens_bot,
        );
        // 顶部内高光（玻璃厚度感）：上弧亮条，长坡渐灭（无切齐感）。
        let glint_h = (thumb_h * 0.58).min(16.0);
        let glint = egui::Rect::from_min_size(
            egui::pos2(thumb.left() + 2.5, thumb.top() + 1.0),
            Vec2::new(thumb.width() - 5.0, glint_h),
        );
        let glint_top = if is_dark {
            egui::Color32::from_rgba_unmultiplied(255, 255, 255, 70)
        } else {
            egui::Color32::from_rgba_unmultiplied(255, 255, 255, 140)
        };
        crate::paint::rounded_fill_v(
            ui.painter(),
            glint,
            6.0,
            glint_top,
            egui::Color32::from_rgba_unmultiplied(255, 255, 255, 0),
        );
        let mut clicked = None;
        for (i, (page, label)) in [(Page::Home, "首页"), (Page::Settings, "设置")]
            .into_iter()
            .enumerate()
        {
            let seg_x = if i == 0 {
                rect.left() + inset
            } else {
                rect.right() - inset - seg_w
            };
            let seg = egui::Rect::from_min_size(egui::pos2(seg_x, rect.top() + inset), Vec2::new(seg_w, thumb_h));
            let selected = self.page == page;
            let resp = ui.allocate_rect(seg, Sense::click());
            if !selected && resp.hovered() {
                crate::paint::rounded_fill(ui.painter(), seg, 13.0, t.capsule_hover);
            }
            crate::paint::text_centered(
                ui.painter(),
                seg.center(),
                label,
                13.5,
                selected,
                if selected { t.ink } else { t.ink2 },
            );
            if resp.clicked() {
                clicked = Some(page);
            }
        }
        if let Some(page) = clicked {
            if self.page == Page::Settings && page == Page::Home {
                self.save_settings_and_leave();
            } else {
                self.page = page;
            }
        }
    }

    /// 「固定比例」二级内容（一级 menu_sub_button 与出图 harness 共用）。
    /// 每行右侧画比例示意矩形（QML AspectMenuRow：横屏宽边 16、竖屏
    /// 高边 14；机身项用探测真值，无缓存时退 20:9 示意）。
    pub(crate) fn fixed_aspect_submenu(&mut self, ui: &mut egui::Ui, package: &str) {
        let current = self.display_prefs.get(package).cloned();
        let body = self.body_preset.clone();
        let dims = |p: &AspectPreset| {
            if p.landscape {
                (16.0_f32, 16.0 * p.height as f32 / p.width as f32)
            } else {
                (14.0 * p.width as f32 / p.height as f32, 14.0_f32)
            }
        };
        let pick = |app: &mut Self, ui: &mut egui::Ui, id: &str, label: &str, gw: f32, gh: f32| {
            let mark = matches!(&current, Some(DisplayChoice::Fixed { aspect }) if aspect == id);
            if app.menu_aspect_item(ui, label, mark, gw, gh) {
                app.set_display_fixed(package, id);
                ui.close_menu();
            }
        };
        menu_caption(ui, &self.tokens, "横屏");
        for preset in duo_core::aspects::aspect_presets()
            .iter()
            .filter(|p| p.landscape)
        {
            let (gw, gh) = dims(preset);
            pick(self, ui, &preset.id, &preset.id, gw, gh);
        }
        let (bl, bp) = match &body {
            Some(b) => (dims(b), dims(&transposed(b))),
            None => ((16.0, 7.6), (6.7, 14.0)),
        };
        pick(self, ui, BODY_LANDSCAPE_ID, "机身", bl.0, bl.1);
        menu_hairline(ui, &self.tokens);
        menu_caption(ui, &self.tokens, "竖屏");
        for preset in duo_core::aspects::aspect_presets()
            .iter()
            .filter(|p| !p.landscape)
        {
            let (gw, gh) = dims(preset);
            pick(self, ui, &preset.id, &preset.id, gw, gh);
        }
        pick(self, ui, BODY_PORTRAIT_ID, "机身", bp.0, bp.1);
    }

    pub(crate) fn tile_menu(&mut self, ui: &mut egui::Ui, entry: &AppEntry) {
        // Main.qml appContextMenu 一级结构逐行对齐：打开 / 置顶到固定栏 /
        // hairline / 自适应窗口 | 固定比例 ▸ / 窗口栏 ▸ / 音频独占（勾选
        // 不收菜单）/ 断开保留画面（勾选不收菜单）/ DPI ▸ / 渲染倍率 ▸。
        // 皮肤 = menu_item（13px 行 32 + painter 4px 勾选点）。
        let package = entry.package.clone();
        if self.menu_item(ui, "打开", None) {
            self.launch(&package, None);
            ui.close_menu();
        }
        let pin_text = if self.pinned.contains_key(&package) {
            "取消置顶"
        } else {
            "置顶到固定栏"
        };
        if self.menu_item(ui, pin_text, None) {
            self.toggle_pin(&package);
            ui.close_menu();
        }
        menu_hairline(ui, &self.tokens);
        let fixed_now = matches!(
            self.display_prefs.get(&package),
            Some(DisplayChoice::Fixed { .. })
        );
        if self.menu_item(ui, "自适应窗口", Some(!fixed_now)) {
            self.set_display_flex(&package);
            ui.close_menu();
        }
        self.menu_sub_button(ui, "固定比例", |app, ui| {
            app.fixed_aspect_submenu(ui, &package);
        });
        self.menu_sub_button(ui, "窗口栏", |app, ui| {
            let bars = app.bar_prefs.get(&package).cloned().unwrap_or_default();
            let row = |app: &mut Self,
                       ui: &mut egui::Ui,
                       which: bool,
                       mode: Option<&'static str>,
                       label: &str| {
                let explicit = if which { bars.top } else { bars.bottom };
                if app.menu_item(ui, label, Some(explicit == mode)) {
                    app.set_bar(&package, which, mode);
                    ui.close_menu();
                }
            };
            menu_caption(ui, &app.tokens, "上巴");
            row(app, ui, true, None, "跟随默认");
            row(app, ui, true, Some("immersive"), "沉浸");
            row(app, ui, true, Some("native"), "系统");
            menu_caption(ui, &app.tokens, "下巴");
            row(app, ui, false, None, "跟随默认");
            row(app, ui, false, Some("immersive"), "沉浸");
            row(app, ui, false, Some("native"), "系统");
            row(app, ui, false, Some("none"), "不显示");
        });
        let exclusive = self.audio_prefs.get(&package).copied().unwrap_or(false);
        if self.menu_item(ui, "音频独占", Some(exclusive)) {
            // QML 勾选行切换不收菜单（圆点即时可见）
            self.toggle_audio_exclusive(&package);
        }
        let keep_vd = self.behavior_prefs.get(&package).copied().unwrap_or(false);
        if self.menu_item(ui, "断开保留画面", Some(keep_vd)) {
            self.toggle_keep_vd(&package);
        }
        self.menu_sub_button(ui, "DPI", |app, ui| {
            let dpi = app.density_prefs.get(&package).copied();
            if app.menu_item(ui, "跟随默认", Some(dpi.is_none())) {
                app.set_density(&package, None);
                ui.close_menu();
            }
            for v in [160i64, 240, 320] {
                if app.menu_item(ui, &v.to_string(), Some(dpi == Some(v))) {
                    app.set_density(&package, Some(v));
                    ui.close_menu();
                }
            }
            let mut custom = dpi.unwrap_or(320);
            let dv = egui::DragValue::new(&mut custom)
                .range(120..=640)
                .speed(10)
                .prefix("自定义 ");
            if ui.add(dv).changed() {
                app.set_density(&package, Some(custom));
            }
        });
        self.menu_sub_button(ui, "渲染倍率", |app, ui| {
            let scale = app.scale_prefs.get(&package).copied();
            if app.menu_item(ui, "跟随默认", Some(scale.is_none())) {
                app.set_scale(&package, None);
                ui.close_menu();
            }
            for v in [1.0f64, 1.4, 2.0, 3.0] {
                let mark = scale.map(|s| (s - v).abs() < 1e-9).unwrap_or(false);
                if app.menu_item(ui, &format!("{v}×"), Some(mark)) {
                    app.set_scale(&package, Some(v));
                    ui.close_menu();
                }
            }
            let mut custom = scale.unwrap_or(1.0);
            let sv = egui::DragValue::new(&mut custom)
                .range(1.0..=4.0)
                .speed(0.1)
                .prefix("微调 ");
            if ui.add(sv).changed() {
                app.set_scale(&package, Some((custom * 10.0).round() / 10.0));
            }
        });
    }

    // -------------------------------------------------------------- 设置页

    fn settings_page(&mut self, ui: &mut egui::Ui) {
        crate::settings::show(self, ui);
    }

    /// 出图泵：帧数 ≥40 且 1.6s 就绪后请求 Screenshot；自己请求的截图
    /// 回包存盘即退。毛玻璃也发截图命令（拍纯背景），所以只认
    /// pending 之后的回包；须跑在 pump_shot_menu 前（后者重跑
    /// begin_pass 会吞掉未读事件——旧版 DUO_SHOT_MENU 永不存盘的根因）。
    fn pump_shot(&mut self, ctx: &egui::Context) {
        let Some((path, frames, started)) = &mut self.shot else {
            return;
        };
        *frames += 1;
        let (need_frames, need_ms) = if self.shot_menu.is_some() {
            (78, 4200)
        } else {
            (40, 2900)
        };
        let ready = *frames >= need_frames && started.elapsed() >= Duration::from_millis(need_ms);
        if ready && !self.shot_capture {
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::new(
                SHOT_TAG.to_string(),
            )));
            self.shot_capture = true;
            self.shot_capture_frame = *frames;
        }
        if ready && self.shot_capture && *frames - self.shot_capture_frame > 30 {
            // 回包丢失重发（无交互环境偶发）
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::new(
                SHOT_TAG.to_string(),
            )));
            self.shot_capture_frame = *frames;
        }
        if !self.shot_capture {
            return;
        }
        let shot = ctx.input(|i| {
            i.events.iter().find_map(|e| match e {
                egui::Event::Screenshot {
                    image, user_data, ..
                } if user_data_eq(user_data, SHOT_TAG) => Some(image.clone()),
                _ => None,
            })
        });
        if let Some(image) = shot {
            let size = [image.width() as u32, image.height() as u32];
            let pixels: Vec<u8> = image
                .pixels
                .iter()
                .flat_map(|c| [c.r(), c.g(), c.b()])
                .collect();
            match image::save_buffer(
                path.as_str(),
                &pixels,
                size[0],
                size[1],
                image::ColorType::Rgb8,
            ) {
                Ok(()) => eprintln!("shot saved: {} ({}x{})", path, size[0], size[1]),
                Err(err) => eprintln!("shot save FAILED: {path}: {err}"),
            }
            self.shot = None;
            self.shot_capture = false;
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    /// DUO_SHOT_MENU 注入：帧 44 右键打开菜单；tile-sub 帧 70 起把指针
    /// 悬停到「固定比例」行（菜单几何 = 内边 4 + 打开32+置顶32+发线9+
    /// 自适应32 → 触发行中心 y+125）展开二级。
    ///
    /// egui 的 hover/click 旗标在 pass 开头由真指针输入推进，进程内
    /// begin_pass 重放造不出命中：unix 走 XTEST 系统事件（真机指针
    /// 路径）；其余平台保留合成 RawInput 重跑 begin_pass。
    fn pump_shot_menu(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        if self.shot.is_none() {
            return;
        }
        let Some(mode) = self.shot_menu.clone() else {
            return;
        };
        let frames = self.shot.as_ref().map(|(_, f, _)| *f).unwrap_or(0);
        if frames < 44 {
            return;
        }
        let screen = ctx.screen_rect();
        let layout = crate::home::HomeLayout::compute(
            screen.width(),
            screen.height(),
            self.has_pinned(),
            !self.running_chips().is_empty(),
        );
        let pos = if mode == "mirror" {
            layout.mirror.center()
        } else {
            // 首个磁贴图标中心（60px 图标在 cell 内 y+10..70）
            let cols = ((layout.grid.width() / 92.0).floor() as usize).max(2);
            let cell_w = layout.grid.width() / cols as f32;
            egui::pos2(layout.grid.left() + cell_w / 2.0, layout.grid.top() + 40.0)
        };
        let sub_at = egui::pos2(pos.x + 64.0, pos.y + 125.0);
        let ppp = ctx.pixels_per_point();
        #[cfg(unix)]
        {
            if frames == 44 && !self.shot_clicked {
                let Some(xid) = window_xid(frame) else {
                    // Wayland：无法注入系统指针，改走 harness 托管菜单
                    self.shot_clicked = true;
                    self.shot_use_harness = true;
                    eprintln!("[shot-menu] no X11 window, using harness menu");
                    return;
                };
                let origin = crate::xtest::client_origin(xid);
                let Some(origin) = origin.filter(|(x, y)| *x >= 0 && *y >= 0) else {
                    self.shot_clicked = true;
                    self.shot_use_harness = true;
                    eprintln!("[shot-menu] no mapped X11 window, using harness menu");
                    return;
                };
                self.shot_clicked = true;
                eprintln!(
                    "[shot-menu] frames={frames} mode={mode} click at={pos:?} origin={origin:?}"
                );
                std::thread::spawn(move || {
                    crate::xtest::pointer_sequence(
                        origin,
                        &[(pos, crate::xtest::PointerAction::ClickRight)],
                        ppp,
                    );
                });
            } else if mode == "tile-sub" && frames >= 70 && !self.shot_sub_moved {
                let Some(xid) = window_xid(frame) else { return };
                let Some(origin) = crate::xtest::client_origin(xid) else {
                    return;
                };
                self.shot_sub_moved = true;
                eprintln!("[shot-menu] frames={frames} mode={mode} hover sub at={sub_at:?}");
                std::thread::spawn(move || {
                    crate::xtest::pointer_sequence(
                        origin,
                        &[(sub_at, crate::xtest::PointerAction::Move)],
                        ppp,
                    );
                });
            }
        }
        #[cfg(not(unix))]
        {
            let _ = (frame, ppp);
            // 二级展开后指针留在触发行（菜单不随指针离开关闭）
            let at = if mode == "tile-sub" && frames >= 58 {
                sub_at
            } else {
                pos
            };
            if frames.is_multiple_of(20) || (44..=60).contains(&frames) {
                eprintln!("[shot-menu] frames={frames} mode={mode} at={at:?}");
            }
            let mut events = vec![egui::Event::PointerMoved(at)];
            if frames == 44 || frames == 45 {
                events.push(egui::Event::PointerButton {
                    pos: at,
                    button: egui::PointerButton::Secondary,
                    pressed: frames == 44,
                    modifiers: Default::default(),
                });
            }
            let options = ctx.options(|o| o.clone());
            ctx.input_mut(|i| {
                let raw = egui::RawInput {
                    events,
                    ..i.raw.clone()
                };
                let next = i
                    .clone()
                    .begin_pass(raw, true, i.pixels_per_point, &options);
                *i = next;
            });
        }
    }

    /// Wayland 出图的托管菜单（无点击注入通道）：与真菜单同一份内容
    /// 闭包与玻璃钩子，Area + Frame::menu 复刻 popup 几何；tile-sub 另
    /// 托管「固定比例」二级。
    fn show_harness_menu(&mut self, ctx: &egui::Context) {
        if !self.harness_menu_open() {
            return;
        }
        if self
            .menu_glass
            .as_ref()
            .is_some_and(|g| g.snapshot.is_none())
        {
            // 玻璃截图帧：不画菜单，拍纯背景（快照到达为止，见
            // context_menu 同款注释）
            return;
        }
        let mode = self.shot_menu.clone().unwrap_or_default();
        let screen = ctx.screen_rect();
        let layout = crate::home::HomeLayout::compute(
            screen.width(),
            screen.height(),
            self.has_pinned(),
            !self.running_chips().is_empty(),
        );
        let pos = if mode == "mirror" {
            layout.mirror.center()
        } else {
            let cols = ((layout.grid.width() / 92.0).floor() as usize).max(2);
            let cell_w = layout.grid.width() / cols as f32;
            egui::pos2(layout.grid.left() + cell_w / 2.0, layout.grid.top() + 40.0)
        };
        let entry = self.grid_entries().first().cloned();
        let package = entry.as_ref().map(|e| e.package.clone());
        let sub_at = self
            .menu_glass
            .as_ref()
            .and_then(|g| g.menu_rect)
            .map(|r| egui::pos2(r.right(), r.top() + 109.0));
        let show_sub = mode == "tile-sub" && sub_at.is_some();
        egui::Area::new(egui::Id::new("duo-shot-harness-menu"))
            .order(egui::Order::Foreground)
            .fixed_pos(pos)
            .default_width(MENU_WIDTH)
            .sense(egui::Sense::hover())
            .show(ctx, |ui| {
                egui::Frame::menu(ui.style()).show(ui, |ui| {
                    ui.set_width(MENU_INNER_WIDTH);
                    ui.with_layout(egui::Layout::top_down_justified(egui::Align::LEFT), |ui| {
                        self.glass_underlay(ui, false);
                        match (&mode, &entry) {
                            (m, Some(entry)) if m != "mirror" => self.tile_menu(ui, entry),
                            _ => self.mirror_menu(ui),
                        }
                        self.glass_record_main(ui);
                    });
                });
            });
        if show_sub {
            let sub_at = sub_at.unwrap_or_else(|| unreachable!());
            egui::Area::new(egui::Id::new("duo-shot-harness-submenu"))
                .order(egui::Order::Foreground)
                .fixed_pos(sub_at)
                .default_width(MENU_WIDTH)
                .sense(egui::Sense::hover())
                .show(ctx, |ui| {
                    egui::Frame::menu(ui.style()).show(ui, |ui| {
                        ui.set_width(MENU_INNER_WIDTH);
                        ui.with_layout(egui::Layout::top_down_justified(egui::Align::LEFT), |ui| {
                            self.glass_underlay(ui, true);
                            if let Some(package) = &package {
                                self.fixed_aspect_submenu(ui, package);
                            }
                            self.glass_record_sub(ui);
                        });
                    });
                });
        }
    }

    /// 音量命令 200ms 防抖（QML volumeDebounce）。
    fn pump_volume_debounce(&mut self) {
        let Some((index, at)) = self.volume_pending else {
            return;
        };
        if at.elapsed() < Duration::from_millis(200) {
            return;
        }
        self.volume_pending = None;
        let Some(binary) = self.duo_core.clone() else {
            return;
        };
        let Some(serial) = self.serial() else { return };
        let adb = self.adb.clone();
        self.volume_bg = Some(Background::spawn(move || {
            let bin = binary.display().to_string();
            let _ = backend::set_volume(&bin, &adb, &serial, index);
        }));
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
                            // r20 重写后旧贴图滞留：清缓存让下帧重读
                            self.icon_tex.borrow_mut().clear();
                            self.menu_snapshot = None;
                            if !patched.is_empty() {
                                self.toast_now("应用图标已更新");
                            }
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
                if online.len() > known.len() && self.installed_bg.is_none() && !self.skip_sweep {
                    self.refresh_installed();
                }
            }
            self.known_online = Some(online);
        }
    }
}

/// X11 客户区窗口 id（XTEST 坐标换算用；非 unix / 非 x11 无值）。
#[cfg(unix)]
fn window_xid(frame: &eframe::Frame) -> Option<u64> {
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    let window = frame.window_handle().ok()?;
    let handle = window.window_handle().ok()?;
    match handle.as_raw() {
        RawWindowHandle::Xlib(h) => Some(h.window),
        RawWindowHandle::Xcb(h) => Some(u64::from(h.window.get())),
        _ => None,
    }
}

impl eframe::App for PanelApp {
    fn clear_color(&self, _visuals: &egui::Visuals) -> [f32; 4] {
        let c = self.tokens.bg;
        let a = if self.settings.draft.glass_enabled && cfg!(target_os = "windows") {
            0.85_f32
        } else {
            1.0_f32
        };
        [
            c.r() as f32 / 255.0,
            c.g() as f32 / 255.0,
            c.b() as f32 / 255.0,
            a,
        ]
    }

    fn update(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        if self.settings.draft.glass_enabled {
            crate::blur::apply_glass(frame, self.tokens.blur_tint());
        }
        if self.shot.is_some() {
            ctx.request_repaint(); // 出图模式：静态画面也推进帧计数
        }
        let cur_size = ctx.screen_rect().size();
        if self.last_screen_size != Some(cur_size) {
            self.last_screen_size = Some(cur_size);
            self.menu_snapshot = None;
        }
        let kind = ThemeKind::from_settings(&self.settings.draft.theme);
        if self.tokens.kind != kind {
            self.tokens = Tokens::of(kind);
            self.menu_snapshot = None;
        }
        let prev_page = self.page;
        // QML Shortcut：Ctrl+, 打开设置；Esc 在设置页 = 保存并返回
        // （2026-09-18 拍板：离开设置即自动保存，无放弃语义）
        if ctx.input(|i| i.modifiers.command && i.key_pressed(egui::Key::Comma)) {
            self.page = Page::Settings;
        }
        if self.page == Page::Settings && ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
            self.save_settings_and_leave();
        }
        self.sync_visuals(ctx);
        self.sessions.reap();
        self.pump_background();
        self.pump_volume_debounce();
        self.pump_probe();
        self.skin_menus(ctx);
        self.pump_menu_glass(ctx);
        self.pump_shot(ctx);
        self.pump_shot_menu(ctx, frame);

        // 画布（bg + 六枚色斑）铺满；卡片自管边距（QML x:20 语义）
        egui::CentralPanel::default()
            .frame(egui::Frame::NONE)
            .show(ctx, |ui| {
                let full = ui.max_rect();
                let bg = if self.settings.draft.glass_enabled && cfg!(target_os = "windows") {
                    egui::Color32::from_rgba_unmultiplied(
                        self.tokens.bg.r(),
                        self.tokens.bg.g(),
                        self.tokens.bg.b(),
                        216,
                    )
                } else {
                    self.tokens.bg
                };
                ui.painter().rect_filled(full, 0, bg);
                match self.page {
                    // 设置页自铺无斑底（QML Rectangle 盖色斑）+ 内容，
                    // 之后胶囊恒在最上（跨页常驻）
                    Page::Home => {
                        crate::paint::canvas_spots(ui.painter(), &self.tokens, full);
                        crate::home::show(self, ui);
                        self.top_capsule(ui);
                    }
                    Page::Settings => {
                        self.settings_page(ui);
                        self.top_capsule(ui);
                    }
                }
            });
        // Wayland 出图：托管菜单浮层（Area，先于 Toast）
        self.show_harness_menu(ctx);
        // Toast（2.5s 淡出语义在 home::toast 内）
        if let Some((_, at)) = &self.toast {
            if at.elapsed() > Duration::from_millis(2500) {
                self.toast = None;
            } else {
                crate::home::toast(self, ctx);
            }
        }
        if self.page != prev_page {
            self.menu_snapshot = None;
        }
        // Toast 计时需要重绘驱动
        if self.toast.is_some() || self.volume_pending.is_some() {
            ctx.request_repaint_after(Duration::from_millis(100));
        } else if !self.sessions.running().is_empty() {
            // 运行卡存活探测：会话退出后芯片免输入也能及时消失
            ctx.request_repaint_after(Duration::from_millis(500));
        }
    }
}

impl Drop for PanelApp {
    fn drop(&mut self) {
        self.sessions.shutdown();
    }
}

/// 菜单行高与悬停皮肤（高 32、行距 0、8% 白悬停洗色，详见 docs/ui/glass-recipe.md §8）。
fn menu_row_style(ui: &mut egui::Ui, t: &Tokens) {
    ui.style_mut().spacing.interact_size.y = 32.0;
    ui.style_mut().spacing.item_spacing.y = 0.0;
    let is_dark = matches!(t.kind, ThemeKind::Dark);
    let text_color = if is_dark { egui::Color32::WHITE } else { t.ink };
    let hover_fill = if is_dark {
        egui::Color32::from_rgba_premultiplied(12, 12, 12, 20)
    } else {
        egui::Color32::from_rgba_unmultiplied(0, 0, 0, 15)
    };
    let active_fill = if is_dark {
        egui::Color32::from_rgba_premultiplied(24, 24, 24, 36)
    } else {
        egui::Color32::from_rgba_unmultiplied(0, 0, 0, 26)
    };
    let widgets = &mut ui.style_mut().visuals.widgets;
    for state in [
        &mut widgets.hovered,
        &mut widgets.open,
        &mut widgets.active,
        &mut widgets.inactive,
    ] {
        state.expansion = 0.0;
        state.corner_radius = 8.into();
        state.bg_stroke = egui::Stroke::NONE;
        state.fg_stroke = egui::Stroke::new(1.0_f32, text_color);
    }
    widgets.hovered.weak_bg_fill = hover_fill;
    widgets.hovered.bg_fill = hover_fill;
    widgets.active.weak_bg_fill = active_fill;
    widgets.active.bg_fill = active_fill;
    widgets.open.weak_bg_fill = hover_fill;
    widgets.open.bg_fill = hover_fill;
    widgets.inactive.weak_bg_fill = egui::Color32::TRANSPARENT;
    widgets.inactive.bg_fill = egui::Color32::TRANSPARENT;
}

/// 菜单小节头（QML MenuSectionLabel：h20、暗色纯白/亮色 ink2、x4+8 左对齐）。
pub(crate) fn menu_caption(ui: &mut egui::Ui, t: &Tokens, label: &str) {
    let (rect, _) =
        ui.allocate_exact_size(egui::vec2(MENU_INNER_WIDTH, 20.0), egui::Sense::hover());
    let pos = egui::pos2(rect.min.x + 8.0, rect.center().y);
    let color = if matches!(t.kind, ThemeKind::Dark) {
        egui::Color32::WHITE
    } else {
        t.ink2
    };
    crate::paint::text_left_at_center(ui.painter(), pos, label, 12.0, color);
}

/// 菜单 hairline 分隔（QML：x12 w-24 h9 内 1px 线）。
pub(crate) fn menu_hairline(ui: &mut egui::Ui, t: &Tokens) {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(MENU_INNER_WIDTH, 9.0), egui::Sense::hover());
    let line = egui::Rect::from_min_size(
        egui::pos2(rect.min.x + 8.0, rect.min.y + 4.0),
        egui::vec2(MENU_INNER_WIDTH - 16.0, 1.0),
    );
    let color = if matches!(t.kind, ThemeKind::Dark) {
        egui::Color32::from_rgba_unmultiplied(255, 255, 255, 36)
    } else {
        t.hairline_on_card
    };
    ui.painter().rect_filled(line, 0, color);
}

#[cfg(test)]
mod menu_tests {
    use super::*;
    use crate::prefs::BarChoice;

    #[test]
    fn menu_geometry_constants_are_consistent() {
        assert_eq!(MENU_WIDTH, 128.0);
        assert_eq!(MENU_MARGIN, 4.0);
        assert_eq!(MENU_INNER_WIDTH, 120.0);
        assert_eq!(MENU_INNER_WIDTH + 2.0 * MENU_MARGIN, MENU_WIDTH);
    }

    #[test]
    fn fixed_aspect_resolution_matches_pyduo() {
        // 冻结表 id 直接过（label = id）
        assert_eq!(resolve_fixed_aspect("16:9", None).as_deref(), Ok("16:9"));
        // 机身对：有设备 → 「机身」；无设备 → 报状态不落库
        let body = duo_core::aspects::aspect_presets()[0].clone();
        assert_eq!(
            resolve_fixed_aspect(BODY_LANDSCAPE_ID, Some(&body)).as_deref(),
            Ok("机身")
        );
        assert_eq!(
            resolve_fixed_aspect(BODY_PORTRAIT_ID, None),
            Err("机身比例需连接设备后使用")
        );
        // 未知 id 拒绝
        assert_eq!(resolve_fixed_aspect("nope", None), Err("未知比例"));
    }

    #[test]
    fn bar_entry_clears_when_both_sides_follow_default() {
        // 上巴设沉浸 → 显式条目
        let first = bar_entry_after(None, true, Some("immersive"));
        assert_eq!(
            first,
            Some(BarChoice {
                top: Some("immersive"),
                bottom: None
            })
        );
        // 上巴也清（跟随默认）→ 两键全 None 整条退场
        let gone = bar_entry_after(first, true, None);
        assert_eq!(gone, None);
        // 单边清不退场
        let kept = bar_entry_after(first, false, Some("none"));
        assert!(kept.is_some());
    }
}

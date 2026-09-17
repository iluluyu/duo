//! 面板应用（egui）：渲染层。UI 逻辑住在 model / sessions / prefs /
//! settings_view / backend（可测纯逻辑或进程封装），本文件只做
//! immediate-mode 绘制与事件转发。结构对齐 DESIGN.md §3：顶栏胶囊 →
//! 首页（设备卡 + 固定卡 + 搜索 + 网格 + 运行卡）或设置页 → Toast。

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use eframe::egui;
use eframe::egui::{Sense, Vec2};

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

/// 引擎路径检测结果：。*/
pub(crate) type ProbeResult = Result<(String, bool, String), String>;

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
    /// DUO_SHOT_MENU=tile|tile-sub|mirror：--shot 模式注入合成右键自动
    /// 开菜单（tile-sub 再悬停「固定比例」展开二级）。对拍回路常备开关
    /// （菜单只能走 Windows exe 验：WSLg 下 popup 行为不同）。
    pub(crate) shot_menu: Option<String>,
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
            shot_menu: std::env::var("DUO_SHOT_MENU").ok(),
        };
        // QML _status_text 初始「就绪」→ 启动即挂状态 toast
        app.toast_now("就绪");
        app.ensure_watch();
        app.refresh_installed();
        app
    }

    fn ensure_watch(&mut self) {
        if self.watch.is_some() && self.watch_adb == self.adb {
            return;
        }
        self.watch = Some(DeviceWatch::start(&self.adb, 2.0));
        self.watch_adb = self.adb.clone();
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
        menu_row_style(ui);
        let pad_x = if marked.is_some() { 20.0 } else { 8.0 };
        let prev_pad = ui.spacing().button_padding;
        ui.style_mut().spacing.button_padding = egui::vec2(pad_x, 0.0);
        let btn = egui::Button::new(egui::RichText::new(label).size(13.0).color(t.ink))
            .min_size(egui::vec2(ui.available_width() - 8.0, 32.0))
            .stroke(egui::Stroke::NONE);
        let resp = ui.add(btn);
        ui.style_mut().spacing.button_padding = prev_pad;
        if marked == Some(true) {
            let dot = egui::pos2(resp.rect.min.x + 10.0, resp.rect.center().y);
            ui.painter().circle_filled(dot, 2.0, t.accent);
        }
        resp.clicked()
    }

    /// 子菜单行（QML MenuSubmenuRow：同勾选行栅格 + 右 ›；行高由
    /// interact_size 拾到 32）。ui.menu_button 在 popup 内自动切换为
    /// hover 展开的 submenu。
    fn menu_sub_button(
        &mut self,
        ui: &mut egui::Ui,
        label: &str,
        add_contents: impl FnOnce(&mut Self, &mut egui::Ui),
    ) {
        let t = self.tokens;
        menu_row_style(ui);
        let prev_pad = ui.spacing().button_padding;
        ui.style_mut().spacing.button_padding = egui::vec2(20.0, 0.0);
        ui.menu_button(egui::RichText::new(label).size(13.0).color(t.ink), |ui| {
            menu_row_style(ui);
            ui.style_mut().spacing.button_padding = egui::vec2(20.0, 0.0);
            add_contents(self, ui);
        });
        ui.style_mut().spacing.button_padding = prev_pad;
    }

    pub(crate) fn mirror_menu(&mut self, ui: &mut egui::Ui) {
        // Main.qml mirrorContextMenu 逐行对齐：打开投屏 / hairline /
        // 窗口栏直接一级平铺（上巴 沉浸/系统；下巴 沉浸/系统/不显示）
        // ——设备镜像无应用包，直接写设置页默认（setDefaultBarMode）。
        // 「镜像时关闭设备屏幕」不在 QML 菜单（设置页字段），已删。
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
            let out = std::process::Command::new(&bin)
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

    /// 保存并返回主页（QML saveChanges：空清单 = accepted → resolveAdb +
    /// pop；非空留在页内红条）。
    pub(crate) fn save_settings_and_return(&mut self) {
        self.settings.save();
        if !self.settings.dirty {
            self.page = Page::Home;
            self.resolve_adb();
            self.settings.dismiss_flash();
        }
    }

    /// 保存后重找 adb（QML Main 侧 resolveAdb 语义）。
    fn resolve_adb(&mut self) {
        self.adb = resolve_adb_path(&self.settings.draft, None, &self.adb.clone());
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
        if !ctx.is_context_menu_open() {
            return;
        }
        let t = self.tokens;
        let is_dark = matches!(t.kind, ThemeKind::Dark);
        // QML hoverWash/pressWash 令牌字面（rgba），叠在玻璃填充上
        let (hover, press) = if is_dark {
            (
                egui::Color32::from_rgba_unmultiplied(255, 255, 255, 15),
                egui::Color32::from_rgba_unmultiplied(255, 255, 255, 31),
            )
        } else {
            (
                egui::Color32::from_rgba_unmultiplied(0, 0, 0, 10),
                egui::Color32::from_rgba_unmultiplied(0, 0, 0, 20),
            )
        };
        let glass = self.settings.draft.glass_enabled;
        ctx.style_mut(|st| {
            st.visuals.window_fill = if glass { t.menu_glass } else { t.menu_fill };
            st.visuals.window_stroke = egui::Stroke::new(
                1.0_f32,
                if glass {
                    t.menu_glass_border
                } else if is_dark {
                    // Style.menuFillBorder：双主题均 10%（#1A…）
                    egui::Color32::from_rgba_unmultiplied(255, 255, 255, 26)
                } else {
                    egui::Color32::from_rgba_unmultiplied(0, 0, 0, 26)
                },
            );
            st.visuals.menu_corner_radius = 12.into();
            st.visuals.popup_shadow = Default::default();
            st.spacing.menu_margin = egui::Margin::same(4);
            st.spacing.menu_width = 128.0; // QML ctxMenu width: 128
            st.visuals.widgets.hovered.weak_bg_fill = hover;
            st.visuals.widgets.hovered.corner_radius = 10.into();
            st.visuals.widgets.active.weak_bg_fill = press;
            st.visuals.widgets.active.corner_radius = 10.into();
            // 二级展开中的触发行（widgets.open）保持洗色（QML active 联动）
            st.visuals.widgets.open.weak_bg_fill = hover;
            st.visuals.widgets.open.corner_radius = 10.into();
            st.visuals.widgets.open.bg_stroke = egui::Stroke::NONE;
            st.visuals.widgets.inactive.weak_bg_fill = egui::Color32::TRANSPARENT;
            st.visuals.widgets.inactive.corner_radius = 10.into();
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
        let mut visuals = if t.kind == ThemeKind::Light {
            egui::Visuals::light()
        } else {
            egui::Visuals::dark()
        };
        // 玻璃开 = 画布半透明，透出 DWM blur；关 = 不透明。
        let canvas = t.bg;
        visuals.panel_fill = canvas;
        visuals.window_fill = canvas;
        visuals.extreme_bg_color = t.bg;
        // 控件层级：强调色选区、可见描边、hover 洗刷（滑轨/拖手不再隐形）。
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
    }

    /// 顶栏胶囊分段导航：底胶囊手绘，两段用 ui.put 子区放按钮——文字
    /// 布局交给 egui，不做任何手工坐标（旧版手工居中有漂移 bug）。
    /// 顶栏胶囊（Main.qml topCapsule 通栏；页面之上常驻）。
    fn top_capsule(&mut self, ui: &mut egui::Ui) {
        let t = self.tokens;
        let rect = egui::Rect::from_min_size(
            egui::pos2(20.0, 16.0),
            Vec2::new(ui.max_rect().width() - 40.0, 32.0),
        );
        ui.allocate_rect(rect, Sense::hover());
        ui.painter()
            .rect_filled(rect, egui::CornerRadius::same(16), t.capsule);
        ui.painter().rect_stroke(
            rect,
            egui::CornerRadius::same(16),
            egui::Stroke::new(1.0_f32, t.card_border),
            egui::StrokeKind::Inside,
        );
        let mut clicked = None;
        for (i, (page, label)) in [(Page::Home, "首页"), (Page::Settings, "设置")]
            .into_iter()
            .enumerate()
        {
            // 分段 x2 / width/2-4 / height-4（QML CapsuleSegment 几何）
            let seg = egui::Rect::from_min_size(
                egui::pos2(
                    rect.left() + 2.0 + i as f32 * (rect.width() / 2.0 - 4.0),
                    rect.top() + 2.0,
                ),
                Vec2::new(rect.width() / 2.0 - 4.0, 28.0),
            );
            let selected = self.page == page;
            if selected {
                crate::paint::rounded_fill(ui.painter(), seg, 14.0, t.segment_fill);
            }
            let resp = ui.allocate_rect(seg, Sense::click());
            if !selected && resp.hovered() {
                crate::paint::rounded_fill(ui.painter(), seg, 14.0, t.capsule_hover);
            }
            crate::paint::text_centered(
                ui.painter(),
                seg.center(),
                label,
                13.0,
                selected,
                if selected { t.ink } else { t.ink2 },
            );
            if resp.clicked() {
                clicked = Some(page);
            }
        }
        if let Some(page) = clicked {
            self.page = page;
        }
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
            let current = app.display_prefs.get(&package).cloned();
            let pick = |app: &mut Self, ui: &mut egui::Ui, id: &str, label: &str| {
                let mark =
                    matches!(&current, Some(DisplayChoice::Fixed { aspect }) if aspect == id);
                if app.menu_item(ui, label, Some(mark)) {
                    app.set_display_fixed(&package, id);
                    ui.close_menu();
                }
            };
            menu_caption(ui, &app.tokens, "横屏");
            for preset in duo_core::aspects::aspect_presets()
                .iter()
                .filter(|p| p.landscape)
            {
                pick(app, ui, &preset.id, &preset.id);
            }
            pick(app, ui, BODY_LANDSCAPE_ID, "机身");
            menu_hairline(ui, &app.tokens);
            menu_caption(ui, &app.tokens, "竖屏");
            for preset in duo_core::aspects::aspect_presets()
                .iter()
                .filter(|p| !p.landscape)
            {
                pick(app, ui, &preset.id, &preset.id);
            }
            pick(app, ui, BODY_PORTRAIT_ID, "机身");
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

    /// 出图泵：帧数 ≥40 且 1.6s 就绪后请求 Screenshot；事件回包存盘即退。
    /// 开菜单对拍时多留时间（菜单 + 二级稳定）。
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
        if ready {
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(Default::default()));
        }
        let shot = ctx.input(|i| {
            i.events.iter().find_map(|e| match e {
                egui::Event::Screenshot { image, .. } => Some(image.clone()),
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
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    /// DUO_SHOT_MENU 注入：帧 44/45 合成右键（press→release）打开菜单；
    /// tile-sub 在帧 58 起把指针悬停到「固定比例」行（菜单几何 = 内边 4 +
    /// 打开32+置顶32+发线9+自适应32 → 触发行中心 y+125）展开二级。
    ///
    /// egui 的 hover/click 判定读 PointerState，而 PointerState 只在 pass
    /// 开头由 RawInput 推进：往 InputState.events 里塞事件对交互无效，须
    /// 用合成 RawInput 重跑 begin_pass（走真机指针路径，非旁路渲染）。
    fn pump_shot_menu(&mut self, ctx: &egui::Context) {
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
        // 二级展开后指针留在触发行（菜单不随指针离开关闭）
        let at = if mode == "tile-sub" && frames >= 58 {
            egui::pos2(pos.x + 64.0, pos.y + 125.0)
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
                if online.len() > known.len() && self.installed_bg.is_none() {
                    self.refresh_installed();
                }
            }
            self.known_online = Some(online);
        }
    }
}

impl eframe::App for PanelApp {
    fn clear_color(&self, _visuals: &egui::Visuals) -> [f32; 4] {
        let c = self.tokens.bg;
        [
            c.r() as f32 / 255.0,
            c.g() as f32 / 255.0,
            c.b() as f32 / 255.0,
            c.a() as f32 / 255.0,
        ]
    }

    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if self.shot.is_some() {
            ctx.request_repaint(); // 出图模式：静态画面也推进帧计数
        }
        let kind = ThemeKind::from_settings(&self.settings.draft.theme);
        if self.tokens.kind != kind {
            self.tokens = Tokens::of(kind);
        }
        // QML Shortcut：Ctrl+, 打开设置；Esc 在设置页 = 取消返回
        // （SettingsPage cancelled，无焦点依赖）
        if ctx.input(|i| i.modifiers.command && i.key_pressed(egui::Key::Comma)) {
            self.page = Page::Settings;
        }
        if self.page == Page::Settings && ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
            self.settings.reject();
            self.page = Page::Home;
        }
        self.sync_visuals(ctx);
        self.pump_background();
        self.pump_volume_debounce();
        self.pump_probe();
        self.skin_menus(ctx);
        self.pump_shot_menu(ctx);
        self.pump_shot(ctx);

        // 画布（bg + 六枚色斑）铺满；卡片自管边距（QML x:20 语义）
        egui::CentralPanel::default()
            .frame(egui::Frame::NONE)
            .show(ctx, |ui| {
                let full = ui.max_rect();
                ui.painter().rect_filled(full, 0, self.tokens.bg);
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
        // Toast（2.5s 淡出语义在 home::toast 内）
        if let Some((_, at)) = &self.toast {
            if at.elapsed() > Duration::from_millis(2500) {
                self.toast = None;
            } else {
                crate::home::toast(self, ctx);
            }
        }
        // Toast 计时需要重绘驱动
        if self.toast.is_some() || self.volume_pending.is_some() {
            ctx.request_repaint_after(Duration::from_millis(100));
        }
    }
}

impl Drop for PanelApp {
    fn drop(&mut self) {
        self.sessions.shutdown();
    }
}

/// 菜单行高栅格（QML MenuRow/SubmenuRow h32；SubMenuButton 高度取自
/// interact_size，Button 行由 min_size 拉齐）。DragValue 同拾 32（QML
/// NumberBox h32）。
fn menu_row_style(ui: &mut egui::Ui) {
    ui.style_mut().spacing.interact_size.y = 32.0;
}

/// 菜单小节头（QML MenuSectionLabel：h20、11px ink2、x4+8 左对齐）。
pub(crate) fn menu_caption(ui: &mut egui::Ui, t: &Tokens, label: &str) {
    let prev = ui.spacing().button_padding;
    let (rect, _) = ui.allocate_exact_size(
        egui::vec2(ui.available_width() - 8.0, 20.0),
        egui::Sense::hover(),
    );
    let pos = egui::pos2(rect.min.x + 12.0, rect.center().y);
    crate::paint::text_left_at_center(ui.painter(), pos, label, 11.0, t.ink2);
    ui.spacing_mut().button_padding = prev;
}

/// 菜单 hairline 分隔（QML：x12 w-24 h9 内 1px 线）。
pub(crate) fn menu_hairline(ui: &mut egui::Ui, t: &Tokens) {
    let w = ui.available_width() - 8.0;
    let (rect, _) = ui.allocate_exact_size(egui::vec2(w, 9.0), egui::Sense::hover());
    let line = egui::Rect::from_min_size(
        egui::pos2(rect.min.x + 12.0, rect.min.y + 4.0),
        egui::vec2(w - 24.0, 1.0),
    );
    ui.painter().rect_filled(line, 0, t.hairline_on_card);
}

#[cfg(test)]
mod menu_tests {
    use super::*;
    use crate::prefs::BarChoice;

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

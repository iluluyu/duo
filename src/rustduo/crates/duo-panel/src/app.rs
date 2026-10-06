//! 面板应用（egui）：渲染层。UI 逻辑住在 model / sessions / prefs /
//! settings_view / backend（可测纯逻辑或进程封装），本文件只做
//! immediate-mode 绘制与事件转发。结构对齐 DESIGN.md §3：顶栏胶囊 →
//! 首页（设备卡 + 固定卡 + 搜索 + 网格 + 运行卡）或设置页 → Toast。

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use eframe::egui;
use eframe::egui::{Color32, ColorImage, Pos2, Rect, Sense, TextureHandle, Vec2};

use crate::sessions;
use duo_core::aspects::{
    body_aspect_from_wm_size, preset_by_id, transposed, AspectPreset, BODY_LANDSCAPE_ID,
    BODY_PORTRAIT_ID,
};
use duo_core::settings::resolve_adb_path;

use crate::backend::{self, Background, DeviceWatch};
use crate::model::{AppEntry, AppsModel};
use crate::paint;
use crate::prefs::{
    load_audio_prefs, load_bar_prefs, load_behavior_prefs, load_density_prefs, load_display_prefs,
    load_pinned_prefs, load_scale_prefs, save_audio_prefs, save_bar_prefs, save_behavior_prefs,
    save_density_prefs, save_display_prefs, save_pinned_prefs, save_scale_prefs, BarChoice,
    DisplayChoice,
};
use crate::sessions::{panel_log_path, session_label, Sessions, MIRROR_KEY};
use crate::settings_view::SettingsPageModel;
use crate::theme::{over, ThemeKind, Tokens};
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
type InstalledResult = Result<(String, Vec<String>, Vec<backend::AppRow>), String>;

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

/// 全屏滚动自检：截图A → 直注滚动 → 截图B → diff 判决。
pub struct SelfCheck {
    pub phase: u32,
    pub frames: u32,
    pub out: String,
    pub img_a: Option<std::sync::Arc<egui::ColorImage>>,
    pub img_b: Option<std::sync::Arc<egui::ColorImage>>,
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
    /// 每台设备的已装应用原始结果（切回免重拉；内存态）。
    installed_stash: BTreeMap<String, (Vec<String>, Vec<backend::AppRow>)>,
    sweep_bg: Option<Background<Result<backend::SweepResult, String>>>,
    move_bg: Option<Background<MoveResult>>,
    volume_bg: Option<Background<()>>,
    /// 无线连接后台任务：Ok(state_name) / Err(原因) → Toast。
    pub(crate) wireless_bg: Option<Background<Result<String, String>>>,
    /// 上一帧的活动设备（变化 = 切换/插拔/离线回退）。
    effective_serial: Option<String>,

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
    /// 机身几何探测缓存（每设备，600s；None = 探测失败也缓存，免反复拉）。
    body_probe: BTreeMap<String, (Option<AspectPreset>, Instant)>,

    // UI 态
    pub search: String,
    /// 无线连接对话框：开 = 设备卡上方浮层输入 IP[:端口]。
    pub(crate) wireless_open: bool,
    /// 对话框输入框内容（打开时预填上次目标）。
    pub(crate) wireless_input: String,
    /// 上次成功/使用的无线目标（持久化 gui_prefs.json wireless 节）。
    pub(crate) wireless_target: String,
    /// 历史无线地址（最近优先；设置页设备卡/对话框共用）。
    pub(crate) wireless_recent: Vec<String>,
    /// 设备自定义名（gui_prefs devices.names；显示优先于 serial）。
    pub(crate) device_names: BTreeMap<String, String>,
    /// 设备命名对话框：Some(serial) = 打开中。
    pub(crate) rename_open: Option<String>,
    /// 设备浮层待开（等玻璃快照就绪；玻璃关/超时立开）。
    picker_pending: bool,
    picker_wait_frames: u32,
    /// 命名对话框输入框内容。
    pub(crate) rename_input: String,
    /// 显式选中的活动设备（内存态；None = 默认 USB 优先）。双在线
    /// （USB+无线）时设备卡右键可切换。
    pub(crate) active_serial: Option<String>,
    pub(crate) toast: Option<(String, Instant)>,
    /// 每台设备的音量拖动值（未动过 = 未知 -1 语义由 volume_known 表达）。
    pub(crate) media_volume: BTreeMap<String, i64>,
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
    /// DWM blur 是否已挂上（sync_glass 跳变时才调 DWM，见 blur.rs 注释）。
    pub(crate) glass_applied: bool,
    /// DUO_SELFCHECK 自检状态机（全屏滚动判决，临时诊断工具）。
    pub(crate) selfcheck: Option<Box<SelfCheck>>,
    /// 自检结束后的画面停留截止（供外部 GDI 实拍）。
    pub(crate) selfcheck_hold_until: std::time::Instant,
    /// 标签拇指动画（起点 x, 起始时刻）；ease-out 见 top_capsule。
    pub(crate) tab_anim: Option<(f32, std::time::Instant)>,
    /// 标签拇指上帧渲染位（动画起点的真值来源）。
    pub(crate) tab_pos: Option<f32>,

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
    /// 图标切换过渡状态（键=包名；见 home.rs IconFade）。
    pub(crate) icon_fades: RefCell<BTreeMap<String, crate::home::IconFade>>,
    /// 玻璃岛贴图缓存（0=胶囊岛 1=搜索岛）：键=（页,滚动量化,暗色,
    /// 岛矩形）。
    pub(crate) island_tex: RefCell<[Option<(IslandKey, TextureHandle)>; 2]>,
    /// 本帧两块岛是否画成（内层控件据此省掉自身填充，杜绝双层）。
    pub(crate) island_drawn: [bool; 2],
    /// 岛背板内容剖面（内容坐标，含滚动前偏移）：设置页=纵向色带；
    /// 首页=色块列表。各页 show() 每帧刷新。
    pub(crate) island_bands: Vec<(f32, f32, Color32)>,
    pub(crate) island_blocks: Vec<(Rect, Color32)>,
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
            installed_stash: BTreeMap::new(),
            sweep_bg: None,
            move_bg: None,
            volume_bg: None,
            wireless_bg: None,
            effective_serial: None,
            apps: AppsModel::default(),
            sessions: Sessions::new(),
            pinned: load_pinned_prefs().into_iter().map(|p| (p, true)).collect(),
            display_prefs: load_display_prefs(),
            bar_prefs: load_bar_prefs(),
            audio_prefs: load_audio_prefs(),
            behavior_prefs: load_behavior_prefs(),
            density_prefs: load_density_prefs(),
            scale_prefs: load_scale_prefs(),
            body_probe: BTreeMap::new(),
            volume_pending: None,
            shot: None,
            search: String::new(),
            // DUO_SHOT_WIRELESS=1：--shot 出图预开无线对话框（同 DUO_SHOT_MENU 语法）。
            wireless_open: std::env::var("DUO_SHOT_WIRELESS").is_ok(),
            wireless_input: String::new(),
            wireless_target: crate::prefs::load_wireless_target(),
            wireless_recent: crate::prefs::load_wireless_recent(),
            device_names: crate::prefs::load_device_names(),
            rename_open: std::env::var("DUO_SHOT_RENAME")
                .ok()
                .map(|_| "192.168.1.100:5555".to_string()),
            picker_pending: false,
            picker_wait_frames: 0,
            rename_input: String::new(),
            active_serial: None,
            toast: None,
            media_volume: BTreeMap::new(),
            grid_scroll: 0.0,
            settings_scroll: 0.0,
            probe_bg: None,
            probe_pill: None,
            shot_capture: false,
            shot_capture_frame: 0,
            glass_applied: false,
            selfcheck_hold_until: std::time::Instant::now(),
            tab_anim: None,
            tab_pos: None,

            selfcheck: std::env::var("DUO_SELFCHECK").ok().map(|p| {
                Box::new(SelfCheck {
                    phase: 0,
                    frames: 0,
                    out: p,
                    img_a: None,
                    img_b: None,
                })
            }),
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
            icon_fades: RefCell::new(BTreeMap::new()),
            island_tex: RefCell::new([None, None]),
            island_drawn: [false, false],
            island_bands: Vec::new(),
            island_blocks: Vec::new(),
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

    /// QML device/fallbackDevice 语义 + 多设备扩展：在线时 serial =
    /// 活动裁决（显式选择 > USB 优先），多台时状态行报台数。
    pub(crate) fn device_summary(&self) -> (String, Option<String>, usize, bool) {
        let Some(watch) = &self.watch else {
            return ("设备监控未启动（缺 duo-core）".into(), None, 0, false);
        };
        let states = watch.states();
        if states.is_empty() {
            return ("未连接设备".into(), None, 0, false);
        }
        let online = watch.online();
        if online.is_empty() {
            let (serial, state) = states
                .iter()
                .next()
                .map(|(k, v)| (k.clone(), Self::state_text(v)))
                .unwrap();
            return (state.to_string(), Some(serial), 0, true);
        }
        let state = if online.len() > 1 {
            format!("{} 台设备在线", online.len())
        } else {
            "在线".into()
        };
        (
            state,
            backend::pick_active_serial(&online, self.active_serial.as_deref()),
            online.len(),
            !states.is_empty(),
        )
    }

    pub(crate) fn running_chips(&self) -> Vec<(String, String, bool)> {
        self.sessions
            .running()
            .into_iter()
            .map(|(key, label)| {
                let package = key.split_once("::").map(|(_, p)| p).unwrap_or(key.as_str());
                let clickable = package != sessions::MIRROR_KEY;
                (key, label, clickable)
            })
            .collect()
    }

    pub(crate) fn stop_session(&mut self, key: &str) {
        self.sessions.stop(key);
    }

    /// 运行卡点击（复合键）：按各自 serial 拉回虚拟屏。
    pub(crate) fn focus_session(&mut self, key: &str) {
        if let Some((serial, package)) = key.split_once("::") {
            self.move_app_to_display(serial, package);
        }
    }

    pub(crate) fn volume_dragged(&mut self, index: i64) {
        if let Some(serial) = self.serial() {
            self.media_volume.insert(serial, index);
        }
        self.volume_pending = Some((index, Instant::now()));
    }

    pub(crate) fn volume_known(&self) -> bool {
        self.serial()
            .is_some_and(|s| self.media_volume.contains_key(&s))
    }

    pub(crate) fn volume_value(&self) -> i64 {
        self.serial()
            .and_then(|s| self.media_volume.get(&s).copied())
            .unwrap_or(0)
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
            crate::theme::srgba(255, 255, 255, 200)
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

    /// 设备卡右键：多在线时先选设备（显式选择覆盖 USB 优先默认），
    /// 无线连接入口，常驻无线各自可断（与当前选择无关）。
    pub(crate) fn device_menu(&mut self, ui: &mut egui::Ui) {
        let online = self.watch.as_ref().map(|w| w.online()).unwrap_or_default();
        if online.len() > 1 {
            let selected = self.serial();
            menu_caption(ui, &self.tokens, "设备");
            for serial in &online {
                let label = if serial.contains(':') {
                    format!("无线 {serial}")
                } else {
                    format!("USB {serial}")
                };
                let marked = selected.as_deref() == Some(serial.as_str());
                if self.menu_item(ui, &label, Some(marked)) {
                    self.active_serial = Some(serial.clone());
                    ui.close_menu();
                }
            }
            menu_hairline(ui, &self.tokens);
        }
        if self.menu_item(ui, "无线连接…", None) {
            self.open_wireless();
            ui.close_menu();
        }
        let wireless: Vec<String> = online.iter().filter(|s| s.contains(':')).cloned().collect();
        if !wireless.is_empty() {
            menu_hairline(ui, &self.tokens);
            for serial in &wireless {
                if self.menu_item(ui, &format!("断开 {serial}"), None) {
                    self.disconnect_wireless_target(serial);
                    ui.close_menu();
                }
            }
        }
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

    /// 玻璃弹层快照引导（右键菜单/设备浮层共用）：建 menu_glass；无
    /// 快照时发一次全窗截图请求（贴图由各弹层的 glass_record_main
    /// 逐帧构建）。玻璃关 = no-op（走 window_fill 不透明退化）。
    pub(crate) fn ensure_menu_glass(&mut self, ctx: &egui::Context) {
        if self.menu_glass.is_none() && self.settings.draft.glass_enabled {
            self.menu_glass = Some(MenuGlass {
                menu_rect: self.menu_rect_hint,
                snapshot: self.menu_snapshot.clone(),
                snapshot_ppp: self.menu_snapshot_ppp,
                requested: self.menu_snapshot.is_some(),
                ..Default::default()
            });
            if self.menu_snapshot.is_none() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::new(
                    GLASS_SHOT_TAG.to_string(),
                )));
                ctx.request_repaint();
            }
        }
    }

    pub(crate) fn context_menu(
        &mut self,
        resp: &egui::Response,
        add_contents: impl FnOnce(&mut Self, &mut egui::Ui),
    ) {
        self.ensure_menu_glass(&resp.ctx);
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
                crate::theme::srgba(0, 0, 0, 15),
                crate::theme::srgba(0, 0, 0, 26),
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
                        crate::theme::srgba(255, 255, 255, 26)
                    } else {
                        crate::theme::srgba(0, 0, 0, 26)
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
        let Some(watch) = &self.watch else {
            return None;
        };
        backend::pick_active_serial(&watch.online(), self.active_serial.as_deref())
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
            let serial_out = serial.clone();
            backend::query_apps(&binary.display().to_string(), &adb, &serial).map(|rows| {
                let installed = rows.iter().map(|r| r.package.clone()).collect();
                (serial_out, installed, rows)
            })
        }));
    }

    /// 应用列表重建（stash 命中与后台收编共用）。
    fn rebuild_apps(&mut self, installed: &[String], rows: &[backend::AppRow]) {
        self.apps.rebuild(installed, &self.pinned);
        let extras: Vec<String> = rows
            .iter()
            .filter(|r| !r.catalog)
            .map(|r| r.package.clone())
            .collect();
        self.apps.merge_third_party(&extras, &self.pinned);
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

    fn adopt_installed(&mut self, result: InstalledResult) {
        match result {
            Ok((serial, installed, rows)) => {
                self.installed_stash
                    .insert(serial.clone(), (installed.clone(), rows.clone()));
                // 结果带着发起时的 serial；已切到别的设备就只入仓不洗列表。
                if self.serial().as_deref() == Some(serial.as_str()) {
                    self.rebuild_apps(&installed, &rows);
                    self.start_sweep();
                }
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
        if self.sessions.is_running(&serial, package) {
            self.move_app_to_display(&serial, package);
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
        let serial = self.serial()?;
        if let Some((preset, at)) = self.body_probe.get(&serial) {
            if at.elapsed() < Duration::from_secs(600) {
                return preset.clone();
            }
        }
        let output = winproc::quiet_command(&self.adb)
            .args(["-s", &serial, "shell", "wm", "size"])
            .output()
            .ok()?;
        let text = String::from_utf8_lossy(&output.stdout).into_owned();
        let preset = body_aspect_from_wm_size(&text);
        self.body_probe
            .insert(serial, (preset.clone(), Instant::now()));
        preset
    }

    pub(crate) fn move_app_to_display(&mut self, serial: &str, package: &str) {
        let adb = self.adb.clone();
        let package = package.to_string();
        let serial = serial.to_string();
        let log = panel_log_path(&serial, &package);
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
        if self.sessions.is_running(&serial, MIRROR_KEY) {
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

    // ------------------------------------------------------------ wireless

    /// 打开无线连接对话框（预填上次目标；焦点在输入框）。
    pub(crate) fn open_wireless(&mut self) {
        self.wireless_input = self.wireless_target.clone();
        self.wireless_open = true;
    }

    /// 后台发起 `duo-core connect`：目标归一在 duo-core 侧（裸 IP 补
    /// :5555）；成功记忆目标 + 关对话框，2s 监视循环自动抬升设备。
    pub(crate) fn start_wireless_connect(&mut self) {
        if self.wireless_bg.is_some() {
            return; // 上一次连接还在途，不叠加
        }
        let raw = self.wireless_input.trim().to_string();
        if raw.is_empty() {
            self.toast_now("请输入设备 IP（如 192.168.1.100）");
            return;
        }
        let Some(binary) = self.duo_core.clone() else {
            self.toast_now("找不到 duo-core 二进制");
            return;
        };
        let adb = self.adb.clone();
        self.toast_now(format!("正在连接 {raw} …"));
        self.wireless_bg = Some(Background::spawn(move || {
            let bin = binary.display().to_string();
            backend::connect_wireless(&bin, &adb, &raw).map(|value| {
                let state = value
                    .get("state")
                    .and_then(|s| s.as_str())
                    .unwrap_or("connected")
                    .to_string();
                let target = value
                    .get("target")
                    .and_then(|t| t.as_str())
                    .unwrap_or_default()
                    .to_string();
                if target.is_empty() {
                    state
                } else {
                    format!("{state}:{target}")
                }
            })
        }));
    }

    /// 断开指定无线设备（serial 含 `:`，来自设备菜单）。
    pub(crate) fn disconnect_wireless_target(&mut self, serial: &str) {
        let Some(binary) = self.duo_core.clone() else {
            self.toast_now("找不到 duo-core 二进制");
            return;
        };
        let adb = self.adb.clone();
        self.toast_now(format!("正在断开 {serial} …"));
        let serial_for_bg = serial.to_string();
        self.wireless_bg = Some(Background::spawn(move || {
            let bin = binary.display().to_string();
            backend::disconnect_wireless(&bin, &adb, Some(&serial_for_bg))
                .map(|_| "disconnected".to_string())
        }));
    }

    /// 在线设备行（设置页设备卡/设备卡切换器）：(无线?, serial, 选中?)。
    pub(crate) fn device_rows(&self) -> Vec<(bool, String, bool)> {
        let online = self.watch.as_ref().map(|w| w.online()).unwrap_or_default();
        let selected = self.serial();
        online
            .into_iter()
            .map(|serial| {
                let is_selected = selected.as_deref() == Some(serial.as_str());
                (serial.contains(':'), serial, is_selected)
            })
            .collect()
    }

    /// 显式选活动设备（设备卡浮层/右键菜单同一入口）；手动切换给
    /// toast（离线自动回退另有文案）。
    pub(crate) fn select_device(&mut self, serial: &str) {
        if self.serial().as_deref() != Some(serial) {
            self.active_serial = Some(serial.to_string());
            let tag = if serial.contains(':') {
                "无线"
            } else {
                "USB"
            };
            let short = serial.split(':').next().unwrap_or(serial);
            self.toast_now(format!("已切换到 {tag} {short}"));
        }
    }

    /// 应用列表探测进行中（网格区加载态）。
    pub(crate) fn apps_loading(&self) -> bool {
        self.installed_bg.is_some()
    }

    /// 设备显示名：自定义名 > serial 去端口前段。
    pub(crate) fn device_display(&self, serial: &str) -> String {
        self.device_names
            .get(serial)
            .cloned()
            .unwrap_or_else(|| serial.split(':').next().unwrap_or(serial).to_string())
    }

    /// 设备浮层开门节流：玻璃开时等全窗快照就绪再开（右键菜单是先热
    /// 快照后开菜单，popover 点击即开则贴图永远迟到一步）；玻璃关或等
    /// 待超 30 帧直接开。返回本帧是否应开。
    pub(crate) fn picker_open_tick(&mut self) -> bool {
        if !self.picker_pending {
            return false;
        }
        self.picker_wait_frames += 1;
        let warm = self.menu_snapshot.is_some();
        if !self.settings.draft.glass_enabled || warm || self.picker_wait_frames > 30 {
            self.picker_pending = false;
            self.picker_wait_frames = 0;
            return true;
        }
        false
    }

    /// 请求开设备浮层（点击/出图钩子）。返回 true = 立即开；false =
    /// 已排队等快照（就绪/超时后 picker_open_tick 放行）。
    pub(crate) fn request_picker(&mut self, ctx: &egui::Context) -> bool {
        self.ensure_menu_glass(ctx);
        if !self.settings.draft.glass_enabled || self.menu_snapshot.is_some() {
            return true;
        }
        if !self.picker_pending {
            self.picker_pending = true;
            self.picker_wait_frames = 0;
        }
        false
    }

    /// 打开设备命名对话框（预填现名）。
    pub(crate) fn open_rename(&mut self, serial: &str) {
        self.rename_input = self.device_names.get(serial).cloned().unwrap_or_default();
        self.rename_open = Some(serial.to_string());
    }

    /// 保存命名（空白 = 清除），刷新内存表并 toast。
    pub(crate) fn save_rename(&mut self) {
        let Some(serial) = self.rename_open.take() else {
            return;
        };
        let name = self.rename_input.trim().to_string();
        crate::prefs::save_device_name(&serial, &name);
        self.device_names = crate::prefs::load_device_names();
        if name.is_empty() {
            self.toast_now("已清除设备命名");
        } else {
            self.toast_now(format!("已命名为 {name}"));
        }
    }

    /// 设备行共享渲染：USB 绿/无线蓝胶囊 + 主文本（名或 serial 前段）
    /// + 命名设备次行 serial（full_secondary=设置页排障全文）+ 选中蓝点。
    pub(crate) fn paint_device_row(
        &self,
        painter: &egui::Painter,
        rect: Rect,
        is_wifi: bool,
        serial: &str,
        selected: bool,
        full_secondary: bool,
    ) {
        let t = self.tokens;
        let tag = Rect::from_min_size(
            egui::pos2(rect.left() + 8.0, rect.center().y - 11.0),
            egui::vec2(46.0, 22.0),
        );
        let tint = if is_wifi {
            over(t.bg, t.accent, 0.16)
        } else {
            over(t.bg, t.running, 0.16)
        };
        paint::rounded_fill(painter, tag, 11.0, tint);
        paint::text_centered(
            painter,
            tag.center(),
            if is_wifi { "无线" } else { "USB" },
            11.0,
            false,
            t.ink2,
        );
        let short = serial.split(':').next().unwrap_or(serial);
        let name = self.device_names.get(serial);
        let text_x = tag.right() + 10.0;
        let max_w = (rect.right() - 44.0 - text_x).max(24.0);
        let elide = |text: &str, size: f32| {
            paint::elide_to_width(text, max_w, &|c| {
                if c.is_ascii() {
                    size * 0.55
                } else {
                    size
                }
            })
        };
        match name {
            Some(name) => {
                let name_line = elide(name, 13.0);
                paint::text_left(
                    painter,
                    egui::pos2(text_x, rect.center().y - 8.0),
                    &name_line,
                    13.0,
                    t.ink,
                );
                let secondary = if full_secondary { serial } else { short };
                let sub_line = elide(secondary, 11.0);
                paint::text_left(
                    painter,
                    egui::pos2(text_x, rect.center().y + 9.0),
                    &sub_line,
                    11.0,
                    t.ink2,
                );
            }
            None => {
                let line = elide(short, 13.0);
                paint::text_left(
                    painter,
                    egui::pos2(text_x, rect.center().y),
                    &line,
                    13.0,
                    t.ink,
                );
            }
        }
        if selected {
            painter.circle_filled(
                egui::pos2(rect.right() - 18.0, rect.center().y),
                3.0,
                t.accent,
            );
        }
    }

    /// 设备卡浮层（点卡展开，2026-10-06 Opus 规格 + 双模型 QA 修复）：
    /// 宽度对齐设备卡（调用方不再 set_width）；行 60 高（双行：名
    /// 13px + serial 11px），静止态每行弱填充（防白色断带），hover 全
    /// 洗色；选中蓝点右置、hover 时让位「改名」（同色互斥）；末行分
    /// 隔线 +「添加设备…」。玻璃贴图与右键菜单同源（ensure_menu_glass
    /// 引导）。
    pub(crate) fn device_picker(&mut self, ui: &mut egui::Ui) {
        let rows = self.device_rows();
        let t = self.tokens;
        let weak_fill = {
            let h = t.hover_on_canvas;
            egui::Color32::from_rgba_unmultiplied(h.r(), h.g(), h.b(), 30)
        };
        ui.style_mut().spacing.item_spacing.y = 0.0;
        // 兜底卡底：玻璃贴图就绪前的帧垫不透明菜单底色（就绪后由毛玻璃
        // 接管，跳过）。预占底位、内容定形后回填矩形。
        let tex_ready = self
            .menu_glass
            .as_ref()
            .and_then(|g| g.main_tex.as_ref())
            .is_some();
        let bg_idx = if tex_ready {
            None
        } else {
            Some(ui.painter().add(egui::Shape::Noop))
        };
        for (is_wifi, serial, selected) in &rows {
            let row_resp =
                ui.allocate_response(egui::vec2(ui.available_width(), 60.0), egui::Sense::click());
            let rect = row_resp.rect;
            let hover = row_resp.hovered();
            let fill = if hover { t.hover_on_canvas } else { weak_fill };
            paint::rounded_fill(ui.painter(), rect, 10.0, fill);
            // 传输胶囊
            let tag = Rect::from_min_size(
                egui::pos2(rect.left() + 8.0, rect.center().y - 11.0),
                egui::vec2(46.0, 22.0),
            );
            let tint = if *is_wifi {
                over(t.bg, t.accent, 0.16)
            } else {
                over(t.bg, t.running, 0.16)
            };
            paint::rounded_fill(ui.painter(), tag, 11.0, tint);
            paint::text_centered(
                ui.painter(),
                tag.center(),
                if *is_wifi { "无线" } else { "USB" },
                11.0,
                false,
                t.ink2,
            );
            // 文本：命名 = 双行（名/serial），未命名 = 单行 serial 前段
            let text_x = tag.right() + 10.0;
            let max_w = (rect.right() - 72.0 - text_x).max(24.0);
            let elide = |text: &str, size: f32| {
                paint::elide_to_width(text, max_w, &|c| {
                    if c.is_ascii() {
                        size * 0.55
                    } else {
                        size
                    }
                })
            };
            let short = serial.split(':').next().unwrap_or(serial);
            match self.device_names.get(serial) {
                Some(name) => {
                    let name_line = elide(name, 13.0);
                    paint::text_left(
                        ui.painter(),
                        egui::pos2(text_x, rect.top() + 21.0),
                        &name_line,
                        13.0,
                        t.ink,
                    );
                    let sub_line = elide(short, 11.0);
                    paint::text_left(
                        ui.painter(),
                        egui::pos2(text_x, rect.top() + 42.0),
                        &sub_line,
                        11.0,
                        t.ink2,
                    );
                }
                None => {
                    let line = elide(short, 13.0);
                    paint::text_left(
                        ui.painter(),
                        egui::pos2(text_x, rect.center().y),
                        &line,
                        13.0,
                        t.ink,
                    );
                }
            }
            if *selected && !hover {
                ui.painter().circle_filled(
                    egui::pos2(rect.right() - 18.0, rect.center().y),
                    3.0,
                    t.accent,
                );
            }
            if row_resp.clicked() {
                let serial = serial.clone();
                self.select_device(&serial);
                ui.ctx().memory_mut(|m| m.close_popup());
            }
            if hover {
                let btn = Rect::from_min_size(
                    egui::pos2(rect.right() - 60.0, rect.center().y - 12.0),
                    egui::vec2(48.0, 24.0),
                );
                let resp = ui.allocate_rect(btn, egui::Sense::click());
                paint::text_centered(ui.painter(), btn.center(), "改名", 11.0, false, t.accent);
                if resp.clicked() {
                    let serial = serial.clone();
                    self.open_rename(&serial);
                    ui.ctx().memory_mut(|m| m.close_popup());
                }
            }
        }
        let sep = ui.available_rect_before_wrap();
        let y = sep.top() + 4.5;
        ui.add_space(9.0);
        ui.painter().line_segment(
            [egui::pos2(sep.left(), y), egui::pos2(sep.right(), y)],
            egui::Stroke::new(1.0_f32, self.tokens.hairline_on_card),
        );
        // 添加设备（无线连接）：矢量加号 + 文字；与设备行区隔（透明静止态）
        let add =
            ui.allocate_response(egui::vec2(ui.available_width(), 48.0), egui::Sense::click());
        let rect = add.rect;
        let wash = if add.hovered() {
            self.tokens.hover_on_canvas
        } else {
            egui::Color32::TRANSPARENT
        };
        paint::rounded_fill(ui.painter(), rect, 10.0, wash);
        let c = egui::pos2(rect.left() + 31.0, rect.center().y);
        let stroke = egui::Stroke {
            width: 1.6,
            color: self.tokens.accent,
        };
        ui.painter().line_segment(
            [egui::pos2(c.x - 5.0, c.y), egui::pos2(c.x + 5.0, c.y)],
            stroke,
        );
        ui.painter().line_segment(
            [egui::pos2(c.x, c.y - 5.0), egui::pos2(c.x, c.y + 5.0)],
            stroke,
        );
        paint::text_left(
            ui.painter(),
            egui::pos2(rect.left() + 64.0, rect.center().y),
            "添加设备…",
            13.0,
            self.tokens.ink,
        );
        if add.clicked() {
            self.open_wireless();
            ui.ctx().memory_mut(|m| m.close_popup());
        }
        if let Some(idx) = bg_idx {
            let rect = ui
                .min_rect()
                .expand2(egui::vec2(8.0, 6.0))
                .intersect(ui.max_rect());
            ui.painter().set(
                idx,
                egui::Shape::rect_filled(
                    rect,
                    12.0,
                    egui::Color32::from_rgba_unmultiplied(
                        t.menu_fill.r(),
                        t.menu_fill.g(),
                        t.menu_fill.b(),
                        244,
                    ),
                ),
            );
        }
    }

    /// 遗忘一个历史无线地址（设置页设备卡）。
    pub(crate) fn forget_wireless(&mut self, addr: &str) {
        crate::prefs::forget_wireless_target(addr);
        self.wireless_recent = crate::prefs::load_wireless_recent();
        self.wireless_target = crate::prefs::load_wireless_target();
        self.toast_now(format!("已忘记 {addr}"));
    }

    /// 直连一个历史地址（设置页设备卡）。
    pub(crate) fn connect_wireless_addr(&mut self, addr: &str) {
        self.wireless_input = addr.to_string();
        self.start_wireless_connect();
    }

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

    /// DWM blur 状态机：开关跳变时才碰 DWM（每帧直调会拖慢系统合成——
    /// 拖任意窗口偶发卡顿的来源）；主题切换重挂一次换染色。
    fn sync_glass(&mut self, frame: &eframe::Frame) {
        let want = self.settings.draft.glass_enabled;
        if want != self.glass_applied {
            if want {
                crate::blur::apply_glass(frame, self.tokens.blur_tint());
            } else {
                crate::blur::clear_glass(frame);
            }
            self.glass_applied = want;
        }
    }

    fn pump_selfcheck(&mut self, ctx: &egui::Context) {
        let Some(sc) = self.selfcheck.as_mut() else {
            return;
        };
        sc.frames += 1;
        ctx.request_repaint(); // 自检期间保持帧推进（响应式渲染默认无事件即停）
        let tag = |p: &str| egui::UserData::new(p.to_string());
        let take_shot = |ctx: &egui::Context, want: &str| {
            ctx.input(|i| {
                i.events.iter().find_map(|e| match e {
                    egui::Event::Screenshot {
                        image, user_data, ..
                    } if user_data_eq(user_data, want) => Some(image.clone()),
                    _ => None,
                })
            })
        };
        match sc.phase {
            0 if sc.frames >= 90 => {
                self.page = match std::env::var("DUO_SELFCHECK_PAGE").as_deref() {
                    Ok("home") => Page::Home,
                    _ => Page::Settings,
                };
                if let Ok(theme) = std::env::var("DUO_SELFCHECK_THEME") {
                    self.settings.draft.theme = theme;
                }
                if std::env::var("DUO_SELFCHECK_GLASS").as_deref() == Ok("0") {
                    self.settings.draft.glass_enabled = false;
                }
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(tag("selfcheck-a")));
                sc.phase = 1;
                ctx.request_repaint();
            }
            1 => {
                if sc.frames % 45 == 0 {
                    ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(tag("selfcheck-a")));
                }
                if let Some(img) = take_shot(ctx, "selfcheck-a") {
                    sc.img_a = Some(img);
                    if std::env::var("DUO_SELFCHECK_DRAG").as_deref() == Ok("1") {
                        #[cfg(target_os = "windows")]
                        crate::winproc::send_drag();
                        sc.phase = 2;
                        ctx.request_repaint();
                    } else if std::env::var("DUO_SELFCHECK_WHEEL").as_deref() == Ok("1") {
                        // 真输入路径取证：win32 合成滚轮（默认 3 格 -120）
                        #[cfg(target_os = "windows")]
                        crate::winproc::send_wheel(-120);
                        sc.phase = 2;
                        ctx.request_repaint();
                    } else {
                        let v: f32 = std::env::var("DUO_SELFCHECK_SCROLL")
                            .ok()
                            .and_then(|x| x.parse().ok())
                            .unwrap_or(400.0);
                        self.settings_scroll = v; // 绕过输入系统直注
                        self.grid_scroll = v;
                        sc.phase = 2;
                        ctx.request_repaint();
                    }
                }
            }
            2 if sc.frames % 12 == 0 => {
                sc.phase = 3;
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(tag("selfcheck-b")));
                ctx.request_repaint();
            }
            3 => {
                if sc.frames % 45 == 0 {
                    ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(tag("selfcheck-b")));
                }
                if let Some(img) = take_shot(ctx, "selfcheck-b") {
                    sc.img_b = Some(img);
                    sc.phase = 4;
                }
            }
            _ => {}
        }
        if sc.phase == 4 {
            let (a, b) = (sc.img_a.clone().unwrap(), sc.img_b.clone().unwrap());
            let clamped = self.settings_scroll;
            let n = a.width().min(b.width()) * a.height().min(b.height());
            let mut diff_px = 0usize;
            if a.width() == b.width() && a.height() == b.height() {
                for (pa, pb) in a.pixels.iter().zip(b.pixels.iter()) {
                    if pa.r() as i32 - pb.r() as i32 != 0
                        || pa.g() as i32 - pb.g() as i32 != 0
                        || pa.b() as i32 - pb.b() as i32 != 0
                    {
                        diff_px += 1;
                    }
                }
            }
            let save = |img: &egui::ColorImage, p: &str| {
                let pixels: Vec<u8> = img
                    .pixels
                    .iter()
                    .flat_map(|c| [c.r(), c.g(), c.b()])
                    .collect();
                let _ = image::save_buffer(
                    p,
                    &pixels,
                    img.width() as u32,
                    img.height() as u32,
                    image::ColorType::Rgb8,
                );
            };
            let dir = std::path::Path::new(&sc.out)
                .parent()
                .map(|p| p.to_path_buf())
                .unwrap_or_default();
            let case = std::env::var("DUO_SELFCHECK_CASE").unwrap_or_else(|_| "x".into());
            save(
                &a,
                &dir.join(format!("selfcheck_{case}_a.png"))
                    .to_string_lossy(),
            );
            save(
                &b,
                &dir.join(format!("selfcheck_{case}_b.png"))
                    .to_string_lossy(),
            );
            let verdict = format!(
                "screen={:?}\nppp={}\nscroll_set=400 scroll_after_clamp={}\ndiff_px={} of {} ({:.2}%)\nscrolled={}\nwheel_hits={} drag_hits={} down_frames={} press={}\n",
                ctx.screen_rect(),
                ctx.pixels_per_point(),
                clamped,
                diff_px, n,
                diff_px as f32 * 100.0 / n.max(1) as f32,
                diff_px as f32 > n as f32 * 0.005,
                crate::settings::WHEEL_HITS.load(std::sync::atomic::Ordering::Relaxed),
                crate::settings::DRAG_HITS.load(std::sync::atomic::Ordering::Relaxed),
                crate::settings::PTR_DOWN_FRAMES.load(std::sync::atomic::Ordering::Relaxed),
                crate::settings::PTR_PRESS.load(std::sync::atomic::Ordering::Relaxed),
            );
            let _ = std::fs::write(&sc.out, verdict);
            self.selfcheck = None;
            let hold: u64 = std::env::var("DUO_SELFCHECK_HOLD")
                .ok()
                .and_then(|x| x.parse().ok())
                .unwrap_or(0);
            if hold > 0 {
                let deadline = std::time::Instant::now() + std::time::Duration::from_secs(hold);
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(tag("selfcheck-hold")));
                let _ = deadline;
            }
            self.selfcheck_hold_until =
                std::time::Instant::now() + std::time::Duration::from_secs(hold);
            ctx.request_repaint();
        }
    }

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
                    crate::theme::srgba(255, 255, 255, 26)
                } else {
                    crate::theme::srgba(0, 0, 0, 26)
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

    /// 滚动层级虚化（参考 ColorOS 17 / iOS 导航栏景深）：玻璃+动画都开
    /// 时，页顶盖 bg 色软凸形渐变幕（双端零透明度，无硬边/无横线——
    /// 2026-09-20 用户反馈：横线多余、亮色发闷、上下有色彩断层）。
    /// 任一开关关闭则不画（原始硬裁边，零开销）。
    /// 悬浮玻璃岛（七修：活合成背板——内容色全知，无需截图。键=
    /// 页/滚动量化/主题，滚动即重建（56×12 图，微秒级）→ 模糊永远
    /// 跟手、零闪烁；染层烤入贴图=单层无错位）。胶囊岛用纵向色带
    /// 剖面（设置页卡片带 / 首页设备卡）。
    #[allow(clippy::too_many_arguments)]
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn island_glass(
        &self,
        ctx: &egui::Context,
        pill: Rect,
        slot: usize,
        page_tag: u8,
        scroll: f32,
        bands: &[(f32, f32, Color32)],
        blocks: &[(Rect, Color32)],
    ) -> Option<TextureHandle> {
        let dark = matches!(self.tokens.kind, ThemeKind::Dark);
        let key = (
            page_tag,
            (scroll / 3.0).round() as u32,
            if dark { 1 } else { 0 },
            (ctx.pixels_per_point() * 8.0).round() as u8,
            pill,
        );
        if let Some((k, tex)) = self.island_tex.borrow().get(slot).and_then(|e| e.as_ref()) {
            if *k == key {
                return Some(tex.clone());
            }
        }
        // 细背板：1 设备像素/格，外扩 8px（lensing 采样边距）
        let ppp = ctx.pixels_per_point().max(0.1);
        let margin = 8.0;
        let cover = pill.expand(margin);
        let w = (cover.width() * ppp).round() as usize;
        let h = (cover.height() * ppp).round() as usize;
        if w < 8 || h < 8 {
            return None;
        }
        // 首帧巨窗（resize 前 max_rect 可达 ~8000 逻辑点）× 150% 缩放会
        // 造出超适配器上限的贴图直接炸 wgpu 验证（2026-10-06 真机定位，
        // 九修引入）；超限帧回落纯色岛，真实窗口尺寸永远远小于上限。
        let max_side = ctx.input(|i| i.max_texture_side);
        if w > max_side || h > max_side {
            return None;
        }
        let bg = self.tokens.bg;
        let mut syn = ColorImage {
            size: [w, h],
            pixels: vec![bg; w * h],
        };
        for row in 0..h {
            let page_y = cover.top() + (row as f32 + 0.5) / h as f32 * cover.height();
            let content_y = page_y + scroll;
            let mut c = bg;
            for (y0, y1, col) in bands.iter().rev() {
                if content_y >= *y0 && content_y < *y1 {
                    c = *col;
                    break;
                }
            }
            for col_i in 0..w {
                let page_x = cover.left() + (col_i as f32 + 0.5) / w as f32 * cover.width();
                let mut px = c;
                for (r, col) in blocks {
                    if page_x >= r.left()
                        && page_x < r.right()
                        && content_y >= r.top()
                        && content_y < r.bottom()
                    {
                        px = *col;
                        break;
                    }
                }
                syn.pixels[row * w + col_i] = px;
            }
        }
        let knobs = crate::glass::LiquidKnobs::resting(pill.height().min(pill.width()), dark);
        let tex = crate::glass::build_liquid_texture(
            ctx,
            &format!("duo-island-{slot}"),
            &syn,
            cover,
            pill,
            ppp,
            dark,
            &knobs,
        );
        if let Some(t) = &tex {
            self.island_tex.borrow_mut()[slot] = Some((key, t.clone()));
        }
        tex
    }

    /// 胶囊岛绘制（内容之上、胶囊之下）。
    fn floating_glass(&mut self, ui: &mut egui::Ui) {
        if !(self.settings.draft.glass_enabled && cfg!(target_os = "windows")) {
            return;
        }
        let full = ui.max_rect();
        // 八修：岛=轨道同矩形（旧外扩 12/6 的"大外圈胶囊"读作双层
        // 且像 BUG）——玻璃即控件本体（iOS 26 液态玻璃分段语义）
        let cap_w = 200.0_f32.min(full.width() - 40.0).max(0.0);
        let pill = Rect::from_min_size(
            Pos2::new(full.center().x - cap_w / 2.0, full.top() + 16.0),
            Vec2::new(cap_w, 32.0),
        );
        let (page_tag, scroll) = match self.page {
            Page::Home => (0u8, 0.0),
            Page::Settings => (1u8, self.settings_scroll),
        };
        let bands = self.island_bands.clone();
        let blocks = self.island_blocks.clone();
        let tex = self.island_glass(ui.ctx(), pill, 0, page_tag, scroll, &bands, &blocks);
        self.island_drawn[0] = tex.is_some();
        if let Some(tex) = tex {
            // 浮岛软投影（Opus Q2：blur 8 α0.08，克制不抢 rim）
            let shadow = egui::Shadow {
                offset: [0, 2],
                blur: 8,
                spread: 0,
                color: crate::theme::srgba(0, 0, 0, 20),
            };
            ui.painter()
                .add(shadow.as_shape(pill, egui::CornerRadius::same(16)));
            ui.painter().add(egui::Shape::image(
                tex.id(),
                pill.expand(8.0),
                egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
                crate::theme::srgba(255, 255, 255, 255),
            ));
            // （十修）外描边删除：液态贴图自带 rim，描边=用户判读的
            // 「多余细线」
        }
    }

    /// 顶栏分段导航（2026-09-19 修订，学 iOS UISegmentedControl /
    /// COLOROS 17 分段控件）：紧凑居中胶囊轨道（不再通栏对半），拇指
    /// 滑动动画 + 轻投影，亮色 = 灰轨道 vs 纯白拇指的强对比（旧版
    /// 白上白零对比）。段内文字交给 egui 居中，坐标仅定段矩形。
    fn top_capsule(&mut self, ui: &mut egui::Ui) {
        let t = self.tokens;
        let full = ui.max_rect();
        // Apple 式克制分段控件（2026-09-19 gemini-3.8-flash 重估定稿，
        // 配方与禁忌见 docs/ui/DESIGN.md §3.2）：200×32（420 面板的 ~48%，
        // 过窄读作 UISwitch）；端头同心：轨道 r16 − 拇指 r13 = 内缩 3；
        // 无高光条/无内发光边——靠微色阶 + 柔影而非玻璃仿真。
        let track_h = 32.0_f32;
        let inset = 3.0;
        let thumb_h = track_h - 2.0 * inset;
        let cap_w = (2.0 * 97.0 + 2.0 * inset).min(full.width() - 40.0).max(0.0);
        let rect = egui::Rect::from_min_size(
            egui::pos2(full.center().x - cap_w / 2.0, full.top() + 16.0),
            Vec2::new(cap_w, track_h),
        );
        ui.allocate_rect(rect, Sense::hover());
        let is_dark = t.kind == ThemeKind::Dark;
        // 七修补丁：岛画成时轨道全透（岛材质即轨道；旧半透 α=「透明层」
        // 双层残留），岛缺失帧回退半透轨道
        let track = if self.settings.draft.glass_enabled {
            if self.island_drawn[0] {
                egui::Color32::TRANSPARENT
            } else {
                crate::theme::srgba(
                    t.segment_track.r(),
                    t.segment_track.g(),
                    t.segment_track.b(),
                    if is_dark { 185 } else { 205 },
                )
            }
        } else {
            t.segment_track
        };
        crate::paint::rounded_fill(ui.painter(), rect, 16.0, track);
        // 结构线（1px、几不可见）：亮色专用；暗色靠亮度差，不描。
        if !is_dark {
            let inner = egui::Rect::from_min_size(
                egui::pos2(rect.left() + 0.5, rect.top() + 0.5),
                Vec2::new(rect.width() - 1.0, rect.height() - 1.0),
            );
            ui.painter().rect_stroke(
                inner,
                egui::CornerRadius::same(16),
                egui::Stroke::new(1.0_f32, egui::Color32::from_black_alpha(10)),
                egui::StrokeKind::Inside,
            );
        }
        let seg_w = ((rect.width() - 2.0 * inset) / 2.0).floor();
        let target_x = if self.page == Page::Home {
            rect.left() + inset
        } else {
            rect.right() - inset - seg_w
        };
        // 拇指动画（2026-09-20 二修：首版 get_or_insert 直接把目标当
        // 原点，Δ恒 0 → 动画退化成瞬移；改为记忆上帧渲染位，目标变化时
        // 从当前位置 ease-out-cubic 0.18s，中断可续接）。
        let x = if self.settings.draft.animations_enabled {
            let now = std::time::Instant::now();
            let prev = self.tab_pos.unwrap_or(target_x);
            if (prev - target_x).abs() <= 0.5 {
                self.tab_anim = None;
                self.tab_pos = Some(target_x);
                target_x
            } else {
                let (from, t0) = self.tab_anim.unwrap_or((prev, now));
                let p = ease_out_cubic(now.duration_since(t0).as_secs_f32() / 0.18);
                let cur = from + (target_x - from) * p;
                if p < 1.0 {
                    self.tab_anim = Some((from, t0));
                    ui.ctx().request_repaint();
                } else {
                    self.tab_anim = None;
                }
                self.tab_pos = Some(cur);
                cur
            }
        } else {
            self.tab_anim = None;
            self.tab_pos = None;
            target_x
        };
        let thumb =
            egui::Rect::from_min_size(egui::pos2(x, rect.top() + inset), Vec2::new(seg_w, thumb_h));
        // 拇指（gemini 方案 A：Apple HIG 精修）——纯色填充 + 1px 发丝描边
        // + 紧致双层浅影：杜绝渐变发脏与缝隙焦黑（禁忌清单见
        // docs/ui/DESIGN.md §3.2）。
        // 玻璃上拇指清晰度（gemini 交叉验证 #3：透明轨道上拇指需自带
        // 对比——暗色提亮+强高光边，亮色加描边+重影）
        // 十三修：亮色线条减负——白拇指砍接触影+发丝线（旧版
        // rim+双影+发丝+低对比边 4 层线=认知负担），只留一层柔影；
        // 暗色保持双影+发丝（强对比下读作一个整体）
        let (fill, hairline) = if is_dark {
            (
                egui::Color32::from_rgb(0x48, 0x48, 0x4A),
                egui::Color32::from_white_alpha(52),
            )
        } else {
            (egui::Color32::WHITE, egui::Color32::TRANSPARENT)
        };
        let contact = egui::Shadow {
            offset: [0, 1],
            blur: 1,
            spread: 0,
            color: egui::Color32::from_black_alpha(if is_dark { 96 } else { 0 }),
        };
        let ambient = egui::Shadow {
            offset: [0, 2],
            blur: 4,
            spread: 0,
            color: egui::Color32::from_black_alpha(if is_dark { 60 } else { 30 }),
        };
        if is_dark {
            for sh in [&ambient, &contact] {
                ui.painter()
                    .add(sh.as_shape(thumb, egui::CornerRadius::same(13)));
            }
        } else {
            ui.painter()
                .add(ambient.as_shape(thumb, egui::CornerRadius::same(13)));
        }
        crate::paint::rounded_fill(ui.painter(), thumb, 13.0, fill);
        if hairline != egui::Color32::TRANSPARENT {
            ui.painter().rect_stroke(
                egui::Rect::from_min_size(
                    egui::pos2(thumb.left() + 0.5, thumb.top() + 0.5),
                    Vec2::new(thumb.width() - 1.0, thumb.height() - 1.0),
                ),
                egui::CornerRadius::same(13),
                egui::Stroke::new(1.0_f32, hairline),
                egui::StrokeKind::Inside,
            );
        }
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
            let seg = egui::Rect::from_min_size(
                egui::pos2(seg_x, rect.top() + inset),
                Vec2::new(seg_w, thumb_h),
            );
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
                false, // Apple 分段控件：两段同字重，选中态靠颜色/拇指区分
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
        let body = self.body_preset();
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
                            // r20 重写后旧贴图滞留：清缓存让下帧重读；
                            // 同路径重写靠重置过渡状态触发交叉淡化。
                            self.icon_tex.borrow_mut().clear();
                            for pkg in &patched {
                                self.icon_fades
                                    .borrow_mut()
                                    .insert(pkg.clone(), crate::home::IconFade::replaced());
                            }
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
        if let Some(bg) = self.wireless_bg.take() {
            match bg.take() {
                Some(Ok(state)) => {
                    if state.starts_with("disconnected") {
                        self.toast_now("已断开无线设备");
                    } else if state.starts_with("already-connected:") {
                        let target = state.trim_start_matches("already-connected:");
                        self.wireless_target = target.to_string();
                        crate::prefs::save_wireless_target(target);
                        self.wireless_recent = crate::prefs::load_wireless_recent();
                        self.toast_now(format!("{target} 已在连接中"));
                        self.wireless_open = false;
                    } else {
                        let target = state.trim_start_matches("connected:");
                        self.wireless_target = target.to_string();
                        crate::prefs::save_wireless_target(target);
                        self.wireless_recent = crate::prefs::load_wireless_recent();
                        self.toast_now(format!(
                            "已连接 {target}（若长时间未上线，请检查设备授权）"
                        ));
                        self.wireless_open = false;
                    }
                }
                Some(Err(err)) => self.toast_now(format!("无线连接失败：{err}")),
                None => self.wireless_bg = Some(bg),
            }
        }
        // 活动设备变化（切换/插拔/离线回退）：换应用列表（stash 命中即换，
        // 否则现拉）；离线触发的回退给 toast（手动切换不吭声）。
        let effective = self.serial();
        if self.effective_serial != effective {
            let prev = self.effective_serial.clone();
            self.effective_serial = effective.clone();
            if let Some(serial) = &effective {
                if let Some((installed, rows)) = self.installed_stash.get(serial).cloned() {
                    self.rebuild_apps(&installed, &rows);
                    self.start_sweep();
                } else if self.installed_bg.is_none() && !self.skip_sweep {
                    self.refresh_installed();
                }
            }
            if let Some(p) = prev {
                let prev_online = self.watch.as_ref().map(|w| w.online()).unwrap_or_default();
                if !prev_online.contains(&p) {
                    match &effective {
                        Some(n) => self.toast_now(format!("设备 {p} 离线，已切换到 {n}")),
                        None => self.toast_now(format!("设备 {p} 已离线")),
                    }
                }
            }
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
    /// eframe wgpu 路径不回传设备纹理上限（glow 路径有），低限适配器
    /// （本机 8192）上 CJK 图集行宽可顶爆 16384 默认 → wgpu 验证崩溃。
    /// 兜底 8192：图集行改向下增长，全平台安全（2026-10 真机定位）。
    fn raw_input_hook(&mut self, _ctx: &egui::Context, raw_input: &mut egui::RawInput) {
        if raw_input.max_texture_side.is_none() {
            raw_input.max_texture_side = Some(8192);
        }
    }

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
        if self.selfcheck.is_none() && self.selfcheck_hold_until > std::time::Instant::now() {
            ctx.request_repaint();
            return; // 停留：冻结画面供外部实拍
        }
        self.pump_selfcheck(ctx);
        self.sync_glass(frame);
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
            self.glass_applied = false; // 换 tint 重挂一次 DWM blur
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
        // 离开首页即收起无线对话框（模态不跨页驻留）。
        if self.page != prev_page {
            self.wireless_open = false;
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

        // 画布铺满（色斑 2026-09-20 撤除）；卡片自管边距（QML x:20 语义）
        egui::CentralPanel::default()
            .frame(egui::Frame::NONE)
            .show(ctx, |ui| {
                let full = ui.max_rect();
                let bg = if self.settings.draft.glass_enabled && cfg!(target_os = "windows") {
                    crate::theme::srgba(
                        self.tokens.bg.r(),
                        self.tokens.bg.g(),
                        self.tokens.bg.b(),
                        216,
                    )
                } else {
                    self.tokens.bg
                };
                ui.painter().rect_filled(full, 0, bg);
                // 岛背板剖面每帧重建（页内 show() 填充）
                self.island_bands.clear();
                self.island_blocks.clear();
                self.island_drawn = [false, false];
                match self.page {
                    Page::Home => {
                        crate::home::show(self, ui);
                    }
                    Page::Settings => {
                        self.settings_page(ui);
                    }
                }
                // 悬浮玻璃岛（胶囊之下、内容之上）
                self.floating_glass(ui);
                self.top_capsule(ui);
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

/// ease-out cubic（p∈[0,1]，尾端减速；标签拇指/图标过渡动画用）。
pub(crate) fn ease_out_cubic(p: f32) -> f32 {
    let p = p.clamp(0.0, 1.0);
    1.0 - (1.0 - p).powi(3)
}

/// 岛贴图缓存键：页 / 滚动量化 / 暗色 / 岛矩形。
type IslandKey = (u8, u32, u8, u8, Rect);

/// 菜单行高与悬停皮肤（高 32、行距 0、8% 白悬停洗色，详见 docs/ui/glass-recipe.md §8）。
fn menu_row_style(ui: &mut egui::Ui, t: &Tokens) {
    ui.style_mut().spacing.interact_size.y = 32.0;
    ui.style_mut().spacing.item_spacing.y = 0.0;
    let is_dark = matches!(t.kind, ThemeKind::Dark);
    let text_color = if is_dark { egui::Color32::WHITE } else { t.ink };
    let hover_fill = if is_dark {
        egui::Color32::from_rgba_premultiplied(12, 12, 12, 20)
    } else {
        crate::theme::srgba(0, 0, 0, 15)
    };
    let active_fill = if is_dark {
        egui::Color32::from_rgba_premultiplied(24, 24, 24, 36)
    } else {
        crate::theme::srgba(0, 0, 0, 26)
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
        crate::theme::srgba(255, 255, 255, 36)
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

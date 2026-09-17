//! duo-panel：Duo 面板的 Rust/egui 起步（TODO 0.4 v0）。
//!
//! UI 逻辑与渲染分离：tiles / settings_view / launch / theme 是零 egui
//! 依赖（或仅色值类型）的可测纯逻辑；app.rs 只做 immediate-mode 绘制。
//! 玻璃 = 窗口透明 + Windows 真系统级 DWM blur（blur.rs），退役 C#
//! overlay 的手采样亚克力路线。

pub mod app;
pub mod backend;
pub mod blur;
pub mod fonts;
pub mod home;
pub mod model;
pub mod paint;
pub mod pinyin;
pub mod prefs;
pub mod sessions;
pub mod settings;
mod settings_view;
pub mod theme;
pub mod winproc;

use eframe::egui;

/// 截图模式参数（--shot PATH [--page home|settings] [--ppp N]）。
pub struct ShotArgs {
    pub path: String,
    pub page: String,
    pub ppp: f32,
    pub theme: Option<String>,
}

fn parse_shot_args() -> Option<ShotArgs> {
    let args: Vec<String> = std::env::args().collect();
    let path = args.iter().position(|a| a == "--shot")?;
    let path = args.get(path + 1)?.clone();
    let page = args
        .iter()
        .position(|a| a == "--page")
        .and_then(|i| args.get(i + 1))
        .cloned()
        .unwrap_or_else(|| "home".into());
    let ppp = args
        .iter()
        .position(|a| a == "--ppp")
        .and_then(|i| args.get(i + 1))
        .and_then(|v| v.parse().ok())
        .unwrap_or(1.25);
    let theme = args
        .iter()
        .position(|a| a == "--theme")
        .and_then(|i| args.get(i + 1))
        .cloned();
    Some(ShotArgs {
        path,
        page,
        ppp,
        theme,
    })
}

/// 进程入口（main.rs 只留薄壳）：单实例守卫 + egui 运行；--shot 出图后自退。
pub fn run() {
    let shot = parse_shot_args();
    if shot.is_none() && !winproc::single_instance("DuoPanel") {
        winproc::notify_already_running("Duo 面板已在运行。");
        return;
    }
    let options = eframe::NativeOptions {
        viewport: viewport(),
        ..Default::default()
    };
    if let Err(err) = eframe::run_native(
        "Duo",
        options,
        Box::new(move |cc| {
            let mut app = app::PanelApp::new(cc);
            if let Some(s) = &shot {
                if s.page == "settings" {
                    app.page = app::Page::Settings;
                }
                if let Some(theme) = &s.theme {
                    app.settings.draft.theme = theme.clone();
                }
                app.shot = Some((s.path.clone(), 0, std::time::Instant::now()));
            }
            Ok(Box::new(app))
        }),
    ) {
        eprintln!("duo-panel: {err}");
        std::process::exit(1);
    }
}

/// 窗口形态：对齐 PyQt 面板（420×660，最小 360×520，标题 Duo）。
pub fn viewport() -> egui::ViewportBuilder {
    let mut builder = egui::ViewportBuilder::default()
        .with_title("Duo")
        .with_inner_size([420.0, 660.0])
        .with_min_inner_size([360.0, 520.0])
        .with_transparent(true);
    if let Some(icon) = load_icon() {
        builder = builder.with_icon(icon);
    }
    builder
}

fn load_icon() -> Option<egui::IconData> {
    let bytes = include_bytes!("../../../../../assets/duo.png");
    let img = image::load_from_memory(bytes).ok()?.into_rgba8();
    let (width, height) = img.dimensions();
    Some(egui::IconData {
        rgba: img.into_raw(),
        width,
        height,
    })
}

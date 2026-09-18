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
pub mod glass;
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
#[cfg(unix)]
pub mod xtest;

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
    let mut viewport = viewport();
    if let Some(s) = &shot {
        // 高分取证：视口物理尺寸×ppp + 钉住 pixels_per_point，出图即
        // 设备像素（Wayland 无头端平台 ppp 恒 1，两者一起才生效）。
        if s.ppp > 0.0 {
            viewport = viewport.with_inner_size([420.0 * s.ppp, 660.0 * s.ppp]);
        }
        // 宽屏取证：DUO_SHOT_W/H 覆盖逻辑尺寸（默认 420x660）。
        let env_f = |k: &str| {
            std::env::var(k).ok().and_then(|v| v.parse::<f32>().ok())
        };
        let sw = env_f("DUO_SHOT_W").unwrap_or(420.0);
        let sh = env_f("DUO_SHOT_H").unwrap_or(660.0);
        let ppp = s.ppp.max(1.0);
        viewport = viewport.with_inner_size([sw * ppp, sh * ppp]);
    }
    let options = eframe::NativeOptions {
        viewport,
        ..Default::default()
    };
    if let Err(err) = eframe::run_native(
        "Duo",
        options,
        Box::new(move |cc| {
            let mut app = app::PanelApp::new(cc);
            if let Some(s) = &shot {
                if s.ppp > 0.0 {
                    cc.egui_ctx.set_pixels_per_point(s.ppp);
                }
            }
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

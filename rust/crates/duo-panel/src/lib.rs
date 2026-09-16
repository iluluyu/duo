//! duo-panel：Duo 面板的 Rust/egui 起步（TODO 0.4 v0）。
//!
//! UI 逻辑与渲染分离：tiles / settings_view / launch / theme 是零 egui
//! 依赖（或仅色值类型）的可测纯逻辑；app.rs 只做 immediate-mode 绘制。
//! 玻璃 = 窗口透明 + Windows 真系统级 DWM blur（blur.rs），退役 C#
//! overlay 的手采样亚克力路线。

pub mod app;
pub mod blur;
pub mod fonts;
pub mod launch;
pub mod settings_view;
pub mod theme;
pub mod tiles;

use eframe::egui;

/// 窗口形态：对齐 PyQt 面板（420×660，最小 360×520，标题 Duo）。
pub fn viewport() -> egui::ViewportBuilder {
    egui::ViewportBuilder::default()
        .with_title("Duo")
        .with_inner_size([420.0, 660.0])
        .with_min_inner_size([360.0, 520.0])
        // 透明画布：clear_color 带 alpha 才能透出系统 blur。
        .with_transparent(true)
}

/// 进程入口（main.rs 只留薄壳）。
pub fn run() {
    let options = eframe::NativeOptions {
        viewport: viewport(),
        ..Default::default()
    };
    if let Err(err) = eframe::run_native(
        "Duo",
        options,
        Box::new(|cc| Ok(Box::new(app::PanelApp::new(cc)))),
    ) {
        eprintln!("duo-panel: {err}");
        std::process::exit(1);
    }
}

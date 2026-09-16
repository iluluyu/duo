//! CJK 系统字体探测（egui 默认字体无中文，标签会变豆腐块）。
//!
//! Windows 优先微软雅黑/等线，Linux 回退 Noto Sans CJK / 文泉驿。
//! 找不到任何候选 = 不注入（v0 降级：西文仍正常）。探测表本身纯数据，
//! 可测；加载在 main 里做一次。

/// 各平台按序探测的系统字体路径。
pub fn cjk_font_candidates() -> Vec<&'static str> {
    if cfg!(target_os = "windows") {
        vec![
            r"C:\Windows\Fonts\msyh.ttc", // 微软雅黑（Segoe UI 同源的
            // Windows 中文正装）
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\Deng.ttf", // 等线
        ]
    } else if cfg!(target_os = "macos") {
        vec![
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
        ]
    } else {
        vec![
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
        ]
    }
}

/// 第一个存在的候选（加载用）。
pub fn first_existing_cjk_font() -> Option<&'static str> {
    cjk_font_candidates()
        .into_iter()
        .find(|p| std::path::Path::new(p).exists())
}

/// Segoe UI 候选（Windows；QML Style.fontDefault）。
pub fn segoe_candidates() -> Vec<&'static str> {
    vec![
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\SegUIVar.ttf",
    ]
}

/// 字体栈注入（QML fontDefault = "Segoe UI"，CJK 走系统回退）：
/// Proportional = [Segoe, CJK]，中文由 CJK 字体兜底，拉丁走 Segoe。
pub fn install_fonts(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    let mut stack: Vec<String> = Vec::new();
    if let Some(path) = segoe_candidates()
        .into_iter()
        .find(|p| std::path::Path::new(p).exists())
    {
        if let Ok(bytes) = std::fs::read(path) {
            fonts.font_data.insert(
                "duo-segoe".into(),
                std::sync::Arc::new(egui::FontData::from_owned(bytes)),
            );
            stack.push("duo-segoe".into());
        }
    }
    if let Some(path) = first_existing_cjk_font() {
        if let Ok(bytes) = std::fs::read(path) {
            fonts.font_data.insert(
                "duo-cjk".into(),
                std::sync::Arc::new(egui::FontData::from_owned(bytes)),
            );
            stack.push("duo-cjk".into());
        }
    }
    for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
        let list = fonts.families.entry(family).or_default();
        for name in &stack {
            list.push(name.clone());
        }
    }
    // DemiBold 档：独立 family（QML Font.DemiBold 的近似，Segoe/雅黑粗体）
    let bold_stack = ["duo-bold-segoe", "duo-bold-cjk"];
    let segoe_b = r"C:\Windows\Fonts\segoeuib.ttf";
    if let Ok(bytes) = std::fs::read(segoe_b) {
        fonts.font_data.insert(
            bold_stack[0].into(),
            std::sync::Arc::new(egui::FontData::from_owned(bytes)),
        );
    }
    let cjk_b = r"C:\Windows\Fonts\msyhbd.ttc";
    if let Ok(bytes) = std::fs::read(cjk_b) {
        fonts.font_data.insert(
            bold_stack[1].into(),
            std::sync::Arc::new(egui::FontData::from_owned(bytes)),
        );
    }
    fonts.families.insert(
        egui::FontFamily::Name("duo-bold".into()),
        bold_stack.iter().map(|s| s.to_string()).collect(),
    );
    ctx.set_fonts(fonts);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candidates_are_platform_sorted_non_empty() {
        let list = cjk_font_candidates();
        assert!(!list.is_empty());
        assert!(list.iter().all(|p| p.len() > 3));
    }

    #[test]
    fn first_existing_is_a_file_or_none() {
        // 只验证合同：返回值要么 None，要么指向存在的文件。
        match first_existing_cjk_font() {
            None => {}
            Some(p) => assert!(std::path::Path::new(p).exists()),
        }
    }
}

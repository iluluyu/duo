//! CJK 系统字体探测（egui 默认字体无中文，标签会变豆腐块）。
//!
//! Windows 优先微软雅黑/等线，Linux 回退 Noto Sans CJK / 文泉驿。
//! 找不到任何候选 = 不注入（v0 降级：西文仍正常）。探测表本身纯数据，
//! 可测；加载在 main 里做一次。

/// 各平台按序探测的系统字体路径。
pub fn cjk_font_candidates() -> Vec<&'static str> {
    if cfg!(target_os = "windows") {
        vec![
            r"C:\Windows\Fonts\msyh.ttc", // 微软雅黑（2026-10-06 复议
            // 定稿：等线正文用户看着费力，正文回雅黑；粗体仍等线 Bold
            // （duo-bold 栈，避开雅黑 Bold 塑料感））
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\Deng.ttf",
        ]
    } else if cfg!(target_os = "macos") {
        vec![
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
        ]
    } else {
        vec![
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
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
    // DemiBold 档：独立 family（QML Font.DemiBold 的近似，Segoe/雅黑粗体）。
    // family 恒非空（常规栈 + egui 内置兑底）：空 family 会静默不绘制——
    // WSLg 无粗体时磁贴首字消失即此根因；缺字 ≤ 字重降档。
    let mut bold_stack: Vec<String> = Vec::new();
    let segoe_b = r"C:\Windows\Fonts\segoeuib.ttf";
    if let Ok(bytes) = std::fs::read(segoe_b) {
        fonts.font_data.insert(
            "duo-bold-segoe".into(),
            std::sync::Arc::new(egui::FontData::from_owned(bytes)),
        );
        bold_stack.push("duo-bold-segoe".into());
    }
    // 粗体中文优先等线 Bold（雅黑 Bold 笔画过重=「塑料感」），缺则回落雅黑
    let cjk_b_candidates: &[&str] = if cfg!(target_os = "windows") {
        &[
            r"C:\Windows\Fonts\Dengb.ttf",
            r"C:\Windows\Fonts\msyhbd.ttc",
        ]
    } else {
        &["/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"]
    };
    let cjk_b = cjk_b_candidates
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .copied();
    if let Some(cjk_b) = cjk_b {
        if let Ok(bytes) = std::fs::read(cjk_b) {
            fonts.font_data.insert(
                "duo-bold-cjk".into(),
                std::sync::Arc::new(egui::FontData::from_owned(bytes)),
            );
            bold_stack.push("duo-bold-cjk".into());
        }
    }
    bold_stack.extend(stack.iter().cloned());
    bold_stack.extend(
        fonts
            .families
            .get(&egui::FontFamily::Proportional)
            .cloned()
            .unwrap_or_default(),
    );
    fonts
        .families
        .insert(egui::FontFamily::Name("duo-bold".into()), bold_stack);
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

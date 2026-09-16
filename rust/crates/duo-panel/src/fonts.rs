//! CJK 系统字体探测（egui 默认字体无中文，标签会变豆腐块）。
//!
//! Windows 优先微软雅黑/等线，Linux 回退 Noto Sans CJK / 文泉驿。
//! 找不到任何候选 = 不注入（v0 降级：西文仍正常）。探测表本身纯数据，
//! 可测；加载在 main 里做一次。

/// 各平台按序探测的系统字体路径。
pub fn cjk_font_candidates() -> Vec<&'static str> {
    if cfg!(target_os = "windows") {
        vec![
            r"C:\Windows\Fonts\msyh.ttc",      // 微软雅黑（Segoe UI 同源的
                                                // Windows 中文正装）
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\Deng.ttf",      // 等线
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

/// 把系统 CJK 字体注入 egui 字体表（proportional/monospace 的兜底位）。
pub fn install_cjk_font(ctx: &egui::Context) {
    let Some(path) = first_existing_cjk_font() else {
        return;
    };
    let Ok(bytes) = std::fs::read(path) else {
        return;
    };
    let mut fonts = egui::FontDefinitions::default();
    fonts
        .font_data
        .insert("duo-cjk".into(), std::sync::Arc::new(egui::FontData::from_owned(bytes)));
    for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
        fonts.families.entry(family).or_default().push("duo-cjk".into());
    }
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

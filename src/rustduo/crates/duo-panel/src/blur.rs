// 窗口级 DWM 模糊合成配方见 docs/ui/glass-recipe.md
// 调用约定：apply/clear 只在开关跳变时调一次（app.rs sync_glass 保证；
// 每帧直调 DwmEnableBlurBehindWindow 会拖慢系统合成——拖窗口卡顿
// 的主要来源，2026-09-20 修复）。

/// 应用一次窗口级 blur（幂等：只在 glass 开时首次调用）。
#[cfg(target_os = "windows")]
pub fn apply_glass(frame: &eframe::Frame, tint: [u8; 4]) {
    use raw_window_handle::HasWindowHandle;
    if let Ok(handle) = frame.window_handle() {
        if let Err(err) =
            window_vibrancy::apply_blur(handle, Some((tint[0], tint[1], tint[2], tint[3])))
        {
            eprintln!("duo-panel: DWM blur 失败（{err}）——窗口退化为半透明底");
        }
    }
}

/// 移除窗口级 blur（glass 关时回退不透明原始窗口）。
#[cfg(target_os = "windows")]
pub fn clear_glass(frame: &eframe::Frame) {
    use raw_window_handle::HasWindowHandle;
    if let Ok(handle) = frame.window_handle() {
        if let Err(err) = window_vibrancy::clear_blur(handle) {
            eprintln!("duo-panel: DWM blur 清除失败（{err}）");
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub fn apply_glass(_frame: &eframe::Frame, _tint: [u8; 4]) {}

#[cfg(not(target_os = "windows"))]
pub fn clear_glass(_frame: &eframe::Frame) {}

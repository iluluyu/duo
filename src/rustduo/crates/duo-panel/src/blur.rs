// 窗口级 DWM 模糊合成配方见 docs/ui/glass-recipe.md

/// 应用一次窗口级 blur（幂等：调用方保证只调一次）。
#[cfg(target_os = "windows")]
pub fn apply_glass(frame: &eframe::Frame, tint: [u8; 4]) {
    use raw_window_handle::HasWindowHandle;
    if let Ok(handle) = frame.window_handle() {
        if let Err(err) =
            window_vibrancy::apply_blur(&handle, Some((tint[0], tint[1], tint[2], tint[3])))
        {
            eprintln!("duo-panel: DWM blur 失败（{err}）——窗口退化为半透明底");
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub fn apply_glass(_frame: &eframe::Frame, _tint: [u8; 4]) {}

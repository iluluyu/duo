//! Windows 玻璃：真系统级 DWM blur（window-vibrancy），非 C# 手采样亚克力。
//!
//! 面板背景 = `theme.canvas(glass)` 的半透明色 + 窗口级 blur behind：
//! 毛玻璃由系统合成器完成（对齐 Windows 11 材质秩序），退役旧 C# overlay
//! 里逐像素采样的亚克力路线。Linux/非 Windows：恒 no-op（窗口保持
//! 半透明纯色底，功能不阻塞）。

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

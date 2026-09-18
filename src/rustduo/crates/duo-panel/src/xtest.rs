//! 出图 harness 的 X11 指针合成（XTEST）：egui 的 hover/click 旗标在
//! pass 开头由真指针输入推进，进程内重跑 begin_pass 造不出命中，故
//! DUO_SHOT_MENU 注入走系统事件路径。仅 unix（Windows 走原合成）。

use eframe::egui::Pos2;
use std::sync::OnceLock;
use x11_dl::xlib::Xlib;
use x11_dl::xtest::Xf86vmode as XTest;

static X11: OnceLock<Option<(Xlib, XTest)>> = OnceLock::new();

fn x11() -> Option<&'static (Xlib, XTest)> {
    X11.get_or_init(|| Some((Xlib::open().ok()?, XTest::open().ok()?)))
        .as_ref()
}

/// 窗口客户区原点的屏幕绝对坐标（物理 px；winit 的 egui 坐标系即客户区）。
pub(crate) fn client_origin(window_id: u64) -> Option<(i32, i32)> {
    let (xlib, _) = x11()?;
    unsafe {
        let dpy = (xlib.XOpenDisplay)(std::ptr::null());
        if dpy.is_null() {
            return None;
        }
        let root = (xlib.XDefaultRootWindow)(dpy);
        let mut rx = 0_i32;
        let mut ry = 0_i32;
        let mut child = 0_u64;
        let ok =
            (xlib.XTranslateCoordinates)(dpy, window_id, root, 0, 0, &mut rx, &mut ry, &mut child);
        (xlib.XCloseDisplay)(dpy);
        if ok == 0 {
            None
        } else {
            Some((rx, ry))
        }
    }
}

/// 真指针序列：移动 → 按下 → 释放（右键）或仅移动/左键。跨帧落点，
/// 在独立线程调用（内含实时 sleep）。
pub(crate) fn pointer_sequence(
    origin: (i32, i32),
    points: &[(Pos2, PointerAction)],
    pixels_per_point: f32,
) {
    let Some((xlib, xtest)) = x11() else { return };
    unsafe {
        let dpy = (xlib.XOpenDisplay)(std::ptr::null());
        if dpy.is_null() {
            return;
        }
        for (pos, action) in points {
            let x = origin.0 + (pos.x * pixels_per_point) as i32;
            let y = origin.1 + (pos.y * pixels_per_point) as i32;
            (xtest.XTestFakeMotionEvent)(dpy, -1, x, y, 0);
            (xlib.XFlush)(dpy);
            if *action == PointerAction::ClickRight {
                std::thread::sleep(std::time::Duration::from_millis(120));
                (xtest.XTestFakeButtonEvent)(dpy, 3, 1, 0);
                (xlib.XFlush)(dpy);
                std::thread::sleep(std::time::Duration::from_millis(80));
                (xtest.XTestFakeButtonEvent)(dpy, 3, 0, 0);
                (xlib.XFlush)(dpy);
            }
            std::thread::sleep(std::time::Duration::from_millis(160));
        }
        (xlib.XCloseDisplay)(dpy);
    }
}

#[derive(Clone, Copy, PartialEq)]
pub(crate) enum PointerAction {
    Move,
    ClickRight,
}

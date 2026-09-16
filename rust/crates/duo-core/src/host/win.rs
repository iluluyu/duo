//! EmbedHost/EmbedBand 的 Win32 落地（docs/window-experience.md §14）。
//! 对译源 duo/resources/chrome_overlay.cs：宿主隐身等待→像素级接管→样式
//! 手术→100ms tick 漂移 Glue→子窗消亡宿主自杀；焦点走 AttachThreadInput；
//! immersive 变体由 BandWin（WS_EX_LAYERED 子窗 + UpdateLayeredWindow）供
//! 标题栏操作面。纯数学全在父模块（跨平台可测）。

use super::{
    child_ex_after_surgery, child_style_after_surgery, child_style_needs_surgery,
    embed_client_target, glue_drifted, hit_button, is_double_click, paint_band, tick_action,
    BandLayout, EngineState, GlyphMask, HostEvent, HostOptions, HostStyle, PxRect, TickAction,
    CLOSE_GRACE_MS, GLYPH_CLOSE, GLYPH_MAXIMIZE, GLYPH_MINIMIZE, GLYPH_RESTORE, MIN_TRACK_H,
    MIN_TRACK_W, TICK_MS, WS_MAXIMIZEBOX, WS_MINIMIZEBOX, WS_POPUP, WS_SYSMENU, WS_THICKFRAME,
};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};
use windows::core::{w, HSTRING, PCWSTR};
use windows::Win32::Foundation::{
    COLORREF, HINSTANCE, HWND, HMODULE, LPARAM, LRESULT, POINT, RECT, SIZE, WPARAM,
};
use windows::Win32::Graphics::Dwm::{
    DwmSetWindowAttribute, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND,
};
use windows::Win32::Graphics::Gdi::{
    BLENDFUNCTION, BI_RGB, BITMAPINFO, BITMAPINFOHEADER, CreateCompatibleDC, CreateDIBSection,
    CreateFontW, DeleteDC, DeleteObject, DIB_RGB_COLORS, GdiFlush, GetDC, GetTextExtentPoint32W,
    GetTextFaceW, HDC, HFONT, HGDIOBJ, OPAQUE, ReleaseDC, ScreenToClient, SelectObject, SetBkColor,
    SetBkMode, SetTextColor, TextOutW, AC_SRC_ALPHA, AC_SRC_OVER,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetDoubleClickTime, ReleaseCapture, SetFocus, TrackMouseEvent, TME_LEAVE, TRACKMOUSEEVENT,
};
use windows::Win32::UI::WindowsAndMessaging::*;

const WM_MOUSELEAVE: u32 = 0x02A3;
const TIMER_ID: usize = 1;
const NO_SWP: SET_WINDOW_POS_FLAGS = SET_WINDOW_POS_FLAGS(0);

const HOST_CLASS: PCWSTR = w!("DuoEmbedHost");
const BAND_CLASS: PCWSTR = w!("DuoEmbedBand");

fn log(msg: &str) {
    eprintln!("host: {msg}");
}

fn rect_px(r: RECT) -> PxRect {
    PxRect {
        left: r.left,
        top: r.top,
        right: r.right,
        bottom: r.bottom,
    }
}

// --------------------------------------------------------------- host window

struct HostWin<'a> {
    opts: HostOptions,
    st: Arc<EngineState>,
    emit: &'a mut dyn FnMut(&HostEvent),
    instance: HMODULE,
    hwnd: HWND,
    child: HWND,
    band: Option<BandWin>,
    waited_ms: i32,
    shown: bool,
    closing: bool,
    close_deadline: Option<Instant>,
}

impl HostWin<'_> {
    fn has_child(&self) -> bool {
        !self.child.is_invalid()
    }

    unsafe fn on_tick(&mut self) {
        self.st.drain_into(&mut *self.emit);
        if self.closing {
            let dead = !self.has_child() || !IsWindow(self.child).as_bool();
            let expired = self.close_deadline.is_some_and(|d| Instant::now() >= d);
            if dead || expired {
                let _ = DestroyWindow(self.hwnd);
            }
            return;
        }
        let engine_alive = !self.st.done.load(Ordering::Acquire);
        if self.has_child() && !IsWindow(self.child).as_bool() {
            // 子窗消亡：引擎重启中 → 清账回头等新窗口；引擎已终 → 宿主自杀。
            self.child = HWND::default();
            self.waited_ms = 0;
            if !engine_alive {
                log("child gone, closing host");
                (self.emit)(&HostEvent::ChildGone);
                let _ = DestroyWindow(self.hwnd);
                return;
            }
        }
        let found = if self.has_child() {
            None
        } else {
            FindWindowW(None, &HSTRING::from(self.opts.title.as_str())).ok()
        };
        let action = tick_action(
            self.has_child(),
            self.has_child() && IsWindow(self.child).as_bool(),
            found.is_some(),
            self.waited_ms,
            engine_alive,
        );
        match action {
            TickAction::Embed => self.embed(found.unwrap_or_default()),
            TickAction::KeepWaiting => self.waited_ms += TICK_MS,
            TickAction::GiveUp => {
                log("giving up, window never appeared");
                (self.emit)(&HostEvent::GiveUp);
                let _ = DestroyWindow(self.hwnd);
            }
            TickAction::FollowChildDeath | TickAction::Glue => self.glue(),
        }
    }

    /// 像素级接管：AdjustWindowRectEx 反推外框 → SetParent → 样式手术 →
    /// 亮相 → 铺满 → band → 焦点转发。shown 门 = C# SetVisibleCore 拦截。
    unsafe fn embed(&mut self, child: HWND) {
        log(&format!("window found hwnd={:x}", child.0 as usize));
        self.child = child;
        if !self.shown {
            let mut want = RECT::default();
            if GetWindowRect(child, &mut want).is_ok() {
                let (cw, ch) = embed_client_target(rect_px(want));
                want.right = want.left + cw;
                want.bottom = want.top + ch;
                let style = WINDOW_STYLE(GetWindowLongW(self.hwnd, GWL_STYLE) as u32);
                let ex = WINDOW_EX_STYLE(GetWindowLongW(self.hwnd, GWL_EXSTYLE) as u32);
                if AdjustWindowRectEx(&mut want, style, false, ex).is_ok() {
                    let _ = SetWindowPos(
                        self.hwnd,
                        None,
                        want.left,
                        want.top,
                        want.right - want.left,
                        want.bottom - want.top,
                        SWP_NOZORDER | SWP_NOACTIVATE,
                    );
                }
            }
        }
        let _ = SetParent(child, self.hwnd);
        let s = GetWindowLongW(child, GWL_STYLE);
        SetWindowLongW(child, GWL_STYLE, child_style_after_surgery(s));
        let ex = GetWindowLongW(child, GWL_EXSTYLE);
        SetWindowLongW(child, GWL_EXSTYLE, child_ex_after_surgery(ex));
        if self.shown {
            // 引擎重启后的再嵌入：宿主已亮相，只重接管新窗口。
            self.fill_client(SWP_FRAMECHANGED | SWP_SHOWWINDOW);
            self.focus_child();
            return;
        }
        if self.opts.style == HostStyle::Immersive {
            let round = DWMWCP_ROUND;
            let _ = DwmSetWindowAttribute(
                self.hwnd,
                DWMWA_WINDOW_CORNER_PREFERENCE,
                &round as *const _ as *const core::ffi::c_void,
                4,
            );
        }
        self.shown = true;
        let _ = ShowWindow(self.hwnd, SW_SHOW);
        self.fill_client(SWP_FRAMECHANGED | SWP_SHOWWINDOW);
        if self.opts.style == HostStyle::Immersive {
            self.band = BandWin::create(self.hwnd, self.instance);
        }
        let _ = SetForegroundWindow(self.hwnd);
        self.focus_child();
        log(&format!("reparented into host style={:?}", self.opts.style));
        (self.emit)(&HostEvent::Embedded);
    }

    /// 子窗推回"恰好等于宿主客户区"：先样式后矩形；子窗坐标经
    /// ScreenToClient 折算再比对（GetWindowRect 对子窗返回屏幕坐标）。
    unsafe fn glue(&mut self) {
        if !self.has_child() || IsIconic(self.hwnd).as_bool() {
            return;
        }
        let s = GetWindowLongW(self.child, GWL_STYLE);
        if child_style_needs_surgery(s) {
            log("style drift repaired");
            SetWindowLongW(self.child, GWL_STYLE, child_style_after_surgery(s));
            self.fill_client(SWP_FRAMECHANGED);
            return;
        }
        let mut cr = RECT::default();
        let mut wr = RECT::default();
        if GetClientRect(self.hwnd, &mut cr).is_err() || GetWindowRect(self.child, &mut wr).is_err()
        {
            return;
        }
        let mut org = POINT { x: wr.left, y: wr.top };
        let _ = ScreenToClient(self.hwnd, &mut org);
        if glue_drifted(
            cr.right,
            cr.bottom,
            (org.x, org.y),
            wr.right - wr.left,
            wr.bottom - wr.top,
        ) {
            self.fill_client(NO_SWP);
        }
        if let Some(band) = &mut self.band {
            band.assert_above();
        }
    }

    unsafe fn fill_client(&self, extra: SET_WINDOW_POS_FLAGS) {
        if !self.has_child() {
            return;
        }
        let mut cr = RECT::default();
        if GetClientRect(self.hwnd, &mut cr).is_err() || cr.right <= 0 || cr.bottom <= 0 {
            return;
        }
        let _ = SetWindowPos(self.child, None, 0, 0, cr.right, cr.bottom, SWP_NOZORDER | extra);
    }

    /// 焦点转发：跨进程 SetFocus 必被拒 → AttachThreadInput 合并队列后转发。
    unsafe fn focus_child(&self) {
        if !self.has_child() || !IsWindowVisible(self.hwnd).as_bool() {
            return;
        }
        let me = GetCurrentThreadId();
        let other = GetWindowThreadProcessId(self.child, None);
        let attached = me != other && AttachThreadInput(me, other, true).as_bool();
        let _ = SetFocus(self.child);
        if attached {
            let _ = AttachThreadInput(me, other, false);
        }
    }

    /// 宿主 ✕：WM_CLOSE 送子窗（干净拆 adb/server），宽限期内等它自退。
    unsafe fn begin_close(&mut self) {
        if self.closing {
            return;
        }
        self.closing = true;
        self.close_deadline =
            Some(Instant::now() + Duration::from_millis(CLOSE_GRACE_MS as u64));
        if self.has_child() && IsWindow(self.child).as_bool() {
            let _ = PostMessageW(self.child, WM_CLOSE, WPARAM(0), LPARAM(0));
        }
    }
}

unsafe extern "system" fn host_wndproc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut HostWin;
    if state.is_null() {
        return DefWindowProcW(hwnd, msg, wparam, lparam);
    }
    let s = &mut *state;
    match msg {
        WM_TIMER if wparam.0 == TIMER_ID => {
            s.on_tick();
            LRESULT(0)
        }
        WM_SIZE if !IsIconic(hwnd).as_bool() => {
            s.fill_client(NO_SWP);
            if let Some(band) = &mut s.band {
                band.sync_width();
            }
            DefWindowProcW(hwnd, msg, wparam, lparam)
        }
        WM_ACTIVATE => {
            if (wparam.0 & 0xFFFF) != 0 {
                s.focus_child();
            }
            DefWindowProcW(hwnd, msg, wparam, lparam)
        }
        WM_SETFOCUS => {
            s.focus_child();
            LRESULT(0)
        }
        WM_GETMINMAXINFO => {
            let mmi = lparam.0 as *mut MINMAXINFO;
            (*mmi).ptMinTrackSize = POINT {
                x: MIN_TRACK_W,
                y: MIN_TRACK_H,
            };
            LRESULT(0)
        }
        WM_ERASEBKGND => LRESULT(1),
        WM_CLOSE => {
            s.begin_close();
            LRESULT(0)
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

// ---------------------------------------------------------------- band window

struct LayeredDib {
    dc: HDC,
    bmp: windows::Win32::Graphics::Gdi::HBITMAP,
    bits: *mut core::ffi::c_void,
    width: i32,
    height: i32,
}

impl LayeredDib {
    unsafe fn new(width: i32, height: i32) -> Option<Self> {
        if width <= 0 || height <= 0 {
            return None;
        }
        let info = dib_info(width, height);
        let mut bits = std::ptr::null_mut();
        let screen = GetDC(None);
        let bmp = CreateDIBSection(Some(screen), &info, DIB_RGB_COLORS, &mut bits, None, 0).ok()?;
        let dc = CreateCompatibleDC(Some(screen));
        SelectObject(dc, bmp);
        ReleaseDC(None, screen);
        Some(Self {
            dc,
            bmp,
            bits,
            width,
            height,
        })
    }

    fn blit(&self, buf: &[u8]) {
        let len = (self.width * self.height * 4) as usize;
        unsafe { std::ptr::copy_nonoverlapping(buf.as_ptr(), self.bits as *mut u8, len.min(buf.len())) };
    }
}

impl Drop for LayeredDib {
    fn drop(&mut self) {
        unsafe {
            let _ = DeleteDC(self.dc);
            let _ = DeleteObject(self.bmp);
        }
    }
}

/// 32bpp top-down DIB（band 与字形掩码共用）。
fn dib_info(width: i32, height: i32) -> BITMAPINFO {
    BITMAPINFO {
        bmiHeader: BITMAPINFOHEADER {
            biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width,
            biHeight: -height,
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB.0,
            ..Default::default()
        },
        ..Default::default()
    }
}

struct BandWin {
    hwnd: HWND,
    host: HWND,
    layout: BandLayout,
    width: i32,
    height: i32,
    hover: bool,
    hover_btn: i32,
    pressed: bool,
    tracking: bool,
    last_click_ms: i64,
    last_click_pos: (i32, i32),
    /// [minimize, maximize, restore, close]，max/restore 随 IsZoomed 换用。
    masks: [GlyphMask; 4],
    dib: Option<LayeredDib>,
    t0: Instant,
}

impl BandWin {
    unsafe fn create(host: HWND, instance: HMODULE) -> Option<BandWin> {
        let dpi = GetDpiForWindow(host) as f32 / 96.0;
        let layout = BandLayout::from_dpi(dpi);
        let mut cr = RECT::default();
        GetClientRect(host, &mut cr).ok()?;
        let hwnd = CreateWindowExW(
            WS_EX_LAYERED | WS_EX_NOACTIVATE,
            BAND_CLASS,
            None,
            WS_CHILD,
            0,
            0,
            cr.right,
            layout.band_h,
            Some(host),
            None,
            Some(HINSTANCE(instance.0)),
            None,
        )
        .ok()?;
        let mut win = BandWin {
            hwnd,
            host,
            layout,
            width: cr.right.max(1),
            height: layout.band_h,
            hover: false,
            hover_btn: -1,
            pressed: false,
            tracking: false,
            last_click_ms: 0,
            last_click_pos: (0, 0),
            masks: rasterize_glyphs(dpi),
            dib: None,
            t0: Instant::now(),
        };
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, &mut win as *mut BandWin as isize);
        let _ = SetWindowPos(
            hwnd,
            Some(HWND_TOP),
            0,
            0,
            cr.right,
            layout.band_h,
            SWP_SHOWWINDOW,
        );
        win.render();
        Some(win)
    }

    /// 唯一保留的 tick 动作：本地兄弟 z 序压回子窗之上。
    unsafe fn assert_above(&self) {
        let _ = SetWindowPos(
            self.hwnd,
            Some(HWND_TOP),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        );
    }

    unsafe fn sync_width(&mut self) {
        let mut cr = RECT::default();
        if GetClientRect(self.host, &mut cr).is_err() || cr.right <= 0 || cr.right == self.width {
            return;
        }
        self.width = cr.right;
        let _ = SetWindowPos(
            self.hwnd,
            None,
            0,
            0,
            cr.right,
            self.height,
            SWP_NOZORDER | SWP_SHOWWINDOW,
        );
        self.render();
    }

    unsafe fn render(&mut self) {
        if self.width <= 0 || self.height <= 0 {
            return;
        }
        let zoomed = IsZoomed(self.host).as_bool();
        let mid = if zoomed { &self.masks[2] } else { &self.masks[1] };
        let buf = paint_band(
            self.width,
            self.height,
            self.hover,
            self.hover_btn,
            [&self.masks[0], mid, &self.masks[3]],
            &self.layout,
        );
        let stale = match &self.dib {
            Some(d) => d.width != self.width || d.height != self.height,
            None => true,
        };
        if stale {
            self.dib = LayeredDib::new(self.width, self.height);
        }
        let Some(dib) = &self.dib else { return };
        dib.blit(&buf);
        let screen = GetDC(None);
        let size = SIZE { cx: self.width, cy: self.height };
        let src = POINT { x: 0, y: 0 };
        let blend = BLENDFUNCTION {
            BlendOp: AC_SRC_OVER as u8,
            BlendFlags: 0,
            SourceConstantAlpha: 255,
            AlphaFormat: AC_SRC_ALPHA as u8,
        };
        let _ = UpdateLayeredWindow(
            self.hwnd,
            Some(screen),
            None,
            Some(&size),
            Some(dib.dc),
            Some(&src),
            COLORREF(0),
            Some(&blend),
            ULW_ALPHA,
        );
        ReleaseDC(None, screen);
    }

    fn now_ms(&self) -> i64 {
        self.t0.elapsed().as_millis() as i64
    }

    unsafe fn on_mouse_move(&mut self, x: i32, y: i32) {
        if !self.tracking {
            let mut tme = TRACKMOUSEEVENT {
                cbSize: std::mem::size_of::<TRACKMOUSEEVENT>() as u32,
                dwFlags: TME_LEAVE,
                hwndTrack: self.hwnd,
                dwHoverTime: 0,
            };
            if TrackMouseEvent(&mut tme).is_ok() {
                self.tracking = true;
            }
        }
        let hit = if self.hover {
            hit_button(x, y, self.width, self.height, &self.layout)
        } else {
            -1
        };
        if !self.hover || hit != self.hover_btn {
            self.hover = true;
            self.hover_btn = hit;
            self.render();
        }
        let cursor = if hit >= 0 { IDC_HAND } else { IDC_SIZEALL };
        if let Ok(c) = LoadCursorW(None, cursor) {
            SetCursor(Some(c));
        }
    }

    unsafe fn on_leave(&mut self) {
        self.tracking = false;
        if self.hover {
            self.hover = false;
            self.hover_btn = -1;
            self.render();
        }
    }

    unsafe fn on_left_down(&mut self, x: i32, y: i32) {
        if hit_button(x, y, self.width, self.height, &self.layout) >= 0 {
            self.pressed = true;
            return;
        }
        // 双击空带 = 最大化切换（HTCAPTION 模态循环吃掉系统双击消息）。
        let now = self.now_ms();
        if is_double_click(
            now,
            (x, y),
            self.last_click_ms,
            self.last_click_pos,
            i64::from(GetDoubleClickTime()),
            GetSystemMetrics(SM_CXDOUBLECLK),
            GetSystemMetrics(SM_CYDOUBLECLK),
        ) {
            self.last_click_ms = 0;
            self.toggle_maximize();
            return;
        }
        self.last_click_ms = now;
        self.last_click_pos = (x, y);
        // 真系统拖动：ReleaseCapture + WM_NCLBUTTONDOWN(HTCAPTION)。
        let _ = ReleaseCapture();
        let _ = SendMessageW(self.host, WM_NCLBUTTONDOWN, WPARAM(HTCAPTION as usize), LPARAM(0));
    }

    unsafe fn on_left_up(&mut self, x: i32, y: i32) {
        if !self.pressed {
            return;
        }
        self.pressed = false;
        match hit_button(x, y, self.width, self.height, &self.layout) {
            0 => {
                let _ = ShowWindow(self.host, SW_MINIMIZE);
            }
            1 => self.toggle_maximize(),
            2 => {
                let _ = PostMessageW(self.host, WM_CLOSE, WPARAM(0), LPARAM(0));
            }
            _ => {}
        }
    }

    unsafe fn toggle_maximize(&self) {
        let cmd = if IsZoomed(self.host).as_bool() {
            SW_RESTORE
        } else {
            SW_MAXIMIZE
        };
        let _ = ShowWindow(self.host, cmd);
    }
}

unsafe extern "system" fn band_wndproc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut BandWin;
    if state.is_null() {
        return DefWindowProcW(hwnd, msg, wparam, lparam);
    }
    let b = &mut *state;
    let x = (lparam.0 & 0xFFFF) as u16 as i32;
    let y = ((lparam.0 >> 16) & 0xFFFF) as u16 as i32;
    match msg {
        WM_MOUSEMOVE => {
            b.on_mouse_move(x, y);
            LRESULT(0)
        }
        WM_MOUSELEAVE => {
            b.on_leave();
            LRESULT(0)
        }
        WM_LBUTTONDOWN if (wparam.0 & 0x0001) != 0 => {
            b.on_left_down(x, y);
            LRESULT(0)
        }
        WM_LBUTTONUP if (wparam.0 & 0x0001) != 0 => {
            b.on_left_up(x, y);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}

/// 字形覆盖掩码：白字黑底光栅化后取 max(B,G,R) 为覆盖率（灰度/子像素皆宜）。
unsafe fn rasterize_glyph(
    dc: HDC,
    font: HFONT,
    ch: u16,
) -> GlyphMask {
    let empty = || GlyphMask {
        width: 0,
        height: 0,
        coverage: Vec::new(),
    };
    let text = [ch, 0];
    let mut size = SIZE::default();
    if !GetTextExtentPoint32W(dc, &text, &mut size).as_bool() || size.cx <= 0 || size.cy <= 0 {
        return empty();
    }
    let info = dib_info(size.cx, size.cy);
    let mut bits = std::ptr::null_mut();
    let Ok(bmp) = CreateDIBSection(Some(dc), &info, DIB_RGB_COLORS, &mut bits, None, 0) else {
        return empty();
    };
    let n = (size.cx * size.cy) as usize;
    std::ptr::write_bytes(bits, 0, n * 4);
    let old_bmp = SelectObject(dc, bmp);
    let old_font = SelectObject(dc, font);
    let _ = SetBkMode(dc, OPAQUE);
    SetBkColor(dc, COLORREF(0));
    SetTextColor(dc, COLORREF(0x00FF_FFFF));
    let _ = TextOutW(dc, 0, 0, &text);
    GdiFlush();
    let px = std::slice::from_raw_parts(bits as *const u8, n * 4);
    let coverage = px.chunks_exact(4).map(|c| c[0].max(c[1]).max(c[2])).collect();
    SelectObject(dc, old_bmp);
    SelectObject(dc, old_font);
    let _ = DeleteObject(bmp);
    GlyphMask {
        width: size.cx as usize,
        height: size.cy as usize,
        coverage,
    }
}

/// GlyphFont 对译：Segoe Fluent Icons → Segoe MDL2 Assets 兜底，12px×dpi。
/// CreateFontW 裸值：charset=1 out=0 clip=0 quality=4(ANTIALIASED) pitch=0。
unsafe fn rasterize_glyphs(dpi: f32) -> [GlyphMask; 4] {
    let make_font = |face: &HSTRING, height: i32| {
        CreateFontW(
            height, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 4, 0, PCWSTR(face.as_ptr()),
        )
    };
    let screen = GetDC(None);
    let dc = CreateCompatibleDC(Some(screen));
    ReleaseDC(None, screen);
    let height = (12.0 * dpi) as i32;
    let fluent = HSTRING::from("Segoe Fluent Icons");
    let mut font = make_font(&fluent, height);
    let old_font = SelectObject(dc, font);
    let mut name = [0u16; 64];
    let got = GetTextFaceW(dc, Some(&mut name)).max(0) as usize;
    let actual = String::from_utf16_lossy(&name[..got.min(name.len())]);
    if !actual.contains("Segoe Fluent") {
        let mdl2 = HSTRING::from("Segoe MDL2 Assets");
        font = make_font(&mdl2, height);
        SelectObject(dc, font);
    }
    let masks = [
        rasterize_glyph(dc, font, GLYPH_MINIMIZE),
        rasterize_glyph(dc, font, GLYPH_MAXIMIZE),
        rasterize_glyph(dc, font, GLYPH_RESTORE),
        rasterize_glyph(dc, font, GLYPH_CLOSE),
    ];
    SelectObject(dc, old_font);
    let _ = DeleteObject(HGDIOBJ(font.0));
    let _ = DeleteDC(dc);
    masks
}

unsafe fn register_class(name: PCWSTR, proc: WNDPROC, instance: HMODULE) -> bool {
    let wc = WNDCLASSW {
        style: CS_HREDRAW | CS_VREDRAW,
        lpfnWndProc: proc,
        hInstance: HINSTANCE(instance.0),
        hCursor: LoadCursorW(None, IDC_ARROW).unwrap_or_default(),
        lpszClassName: name,
        ..Default::default()
    };
    RegisterClassW(&wc) != 0
}

/// 宿主窗口主线程：类注册 → 隐身建窗 → 100ms timer tick → 消息循环。
/// 返回 = 宿主窗口销毁（引擎收编与退出码结算由 run_host 负责）。
pub(crate) unsafe fn window_main(
    opts: &HostOptions,
    st: &Arc<EngineState>,
    emit: &mut dyn FnMut(&HostEvent),
) {
    let _ = SetProcessDPIAware();
    let instance = GetModuleHandleW(None).unwrap_or_default();
    if !register_class(HOST_CLASS, Some(host_wndproc), instance)
        || !register_class(BAND_CLASS, Some(band_wndproc), instance)
    {
        log("failed to register window classes");
        return;
    }
    let immersive = opts.style == HostStyle::Immersive;
    // 沉浸式：无边框但真非客户区缩放边（原生边/角缩放 + snap + 阴影 +
    // DWM 圆角白拿）；native：普通带 caption 宿主。均不带 WS_VISIBLE：
    // 嵌入前不亮相（对译 SetVisibleCore 拦截）。
    let style = if immersive {
        WINDOW_STYLE(
            (WS_POPUP | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX) as u32,
        )
    } else {
        WS_OVERLAPPEDWINDOW
    };
    let mut state = Box::new(HostWin {
        opts: opts.clone(),
        st: st.clone(),
        emit,
        instance,
        hwnd: HWND::default(),
        child: HWND::default(),
        band: None,
        waited_ms: 0,
        shown: false,
        closing: false,
        close_deadline: None,
    });
    let title = HSTRING::from(opts.title.as_str());
    let Ok(hwnd) = CreateWindowExW(
        WS_EX_WINDOWEDGE,
        HOST_CLASS,
        Some(&title),
        style,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        900,
        640,
        None,
        None,
        Some(HINSTANCE(instance.0)),
        None,
    ) else {
        log("CreateWindow failed");
        return;
    };
    state.hwnd = hwnd;
    SetWindowLongPtrW(hwnd, GWLP_USERDATA, &mut *state as *mut HostWin as isize);
    if SetTimer(Some(hwnd), TIMER_ID, TICK_MS as u32, None) == 0 {
        log("SetTimer failed");
        let _ = DestroyWindow(hwnd);
    }
    let mut msg = MSG::default();
    while GetMessageW(&mut msg, None, 0, 0).as_bool() {
        let _ = TranslateMessage(&msg);
        let _ = DispatchMessageW(&msg);
    }
    SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
    if let Some(band) = state.band.as_ref() {
        SetWindowLongPtrW(band.hwnd, GWLP_USERDATA, 0);
    }
}

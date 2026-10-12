// Windows 子进程与 Job Object 进程生命周期契约见 docs/window-experience.md §12
#![allow(clippy::disallowed_types)]

use std::process::{Child, Command};

pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[cfg(windows)]
use windows::Win32::Foundation::HANDLE;

/// Windows：CREATE_NO_WINDOW（面板是窗口进程，adb 轮询不得闪控制台）。
pub fn creation_flags() -> u32 {
    if cfg!(windows) {
        CREATE_NO_WINDOW
    } else {
        0
    }
}

pub fn quiet_command(program: &str) -> Command {
    duo_core::quiet::quiet_command(program)
}

pub fn silent_command(program: &str, args: &[String]) -> Command {
    let mut command = quiet_command(program);
    command.args(args);
    command
}

/// 终止 proc 及其全部后代整树。Windows 先让窗口自己收掉交换链，再静默 taskkill。
pub fn terminate_tree(child: &mut Child) {
    #[cfg(windows)]
    {
        if matches!(child.try_wait(), Ok(None)) {
            close_tree_quietly(child.id());
        }
    }
    #[cfg(not(windows))]
    {
        let _ = child.kill();
    }
    let _ = child.wait();
}

/// 结束进程树但不占用调用线程。面板关窗用这条，避免 taskkill 卡住界面。
pub fn terminate_tree_detach(mut child: Child) {
    #[cfg(windows)]
    {
        let pid = child.id().to_string();
        if std::thread::Builder::new()
            .name("duo-kill".into())
            .spawn(move || terminate_tree(&mut child))
            .is_err()
        {
            let _ = quiet_command("taskkill")
                .args(["/T", "/F", "/PID", &pid])
                .spawn();
        }
    }
    #[cfg(not(windows))]
    {
        terminate_tree(&mut child);
    }
}

#[cfg(windows)]
fn close_tree_quietly(root: u32) {
    use std::time::{Duration, Instant};
    post_wm_close(&process_tree(root));
    let deadline = Instant::now() + Duration::from_millis(1200);
    while Instant::now() < deadline {
        if !pid_alive(root) {
            return;
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    let _ = quiet_command("taskkill")
        .args(["/T", "/F", "/PID", &root.to_string()])
        .status();
}

#[cfg(windows)]
fn process_tree(root: u32) -> Vec<u32> {
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    let mut edges = Vec::new();
    unsafe {
        let Ok(snap) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
            return vec![root];
        };
        let mut entry = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        if Process32FirstW(snap, &mut entry).is_ok() {
            loop {
                edges.push((entry.th32ProcessID, entry.th32ParentProcessID));
                if Process32NextW(snap, &mut entry).is_err() {
                    break;
                }
            }
        }
        let _ = windows::Win32::Foundation::CloseHandle(snap);
    }
    let mut pids = vec![root];
    let mut i = 0;
    while i < pids.len() {
        let parent = pids[i];
        for &(pid, ppid) in &edges {
            if ppid == parent && !pids.contains(&pid) {
                pids.push(pid);
            }
        }
        i += 1;
    }
    pids
}

#[cfg(windows)]
fn post_wm_close(pids: &[u32]) {
    use windows::Win32::Foundation::{BOOL, HWND, LPARAM, WPARAM};
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindowThreadProcessId, IsWindowVisible, PostMessageW,
    };
    struct Ctx<'a> {
        pids: &'a [u32],
    }
    unsafe extern "system" fn each(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let ctx = &*(lparam.0 as *const Ctx);
        let mut pid = 0u32;
        GetWindowThreadProcessId(hwnd, Some(&mut pid));
        if ctx.pids.contains(&pid) && IsWindowVisible(hwnd).as_bool() {
            let _ = PostMessageW(hwnd, 0x0010, WPARAM(0), LPARAM(0));
        }
        BOOL(1)
    }
    let ctx = Ctx { pids };
    unsafe {
        let _ = EnumWindows(Some(each), LPARAM(&ctx as *const Ctx as isize));
    }
}

#[cfg(windows)]
fn pid_alive(pid: u32) -> bool {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        let Ok(process) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
            return false;
        };
        let mut code = 0u32;
        let live = GetExitCodeProcess(process, &mut code).is_ok() && code == 259;
        let _ = CloseHandle(process);
        live
    }
}

/// Windows kill-on-close Job Object 容器。
#[derive(Default)]
pub struct ChildJob {
    #[cfg(windows)]
    handle: std::sync::Mutex<Option<HANDLE>>,
}

#[cfg(windows)]
impl ChildJob {
    pub fn new() -> Self {
        Self {
            handle: std::sync::Mutex::new(create_job().ok()),
        }
    }

    pub fn active(&self) -> bool {
        self.handle.lock().map(|h| h.is_some()).unwrap_or(false)
    }

    pub fn add(&self, child: &Child) {
        use windows::Win32::Foundation::CloseHandle;
        use windows::Win32::System::JobObjects::AssignProcessToJobObject;
        use windows::Win32::System::Threading::{
            OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
        };
        let Ok(guard) = self.handle.lock() else {
            return;
        };
        let Some(handle) = guard.as_ref() else {
            return;
        };
        let job = HANDLE(handle.0);
        unsafe {
            let Ok(process) = OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_TERMINATE,
                windows::Win32::Foundation::BOOL::default(),
                child.id(),
            ) else {
                return;
            };
            let _ = AssignProcessToJobObject(job, process);
            let _ = CloseHandle(process);
        }
    }

    pub fn close(&self) {
        use windows::Win32::Foundation::CloseHandle;
        if let Ok(mut guard) = self.handle.lock() {
            if let Some(handle) = guard.take() {
                unsafe {
                    let _ = CloseHandle(handle);
                }
            }
        }
    }
}

#[cfg(not(windows))]
impl ChildJob {
    pub fn new() -> Self {
        Self {}
    }

    pub fn active(&self) -> bool {
        false
    }

    pub fn add(&self, _child: &Child) {}

    pub fn close(&self) {}
}

impl Drop for ChildJob {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(windows)]
fn create_job() -> Result<HANDLE, ()> {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    unsafe {
        let Ok(job) = CreateJobObjectW(None, None) else {
            return Err(());
        };
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok.is_ok() {
            Ok(job)
        } else {
            let _ = CloseHandle(job);
            Err(())
        }
    }
}

/// 单实例：Windows 命名互斥体；已占用时拉回已有窗口而非弹死胡同提示
/// （2026-09-20：二次点击开始菜单直接激活旧窗口并静默退出）。
pub fn single_instance(key: &str) -> bool {
    #[cfg(windows)]
    {
        use windows::core::w;
        use windows::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
        use windows::Win32::System::Threading::CreateMutexW;
        let _ = key;
        unsafe {
            let Ok(handle) = CreateMutexW(None, false, w!("Local\\DuoPanelSingleInstance")) else {
                return false;
            };
            let _ = handle;
            GetLastError() != ERROR_ALREADY_EXISTS
        }
    }
    #[cfg(not(windows))]
    {
        let _ = key;
        true
    }
}

/// 枚举回调命中标记（windows 侧）。
#[cfg(windows)]
static ACTIVATED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// 二次启动：找到首个实例的主窗口拉回前台（枚举窗口按进程名匹配，
/// 不靠标题撞名；找不到窗口才退回提示框）。
#[cfg(windows)]
pub fn activate_existing(title: &str) {
    use windows::core::PWSTR;
    use windows::Win32::Foundation::{CloseHandle, BOOL, HWND, LPARAM};
    use windows::Win32::System::Threading::{
        GetCurrentProcessId, OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
        PROCESS_QUERY_LIMITED_INFORMATION,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindowTextW, GetWindowThreadProcessId, IsIconic, IsWindowVisible,
        SetForegroundWindow, ShowWindow, SW_RESTORE, SW_SHOW,
    };
    let _ = title;
    extern "system" fn on_window(hwnd: HWND, _l: LPARAM) -> BOOL {
        unsafe {
            let mut pid = 0_u32;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            if pid == 0 || pid == GetCurrentProcessId() || !IsWindowVisible(hwnd).as_bool() {
                return BOOL(1);
            }
            let Ok(process) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, BOOL(0), pid) else {
                return BOOL(1);
            };
            let mut name = [0_u16; 260];
            let mut len = name.len() as u32;
            let is_duo = QueryFullProcessImageNameW(
                process,
                PROCESS_NAME_WIN32,
                PWSTR(name.as_mut_ptr()),
                &mut len,
            )
            .is_ok()
                && String::from_utf16_lossy(&name[..len as usize])
                    .to_ascii_lowercase()
                    .ends_with("duo.exe");
            let _ = CloseHandle(process);
            if !is_duo {
                return BOOL(1);
            }
            let mut text = [0_u16; 64];
            let n = GetWindowTextW(hwnd, &mut text);
            if n > 0 {
                if IsIconic(hwnd).as_bool() {
                    let _ = ShowWindow(hwnd, SW_RESTORE);
                } else {
                    let _ = ShowWindow(hwnd, SW_SHOW);
                }
                let _ = SetForegroundWindow(hwnd);
                ACTIVATED.store(true, std::sync::atomic::Ordering::Relaxed);
                return BOOL(0);
            }
            BOOL(1)
        }
    }
    unsafe {
        let _ = EnumWindows(Some(on_window), LPARAM(0));
    }
    if !ACTIVATED.load(std::sync::atomic::Ordering::Relaxed) {
        notify_already_running(
            "Duo 面板已在运行，但未找到可激活的窗口（进程可能已失去响应）。\n可在任务管理器结束 Duo 后重试。",
        );
    }
}

#[cfg(not(windows))]
pub fn activate_existing(_title: &str) {}

/// 已有实例提示框（无控制台窗口进程的 stderr 不可见）。
pub fn notify_already_running(message: &str) {
    #[cfg(windows)]
    {
        use windows::core::PCWSTR;
        use windows::Win32::UI::WindowsAndMessaging::{
            MessageBoxW, MB_ICONINFORMATION, MB_TOPMOST,
        };
        let wide: Vec<u16> = message.encode_utf16().chain([0]).collect();
        let title: Vec<u16> = "Duo".encode_utf16().chain([0]).collect();
        unsafe {
            MessageBoxW(
                None,
                PCWSTR(wide.as_ptr()),
                PCWSTR(title.as_ptr()),
                MB_ICONINFORMATION | MB_TOPMOST,
            );
        }
    }
    #[cfg(not(windows))]
    {
        eprintln!("{message}");
    }
}

/// 自检取证：向本进程窗口中心发一记滚轮（duo 内部使用）。
#[cfg(target_os = "windows")]
pub fn send_wheel(delta: i32) {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_MOUSE, MOUSEEVENTF_WHEEL, MOUSEINPUT,
    };
    unsafe {
        let mi = MOUSEINPUT {
            dx: 0,
            dy: 0,
            mouseData: delta as u32,
            dwFlags: MOUSEEVENTF_WHEEL,
            time: 0,
            dwExtraInfo: 0,
        };
        let inp = INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: windows::Win32::UI::Input::KeyboardAndMouse::INPUT_0 { mi },
        };
        // 指针放到本窗口物理中心，保证 WM_MOUSEWHEEL 落在本窗口
        let (cx, cy) = window_center();
        windows::Win32::UI::WindowsAndMessaging::SetCursorPos(cx, cy).ok();
        std::thread::sleep(std::time::Duration::from_millis(50));
        SendInput(&[inp], std::mem::size_of::<INPUT>() as i32);
    }
}

/// 自检取证：在本窗口中心偏上处按下左键、上拖 200px、抬起。
#[cfg(target_os = "windows")]
pub fn send_drag() {
    // 后台线程合成：主线程不阻塞，事件跨多帧到达（同帧 press+release
    // 会被 egui 判成点击而非拖拽——取证缺陷非产品缺陷）
    std::thread::spawn(|| unsafe { send_drag_impl() });
}

#[cfg(target_os = "windows")]
unsafe fn send_drag_impl() {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_MOUSE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTDOWN,
        MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MOVE, MOUSEEVENTF_VIRTUALDESK, MOUSEINPUT,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};
    {
        let (vx, vy) = (GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
        let norm = |x: i32, y: i32| ((x * 65535) / vx.max(1), (y * 65535) / vy.max(1));
        let (cx, cy) = crate::winproc::window_center();
        let (x, mut y) = (cx, cy);
        let move_abs =
            |px: i32,
             py: i32,
             extra: windows::Win32::UI::Input::KeyboardAndMouse::MOUSE_EVENT_FLAGS| {
                let (ax, ay) = norm(px, py);
                let mi = MOUSEINPUT {
                    dx: ax,
                    dy: ay,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_MOVE
                        | MOUSEEVENTF_ABSOLUTE
                        | MOUSEEVENTF_VIRTUALDESK
                        | extra,
                    time: 0,
                    dwExtraInfo: 0,
                };
                let inp = INPUT {
                    r#type: INPUT_MOUSE,
                    Anonymous: windows::Win32::UI::Input::KeyboardAndMouse::INPUT_0 { mi },
                };
                SendInput(&[inp], std::mem::size_of::<INPUT>() as i32);
            };
        move_abs(x, y, MOUSEEVENTF_LEFTDOWN);
        for _ in 0..20 {
            y -= 10;
            move_abs(
                x,
                y,
                windows::Win32::UI::Input::KeyboardAndMouse::MOUSE_EVENT_FLAGS(0),
            );
            std::thread::sleep(std::time::Duration::from_millis(16));
        }
        move_abs(x, y, MOUSEEVENTF_LEFTUP);
    }
}

/// 本进程主窗口物理中心。
#[cfg(target_os = "windows")]
pub fn window_center() -> (i32, i32) {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::RECT;
    use windows::Win32::UI::WindowsAndMessaging::{FindWindowW, GetWindowRect};
    unsafe {
        let name: Vec<u16> = "Duo".encode_utf16().chain(std::iter::once(0)).collect();
        let hwnd = FindWindowW(PCWSTR::null(), PCWSTR(name.as_ptr())).unwrap_or_default();
        let mut rc = RECT::default();
        let _ = GetWindowRect(hwnd, &mut rc);
        ((rc.left + rc.right) / 2, (rc.top + rc.bottom) / 2)
    }
}

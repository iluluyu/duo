//! Windows 子进程管道（winproc.py 对译）：静默 spawn（CREATE_NO_WINDOW）、
//! 树杀（taskkill /T /F）、面板退出拖走整树的 kill-on-close Job Object。
//! 非 Windows 降级 POSIX 语义（terminate + 显式清单杀）。

#![allow(clippy::disallowed_types)]

use std::process::{Child, Command};

#[cfg(windows)]
use std::sync::Mutex;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Windows：CREATE_NO_WINDOW（面板是窗口进程，adb 轮询不得闪控制台）。
pub fn creation_flags() -> u32 {
    if cfg!(windows) {
        CREATE_NO_WINDOW
    } else {
        0
    }
}

/// spawn 配方：静默 + 不接管 stdio（mirror CLI 自管日志）。
pub fn silent_command(program: &str, args: &[String]) -> Command {
    let mut command = Command::new(program);
    command.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

/// 终止 proc 及其全部后代。Windows 裸 kill 是 TerminateProcess：会话 CLI
/// 的 SIGTERM 清理器从不执行，scrcpy/宿主窗会孤儿化——taskkill 整树；
/// POSIX 走 SIGTERM（处理器有机会跑）。
pub fn terminate_tree(child: &mut Child) {
    #[cfg(windows)]
    {
        if child.try_wait().map(|w| w.is_none()).unwrap_or(false) {
            let _ = Command::new("taskkill")
                .args(["/T", "/F", "/PID", &child.id().to_string()])
                .status();
        }
    }
    #[cfg(not(windows))]
    {
        let _ = child.kill();
    }
    let _ = child.wait();
}

/// kill-on-close 容器（Windows Job Object；非 Windows 透明空操作，由
/// Sessions 显式树杀兜底）。add 失败静默：没进 job 的会话照常工作，只是
/// 失去崩溃安全网。
#[derive(Default)]
pub struct ChildJob {
    #[cfg(windows)]
    handle: Mutex<Option<isize>>,
}

#[cfg(windows)]
impl ChildJob {
    pub fn new() -> Self {
        Self {
            handle: Mutex::new(create_job().ok()),
        }
    }

    pub fn active(&self) -> bool {
        self.handle.lock().map(|h| h.is_some()).unwrap_or(false)
    }

    pub fn add(&self, child: &Child) {
        let guard = self.handle.lock().ok();
        let Some(handle) = guard.as_ref().and_then(|h| *h) else {
            return;
        };
        unsafe {
            let process = windows::Win32::System::Threading::OpenProcess(
                windows::Win32::Foundation::PROCESS_SET_QUOTA
                    | windows::Win32::Foundation::PROCESS_TERMINATE,
                false,
                child.id(),
            );
            if process.is_ok() {
                let _ = windows::Win32::System::JobObjects::AssignProcessToJobObject(
                    windows::Win32::Foundation::HANDLE(handle),
                    process.unwrap(),
                );
                let _ = windows::Win32::Foundation::CloseHandle(process.unwrap());
            }
        }
    }

    pub fn close(&self) {
        if let Ok(mut guard) = self.handle.lock() {
            if let Some(handle) = guard.take() {
                unsafe {
                    let _ = windows::Win32::Foundation::CloseHandle(
                        windows::Win32::Foundation::HANDLE(handle),
                    );
                }
            }
        }
    }
}

#[cfg(windows)]
fn create_job() -> Result<isize, ()> {
    unsafe {
        use windows::Win32::System::JobObjects::*;
        let job = CreateJobObjectW(None, None);
        if job.is_invalid() {
            return Err(());
        }
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok.is_ok() {
            Ok(job.0 as isize)
        } else {
            let _ = windows::Win32::Foundation::CloseHandle(job);
            Err(())
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

/// 单实例：Windows 命名互斥体；已占用弹原生提示框后由调用方退出。
pub fn single_instance(key: &str) -> bool {
    #[cfg(windows)]
    {
        use windows::core::w;
        unsafe {
            let created = windows::Win32::System::Threading::CreateMutexW(
                None,
                false,
                w!("Local\\DuoPanelSingleInstance"),
            );
            let _ = key;
            windows::Win32::Foundation::GetLastError()
                != windows::Win32::Foundation::ERROR_ALREADY_EXISTS
                && created.is_invalid() == false
        }
    }
    #[cfg(not(windows))]
    {
        let _ = key;
        true
    }
}

/// 已有实例提示框（无控制台窗口进程的 stderr 不可见）。
pub fn notify_already_running(message: &str) {
    #[cfg(windows)]
    {
        use windows::core::PCWSTR;
        let wide: Vec<u16> = message.encode_utf16().chain([0]).collect();
        let title: Vec<u16> = "Duo".encode_utf16().chain([0]).collect();
        unsafe {
            windows::Win32::UI::WindowsAndMessaging::MessageBoxW(
                None,
                PCWSTR(wide.as_ptr()),
                PCWSTR(title.as_ptr()),
                windows::Win32::UI::WindowsAndMessaging::MB_ICONINFORMATION
                    | windows::Win32::UI::WindowsAndMessaging::MB_TOPMOST,
            );
        }
    }
    #[cfg(not(windows))]
    {
        eprintln!("{message}");
    }
}

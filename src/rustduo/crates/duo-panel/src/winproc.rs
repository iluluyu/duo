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

/// 终止 proc 及其全部后代整树。
pub fn terminate_tree(child: &mut Child) {
    #[cfg(windows)]
    {
        if matches!(child.try_wait(), Ok(None)) {
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

/// 单实例：Windows 命名互斥体；已占用弹原生提示框后由调用方退出。
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

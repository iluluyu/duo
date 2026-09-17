//! Windows 静默 spawn：GUI 进程裸 spawn 控制台工具会闪黑窗，2s 轮询即
//! 闪屏。对译 pyduo core/winproc.py 的 creation_flags：库内所有子进程
//! spawn 统一走本模块；非 Windows 平台就是普通 Command。

use std::process::Command;

/// CREATE_NO_WINDOW 于 Windows；其余平台等价 ``Command::new``。
pub fn quiet_command(program: &str) -> Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let mut cmd = Command::new(program);
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd
    }
    #[cfg(not(windows))]
    {
        Command::new(program)
    }
}

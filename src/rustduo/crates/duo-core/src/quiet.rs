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

/// 边等边抽干 stdout/stderr。先等退出再读会在输出超过管道缓冲时死锁。
pub(crate) fn wait_piped(
    mut child: std::process::Child,
    timeout: std::time::Duration,
) -> Result<(std::process::ExitStatus, Vec<u8>, Vec<u8>), WaitPipedError> {
    use std::io::Read;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let out_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut pipe) = stdout {
            let _ = pipe.read_to_end(&mut buf);
        }
        buf
    });
    let err_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut pipe) = stderr {
            let _ = pipe.read_to_end(&mut buf);
        }
        buf
    });
    let deadline = std::time::Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = out_thread.join();
                    let _ = err_thread.join();
                    return Err(WaitPipedError::TimedOut);
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(err) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = out_thread.join();
                let _ = err_thread.join();
                return Err(WaitPipedError::Wait(err));
            }
        }
    };
    let _ = child.wait();
    let stdout = out_thread.join().unwrap_or_default();
    let stderr = err_thread.join().unwrap_or_default();
    Ok((status, stdout, stderr))
}

#[derive(Debug)]
pub(crate) enum WaitPipedError {
    TimedOut,
    Wait(std::io::Error),
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    #[test]
    fn wait_piped_drains_past_pipe_capacity() {
        let child = Command::new("dd")
            .args(["if=/dev/zero", "bs=1024", "count=200", "status=none"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("dd");
        let (status, stdout, _) = wait_piped(child, Duration::from_secs(5)).expect("drained");
        assert!(status.success());
        assert_eq!(stdout.len(), 200 * 1024);
    }

    #[test]
    fn wait_piped_times_out() {
        let child = Command::new("sleep")
            .arg("30")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("sleep");
        let started = Instant::now();
        let err = wait_piped(child, Duration::from_millis(200)).unwrap_err();
        assert!(matches!(err, WaitPipedError::TimedOut));
        assert!(started.elapsed() < Duration::from_secs(3));
    }
}

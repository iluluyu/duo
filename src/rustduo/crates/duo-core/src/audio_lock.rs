//! 跨 Duo 镜像会话的单音频仲裁。对译自 duo/core/audio_lock.py；
//! 合同镜像 tests/test_audio_lock.py。
//!
//! Android 允许多个 scrcpy 客户端同时采集混音器，但采集互相争抢、结果
//! 爆音。Duo 因此把音频只授予至多一个活会话：数据目录里的小锁文件记录
//! 持有者 PID，后来者静音启动，持有者退出时释放（陈锁按 PID 存活判定
//! 回收）。

use std::path::{Path, PathBuf};

use crate::paths;

/// data_dir()/audio.lock（base 可注入：测试与 bin 的 --data-dir 语义）。
pub fn audio_lock_path(base: Option<&Path>) -> PathBuf {
    paths::data_dir(base).join("audio.lock")
}

/// PID 是否存活（0 一律算死）。
///
/// Python 侧用 ``os.kill(pid, 0)`` 可移植探测：ProcessLookupError = 死，
/// PermissionError = 活着但非本用户，Windows 上死 PID 报 WinError 6。
/// 本 crate 零外部依赖，探测原语按宿主分流（见 system_pid_alive）。
pub fn pid_alive(pid: i64) -> bool {
    if pid <= 0 {
        return false;
    }
    system_pid_alive(pid)
}

/// Linux 宿主：/proc/<pid> 目录存在 == 进程存在（权限不足一样可见，
/// 与 PermissionError 即存活的 Python 语义一致；僵尸进程同样在表）。
#[cfg(target_os = "linux")]
fn system_pid_alive(pid: i64) -> bool {
    Path::new(format!("/proc/{pid}").as_str()).exists()
}

/// 非 Linux 宿主：无零依赖 signal-0 原语，保守视为存活——宁可错拒
/// acquire（音频退回静音），不因误判锁主死亡而双持音频；陈锁恢复走
/// bin 的 audio-lock release（带存活判定的显式清理）。
#[cfg(not(target_os = "linux"))]
fn system_pid_alive(_pid: i64) -> bool {
    true
}

/// 锁文件里记录的 PID；缺失/不可读/非整数都是 0。
pub fn read_owner(lock_path: &Path) -> i64 {
    match std::fs::read_to_string(lock_path) {
        Ok(text) => text.trim().parse().unwrap_or(0),
        Err(_) => 0,
    }
}

/// 以本进程身份认领设备音频流。对译 Python AudioLock；身份（PID）与
/// 存活判定可注入（Python 测试靠 monkeypatch os 达成同一件事）。
pub struct AudioLock {
    lock_path: PathBuf,
    our_pid: i64,
    probe: Box<dyn Fn(i64) -> bool>,
    held: bool,
}

impl AudioLock {
    /// 数据目录下的进程锁：真实 PID + 宿主存活探测。
    pub fn new(base: Option<&Path>) -> Self {
        Self::with_identity(
            audio_lock_path(base),
            i64::from(std::process::id()),
            Box::new(pid_alive),
        )
    }

    /// 显式锁文件路径 + 真实 PID（默认身份）。
    pub fn at_path(lock_path: PathBuf) -> Self {
        Self::with_identity(
            lock_path,
            i64::from(std::process::id()),
            Box::new(pid_alive),
        )
    }

    /// 完全注入身份与存活判定（测试注入点）。
    pub fn with_identity(
        lock_path: PathBuf,
        our_pid: i64,
        probe: Box<dyn Fn(i64) -> bool>,
    ) -> Self {
        Self {
            lock_path,
            our_pid,
            probe,
            held: false,
        }
    }

    /// 本实例是否持有音频锁。
    pub fn held(&self) -> bool {
        self.held
    }

    /// 取锁：活着的他者持有时拒绝（false）；陈锁/自锁/空锁一律收回。
    pub fn acquire(&mut self) -> bool {
        let owner = read_owner(&self.lock_path);
        if owner != 0 && owner != self.our_pid && (self.probe)(owner) {
            return false;
        }
        if let Some(parent) = self.lock_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(&self.lock_path, format!("{}\n", self.our_pid));
        self.held = true;
        true
    }

    /// 还锁；未持有时是 no-op（只清自己的锁，不误删他者）。
    pub fn release(&mut self) {
        if !self.held {
            return;
        }
        self.held = false;
        if read_owner(&self.lock_path) == self.our_pid {
            let _ = std::fs::remove_file(&self.lock_path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 每个测试一个一次性锁目录（进程内并发互不踩脚）。
    fn lock_dir(tag: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("duo-core-audio-lock-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::create_dir_all(&dir);
        dir
    }

    #[test]
    fn acquire_and_release_roundtrip() {
        // 锁被取走一次，由持有者归还；double release 是 no-op。
        let dir = lock_dir("roundtrip");
        let path = dir.join("audio.lock");
        let mut lock = AudioLock::at_path(path.clone());
        assert!(!lock.held());
        assert!(lock.acquire());
        assert!(lock.held());
        assert_eq!(read_owner(&path), i64::from(std::process::id()));
        lock.release();
        assert!(!lock.held());
        assert_eq!(read_owner(&path), 0);
        lock.release();
        assert_eq!(read_owner(&path), 0);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn second_process_is_denied() {
        // 活着的他者持锁 → 后来者拒绝；死锁主（陈锁）→ 收回重用。
        let dir = lock_dir("deny");
        let path = dir.join("audio.lock");
        std::fs::write(&path, "999999999\n").unwrap();
        // 探测=死：陈锁被收回。
        let mut lock = AudioLock::with_identity(path.clone(), 4242, Box::new(|_| false));
        assert!(lock.acquire());
        assert_eq!(read_owner(&path), 4242);
        // 探测=活（PermissionError 即存活的语义）：他者持锁，拒绝且不覆盖。
        let mut foreign = AudioLock::with_identity(path.clone(), 1111, Box::new(|_| true));
        assert!(!foreign.acquire());
        assert!(!foreign.held());
        assert_eq!(read_owner(&path), 4242);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn corrupt_lock_is_treated_as_free() {
        // 锁文件里的垃圾不得把音频永久卡死。
        let dir = lock_dir("corrupt");
        let path = dir.join("audio.lock");
        std::fs::write(&path, "not-a-pid\n").unwrap();
        let mut lock = AudioLock::at_path(path.clone());
        assert!(lock.acquire());
        assert_eq!(read_owner(&path), i64::from(std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn pid_alive_zero_is_dead() {
        // PID 0 一律不是活进程。
        assert!(!pid_alive(0));
        assert!(!pid_alive(-5));
    }

    #[test]
    #[cfg(target_os = "linux")] // /proc 探测宿主；Windows 侧语义见 bin release
    fn pid_alive_nonexistent_is_dead() {
        assert!(!pid_alive(999_999_999));
        assert!(pid_alive(i64::from(std::process::id())));
    }

    #[test]
    fn release_only_deletes_own_lock_file() {
        // 未持锁时 release 不动他者的锁文件。
        let dir = lock_dir("foreign-release");
        let path = dir.join("audio.lock");
        std::fs::write(&path, "999999999\n").unwrap();
        let mut lock = AudioLock::at_path(path.clone());
        lock.release();
        assert_eq!(read_owner(&path), 999_999_999);
        let _ = std::fs::remove_dir_all(&dir);
    }
}

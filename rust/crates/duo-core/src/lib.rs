//! duo-core: Duo 核心逻辑的 Rust 下沉（TODO 0.2 第一档）。
//!
//! 模块逐个从 `duo/core/*.py` 对译，pytest 合同逐条镜像为 `#[cfg(test)]`。
//! 纯逻辑层零外部依赖；进程层（session/adb/monitor/apps）与宿主窗口在
//! 0.2.2/0.3 进入本 crate 的 bin 目标。设计记录：docs/window-experience.md
//! §14 与 TODO.md §0。

pub mod adb;
pub mod apps;
pub mod aspects;
pub mod audio_lock;
pub mod catalog;
pub mod codec;
pub mod devices;
pub mod engine;
pub mod host;
pub mod icongen;
pub mod icons;
pub mod mirror;
pub mod monitor;
pub mod paths;
pub mod session;
pub mod settings;

/// 与 Python `round()` 同语义的四舍五入（银行家舍入：.5 取偶）。
/// 尺寸计算必须与 Python 实现逐位一致——这些值直接进 scrcpy argv。
pub fn py_round(x: f64) -> i64 {
    let r = x.round();
    if (x - x.trunc()).abs() == 0.5 && r % 2.0 != 0.0 {
        (r - x.signum()) as i64
    } else {
        r as i64
    }
}

#[cfg(test)]
mod tests {
    use super::py_round;

    #[test]
    fn py_round_is_half_to_even() {
        assert_eq!(py_round(2.5), 2);
        assert_eq!(py_round(3.5), 4);
        assert_eq!(py_round(-2.5), -2);
        assert_eq!(py_round(2.4), 2);
        assert_eq!(py_round(2.6), 3);
    }
}

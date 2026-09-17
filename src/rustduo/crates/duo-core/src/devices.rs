//! 设备热插拔监控（2s 轮询 ``adb devices``）。对译自 duo/core/devices.py；
//! 合同镜像 tests/test_devices.py。
//!
//! 失败合同：一次产不出可信设备列表的查询（adb 缺失/超时/非零退出）是
//! Err——绝不与“无设备”混同。守约窗口内保留上一张地图（无事件、面板
//! 不闪），连续 3 次失败才真正清空（“确实没了”）。

use std::collections::BTreeMap;
use std::io::Read;
use std::thread;
use std::time::Duration;

use crate::quiet::quiet_command;

/// 监督方在设备离线时使用的退出码（与 Python EXIT_DEVICE_LOST 一致）。
pub const EXIT_DEVICE_LOST: i32 = 2;

pub const DEFAULT_INTERVAL_S: f64 = 2.0;
const MAX_CONSECUTIVE_FAILURES: u32 = 3;
const QUERY_TIMEOUT_S: f64 = 10.0;

const KNOWN_STATES: [&str; 4] = ["device", "offline", "unauthorized", "recovery"];

/// serial -> state（BTreeMap：JSON 输出确定性排序）。
pub type DeviceStates = BTreeMap<String, String>;

/// 解析 ``adb devices`` 输出。
pub fn parse_device_states(devices_output: &str) -> DeviceStates {
    let mut states = DeviceStates::new();
    for line in devices_output.split('\n').skip(1) {
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() >= 2 && KNOWN_STATES.contains(&fields[1]) {
            states.insert(fields[0].to_string(), fields[1].to_string());
        }
    }
    states
}

/// 一次 ``adb devices`` 查询。失败（rc≠0/超时/无法启动）返回 Err——
/// “查询失败”与“无设备”必须可区分，否则一次抖动就清空面板列表。
pub fn run_devices_query(adb_binary: &str) -> Result<DeviceStates, String> {
    // 超时轮询（Python 合同：挂死 10s 也是查询失败，不是空列表）。
    let mut child = quiet_command(adb_binary)
        .arg("devices")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("adb devices failed to launch: {e}"))?;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(QUERY_TIMEOUT_S);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("adb devices timed out".into());
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(e) => return Err(format!("adb devices wait failed: {e}")),
        }
    };
    let mut stdout_buf = Vec::new();
    let mut stderr_buf = Vec::new();
    if let Some(mut pipe) = child.stdout.take() {
        let _ = pipe.read_to_end(&mut stdout_buf);
    }
    if let Some(mut pipe) = child.stderr.take() {
        let _ = pipe.read_to_end(&mut stderr_buf);
    }
    let _ = child.wait();
    if !status.success() {
        let msg = String::from_utf8_lossy(if stderr_buf.is_empty() {
            &stdout_buf
        } else {
            &stderr_buf
        });
        return Err(format!(
            "adb devices failed (rc={}): {}",
            status.code().unwrap_or(-1),
            msg.chars().take(120).collect::<String>()
        ));
    }
    Ok(parse_device_states(&String::from_utf8_lossy(&stdout_buf)))
}

/// 监控状态机：轮询结果的守约裁决（面板侧消费）。
#[derive(Debug, Default)]
pub struct MonitorState {
    states: DeviceStates,
    failures: u32,
}

impl MonitorState {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn states(&self) -> &DeviceStates {
        &self.states
    }

    /// 守约窗口内（有失败但未连续 3 次）为真：此间上报地图保持旧值。
    pub fn degraded(&self) -> bool {
        self.failures > 0
    }

    pub fn online(&self) -> Vec<String> {
        self.states
            .iter()
            .filter(|(_, s)| s.as_str() == "device")
            .map(|(k, _)| k.clone())
            .collect()
    }

    /// 应用一次查询结果；返回 Some(new_states) 当且仅当上报地图变化
    /// （守约窗口内的失败不产生事件）。
    pub fn apply_query(&mut self, query: Result<DeviceStates, String>) -> Option<DeviceStates> {
        match query {
            Err(_) => {
                self.failures += 1;
                if self.failures >= MAX_CONSECUTIVE_FAILURES && !self.states.is_empty() {
                    self.states.clear();
                    return Some(self.states.clone());
                }
                None
            }
            Ok(states) => {
                self.failures = 0;
                if states != self.states {
                    self.states = states;
                    Some(self.states.clone())
                } else {
                    None
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_known_states_and_skips_noise() {
        let out = "List of devices attached\n4444bd6b\tdevice\nabc\toffline\nxyz\tunauthorized\nr\trecovery\nbad\tmumbling\nincomplete\n";
        let states = parse_device_states(out);
        assert_eq!(states.len(), 4);
        assert_eq!(states.get("4444bd6b").map(String::as_str), Some("device"));
        assert_eq!(states.get("r").map(String::as_str), Some("recovery"));
        assert!(!states.contains_key("bad"));
        assert!(!states.contains_key("incomplete"));
        assert!(parse_device_states("").is_empty());
    }

    #[test]
    fn query_failure_rides_grace_window() {
        let mut m = MonitorState::new();
        let good: DeviceStates = [("4444bd6b".into(), "device".into())].into_iter().collect();
        assert_eq!(m.apply_query(Ok(good.clone())), Some(good.clone()));
        // 单次失败：保持旧地图、无事件、标记 degraded。
        assert_eq!(m.apply_query(Err("flake".into())), None);
        assert!(m.degraded());
        assert_eq!(m.states().len(), 1);
        // 恢复：计数清零，无变化无事件。
        assert_eq!(m.apply_query(Ok(good.clone())), None);
        assert!(!m.degraded());
        // 连续 3 次失败：地图真正清空（一次事件）。
        assert_eq!(m.apply_query(Err("gone".into())), None);
        assert_eq!(m.apply_query(Err("gone".into())), None);
        assert_eq!(m.apply_query(Err("gone".into())), Some(DeviceStates::new()));
        assert!(m.states().is_empty());
    }
}

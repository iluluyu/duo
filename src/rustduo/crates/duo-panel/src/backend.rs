//! duo-core 二进制的发现与子进程调用（duocore.py 客户端合同的面板内
//! 对译；面板与 duo-core 同数据目录，设置/prefs 直接走库调用，长跑与
//! 阻塞型查询走子进程：watch / apps / sweep / volume / mirror）。

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::winproc;

/// 二进制名（Windows .exe 后缀）。
fn exe_name() -> &'static str {
    if cfg!(windows) {
        "duo-core.exe"
    } else {
        "duo-core"
    }
}

/// exe 自身目录（deployed：与 duo-core.exe 同目录）。
fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
}

/// 发现顺序：DUO_CORE_BIN > exe 同目录 > 仓库 cargo 产物 > tools 目录。
pub fn find_duo_core() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(override_path) = std::env::var_os("DUO_CORE_BIN") {
        candidates.push(PathBuf::from(override_path));
    }
    if let Some(dir) = exe_dir() {
        candidates.push(dir.join(exe_name()));
    }
    for profile in ["release", "debug"] {
        candidates.push(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../target")
                .join(profile)
                .join(exe_name()),
        );
    }
    candidates.push(
        duo_core::paths::data_dir(None)
            .join("tools")
            .join(exe_name()),
    );
    candidates.into_iter().find(|candidate| candidate.is_file())
}

/// 一次性运行并取 stdout 首行 JSON（devices/apps/sweep 类查询）。
pub fn run_capture(binary: &str, args: &[String]) -> Result<String, String> {
    let output = winproc::silent_command(binary, args)
        .output()
        .map_err(|e| format!("duo-core failed to run: {e}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(if output.stderr.is_empty() {
            &output.stdout
        } else {
            &output.stderr
        });
        return Err(format!(
            "duo-core {} failed (rc={:?}): {}",
            args.first().map(String::as_str).unwrap_or(""),
            output.status.code(),
            detail.chars().take(120).collect::<String>()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// `duo-core devices --adb <adb>` → serial → state。
pub fn query_devices(
    binary: &str,
    adb: &str,
) -> Result<std::collections::BTreeMap<String, String>, String> {
    let stdout = run_capture(binary, &["devices".into(), "--adb".into(), adb.into()])?;
    let value: serde_json::Value = serde_json::from_str(stdout.trim())
        .map_err(|e| format!("devices emitted invalid JSON: {e}"))?;
    let Some(obj) = value.as_object() else {
        return Err("devices emitted non-object JSON".into());
    };
    Ok(obj
        .iter()
        .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
        .collect())
}

/// `duo-core apps --adb <adb> --serial <s>` → 已装应用行。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppRow {
    pub package: String,
    pub label: String,
    pub catalog: bool,
}

pub fn query_apps(binary: &str, adb: &str, serial: &str) -> Result<Vec<AppRow>, String> {
    let stdout = run_capture(
        binary,
        &[
            "apps".into(),
            "--adb".into(),
            adb.into(),
            "--serial".into(),
            serial.into(),
        ],
    )?;
    let value: serde_json::Value = serde_json::from_str(stdout.trim())
        .map_err(|e| format!("apps emitted invalid JSON: {e}"))?;
    let Some(list) = value.as_array() else {
        return Err("apps emitted non-array JSON".into());
    };
    Ok(list
        .iter()
        .filter_map(|entry| {
            let package = entry.get("package")?.as_str()?.to_string();
            let label = entry.get("label")?.as_str()?.to_string();
            let catalog = entry
                .get("catalog")
                .and_then(|c| c.as_bool())
                .unwrap_or(false);
            Some(AppRow {
                package,
                label,
                catalog,
            })
        })
        .collect())
}

/// sweep 结果（JSON 合同见 duo-core cmd_sweep）。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct SweepResult {
    pub rendered: bool,
    pub labels: std::collections::BTreeMap<String, String>,
}

pub fn run_sweep(binary: &str, adb: &str, serial: &str) -> Result<SweepResult, String> {
    let stdout = run_capture(
        binary,
        &[
            "sweep".into(),
            "--adb".into(),
            adb.into(),
            "--serial".into(),
            serial.into(),
        ],
    )?;
    // 空输出 = 无标签可更新（duo-core 老桩/无 renderer 环境），静默而非报错
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        return Ok(SweepResult::default());
    }
    let value: serde_json::Value =
        serde_json::from_str(trimmed).map_err(|e| format!("sweep emitted invalid JSON: {e}"))?;
    let rendered = value
        .get("rendered")
        .and_then(|r| r.as_bool())
        .unwrap_or(false);
    let labels = value
        .get("labels")
        .and_then(|l| l.as_object())
        .map(|obj| {
            obj.iter()
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default();
    Ok(SweepResult { rendered, labels })
}

/// `duo-core volume --adb --serial --index`（面板音量滑条）。
pub fn set_volume(binary: &str, adb: &str, serial: &str, index: i64) -> Result<(), String> {
    run_capture(
        binary,
        &[
            "volume".into(),
            "--adb".into(),
            adb.into(),
            "--serial".into(),
            serial.into(),
            "--index".into(),
            index.to_string(),
        ],
    )
    .map(|_| ())
}

/// `duo-core connect --adb --target`（设备卡无线连接）：归一目标由
/// duo-core 补端口，成功回显 `{target, state}`；失败 Err 携带 adb 原因。
pub fn connect_wireless(
    binary: &str,
    adb: &str,
    target: &str,
) -> Result<serde_json::Value, String> {
    let stdout = run_capture(
        binary,
        &[
            "connect".into(),
            "--adb".into(),
            adb.into(),
            "--target".into(),
            target.into(),
        ],
    )?;
    serde_json::from_str(stdout.trim()).map_err(|e| format!("duo-core connect 输出异常：{e}"))
}

/// `duo-core disconnect --adb [--target]`（无线设备右键断开；缺 target
/// 全断）。
pub fn disconnect_wireless(binary: &str, adb: &str, target: Option<&str>) -> Result<(), String> {
    let mut args = vec!["disconnect".to_string(), "--adb".into(), adb.into()];
    if let Some(target) = target {
        args.push("--target".into());
        args.push(target.into());
    }
    run_capture(binary, &args).map(|_| ())
}

// ------------------------------------------------------------- watch pump

/// 设备监控（库内轮询线程 → 共享状态图；2026-09-17 起零子进程）。
///
/// 旧实现 spawn `duo-core watch` 常驻子进程，GUI 面板 stdin 为 NULL →
/// 子进程 stdin-EOF 线程立即自杀（设备列表恒空，真机数据失败根因）；
/// 且面板退出后可能孤儿。改为面板直接链接 duo-core 库，线程内每
/// interval 调 `run_devices_query`，语义与 cmd_watch 完全一致
/// （MonitorState 守约窗口 + states/degraded）。
pub struct DeviceWatch {
    states: Arc<Mutex<std::collections::BTreeMap<String, String>>>,
    degraded: Arc<Mutex<bool>>,
}

impl DeviceWatch {
    pub fn start(adb: &str, interval_s: f64) -> Self {
        let states = Arc::new(Mutex::new(std::collections::BTreeMap::new()));
        let degraded = Arc::new(Mutex::new(false));
        let shared_states = states.clone();
        let shared_degraded = degraded.clone();
        let adb = adb.to_string();
        thread::spawn(move || {
            let mut monitor = duo_core::devices::MonitorState::new();
            loop {
                if let Some(fresh) = monitor.apply_query(duo_core::devices::run_devices_query(&adb))
                {
                    *shared_states.lock().unwrap_or_else(|p| p.into_inner()) = fresh;
                }
                *shared_degraded.lock().unwrap_or_else(|p| p.into_inner()) = monitor.degraded();
                thread::sleep(Duration::from_secs_f64(interval_s.max(0.2)));
            }
        });
        Self { states, degraded }
    }

    /// 最近一次状态图（空 = 首帧未达）。
    pub fn states(&self) -> std::collections::BTreeMap<String, String> {
        self.states
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone()
    }

    pub fn degraded(&self) -> bool {
        *self.degraded.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// 在线 serial 列表（面板选择器输入）。
    pub fn online(&self) -> Vec<String> {
        self.states()
            .into_iter()
            .filter(|(_, state)| state == "device")
            .map(|(serial, _)| serial)
            .collect()
    }
}

/// 活动设备裁决：显式选择（仍在线者）> USB 优先 > 首个无线。
/// USB + 无线常驻双在线是无线功能的稳态；旧「恰一台才 Some」在双在线
/// 时恒 None，面板全线报设备未连接（2026-10-06 真机：用户插线后反而
/// 无法使用的根因）。默认 USB 优先 = 插线走有线、拔线无缝切无线，
/// 右键选择可显式覆盖（active 仅内存态，重启回默认）。
pub fn pick_active_serial(online: &[String], active: Option<&str>) -> Option<String> {
    if let Some(active) = active {
        if online.iter().any(|s| s == active) {
            return Some(active.to_string());
        }
    }
    online
        .iter()
        .find(|s| !s.contains(':'))
        .or_else(|| online.first())
        .cloned()
}

/// 背景任务柄（线程内 catch 一切，结果经轮询取回）。
pub struct Background<T: Send + 'static> {
    result: Arc<Mutex<Option<T>>>,
}

impl<T: Send + 'static> Background<T> {
    pub fn spawn(work: impl FnOnce() -> T + Send + 'static) -> Self {
        let result = Arc::new(Mutex::new(None));
        let slot = result.clone();
        thread::spawn(move || {
            let value = work();
            *slot.lock().unwrap_or_else(|p| p.into_inner()) = Some(value);
        });
        Self { result }
    }

    pub fn take(&self) -> Option<T> {
        self.result.lock().unwrap_or_else(|p| p.into_inner()).take()
    }
}

/// 供 spawn 前健康等待用的极小 sleep。
pub fn yield_slice() {
    thread::sleep(Duration::from_millis(1));
}

#[cfg(test)]
mod tests {
    use super::pick_active_serial;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn explicit_selection_wins_while_online() {
        let online = s(&["4444bd6b", "192.168.1.100:5555"]);
        assert_eq!(
            pick_active_serial(&online, Some("192.168.1.100:5555")).as_deref(),
            Some("192.168.1.100:5555")
        );
        assert_eq!(
            pick_active_serial(&online, Some("4444bd6b")).as_deref(),
            Some("4444bd6b")
        );
    }

    #[test]
    fn stale_selection_falls_back_to_usb_first() {
        let online = s(&["192.168.1.100:5555", "4444bd6b"]);
        assert_eq!(
            pick_active_serial(&online, Some("10.9.9.9:5555")).as_deref(),
            Some("4444bd6b"),
            "离线选择被忽略，双在线默认 USB"
        );
    }

    #[test]
    fn usb_preferred_wireless_only_and_empty() {
        assert_eq!(
            pick_active_serial(&s(&["192.168.1.100:5555", "4444bd6b"]), None).as_deref(),
            Some("4444bd6b")
        );
        assert_eq!(
            pick_active_serial(&s(&["192.168.1.100:5555"]), None).as_deref(),
            Some("192.168.1.100:5555")
        );
        assert_eq!(pick_active_serial(&[], None), None);
    }
}

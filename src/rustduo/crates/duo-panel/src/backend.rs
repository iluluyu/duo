//! duo-core 二进制的发现与子进程调用（duocore.py 客户端合同的面板内
//! 对译；面板与 duo-core 同数据目录，设置/prefs 直接走库调用，长跑与
//! 阻塞型查询走子进程：watch / apps / sweep / volume / mirror）。

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
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

// ------------------------------------------------------------- watch pump

/// 设备监控（duo-core watch 子进程 + 读行线程 → 共享状态图）。
pub struct DeviceWatch {
    states: Arc<Mutex<std::collections::BTreeMap<String, String>>>,
    degraded: Arc<Mutex<bool>>,
    child: Option<Child>,
    stop: Arc<AtomicBool>,
}

impl DeviceWatch {
    pub fn start(binary: &str, adb: &str, interval_s: f64) -> Self {
        let states = Arc::new(Mutex::new(std::collections::BTreeMap::new()));
        let degraded = Arc::new(Mutex::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let child = Command::new(binary)
            .args(["watch", "--adb", adb, "--interval", &interval_s.to_string()])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .ok()
            .map(|mut child| {
                if let Some(stdout) = child.stdout.take() {
                    let states = states.clone();
                    let degraded = degraded.clone();
                    let stop = stop.clone();
                    thread::spawn(move || {
                        let reader = BufReader::new(stdout);
                        for line in reader.lines().map_while(Result::ok) {
                            if stop.load(Ordering::Acquire) {
                                return;
                            }
                            let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else {
                                continue;
                            };
                            if value.get("type").and_then(|t| t.as_str()) != Some("devices") {
                                continue;
                            }
                            if let Some(map) = value.get("states").and_then(|s| s.as_object()) {
                                let fresh: std::collections::BTreeMap<String, String> = map
                                    .iter()
                                    .filter_map(|(k, v)| {
                                        v.as_str().map(|s| (k.clone(), s.to_string()))
                                    })
                                    .collect();
                                *states.lock().unwrap_or_else(|p| p.into_inner()) = fresh;
                            }
                            if let Some(flag) = value.get("degraded").and_then(|d| d.as_bool()) {
                                *degraded.lock().unwrap_or_else(|p| p.into_inner()) = flag;
                            }
                        }
                    });
                }
                child
            });
        Self {
            states,
            degraded,
            child,
            stop,
        }
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

    /// 唯一在线 serial（空 = 无/多台）。
    pub fn online_serial(&self) -> Option<String> {
        let online: Vec<String> = self
            .states()
            .into_iter()
            .filter(|(_, state)| state == "device")
            .map(|(serial, _)| serial)
            .collect();
        if online.len() == 1 {
            Some(online[0].clone())
        } else {
            None
        }
    }
}

impl Drop for DeviceWatch {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(child) = self.child.as_mut() {
            winproc::terminate_tree(child);
        }
    }
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

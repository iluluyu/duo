//! duo-core 二进制：进程层 JSON-lines CLI（TODO 0.2.2）。
//!
//! 子命令：
//!   devices --adb <path>                一次性设备状态（stdout 一个 JSON 对象）
//!   watch   --adb <path> [--interval s] 长跑：状态图变化时逐行 JSON
//!   session --spec <json>               受监督引擎会话（事件流 + 退出码）
//!   settings --data-dir <dir> get [key] 设置读取（stdout JSON）
//!   settings --data-dir <dir> set <json> 设置合并写入（stdout 回显生效值）
//!   volume   --adb <path> --serial <s> --index <n>  设备媒体音量写入
//!   apps     --adb <path> --serial <s>   已装应用枚举 + 目录合并
//!   audio-lock --data-dir <dir> acquire|release|status  单音频仲裁锁
//!
//! 协议合同见 src/pyduo/core/duocore.py（Python 面板侧客户端）与 TODO.md §0。

use std::io::{BufRead, Read, Write};
use std::path::PathBuf;
use std::process::{exit, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use duo_core::adb::{clamp_media_volume, media_volume_process_argv, MEDIA_VOLUME_TIMEOUT_S};
use duo_core::apps::{run_apps_query, AppRow};
use duo_core::audio_lock::{audio_lock_path, pid_alive, read_owner, AudioLock};
use duo_core::devices::{run_devices_query, MonitorState, DEFAULT_INTERVAL_S};
use duo_core::session::{run_session, SessionEvent, SessionSpec};
use duo_core::settings::{load_settings, sanitize, save_settings, settings_path};

const USAGE: &str = "usage: duo-core <command> [options]
  devices --adb <path>
  watch   --adb <path> [--interval <seconds>]
  session --spec <json>
  settings --data-dir <dir> get [key]
  settings --data-dir <dir> set <json>
  volume   --adb <path> --serial <serial> --index <n>
  apps     --adb <path> --serial <serial>
  sweep    --adb <path> --serial <serial> [--data-dir <dir>] [--packages <json>]
  audio-lock --data-dir <dir> acquire|release|status
  mirror [mirror flags]                 branded app session (duo mirror 对译)";

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let Some(cmd) = argv.first() else {
        eprintln!("{USAGE}");
        exit(2);
    };
    let flag = |name: &str| -> Option<String> {
        argv.iter()
            .position(|a| a == name)
            .and_then(|i| argv.get(i + 1).cloned())
    };
    match cmd.as_str() {
        "devices" => cmd_devices(&flag("--adb")),
        "watch" => cmd_watch(&flag("--adb"), &flag("--interval")),
        "session" => cmd_session(&flag("--spec")),
        "settings" => cmd_settings(&flag("--data-dir"), &argv[1..]),
        "volume" => cmd_volume(&flag("--adb"), &flag("--serial"), &flag("--index")),
        "apps" => cmd_apps(&flag("--adb"), &flag("--serial")),
        "sweep" => cmd_sweep(
            &flag("--adb"),
            &flag("--serial"),
            &flag("--data-dir"),
            &flag("--packages"),
        ),
        "audio-lock" => cmd_audio_lock(&flag("--data-dir"), &argv[1..]),
        "mirror" => exit(duo_core::mirror::run(&argv[1..])),
        _ => {
            eprintln!("{USAGE}");
            exit(2);
        }
    }
}

fn adb_required(adb: &Option<String>) -> String {
    adb.clone().unwrap_or_else(|| {
        eprintln!("{USAGE}: --adb required");
        exit(2);
    })
}

fn data_dir_required(data_dir: &Option<String>) -> PathBuf {
    data_dir.clone().map(PathBuf::from).unwrap_or_else(|| {
        eprintln!("{USAGE}: --data-dir required");
        exit(2);
    })
}

/// 从 args 里剥掉 ``--flag value`` 对，剩下的按序返回（子命令位置参数）。
fn positionals(args: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut skip_next = false;
    for arg in args {
        if skip_next {
            skip_next = false;
        } else if arg.starts_with("--") {
            skip_next = true;
        } else {
            out.push(arg.clone());
        }
    }
    out
}

fn emit_json(value: &serde_json::Value) {
    let mut stdout = std::io::stdout().lock();
    let _ = serde_json::to_writer(&mut stdout, value);
    let _ = writeln!(stdout);
    let _ = stdout.flush();
}

/// Settings → stdout JSON（与 settings.json 同一字段顺序）。
fn settings_json(settings: &duo_core::settings::Settings) -> serde_json::Value {
    serde_json::to_value(settings).expect("Settings 序列化不可失败")
}

fn cmd_devices(adb: &Option<String>) {
    let adb = adb_required(adb);
    match run_devices_query(&adb) {
        Ok(states) => {
            let obj: serde_json::Map<String, serde_json::Value> = states
                .into_iter()
                .map(|(k, v)| (k, serde_json::Value::String(v)))
                .collect();
            emit_json(&serde_json::Value::Object(obj));
        }
        Err(err) => {
            eprintln!("{err}");
            exit(2);
        }
    }
}

fn devices_event(
    states: &std::collections::BTreeMap<String, String>,
    degraded: bool,
) -> serde_json::Value {
    serde_json::json!({
        "type": "devices",
        "states": states,
        "degraded": degraded,
    })
}

fn cmd_watch(adb: &Option<String>, interval: &Option<String>) {
    let adb = adb_required(adb);
    let interval: f64 = interval
        .as_deref()
        .and_then(|s| s.parse().ok())
        .unwrap_or(DEFAULT_INTERVAL_S);
    let mut monitor = MonitorState::new();
    // 父进程关闭 stdin（面板退出）= 停止信号。
    thread::spawn(|| {
        let _ = std::io::stdin().lock().read_line(&mut String::new());
        exit(0);
    });
    loop {
        if let Some(states) = monitor.apply_query(run_devices_query(&adb)) {
            emit_json(&devices_event(&states, monitor.degraded()));
        }
        thread::sleep(Duration::from_secs_f64(interval.max(0.2)));
    }
}

fn session_event_json(event: &SessionEvent) -> serde_json::Value {
    match event {
        SessionEvent::Started => serde_json::json!({"type": "session", "event": "started"}),
        SessionEvent::Restarted { restarts } => {
            serde_json::json!({"type": "session", "event": "restarted", "restarts": restarts})
        }
        SessionEvent::Exited { code } => {
            serde_json::json!({"type": "session", "event": "exit", "code": code})
        }
    }
}

fn cmd_session(spec_json: &Option<String>) {
    let Some(spec_json) = spec_json else {
        eprintln!("{USAGE}: --spec required");
        exit(2);
    };
    let spec = match SessionSpec::from_json(spec_json) {
        Ok(spec) => spec,
        Err(err) => {
            eprintln!("bad session spec: {err}");
            exit(2);
        }
    };
    let code = run_session(&spec, &mut |event: &SessionEvent| {
        emit_json(&session_event_json(event));
    });
    exit(code)
}

/// settings --data-dir <dir> get [key] | set <json>
///
/// get：整份（或单键）生效设置 → stdout JSON。set：JSON 对象浅合并进已存
/// settings.json（显式键覆盖、缺键保持已存值/默认），坏值逐字段回退默认
/// 并把问题透传到 stderr，stdout 回显生效后的整份设置。
fn cmd_settings(data_dir: &Option<String>, args: &[String]) {
    let base = data_dir_required(data_dir);
    let pos = positionals(args);
    match (pos.first().map(String::as_str), pos.get(1)) {
        (Some("get"), None) => {
            let (settings, _) = load_settings(Some(base.as_path()));
            emit_json(&settings_json(&settings));
        }
        (Some("get"), Some(key)) => {
            let (settings, _) = load_settings(Some(base.as_path()));
            match settings_json(&settings).get(key) {
                Some(value) => emit_json(value),
                None => {
                    eprintln!("unknown settings key: {key}");
                    exit(2);
                }
            }
        }
        (Some("set"), Some(json)) => {
            let patch: serde_json::Value = match serde_json::from_str(json) {
                Ok(value) => value,
                Err(err) => {
                    eprintln!("bad settings json: {err}");
                    exit(2);
                }
            };
            let Some(patch) = patch.as_object() else {
                eprintln!("settings patch must be a JSON object");
                exit(2);
            };
            let mut raw = std::fs::read_to_string(settings_path(Some(base.as_path())))
                .ok()
                .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
                .and_then(|value| value.as_object().cloned())
                .unwrap_or_default();
            for (key, value) in patch {
                raw.insert(key.clone(), value.clone());
            }
            let mut problems = Vec::new();
            let settings = sanitize(&raw, &mut problems);
            for problem in &problems {
                eprintln!("{problem}");
            }
            if let Err(err) = save_settings(&settings, Some(base.as_path())) {
                eprintln!("{err}");
                exit(2);
            }
            emit_json(&settings_json(&settings));
        }
        _ => {
            eprintln!("{USAGE}");
            exit(2);
        }
    }
}

/// volume --adb <path> --serial <s> --index <n>
///
/// 调 adb.rs 的 argv 拼装 spawn 一次 ``cmd media_session volume``：索引
/// 先钳进 0..15，stdout 回显生效档位，adb 失败透传 stderr 与退出码。
/// 5s 超时预算（Python media_volume 合同）：挂死杀进程，不拖手感。
fn cmd_volume(adb: &Option<String>, serial: &Option<String>, index: &Option<String>) {
    let adb = adb_required(adb);
    let Some(serial) = serial.clone() else {
        eprintln!("{USAGE}: --serial required");
        exit(2);
    };
    let Some(index) = index.as_deref().and_then(|s| s.parse::<i64>().ok()) else {
        eprintln!("{USAGE}: --index <n> required");
        exit(2);
    };
    let clamped = clamp_media_volume(index);
    let argv = media_volume_process_argv(&adb, &serial, clamped);
    let mut child = match duo_core::quiet::quiet_command(&argv[0])
        .args(&argv[1..])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(err) => {
            eprintln!("adb failed to launch: {err}");
            exit(2);
        }
    };
    let deadline = Instant::now() + Duration::from_secs_f64(MEDIA_VOLUME_TIMEOUT_S);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    eprintln!("adb media volume timed out");
                    exit(2);
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(err) => {
                eprintln!("adb wait failed: {err}");
                exit(2);
            }
        }
    };
    let mut stderr_buf = Vec::new();
    if let Some(mut pipe) = child.stderr.take() {
        let _ = pipe.read_to_end(&mut stderr_buf);
    }
    if !status.success() {
        let detail = String::from_utf8_lossy(&stderr_buf);
        eprintln!(
            "adb media volume failed (rc={}): {}",
            status.code().unwrap_or(-1),
            detail.chars().take(120).collect::<String>()
        );
        exit(status.code().unwrap_or(2));
    }
    emit_json(&serde_json::json!({ "index": clamped }));
}

/// apps --adb <path> --serial <s>
///
/// 枚举已装应用（``pm list packages -3``）并合并目录（catalog.rs）：
/// 目录内按冻结播种序在前、其余按包名序殿后，stdout 一个 JSON 数组
/// ``[{"package","label","catalog"}]``。标签取目录预设名，未收录清洗后
/// 回退包名；设备标签/图标解析留 Python 面板侧（duo/core/apps.py）。
fn cmd_apps(adb: &Option<String>, serial: &Option<String>) {
    let adb = adb_required(adb);
    let Some(serial) = serial.clone() else {
        eprintln!("{USAGE}: --serial required");
        exit(2);
    };
    match run_apps_query(&adb, &serial) {
        Ok(rows) => {
            let value: Vec<serde_json::Value> = rows.iter().map(app_row_json).collect();
            emit_json(&serde_json::Value::Array(value));
        }
        Err(err) => {
            eprintln!("{err}");
            exit(2);
        }
    }
}

/// AppRow -> stdout JSON 行（键序 package/label/catalog 即字段序合同）。
fn app_row_json(row: &AppRow) -> serde_json::Value {
    serde_json::json!({
        "package": row.package,
        "label": row.label,
        "catalog": row.catalog,
    })
}

/// sweep --adb <path> --serial <s> [--packages <json>]：设备端渲染图标 +
/// 标签 sweep（apps.py render_device_icons 合同）。stdout 一个 JSON 对象
/// {"rendered": bool, "labels": {pkg: label}}；渲染失败 rendered=false
/// （面板回退预设，不拦启动）。
fn cmd_sweep(
    adb: &Option<String>,
    serial: &Option<String>,
    data_dir: &Option<String>,
    packages: &Option<String>,
) {
    let adb = adb_required(adb);
    let Some(serial) = serial.clone() else {
        eprintln!("{USAGE}: --serial required");
        exit(2);
    };
    let packages: Vec<String> = match packages {
        Some(json) => match serde_json::from_str(json) {
            Ok(list) => list,
            Err(err) => {
                eprintln!("bad packages json: {err}");
                exit(2);
            }
        },
        None => {
            // 未显式给包清单 → 设备已装第三方包全量。
            match run_apps_query(&adb, &serial) {
                Ok(rows) => rows.into_iter().map(|row| row.package).collect(),
                Err(err) => {
                    eprintln!("{err}");
                    exit(2);
                }
            }
        }
    };
    let base = data_dir.as_deref().map(PathBuf::from);
    let mut transport = duo_core::sweep::AdbTransport::new(&adb, &serial);
    let outcome = duo_core::sweep::render_device_icons(&mut transport, &packages, base.as_deref());
    let labels: serde_json::Map<String, serde_json::Value> = outcome
        .labels
        .into_iter()
        .map(|(k, v)| (k, serde_json::Value::String(v)))
        .collect();
    emit_json(&serde_json::json!({
        "rendered": outcome.rendered,
        "labels": labels,
    }));
}

/// audio-lock --data-dir <dir> acquire|release|status
///
/// 单音频仲裁锁（duo.core.audio_lock 合同）的跨进程入口。acquire 走库
/// 语义（活锁主拒绝）；release 按 PID 存活回收陈锁——只有 owner 已死/
/// 是本进程/无人持有时才删文件，绝不偷活锁主的锁。
fn cmd_audio_lock(data_dir: &Option<String>, args: &[String]) {
    let base = data_dir_required(data_dir);
    let pos = positionals(args);
    match pos.first().map(String::as_str) {
        Some("acquire") => {
            let mut lock = AudioLock::new(Some(base.as_path()));
            let acquired = lock.acquire();
            emit_json(&serde_json::json!({ "acquired": acquired }));
        }
        Some("release") => {
            let path = audio_lock_path(Some(base.as_path()));
            let owner = read_owner(&path);
            let released =
                owner == 0 || owner == i64::from(std::process::id()) || !pid_alive(owner);
            if released && owner != 0 {
                let _ = std::fs::remove_file(&path);
            }
            emit_json(&serde_json::json!({ "released": released, "owner": owner }));
        }
        Some("status") => {
            let path = audio_lock_path(Some(base.as_path()));
            let owner = read_owner(&path);
            emit_json(&serde_json::json!({ "owner": owner, "alive": pid_alive(owner) }));
        }
        _ => {
            eprintln!("{USAGE}");
            exit(2);
        }
    }
}

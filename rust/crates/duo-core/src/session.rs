//! 受监督引擎会话：spawn、日志落盘、崩溃重启。对译自 duo/core/session.py；
//! 合同镜像 tests/test_session.py。

use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// display id 只在日志尾部 64KiB 里找（对译 _LOG_TAIL_BYTES）。
const LOG_TAIL_BYTES: u64 = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub enum SessionEvent {
    Started,
    Restarted { restarts: u32 },
    Exited { code: i32 },
}

/// 一次引擎会话的全部启动参数（对译 Python SessionSpec）。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionSpec {
    pub command: Vec<String>,
    pub log_path: PathBuf,
    #[serde(default = "default_max_restarts")]
    pub max_restarts: u32,
    #[serde(default = "default_restart_delay_s")]
    pub restart_delay_s: f64,
    /// 额外环境变量（如 ADB pin），覆盖在继承环境之上。
    #[serde(default)]
    pub env: BTreeMap<String, String>,
}

fn default_max_restarts() -> u32 {
    3
}

fn default_restart_delay_s() -> f64 {
    2.0
}

impl SessionSpec {
    pub fn from_json(s: &str) -> Result<Self, String> {
        serde_json::from_str(s).map_err(|e| e.to_string())
    }

    pub fn to_json(&self) -> String {
        serde_json::to_string(self).expect("SessionSpec is always serializable")
    }
}

/// 会话日志里解析虚拟 display id：匹配 ``New display:... (id=N)``，最后
/// 一个匹配胜出——追加式日志里最新一行属于最新一次 engine run。
pub fn parse_display_id(log_text: &str) -> Option<u32> {
    let mut last = None;
    for line in log_text.lines() {
        if let Some((_, rest)) = line.split_once("New display:") {
            last = id_after(rest).or(last);
        }
    }
    last
}

/// 在 ``New display:`` 之后找最后一个 ``(id=<digits>)``（贪婪回溯序）。
fn id_after(rest: &str) -> Option<u32> {
    for (pos, _) in rest.rmatch_indices("(id=") {
        let tail = &rest[pos + 4..];
        let end = tail
            .find(|c: char| !c.is_ascii_digit())
            .unwrap_or(tail.len());
        if end > 0 && tail[end..].starts_with(')') {
            if let Ok(id) = tail[..end].parse::<u32>() {
                return Some(id);
            }
        }
    }
    None
}

/// 读日志尾部 64KiB 再解析；文件缺失/不可读 → None。
pub fn display_id_from_log(log_path: &Path) -> Option<u32> {
    let mut file = File::open(log_path).ok()?;
    let size = file.metadata().ok()?.len();
    if size > LOG_TAIL_BYTES {
        file.seek(SeekFrom::Start(size - LOG_TAIL_BYTES)).ok()?;
    }
    let mut buf = Vec::new();
    file.read_to_end(&mut buf).ok()?;
    parse_display_id(&String::from_utf8_lossy(&buf))
}

/// 跑一个受监督会话：spawn spec.command，stdout/stderr 追加进 spec.log_path。
/// 退出码 0 → 返回；非 0 且还有重启额度 → 睡 restart_delay_s 后重 spawn
/// （发 Restarted 事件）；额度耗尽返回最后退出码。结束时发一次 Exited。
/// env 合并：在继承环境之上覆盖 spec.env。返回最终退出码。
pub fn run_session(spec: &SessionSpec, on_event: &mut dyn FnMut(&SessionEvent)) -> i32 {
    run_session_abortable(spec, &AtomicBool::new(false), on_event)
}

/// 同 run_session，但子进程存活期间轮询 abort：置位即杀当前子进程并立即
/// 返回（host 宿主退出/放弃时收编引擎，不留孤儿）。被杀 run 的退出码取
/// 实际 status，平台无码时按 130（SIGINT 惯例）记。
pub fn run_session_abortable(
    spec: &SessionSpec,
    abort: &AtomicBool,
    on_event: &mut dyn FnMut(&SessionEvent),
) -> i32 {
    let mut restarts: u32 = 0;
    on_event(&SessionEvent::Started);
    loop {
        let code = spawn_and_wait(spec, abort);
        if code == 0 || restarts >= spec.max_restarts {
            on_event(&SessionEvent::Exited { code });
            return code;
        }
        restarts += 1;
        thread::sleep(Duration::from_secs_f64(spec.restart_delay_s.max(0.0)));
        on_event(&SessionEvent::Restarted { restarts });
    }
}

/// spawn 一次并等到退出；stdout/stderr 落盘。spawn 失败按 127 记为崩溃。
fn spawn_and_wait(spec: &SessionSpec, abort: &AtomicBool) -> i32 {
    if let Some(parent) = spec.log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let Some(program) = spec.command.first() else {
        return 127;
    };
    let mut cmd = Command::new(program);
    cmd.args(&spec.command[1..]);
    match open_append(&spec.log_path) {
        Ok(log) => {
            let err = log
                .try_clone()
                .ok()
                .map(Stdio::from)
                .unwrap_or_else(Stdio::null);
            cmd.stdout(Stdio::from(log)).stderr(err);
        }
        Err(_) => {
            cmd.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }
    for (k, v) in &spec.env {
        cmd.env(k, v);
    }
    let Ok(mut child) = cmd.spawn() else {
        return 127;
    };
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.code().unwrap_or(127),
            Ok(None) => {}
            Err(_) => return 127,
        }
        if abort.load(Ordering::Relaxed) {
            let _ = child.kill();
            return child.wait().ok().and_then(|s| s.code()).unwrap_or(130);
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn open_append(log_path: &Path) -> std::io::Result<File> {
    OpenOptions::new().create(true).append(true).open(log_path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::fs::read_to_string;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// 每个测试一个独立临时目录（并行安全）。
    fn scratch_dir(tag: &str) -> PathBuf {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "duo-core-session-{tag}-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn sh_spec(dir: &Path, script: &str) -> SessionSpec {
        SessionSpec {
            command: vec!["/bin/sh".into(), "-c".into(), script.into()],
            log_path: dir.join("session.log"),
            max_restarts: 3,
            restart_delay_s: 0.05,
            env: BTreeMap::new(),
        }
    }

    // ------------------------------------------------ virtual display id parse

    #[test]
    fn parse_display_id_contract() {
        // 无匹配 → None（空日志 / 无 announce 行 / 没带 id）。
        assert_eq!(parse_display_id(""), None);
        assert_eq!(
            parse_display_id("[server] INFO: Texture: 2400x3392\n"),
            None
        );
        assert_eq!(
            parse_display_id("New display: 1200x1600/280 without id\n"),
            None
        );
        // 单个真实 announce 行（带前缀噪声）。
        assert_eq!(
            parse_display_id("[server] INFO: New display: 1200x1600/280 (id=157)\n"),
            Some(157)
        );
        // 多个匹配取最后一个：最新一次 engine run 是权威值。
        assert_eq!(
            parse_display_id(
                "[server] INFO: New display: 1200x1600/280 (id=157)\n\
                 [server] INFO: Texture: 1200x1600\n\
                 [server] INFO: New display: 800x600/160 (id=203)\n"
            ),
            Some(203)
        );
        // 多位数字 id + 前后噪声行。
        assert_eq!(
            parse_display_id("junk\nNew display: 800x600/160 (id=1029384756)\nnoise\n"),
            Some(1029384756)
        );
    }

    #[test]
    fn display_id_from_log_reads_tail_only() {
        let dir = scratch_dir("tail");
        // 缺文件 / 空文件 → None（还没 spawn 过）。
        assert_eq!(display_id_from_log(&dir.join("missing.log")), None);
        let empty = dir.join("empty.log");
        std::fs::write(&empty, b"").unwrap();
        assert_eq!(display_id_from_log(&empty), None);

        let line = "[server] INFO: New display: 1200x1600/280 (id=158)\n";
        let head = "x".repeat(80 * 1024); // > 64KiB
                                          // announce 行在头部：落在尾部窗口外 → None。
        let early = dir.join("early.log");
        std::fs::write(&early, format!("{line}{head}")).unwrap();
        assert_eq!(display_id_from_log(&early), None);
        // announce 行在尾部 → Some。
        let late = dir.join("late.log");
        std::fs::write(&late, format!("{head}{line}")).unwrap();
        assert_eq!(display_id_from_log(&late), Some(158));
    }

    // ----------------------------------------------------------- spec JSON I/O

    #[test]
    fn from_json_defaults_for_optional_fields() {
        let spec =
            SessionSpec::from_json(r#"{"command":["a","b"],"log_path":"/tmp/duo.log"}"#).unwrap();
        assert_eq!(spec.command, vec!["a".to_string(), "b".to_string()]);
        assert_eq!(spec.log_path, PathBuf::from("/tmp/duo.log"));
        assert_eq!(spec.max_restarts, 3);
        assert_eq!(spec.restart_delay_s, 2.0);
        assert!(spec.env.is_empty());
    }

    #[test]
    fn from_json_requires_command_and_log_path() {
        assert!(SessionSpec::from_json(r#"{"log_path":"/tmp/x.log"}"#).is_err());
        assert!(SessionSpec::from_json(r#"{"command":["sh"]}"#).is_err());
        assert!(SessionSpec::from_json("not json").is_err());
    }

    #[test]
    fn to_json_from_json_round_trip() {
        let mut env = BTreeMap::new();
        env.insert("ZED".to_string(), "1".to_string());
        env.insert("ADB".to_string(), r"C:\tools\adb.exe".to_string());
        let spec = SessionSpec {
            command: vec!["/bin/sh".into(), "-c".into(), "true".into()],
            log_path: PathBuf::from("/tmp/duo/rt.log"),
            max_restarts: 5,
            restart_delay_s: 0.25,
            env,
        };
        let json = spec.to_json();
        assert_eq!(SessionSpec::from_json(&json).unwrap(), spec);
    }

    // ------------------------------------------------------------- supervision

    #[test]
    fn run_session_clean_exit_events_and_log() {
        let dir = scratch_dir("clean");
        let spec = sh_spec(&dir, "echo 'engine says hi'; exit 0");
        let mut events = Vec::new();
        let code = run_session(&spec, &mut |e| events.push(e.clone()));
        assert_eq!(code, 0);
        assert_eq!(
            events,
            vec![SessionEvent::Started, SessionEvent::Exited { code: 0 }]
        );
        assert!(read_to_string(&spec.log_path)
            .unwrap()
            .contains("engine says hi"));
    }

    #[test]
    fn run_session_env_reaches_child() {
        let dir = scratch_dir("env");
        let mut spec = sh_spec(&dir, "printf %s \"$DUO_TEST_PIN\"");
        spec.env
            .insert("DUO_TEST_PIN".to_string(), r"C:\tools\adb.exe".to_string());
        assert_eq!(run_session(&spec, &mut |_| {}), 0);
        assert!(read_to_string(&spec.log_path)
            .unwrap()
            .contains(r"C:\tools\adb.exe"));
    }

    #[test]
    fn run_session_restarts_then_succeeds() {
        let dir = scratch_dir("retry");
        let counter = dir.join("runs");
        // 前两次崩溃、第三次成功（用计数文件记录是第几次 run）。
        let script = format!(
            "n=$(cat \"{c}\" 2>/dev/null || echo 0); n=$((n+1)); echo $n >\"{c}\"; \
             [ \"$n\" -ge 3 ] && exit 0; exit 1",
            c = counter.display()
        );
        let spec = sh_spec(&dir, &script);
        let mut events = Vec::new();
        let code = run_session(&spec, &mut |e| events.push(e.clone()));
        assert_eq!(code, 0);
        assert_eq!(
            events,
            vec![
                SessionEvent::Started,
                SessionEvent::Restarted { restarts: 1 },
                SessionEvent::Restarted { restarts: 2 },
                SessionEvent::Exited { code: 0 },
            ]
        );
    }

    #[test]
    fn run_session_exhausts_restarts() {
        let dir = scratch_dir("crash");
        let mut spec = sh_spec(&dir, "exit 7");
        spec.max_restarts = 2;
        let mut events = Vec::new();
        let code = run_session(&spec, &mut |e| events.push(e.clone()));
        assert_eq!(code, 7);
        assert_eq!(
            events,
            vec![
                SessionEvent::Started,
                SessionEvent::Restarted { restarts: 1 },
                SessionEvent::Restarted { restarts: 2 },
                SessionEvent::Exited { code: 7 },
            ]
        );
    }

    #[test]
    fn run_session_abortable_kills_hung_child_fast() {
        let dir = scratch_dir("abort");
        let spec = sh_spec(&dir, "sleep 30; exit 0");
        let abort = std::sync::Arc::new(AtomicBool::new(false));
        let flag = abort.clone();
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(150));
            flag.store(true, Ordering::Relaxed);
        });
        let t0 = std::time::Instant::now();
        let code = run_session_abortable(&spec, &abort, &mut |_| {});
        assert!(t0.elapsed().as_secs() < 5, "abort must not wait the full sleep");
        assert_ne!(code, 0);
        // 预置 abort：spawn 后第一轮轮询即杀，同样不得挂满整个 sleep。
        let t1 = std::time::Instant::now();
        run_session_abortable(&spec, &AtomicBool::new(true), &mut |_| {});
        assert!(t1.elapsed().as_secs() < 5);
    }
}

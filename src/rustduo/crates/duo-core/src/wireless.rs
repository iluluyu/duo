//! 无线 adb 连接（`adb connect ip:port`）。面板设备卡的「无线连接」入口。
//!
//! 合同：
//! - 目标归一：裸 IP 补默认端口 5555；带端口（含 `:`）原样透传；
//! - 结果裁决走 stdout 文本而非退出码（adb connect 对 "failed to
//!   connect" 仍可能 rc=0，历史行为）；
//! - 挂死预算 10s（与 devices 查询同级）：不可达主机上的重试不得拖死
//!   面板。

use std::io::Read;
use std::process::Child;
use std::thread;
use std::time::Duration;

use crate::quiet::quiet_command;

/// 经典 adb over TCP 端口（`adb tcpip` / 「无线调试」直连档）。
pub const DEFAULT_ADB_PORT: u16 = 5555;

/// 一次 connect/disconnect 往返的挂死预算。
pub const CONNECT_TIMEOUT_S: f64 = 10.0;

/// 连接结果（面板 Toast 文案区分首次连接与已连接）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectOutcome {
    Connected,
    AlreadyConnected,
}

impl ConnectOutcome {
    pub fn state_name(self) -> &'static str {
        match self {
            ConnectOutcome::Connected => "connected",
            ConnectOutcome::AlreadyConnected => "already-connected",
        }
    }
}

/// 目标归一：去空白，空串拒绝；无 `:` 补 `:5555`，有则原样（IPv6 字面
/// 量不在支持面内——Windows adb 需 `[::1]:port` 形式，颜色 OEM 场景
/// 无此需求）。
pub fn normalize_target(input: &str) -> Result<String, String> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return Err("wireless target is empty".into());
    }
    if trimmed.contains(':') {
        return Ok(trimmed.to_string());
    }
    Ok(format!("{trimmed}:{DEFAULT_ADB_PORT}"))
}

/// `adb connect` 输出裁决。成功词形（真机/上游 adb 源）：
/// ``connected to <target>``、``already connected to <target>``（新版
/// adb 目标带引号）；失败词形：``cannot connect to ...: <reason>``、
/// ``failed to connect to ...``、``error: ...``。
pub fn parse_connect_output(target: &str, text: &str) -> Result<ConnectOutcome, String> {
    let lowered = text.to_lowercase();
    if lowered.contains("already connected") {
        return Ok(ConnectOutcome::AlreadyConnected);
    }
    if lowered.contains("connected to") {
        return Ok(ConnectOutcome::Connected);
    }
    let detail = text
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("no output from adb");
    Err(format!("connect {target} failed: {detail}"))
}

/// `[adb, "connect", target]`。
pub fn connect_argv(adb: &str, target: &str) -> Vec<String> {
    vec![adb.to_string(), "connect".into(), target.to_string()]
}

/// `[adb, "disconnect"]`（全部断开）或 `[adb, "disconnect", target]`。
pub fn disconnect_argv(adb: &str, target: Option<&str>) -> Vec<String> {
    match target {
        Some(target) => vec![adb.to_string(), "disconnect".into(), target.to_string()],
        None => vec![adb.to_string(), "disconnect".into()],
    }
}

/// 带挂死预算的一次 spawn：返回 (rc, stdout, stderr)；超时杀进程回 Err。
pub fn run_argv_with_timeout(
    argv: &[String],
    timeout_s: f64,
) -> Result<(i32, String, String), String> {
    let mut command = quiet_command(&argv[0]);
    command
        .args(&argv[1..])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .stdin(std::process::Stdio::null());
    let mut child: Child = command
        .spawn()
        .map_err(|e| format!("adb failed to launch: {e}"))?;
    let deadline = std::time::Instant::now() + Duration::from_secs_f64(timeout_s);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("adb command timed out".into());
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(e) => return Err(format!("adb wait failed: {e}")),
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
    Ok((
        status.code().unwrap_or(-1),
        String::from_utf8_lossy(&stdout_buf).into_owned(),
        String::from_utf8_lossy(&stderr_buf).into_owned(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bare_ip_gets_default_port() {
        assert_eq!(
            normalize_target("192.168.1.100").unwrap(),
            "192.168.1.100:5555"
        );
        assert_eq!(
            normalize_target(" 192.168.1.100 \n").unwrap(),
            "192.168.1.100:5555"
        );
    }

    #[test]
    fn host_port_passthrough_and_rejects_empty() {
        assert_eq!(
            normalize_target("192.168.1.100:40135").unwrap(),
            "192.168.1.100:40135"
        );
        assert_eq!(
            normalize_target("192.168.1.4:5555").unwrap(),
            "192.168.1.4:5555"
        );
        assert!(normalize_target("").is_err());
        assert!(normalize_target("   ").is_err());
    }

    #[test]
    fn connect_output_success_wordings() {
        assert_eq!(
            parse_connect_output("t", "connected to 192.168.1.100:5555\n").unwrap(),
            ConnectOutcome::Connected
        );
        assert_eq!(
            parse_connect_output("t", "already connected to 192.168.1.100:5555\n").unwrap(),
            ConnectOutcome::AlreadyConnected
        );
        // already 优先于 connected（包含子串，先判 already）。
        assert_eq!(
            parse_connect_output("t", "already connected to 't'\n").unwrap(),
            ConnectOutcome::AlreadyConnected
        );
    }

    #[test]
    fn connect_output_failure_wordings_carry_reason() {
        let err = parse_connect_output(
            "192.168.1.100:5555",
            "cannot connect to 192.168.1.100:5555: Connection refused",
        )
        .unwrap_err();
        assert!(err.contains("192.168.1.100:5555"));
        assert!(err.contains("Connection refused"));
        assert!(parse_connect_output(
            "t",
            "failed to connect to '192.168.1.100:40135': Operation timed out"
        )
        .is_err());
        assert!(parse_connect_output("t", "error: closed").is_err());
        assert!(parse_connect_output("t", "").is_err());
    }

    #[test]
    fn argv_shapes() {
        assert_eq!(
            connect_argv("/fake/adb.exe", "192.168.1.100:5555"),
            ["/fake/adb.exe", "connect", "192.168.1.100:5555"]
        );
        assert_eq!(
            disconnect_argv("/fake/adb.exe", Some("192.168.1.100:5555")),
            ["/fake/adb.exe", "disconnect", "192.168.1.100:5555"]
        );
        assert_eq!(
            disconnect_argv("/fake/adb.exe", None),
            ["/fake/adb.exe", "disconnect"]
        );
    }

    #[test]
    fn state_names_are_stable_json_strings() {
        assert_eq!(ConnectOutcome::Connected.state_name(), "connected");
        assert_eq!(
            ConnectOutcome::AlreadyConnected.state_name(),
            "already-connected"
        );
    }
}

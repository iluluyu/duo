//! 点击磁贴 → duo-core 受监督会话的装配（纯逻辑，可测）。
//!
//! v0 对齐 `duo mirror --app <pkg> --embed`（默认沉浸式宿主）的 scrcpy
//! argv 形态：flex 虚拟屏 + `--start-app=+pkg` + `--window-title` +
//! borderless（嵌入模式 scrcpy 必须是纯表面）。监督进程是
//! `duo-core session --spec <json>`（TODO 0.2.2 的 JSON-lines 协议），
//! 面板只 spawn 子进程、不监督（崩溃重启由 duo-core 做）。

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use duo_core::engine::{DisplaySpec, DisplayMode, EngineArgs, VideoSpec};
use duo_core::session::SessionSpec;
use duo_core::settings::Settings;

/// 面板点击一次启动的全部输入。
#[derive(Debug, Clone)]
pub struct LaunchRequest {
    /// 目标 app 包名（目录条目）。
    pub package: String,
    /// 窗口标题 = 目录标签（--window-title，宿主窗口语义）。
    pub label: String,
    /// 目标设备 serial。
    pub serial: String,
    /// scrcpy 二进制路径（解析：设置 > 探测 > "scrcpy" 字面量）。
    pub scrcpy_binary: String,
    /// 面板 adb（ADB 环境变量钉死，防混版本互杀）。
    pub adb_binary: String,
}

/// 会话日志落点：logs_dir()/panel-<pkg>.log（对齐 Python panel_log_path：
/// 一个包一个文件，追加式，display-id 从这里回读）。
pub fn panel_log_path(base: Option<&Path>, package: &str) -> PathBuf {
    duo_core::paths::logs_dir(base).join(format!("panel-{package}.log"))
}

/// settings.video_codec 的 scrcpy 落点：auto → h265（探测在 0.2.2 未下沉，
/// v0 走默认择优）；显式值原样。
pub fn effective_codec(settings: &Settings) -> String {
    match settings.video_codec.as_str() {
        "h264" | "h265" | "av1" => settings.video_codec.clone(),
        _ => "h265".into(),
    }
}

/// 组装 EngineArgs（参数对齐 duo mirror --embed 的面板路径）。
pub fn engine_args(req: &LaunchRequest, settings: &Settings) -> EngineArgs {
    let mut args = EngineArgs::new(req.serial.clone());
    args.display = DisplaySpec {
        mode: DisplayMode::Flex,
        // 缺省 16:9 初始形状由 engine 层兜底（1920x1080）；建屏密度跟随
        // 设置（None = 跟随设备）。
        width: None,
        height: None,
        dpi: settings.dpi.map(|d| d as u32),
    };
    args.video = VideoSpec {
        codec: effective_codec(settings),
        encoder: None,
        bitrate_mbps: settings.bitrate_mbps.unwrap_or(30).clamp(1, 200) as u32,
        max_fps: settings.fps.unwrap_or(60).clamp(1, 240) as u32,
    };
    args.app_package = Some(req.package.clone());
    args.window_title = Some(req.label.clone());
    // --embed 语义：scrcpy 窗口为纯表面，标题栏/胶囊由宿主窗口承担
    // （沉浸式默认）。
    args.borderless = true;
    // 设置门控先于默认（EngineArgs::new 的实验预设开 screen_off；面板
    // 路径以 settings.turn_screen_off 为准，对齐 duo mirror）。
    args.screen_off = settings.turn_screen_off;
    // 音频策略：off = 静音；latest/all 交给 CLI 仲裁（v0 直接带音频起）。
    args.audio = settings.audio_policy != "off";
    args
}

/// 组装完整 SessionSpec（command + 日志 + ADB pin 环境）。
pub fn session_spec(
    req: &LaunchRequest,
    settings: &Settings,
    data_dir: Option<&Path>,
) -> Result<SessionSpec, String> {
    let argv = engine_args(req, settings).to_argv(&req.scrcpy_binary)?;
    let mut env = BTreeMap::new();
    env.insert("ADB".to_string(), req.adb_binary.clone());
    Ok(SessionSpec {
        command: argv,
        log_path: panel_log_path(data_dir, &req.package),
        max_restarts: 3,
        restart_delay_s: 2.0,
        env,
    })
}

/// duo-core 二进制的解析顺序：环境变量覆盖 > PATH 上的 duo-core。
pub fn duo_core_binary() -> String {
    std::env::var("DUO_CORE_BIN").unwrap_or_else(|_| "duo-core".into())
}

/// spawn 子进程的 argv：`duo-core session --spec <json>`。
pub fn spawn_argv(spec: &SessionSpec) -> Vec<String> {
    vec![
        "session".into(),
        "--spec".into(),
        spec.to_json(),
    ]
}

/// 真正起子进程（detached：日志归 duo-core 追加，不接管道防 SIGPIPE）。
pub fn spawn_session(spec: &SessionSpec) -> Result<std::process::Child, String> {
    let argv = spawn_argv(spec);
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&spec.log_path)
        .map_err(|e| format!("会话日志打不开（{e}）"))?;
    std::process::Command::new(duo_core_binary())
        .args(&argv)
        .stdout(log)
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("duo-core 起不来（{e}）"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req() -> LaunchRequest {
        LaunchRequest {
            package: "com.tencent.mm".into(),
            label: "微信".into(),
            serial: "ABC123".into(),
            scrcpy_binary: r"C:\tools\scrcpy.exe".into(),
            adb_binary: r"C:\tools\adb.exe".into(),
        }
    }

    fn base(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "duo-panel-launch-{tag}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn argv_matches_embed_shape() {
        let spec = session_spec(&req(), &Settings::default(), None).unwrap();
        let argv = &spec.command;
        let joined = argv.join(" ");
        assert_eq!(argv[0], r"C:\tools\scrcpy.exe");
        assert!(joined.contains("--serial=ABC123"));
        assert!(joined.contains("--flex-display"));
        assert!(joined.contains("--new-display=1920x1080/160"));
        assert!(joined.contains("--start-app=+com.tencent.mm"));
        assert!(joined.contains("--window-title=微信"));
        assert!(joined.contains("--window-borderless"));
        assert!(!joined.contains("--turn-screen-off"), "设置默认关屏 = false");
    }

    #[test]
    fn settings_drive_codec_fps_bitrate_and_screen_off() {
        let mut s = Settings::default();
        s.video_codec = "h264".into();
        s.fps = Some(120);
        s.bitrate_mbps = Some(8);
        s.turn_screen_off = true;
        s.dpi = None;
        let argv = engine_args(&req(), &s).to_argv("scrcpy").unwrap();
        let joined = argv.join(" ");
        assert!(joined.contains("--video-codec=h264"));
        assert!(joined.contains("--max-fps=120"));
        assert!(joined.contains("--video-bit-rate=8M"));
        assert!(joined.contains("--turn-screen-off"));
        // dpi=None 跟随设备：new-display 不带 /N 后缀。
        assert!(joined.contains("--new-display=1920x1080 "));
        assert!(joined.contains(" "));
        assert!(!joined.contains("--new-display=1920x1080/"));
    }

    #[test]
    fn audio_off_policy_mutes_the_session() {
        let mut s = Settings::default();
        s.audio_policy = "off".into();
        let argv = engine_args(&req(), &s).to_argv("scrcpy").unwrap();
        assert!(argv.iter().any(|a| a == "--no-audio"));
    }

    #[test]
    fn auto_codec_falls_back_to_h265() {
        assert_eq!(effective_codec(&Settings::default()), "h265");
        let mut s = Settings::default();
        s.video_codec = "av1".into();
        assert_eq!(effective_codec(&s), "av1");
    }

    #[test]
    fn spec_carries_adb_pin_and_panel_log() {
        let dir = base("spec");
        let spec = session_spec(&req(), &Settings::default(), Some(&dir)).unwrap();
        assert_eq!(
            spec.env.get("ADB").map(String::as_str),
            Some(r"C:\tools\adb.exe")
        );
        assert_eq!(
            spec.log_path,
            dir.join("logs").join("panel-com.tencent.mm.log")
        );
        // spec JSON 可被 duo-core 会话子命令反序列化。
        let json = spec.to_json();
        let back = SessionSpec::from_json(&json).unwrap();
        assert_eq!(back.command, spec.command);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn spawn_argv_is_session_subcommand_with_spec() {
        let spec = session_spec(&req(), &Settings::default(), None).unwrap();
        let argv = spawn_argv(&spec);
        assert_eq!(argv[0], "session");
        assert_eq!(argv[1], "--spec");
        assert!(SessionSpec::from_json(&argv[2]).is_ok());
    }
}

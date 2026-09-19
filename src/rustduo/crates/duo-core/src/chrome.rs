//! 窗口 chrome overlay：无边框会话的 Windows 侧悬停控件（对译
//! `pyduo/core/chrome.py`，真机验证过的路径）。``duo mirror --chrome``
//! 让 scrcpy 跑无边框窗口，胶囊/下巴由 C# overlay 提供——它必须住在
//! Windows 侧与 scrcpy 窗口对话（FindWindow/GetWindowRect/样式/z 序）。
//! overlay 源码随包内嵌（与 pyduo resources 同一份文件），首次使用时
//! 用每台 Windows 自带的 .NET Framework ``csc.exe`` 现场编译（~0.2s）
//! 并缓存在数据目录。Rust 版原生跑在 Windows，路径已是 Windows 形态；
//! 仅 POSIX 绝对路径（WSL 场景）经 ``wslpath -w`` 换算。

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use sha2::{Digest, Sha256};

use crate::paths::{data_dir, logs_dir};
use crate::quiet::quiet_command;

/// overlay 源（pyduo resources 里同一份 chrome_overlay.cs，编译期嵌入）。
pub const OVERLAY_SOURCE_TEXT: &str = include_str!("../resources/chrome_overlay.cs");

/// .NET Framework 编译器候选，优先后用（C:\ 与 /mnt/c/ 两形态，存在性探测）。
pub const CSC_CANDIDATES: [&str; 4] = [
    "C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe",
    "C:\\Windows\\Microsoft.NET\\Framework\\v4.0.30319\\csc.exe",
    "/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe",
    "/mnt/c/Windows/Microsoft.NET/Framework/v4.0.30319/csc.exe",
];

/// 数据目录下的缓存产物名（exe + 源哈希 sidecar）。
pub const EXE_NAME: &str = "DuoChromeOverlay.exe";

const COMPILE_TIMEOUT_S: f64 = 60.0;
const WSLPATH_TIMEOUT_S: f64 = 10.0;
const TERMINATE_TIMEOUT_S: f64 = 5.0;

/// overlay 无法准备或启动（= pyduo ChromeError；Rust 侧统一 String 传消息）。
pub type ChromeError = String;

/// overlay 源内容哈希（缓存失效键）。
pub fn source_stamp() -> String {
    let mut hasher = Sha256::new();
    hasher.update(OVERLAY_SOURCE_TEXT.as_bytes());
    hex(&hasher.finalize())
}

fn hex(bytes: &[u8]) -> String {
    const CHARS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(CHARS[(b >> 4) as usize] as char);
        out.push(CHARS[(b & 0xf) as usize] as char);
    }
    out
}

/// scrcpy 是否该跑 ``--window-borderless``：上巴 native = 真系统标题栏，
/// 必须让 scrcpy 建自己的带框窗（SDL 无边框窗的 WM_NCCALCSIZE 接管让
/// 事后补的 WS_CAPTION 永远占不到标题带，2026-09-09 真机定稿）。
pub fn borderless_for(top_mode: &str) -> bool {
    top_mode != "native"
}

/// 按应用的胶囊固定旗标文件（overlay 写、启动读）：``"1"`` 固定，缺文件
/// 或 ``"0"`` 不固定。包名本身文件名安全，替换仅防御；整机镜像无包名
/// 不落盘（固定随会话生灭）。
pub fn top_pin_path(package: &str, base: Option<&Path>) -> PathBuf {
    let safe: String = package
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || "._-".contains(c) {
                c
            } else {
                '_'
            }
        })
        .collect();
    data_dir(base)
        .join("overlay-pin")
        .join(format!("{safe}.flag"))
}

/// 该应用本次启动是否固定胶囊（默认关）。
pub fn read_top_pin(package: &str, base: Option<&Path>) -> bool {
    std::fs::read_to_string(top_pin_path(package, base))
        .map(|text| text.trim() == "1")
        .unwrap_or(false)
}

/// 缓存 exe 是否与源 stamp 匹配：两文件都在、sidecar 恰为 stamp。
pub fn build_is_fresh(exe: &Path, stamp_file: &Path, stamp: &str) -> bool {
    if !exe.is_file() || !stamp_file.is_file() {
        return false;
    }
    std::fs::read_to_string(stamp_file)
        .map(|text| text.trim() == stamp)
        .unwrap_or(false)
}

/// 组装编译 overlay 的 csc.exe argv。``-codepage:65001``：源是 UTF-8 无
/// BOM，legacy csc 默认按系统 ANSI（中文 Windows = GBK）读——多字节注释
/// 偶发错位出 CS1056，显式按 UTF-8 读才确定。
pub fn compile_command(csc: &str, source_win: &str, out_win: &str) -> Vec<String> {
    vec![
        csc.to_string(),
        "-nologo".into(),
        "-target:winexe".into(),
        "-optimize+".into(),
        "-codepage:65001".into(),
        format!("-out:{out_win}"),
        "-r:System.dll".into(),
        "-r:System.Drawing.dll".into(),
        "-r:System.Windows.Forms.dll".into(),
        source_win.to_string(),
    ]
}

/// overlay_command 的 kwargs 面（默认值对齐 pyduo 函数签名默认）。
#[derive(Debug, Clone, PartialEq)]
pub struct OverlayArgs {
    pub exe: String,
    pub title: String,
    pub serial: String,
    pub adb_path: String,
    /// 无 ``--app`` 的整机镜像 = True（下巴长按发 HOME）。
    pub home: bool,
    /// flex | fixed | mirror（同驱 resize 策略与下巴形态）。
    pub display_mode: String,
    /// fixed 携带已知初始视频尺寸；flex 自由窗口不传。
    pub video_width: Option<u32>,
    pub video_height: Option<u32>,
    /// Windows 路径（live 尺寸 ``Texture:`` 行的尾读通道）。
    pub session_log: Option<String>,
    /// G2 圆角（DIP）；0 = 不下发。
    pub corner_radius_dip: i64,
    pub top_bar_mode: String,
    pub bottom_bar_mode: String,
    pub pin_top: bool,
    /// 固定回写文件（Windows 路径）；None = 随会话生灭。
    pub pin_file: Option<String>,
    /// False = 普通不透明材质（非毛玻璃）。
    pub glass: bool,
    /// light | dark | system（仅普通材质消费）。
    pub bar_theme: String,
}

impl Default for OverlayArgs {
    fn default() -> Self {
        Self {
            exe: String::new(),
            title: String::new(),
            serial: String::new(),
            adb_path: String::new(),
            home: false,
            display_mode: "flex".into(),
            video_width: None,
            video_height: None,
            session_log: None,
            corner_radius_dip: 0,
            top_bar_mode: "immersive".into(),
            bottom_bar_mode: "immersive".into(),
            pin_top: false,
            pin_file: None,
            glass: true,
            bar_theme: "system".into(),
        }
    }
}

/// 组装启动已编译 overlay 的 argv（PE 二进制收真 UTF-16 argv，CJK 标题
/// 直传无需 base64；模式串纯透传，枚举在上游校验）。
pub fn overlay_command(args: &OverlayArgs) -> Vec<String> {
    let mut argv = vec![
        args.exe.clone(),
        "--title".into(),
        args.title.clone(),
        "--serial".into(),
        args.serial.clone(),
        "--adb".into(),
        args.adb_path.clone(),
        "--home".into(),
        flag(args.home),
        "--display-mode".into(),
        args.display_mode.clone(),
        "--chrome-top".into(),
        args.top_bar_mode.clone(),
        "--chrome-bottom".into(),
        args.bottom_bar_mode.clone(),
        "--pin-top".into(),
        flag(args.pin_top),
        "--glass".into(),
        flag(args.glass),
        "--bar-theme".into(),
        args.bar_theme.clone(),
    ];
    if matches!((args.video_width, args.video_height), (Some(w), Some(h)) if w > 0 && h > 0) {
        argv.push("--video-w".into());
        argv.push(args.video_width.unwrap().to_string());
        argv.push("--video-h".into());
        argv.push(args.video_height.unwrap().to_string());
    }
    if let Some(log) = args.session_log.as_deref().filter(|s| !s.is_empty()) {
        argv.push("--session-log".into());
        argv.push(log.to_string());
    }
    if let Some(pin) = args.pin_file.as_deref().filter(|s| !s.is_empty()) {
        argv.push("--pin-file".into());
        argv.push(pin.to_string());
    }
    if args.corner_radius_dip > 0 {
        argv.push("--corner-radius".into());
        argv.push(args.corner_radius_dip.to_string());
    }
    argv
}

fn flag(on: bool) -> String {
    if on {
        "1".into()
    } else {
        "0".into()
    }
}

/// POSIX 绝对路径经 ``wslpath -w`` 换 Windows 形态；其余原样返回（已是
/// Windows 形态——原生 Windows 场景恒等，对译 pyduo 的非绝对路径分支）。
pub fn wsl_to_windows_path(path: &str) -> Result<String, ChromeError> {
    if !path.starts_with('/') {
        return Ok(path.to_string());
    }
    let mut child = quiet_command("wslpath")
        .arg("-w")
        .arg(path)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| format!("wslpath failed for {path}: {err}"))?;
    let deadline = Instant::now() + Duration::from_secs_f64(WSLPATH_TIMEOUT_S);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut out = String::new();
                if let Some(mut pipe) = child.stdout.take() {
                    let _ = std::io::Read::read_to_string(&mut pipe, &mut out);
                }
                let translated = out.trim();
                if !status.success() || translated.is_empty() {
                    return Err(format!("wslpath could not translate {path}"));
                }
                return Ok(translated.to_string());
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!("wslpath failed for {path}: timed out"));
                }
                thread::sleep(Duration::from_millis(20));
            }
            Err(err) => return Err(format!("wslpath failed for {path}: {err}")),
        }
    }
}

/// 第一个装好的 .NET Framework 编译器路径。
pub fn find_csc() -> Result<String, ChromeError> {
    for candidate in CSC_CANDIDATES {
        if Path::new(candidate).is_file() {
            return Ok(candidate.to_string());
        }
    }
    Err("no .NET Framework csc.exe found under /mnt/c/Windows/Microsoft.NET".into())
}

fn chmod_exec(path: &Path) {
    // WSL 互操作要求 Linux 文件系统上的 exe 有执行位才能被 spawn。
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755));
    }
    #[cfg(not(unix))]
    let _ = path;
}

/// 编译 overlay（缓存过期时）并返回 exe 路径。源内嵌进二进制，落盘到
/// build 目录供 csc 读。启动 csc 失败（如超时）但 exe 已在场 = 并发
/// 竞态对手赢了，复用其产物。
pub fn ensure_built(base: Option<&Path>) -> Result<PathBuf, ChromeError> {
    let build_dir = data_dir(base).join("overlay");
    std::fs::create_dir_all(&build_dir).map_err(|err| format!("overlay dir: {err}"))?;
    let source = build_dir.join("chrome_overlay.cs");
    // 内容有变才重写（mtime 抖动会让并发竞态对手误判缓存失效）。
    let matches_embedded = matches!(
        std::fs::read(&source),
        Ok(bytes) if bytes == OVERLAY_SOURCE_TEXT.as_bytes()
    );
    if !matches_embedded {
        std::fs::write(&source, OVERLAY_SOURCE_TEXT)
            .map_err(|err| format!("overlay source: {err}"))?;
    }
    let exe = build_dir.join(EXE_NAME);
    let stamp_file = build_dir.join(format!("{EXE_NAME}.sha256"));
    let stamp = source_stamp();
    if build_is_fresh(&exe, &stamp_file, &stamp) {
        chmod_exec(&exe);
        return Ok(exe);
    }
    let csc = find_csc()?;
    let command = compile_command(
        &csc,
        &wsl_to_windows_path(&source.display().to_string())?,
        &wsl_to_windows_path(&exe.display().to_string())?,
    );
    match run_compile(&command) {
        Ok(outcome) => {
            if !outcome.success || !exe.is_file() {
                return Err(format!(
                    "csc failed: {}",
                    compile_detail(&command[0], &outcome)
                ));
            }
        }
        Err(detail) => {
            // 并发构建赢了竞态：复用其产物。
            if build_is_fresh(&exe, &stamp_file, &stamp) || exe.is_file() {
                chmod_exec(&exe);
                return Ok(exe);
            }
            return Err(detail);
        }
    }
    std::fs::write(&stamp_file, format!("{stamp}\n"))
        .map_err(|err| format!("stamp write: {err}"))?;
    chmod_exec(&exe);
    Ok(exe)
}

struct CompileOutcome {
    success: bool,
    output_tail: String,
}

/// 跑一次 csc（静默、限 COMPILE_TIMEOUT_S）。启动失败/超时返回 Err。
fn run_compile(command: &[String]) -> Result<CompileOutcome, String> {
    let mut child = quiet_command(&command[0])
        .args(&command[1..])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("csc failed to launch: {err}"))?;
    let deadline = Instant::now() + Duration::from_secs_f64(COMPILE_TIMEOUT_S);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut out = Vec::new();
                let mut err = Vec::new();
                if let Some(mut pipe) = child.stdout.take() {
                    let _ = std::io::Read::read_to_end(&mut pipe, &mut out);
                }
                if let Some(mut pipe) = child.stderr.take() {
                    let _ = std::io::Read::read_to_end(&mut pipe, &mut err);
                }
                // csc 输出可能是本地化（GBK）文本，有损解码即可诊断。
                let raw = if err.is_empty() { out } else { err };
                return Ok(CompileOutcome {
                    success: status.success(),
                    output_tail: String::from_utf8_lossy(&raw).trim().to_string(),
                });
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(format!(
                        "csc failed to launch: timed out after {COMPILE_TIMEOUT_S}s"
                    ));
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(err) => return Err(format!("csc failed to launch: {err}")),
        }
    }
}

fn compile_detail(csc: &str, outcome: &CompileOutcome) -> String {
    let head: String = outcome.output_tail.chars().take(400).collect();
    format!("{csc}: {head}")
}

/// overlay 子进程，绑定一个镜像会话。command 公开供诊断 banner/测试。
pub struct ChromeOverlay {
    pub command: Vec<String>,
    base: Option<PathBuf>,
    child: Option<Child>,
}

impl ChromeOverlay {
    /// ensure_built + 路径换算 + argv 装配（对译 pyduo ChromeOverlay.__init__）。
    pub fn new(base: Option<&Path>, args: OverlayArgs) -> Result<Self, ChromeError> {
        let exe = ensure_built(base)?;
        let command = overlay_command(&OverlayArgs {
            exe: exe.display().to_string(),
            adb_path: wsl_to_windows_path(&args.adb_path)?,
            session_log: match &args.session_log {
                Some(log) => Some(wsl_to_windows_path(log)?),
                None => None,
            },
            pin_file: match &args.pin_file {
                Some(pin) => Some(wsl_to_windows_path(pin)?),
                None => None,
            },
            ..args
        });
        Ok(Self {
            command,
            base: base.map(Path::to_path_buf),
            child: None,
        })
    }

    /// overlay 进程当前是否存活（try_wait 需 &mut，同 Python poll 语义）。
    pub fn running(&mut self) -> bool {
        self.child
            .as_mut()
            .is_some_and(|child| child.try_wait().map(|s| s.is_none()).unwrap_or(false))
    }

    /// 启动 overlay，stdout/stderr 归并进 duo 自己的日志（不依赖 overlay
    /// 进程的 %TEMP% 可写性——真机测试失败曾零证据可查）。banner 先落
    /// 源码指纹 + 模式 + 完整 argv：下次"没有成功"直接回答跑的哪个版本。
    pub fn start(&mut self) -> std::io::Result<PathBuf> {
        let log_path = logs_dir(self.base.as_deref())
            .join("overlay")
            .join("chrome-latest.log");
        std::fs::create_dir_all(log_path.parent().expect("log path has parent"))?;
        let banner = self.banner();
        {
            let mut log = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&log_path)?;
            log.write_all(banner.as_bytes())?;
        }
        // 子进程继承日志句柄；父侧句柄 spawn 后即关（同 Session.start）。
        let log_file = std::fs::OpenOptions::new().append(true).open(&log_path)?;
        self.child = Some(
            quiet_command(&self.command[0])
                .args(&self.command[1..])
                .stdout(Stdio::from(log_file))
                .stderr(Stdio::null())
                .spawn()?,
        );
        Ok(log_path)
    }

    /// banner 文本（pub(crate) 供测试直查，不 spawn）。
    pub(crate) fn banner(&self) -> String {
        let arg_after = |flag: &str| {
            self.command
                .iter()
                .position(|a| a == flag)
                .and_then(|i| self.command.get(i + 1))
                .map(String::as_str)
                .unwrap_or("?")
        };
        format!(
            "=== {} source={} top={} bottom={} pin={} glass={} argv={}\n",
            banner_timestamp(),
            &source_stamp()[..12],
            arg_after("--chrome-top"),
            arg_after("--chrome-bottom"),
            arg_after("--pin-top"),
            arg_after("--glass"),
            python_list(&self.command),
        )
    }

    /// 终止 overlay（未运行时 no-op）：先杀，宽限内等退出（Windows 上
    /// terminate 即 TerminateProcess，与 pyduo 的 terminate→kill 链等效）。
    pub fn stop(&mut self) {
        let Some(mut child) = self.child.take() else {
            return;
        };
        if child.try_wait().map(|s| s.is_none()).unwrap_or(false) {
            let _ = child.kill();
            let deadline = Instant::now() + Duration::from_secs_f64(TERMINATE_TIMEOUT_S);
            while Instant::now() < deadline {
                match child.try_wait() {
                    Ok(Some(_)) | Err(_) => return,
                    Ok(None) => thread::sleep(Duration::from_millis(50)),
                }
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for ChromeOverlay {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Python ``str(list)`` 形态（banner 的 argv 段对齐 pyduo 输出）。
fn python_list(items: &[String]) -> String {
    let joined = items
        .iter()
        .map(|item| format!("'{item}'"))
        .collect::<Vec<_>>()
        .join(", ");
    format!("[{joined}]")
}

/// banner 时间戳（pyduo 用本地时间；无时区库，UTC 诊断即可，与
/// session_log 的既有决定一致）。
fn banner_timestamp() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (y, m, d) = crate::mirror::civil_from_days(days);
    format!(
        "{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02}",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn scratch_dir(tag: &str) -> PathBuf {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "duo-core-chrome-{tag}-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn overlay_source_shipped() {
        // 源随二进制内嵌；关键防退化标记抽查。
        assert!(OVERLAY_SOURCE_TEXT.contains("UpdateLayeredWindow"));
        assert!(OVERLAY_SOURCE_TEXT.contains("SetProcessDPIAware"));
        assert!(OVERLAY_SOURCE_TEXT.contains("--pin-file"));
    }

    #[test]
    fn source_stamp_is_sha256_of_source() {
        let mut hasher = Sha256::new();
        hasher.update(OVERLAY_SOURCE_TEXT.as_bytes());
        assert_eq!(source_stamp(), hex(&hasher.finalize()));
        assert_eq!(source_stamp().len(), 64);
    }

    #[test]
    fn borderless_for_native_top_is_decorated() {
        assert!(!borderless_for("native"));
        assert!(borderless_for("immersive"));
        assert!(borderless_for("none"));
    }

    #[test]
    fn top_pin_path_sanitizes_and_reads_flag() {
        let base = scratch_dir("pin");
        let pin = top_pin_path("cn.foo.Bar", Some(&base));
        assert_eq!(pin.parent().unwrap().file_name().unwrap(), "overlay-pin");
        assert_eq!(pin.file_name().unwrap(), "cn.foo.Bar.flag");
        assert!(!read_top_pin("cn.foo.Bar", Some(&base)), "缺文件 = 不固定");
        // 运行时接线会先建 overlay-pin 目录（同 pyduo __main__ 的 mkdir）。
        fs::create_dir_all(pin.parent().unwrap()).unwrap();
        fs::write(&pin, "1").unwrap();
        assert!(read_top_pin("cn.foo.Bar", Some(&base)));
        fs::write(&pin, "0\n").unwrap();
        assert!(!read_top_pin("cn.foo.Bar", Some(&base)));
        // 异常字符防御性替换，旗标不逃出目录。
        let weird = top_pin_path("a/b\\c:d", Some(&base));
        let name = weird.file_name().unwrap().to_string_lossy().into_owned();
        assert!(!name.contains('/') && !name.contains('\\') && !name.contains(':'));
    }

    #[test]
    fn build_is_fresh_matches_stamp() {
        let dir = scratch_dir("fresh");
        let exe = dir.join(EXE_NAME);
        let stamp = dir.join(format!("{EXE_NAME}.sha256"));
        assert!(!build_is_fresh(&exe, &stamp, "abc"));
        fs::write(&exe, b"MZ").unwrap();
        assert!(!build_is_fresh(&exe, &stamp, "abc"));
        fs::write(&stamp, "abc\n").unwrap();
        assert!(build_is_fresh(&exe, &stamp, "abc"));
        assert!(!build_is_fresh(&exe, &stamp, "other"));
    }

    #[test]
    fn compile_command_shape() {
        let argv = compile_command(
            "/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe",
            "\\\\wsl.localhost\\archlinux\\home\\duo\\chrome_overlay.cs",
            "\\\\wsl.localhost\\archlinux\\home\\.cache\\DuoChromeOverlay.exe",
        );
        assert!(argv[0].ends_with("csc.exe"));
        assert!(argv.contains(&"-nologo".to_string()));
        assert!(argv.contains(&"-target:winexe".to_string()));
        assert!(argv.contains(&"-optimize+".to_string()));
        assert!(argv.contains(&"-codepage:65001".to_string()));
        assert!(argv
            .iter()
            .any(|a| a.starts_with("-out:") && a.ends_with("DuoChromeOverlay.exe")));
        assert!(argv.contains(&"-r:System.Windows.Forms.dll".to_string()));
        assert!(argv.contains(&"-r:System.Drawing.dll".to_string()));
        assert!(argv.last().unwrap().ends_with("chrome_overlay.cs"));
    }

    fn args(exe: &str, title: &str, serial: &str, adb: &str, home: bool) -> OverlayArgs {
        OverlayArgs {
            exe: exe.into(),
            title: title.into(),
            serial: serial.into(),
            adb_path: adb.into(),
            home,
            ..OverlayArgs::default()
        }
    }

    fn value_after<'a>(argv: &'a [String], flag: &str) -> &'a str {
        argv.iter()
            .position(|a| a == flag)
            .and_then(|i| argv.get(i + 1))
            .map(String::as_str)
            .unwrap()
    }

    #[test]
    fn overlay_command_plain_argv() {
        let argv = overlay_command(&args(
            "/x/DuoChromeOverlay.exe",
            "不背单词",
            "4444bd6b",
            "C:\\a.exe",
            true,
        ));
        assert_eq!(argv[0], "/x/DuoChromeOverlay.exe");
        assert_eq!(value_after(&argv, "--title"), "不背单词");
        assert_eq!(value_after(&argv, "--serial"), "4444bd6b");
        assert_eq!(value_after(&argv, "--adb"), "C:\\a.exe");
        assert_eq!(value_after(&argv, "--home"), "1");
        assert!(!argv.join(" ").contains("TitleB64"));
    }

    #[test]
    fn overlay_command_home_off_for_virtual_displays() {
        let argv = overlay_command(&args("/x/DuoChromeOverlay.exe", "t", "s", "a", false));
        assert_eq!(value_after(&argv, "--home"), "0");
    }

    #[test]
    fn overlay_command_carries_glass_and_bar_theme() {
        let argv = overlay_command(&args("/x.exe", "t", "s", "a", false));
        assert_eq!(value_after(&argv, "--glass"), "1");
        assert_eq!(value_after(&argv, "--bar-theme"), "system");
        let argv = overlay_command(&OverlayArgs {
            glass: false,
            bar_theme: "dark".into(),
            top_bar_mode: "native".into(),
            bottom_bar_mode: "native".into(),
            ..args("/x.exe", "t", "s", "a", false)
        });
        assert_eq!(value_after(&argv, "--glass"), "0");
        assert_eq!(value_after(&argv, "--bar-theme"), "dark");
    }

    #[test]
    fn overlay_command_carries_display_mode_and_log() {
        let argv = overlay_command(&OverlayArgs {
            display_mode: "mirror".into(),
            session_log: Some(r"C:\logs\1.log".into()),
            ..args("/x.exe", "t", "s", "a", false)
        });
        assert_eq!(value_after(&argv, "--display-mode"), "mirror");
        assert_eq!(value_after(&argv, "--session-log"), r"C:\logs\1.log");
        assert!(!argv.iter().any(|a| a == "--video-w"));
        let argv = overlay_command(&OverlayArgs {
            display_mode: "fixed".into(),
            video_width: Some(1252),
            video_height: Some(2088),
            ..args("/x.exe", "t", "s", "a", false)
        });
        assert_eq!(value_after(&argv, "--video-w"), "1252");
        assert_eq!(value_after(&argv, "--video-h"), "2088");
        let argv = overlay_command(&args("/x.exe", "t", "s", "a", false));
        assert_eq!(value_after(&argv, "--display-mode"), "flex");
        assert!(!argv.iter().any(|a| a == "--session-log"));
        // flex 自由窗口也收显式 seed（pyduo 语义：launch display box）。
        let argv = overlay_command(&OverlayArgs {
            display_mode: "flex".into(),
            video_width: Some(2560),
            video_height: Some(1440),
            ..args("/x.exe", "t", "s", "a", false)
        });
        assert_eq!(value_after(&argv, "--video-w"), "2560");
        assert_eq!(value_after(&argv, "--video-h"), "1440");
    }

    #[test]
    fn overlay_command_corner_radius() {
        let argv = overlay_command(&OverlayArgs {
            corner_radius_dip: 48,
            ..args("/x.exe", "t", "s", "a", false)
        });
        assert_eq!(value_after(&argv, "--corner-radius"), "48");
        let argv = overlay_command(&args("/x.exe", "t", "s", "a", false));
        assert!(!argv.iter().any(|a| a == "--corner-radius"));
    }

    #[test]
    fn overlay_command_carries_bar_modes() {
        let argv = overlay_command(&args("/x.exe", "t", "s", "a", false));
        assert_eq!(value_after(&argv, "--chrome-top"), "immersive");
        assert_eq!(value_after(&argv, "--chrome-bottom"), "immersive");
        let argv = overlay_command(&OverlayArgs {
            top_bar_mode: "none".into(),
            bottom_bar_mode: "none".into(),
            ..args("/x.exe", "t", "s", "a", false)
        });
        assert_eq!(value_after(&argv, "--chrome-top"), "none");
        assert_eq!(value_after(&argv, "--chrome-bottom"), "none");
    }

    #[test]
    fn overlay_command_pin_flags() {
        let argv = overlay_command(&args("/x.exe", "t", "s", "a", false));
        assert_eq!(value_after(&argv, "--pin-top"), "0");
        assert!(!argv.iter().any(|a| a == "--pin-file"));
        let argv = overlay_command(&OverlayArgs {
            pin_top: true,
            pin_file: Some(r"C:\p\x.flag".into()),
            ..args("/x.exe", "t", "s", "a", false)
        });
        assert_eq!(value_after(&argv, "--pin-top"), "1");
        assert_eq!(value_after(&argv, "--pin-file"), r"C:\p\x.flag");
    }

    #[test]
    fn overlay_command_has_no_embed_path() {
        // embed 实验已删：overlay argv 不得再带 --embed 族旗标。
        let argv = overlay_command(&args("/x.exe", "t", "s", "a", false));
        assert!(!argv.iter().any(|a| a.starts_with("--embed")));
    }

    #[test]
    fn wsl_path_translation_passes_windows_shapes_through() {
        // 非 POSIX 绝对路径原样返回（原生 Windows 场景的恒等分支）。
        assert_eq!(
            wsl_to_windows_path("C:\\bin\\adb.exe").unwrap(),
            "C:\\bin\\adb.exe"
        );
        assert_eq!(wsl_to_windows_path("adb").unwrap(), "adb");
    }

    #[test]
    fn csc_candidates_cover_both_path_shapes() {
        assert!(CSC_CANDIDATES.iter().any(|c| c.starts_with("C:\\")));
        assert!(CSC_CANDIDATES.iter().any(|c| c.starts_with("/mnt/c/")));
    }

    #[test]
    fn ensure_built_reuses_fresh_cache_without_csc() {
        // stamp 命中直接复用缓存：find_csc 都不查（无 csc 的机器上命中
        // 缓存的会话照常启动）。
        let base = scratch_dir("cache");
        let build = base.join("overlay");
        fs::create_dir_all(&build).unwrap();
        fs::write(build.join(EXE_NAME), b"MZ").unwrap();
        fs::write(
            build.join(format!("{EXE_NAME}.sha256")),
            format!("{}\n", source_stamp()),
        )
        .unwrap();
        let exe = ensure_built(Some(&base)).expect("fresh cache reused");
        assert_eq!(exe, build.join(EXE_NAME));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&exe).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o755, "产物保持可执行位");
        }
    }

    #[test]
    fn ensure_built_compiles_or_reports_missing_csc() {
        let base = scratch_dir("nocsc");
        match find_csc() {
            Err(_) => {
                // 无 csc（CI/Linux）：干净目录上只失败不 panic。
                let err = ensure_built(Some(&base)).expect_err("no csc");
                assert!(err.contains("csc.exe"), "got {err}");
            }
            Ok(_) => {
                // 有真 csc（Windows/WSL 互操作）：实编译守门（CS0122 类
                // 事故只被括号配平检查放过）。
                let exe = ensure_built(Some(&base)).expect("real csc build");
                assert!(exe.is_file());
                let stamp =
                    fs::read_to_string(base.join("overlay").join(format!("{EXE_NAME}.sha256")))
                        .unwrap();
                assert_eq!(stamp.trim(), source_stamp());
                // 二次调用命中缓存（不重编译）。
                assert_eq!(ensure_built(Some(&base)).unwrap(), exe);
            }
        }
    }

    #[test]
    fn stop_before_start_is_noop() {
        let mut overlay = ChromeOverlay {
            command: vec!["/bin/true".into()],
            base: None,
            child: None,
        };
        assert!(!overlay.running());
        overlay.stop();
        assert!(!overlay.running());
    }

    #[test]
    fn banner_carries_stamp_modes_and_argv() {
        let overlay = ChromeOverlay {
            command: overlay_command(&OverlayArgs {
                exe: "/x.exe".into(),
                title: "不背单词".into(),
                top_bar_mode: "native".into(),
                bottom_bar_mode: "immersive".into(),
                ..args("/x.exe", "不背单词", "s", "a", false)
            }),
            base: None,
            child: None,
        };
        let banner = overlay.banner();
        assert!(banner.contains(&format!("source={}", &source_stamp()[..12])));
        assert!(banner.contains("top=native bottom=immersive"));
        assert!(banner.contains("--title") && banner.contains("不背单词"));
        assert!(banner.contains("argv=['/x.exe', '--title', '不背单词'"));
    }

    #[test]
    fn start_spawns_and_logs() {
        let base = scratch_dir("start");
        let mut overlay = ChromeOverlay {
            command: vec!["/bin/echo".into(), "hi".into()],
            base: Some(base.clone()),
            child: None,
        };
        let log = overlay.start().expect("overlay starts (echo)");
        assert_eq!(
            log,
            base.join("logs").join("overlay").join("chrome-latest.log")
        );
        assert!(log.is_file());
        // 子进程输出也归并进同一日志（stdout 继承日志句柄）。
        let deadline = Instant::now() + Duration::from_secs(5);
        let text = loop {
            let text = fs::read_to_string(&log).unwrap();
            if text.contains("hi") || Instant::now() >= deadline {
                break text;
            }
            thread::sleep(Duration::from_millis(20));
        };
        assert!(text.contains("hi"), "child stdout merged: {text}");
        assert!(overlay.running() || overlay.child.is_some());
        overlay.stop();
        assert!(!overlay.running());
    }
}

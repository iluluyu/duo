//! scrcpy 引擎 argv 装配。对译自 duo/core/engine.py；
//! 合同镜像 tests/test_engine_args.py。
//!
//! 版本怪癖（真机实验结论，原注释同源）：
//! - scrcpy >= 3.0 无正向 ``--clipboard-autosync`` 旗标——永不发射正向形式；
//! - ``--flex-display`` 必须配 ``--new-display``；
//! - flex 尺寸源恒为原分辨率；平滑度预算由 video_codec=h264 + fps 承担；
//! - ``--start-app`` 恒带 ``+`` 前缀（无前缀时已有活动任务的应用会被
//!   “投递到运行实例”，虚拟屏上什么都看不到，§7.1.4）；
//! - adb 钉死不走 argv（scrcpy 4.1 无 --adb 选项），经 ADB 环境变量
//!   （进程层 0.2.2 移植 adb_pin_env）。

/// 显示模式。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DisplayMode {
    /// 物理设备屏镜像。
    Mirror,
    /// 持续跟随窗口的虚拟屏（scrcpy >= 4.1 ``--flex-display``）。
    #[default]
    Flex,
    /// 锁定分辨率的虚拟屏。
    Fixed,
}

/// 一次 scrcpy 窗口摆放（屏幕坐标）。对译 Python WindowGeometry。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct WindowGeometry {
    pub x: i64,
    pub y: i64,
    pub width: i64,
    pub height: i64,
}

/// 一条 scrcpy 显示规格。
#[derive(Debug, Clone, PartialEq, Default)]
pub struct DisplaySpec {
    pub mode: DisplayMode,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub dpi: Option<u32>,
}

impl DisplaySpec {
    /// 编译为 scrcpy 显示旗标。fixed 模式缺宽高是配置错误。
    pub fn to_flags(&self) -> Result<Vec<String>, String> {
        match self.mode {
            DisplayMode::Mirror => Ok(Vec::new()),
            DisplayMode::Flex => {
                // 缺省 16:9 初始形状（1920x1080，参考平板横屏）；
                // --flex-display 让虚拟屏持续跟随窗口，方向请求由宿主
                // 一次性锁死；--render-fit=stretched 只平滑跟随过渡期。
                let (w, h) = match (self.width, self.height) {
                    (Some(w), Some(h)) => (w, h),
                    _ => (1920, 1080),
                };
                let mut value = format!("{w}x{h}");
                if let Some(dpi) = self.dpi {
                    value.push_str(&format!("/{dpi}"));
                }
                Ok(vec![
                    format!("--new-display={value}"),
                    "--flex-display".into(),
                    "--no-window-aspect-ratio-lock".into(),
                    "--render-fit=stretched".into(),
                ])
            }
            DisplayMode::Fixed => {
                let (w, h) = match (self.width, self.height) {
                    (Some(w), Some(h)) => (w, h),
                    _ => return Err("fixed display mode requires width and height".into()),
                };
                let mut value = format!("{w}x{h}");
                if let Some(dpi) = self.dpi {
                    value.push_str(&format!("/{dpi}"));
                }
                Ok(vec![format!("--new-display={value}")])
            }
        }
    }
}

/// 视频编码参数。encoder=None 让 scrcpy 自选（正常钉死来自硬件编码器
/// 探测，duo.core.codec，0.2.2 移植）。
#[derive(Debug, Clone)]
pub struct VideoSpec {
    pub codec: String,
    pub encoder: Option<String>,
    pub bitrate_mbps: u32,
    pub max_fps: u32,
}

impl Default for VideoSpec {
    fn default() -> Self {
        Self {
            codec: "h265".into(),
            encoder: None,
            bitrate_mbps: 30,
            max_fps: 90,
        }
    }
}

impl VideoSpec {
    pub fn to_flags(&self) -> Vec<String> {
        let mut flags = vec![format!("--video-codec={}", self.codec)];
        if let Some(encoder) = &self.encoder {
            flags.push(format!("--video-encoder={encoder}"));
        }
        flags.push(format!("--video-bit-rate={}M", self.bitrate_mbps));
        flags.push(format!("--max-fps={}", self.max_fps));
        flags
    }
}

/// 一次完整镜像会话，可编译为 scrcpy 命令。
#[derive(Debug, Clone, Default)]
pub struct EngineArgs {
    pub serial: String,
    pub adb_binary: Option<String>,
    pub display: DisplaySpec,
    pub video: VideoSpec,
    pub app_package: Option<String>,
    pub screen_off: bool,
    pub stay_awake: bool,
    /// 会话结束后保留虚拟屏内容（仅自建显示有效；镜像恒不发射）。
    pub vd_keep_content: bool,
    pub keyboard: String,
    pub audio: bool,
    pub audio_codec: String,
    pub audio_buffer_ms: u32,
    pub window_title: Option<String>,
    pub window_x: Option<i32>,
    pub window_y: Option<i32>,
    pub window_width: Option<u32>,
    pub window_height: Option<u32>,
    pub borderless: bool,
    /// stderr 周期 fps 行进会话日志（卡顿诊断）。
    pub print_fps: bool,
}

/// 实验验证过的默认会话形态（screen_off/stay_awake/uhid/flac/100ms 开）。
impl EngineArgs {
    pub fn new(serial: impl Into<String>) -> Self {
        Self {
            serial: serial.into(),
            screen_off: true,
            stay_awake: true,
            keyboard: "uhid".into(),
            audio: true,
            audio_codec: "flac".into(),
            audio_buffer_ms: 100,
            print_fps: true,
            ..Default::default()
        }
    }
}

fn int_flag(name: &str, value: Option<i64>) -> Vec<String> {
    match value {
        Some(v) => vec![format!("--{name}={v}")],
        None => Vec::new(),
    }
}

impl EngineArgs {
    /// 编译为完整 scrcpy argv。参数顺序与 Python 实现一致。
    pub fn to_argv(&self, binary: &str) -> Result<Vec<String>, String> {
        let mut argv = vec![binary.to_string(), format!("--serial={}", self.serial)];
        argv.extend(self.display.to_flags()?);
        if self.vd_keep_content && self.display.mode != DisplayMode::Mirror {
            argv.push("--no-vd-destroy-content".into());
        }
        if let Some(package) = &self.app_package {
            // '+' 前缀 force-stop 后启动；幂等——已带前缀不再叠成 '++'。
            let package = package.trim_start_matches('+');
            argv.push(format!("--start-app=+{package}"));
        }
        if self.screen_off {
            argv.push("--turn-screen-off".into());
        }
        if self.stay_awake {
            argv.push("--stay-awake".into());
        }
        argv.push(format!("--keyboard={}", self.keyboard));
        argv.extend(self.video.to_flags());
        if self.print_fps {
            argv.push("--print-fps".into());
        }
        if !self.audio {
            argv.push("--no-audio".into());
        } else {
            argv.push(format!("--audio-codec={}", self.audio_codec));
            argv.push(format!("--audio-buffer={}", self.audio_buffer_ms));
        }
        if let Some(title) = &self.window_title {
            argv.push(format!("--window-title={title}"));
        }
        if self.borderless {
            argv.push("--window-borderless".into());
        }
        argv.extend(int_flag("window-x", self.window_x.map(i64::from)));
        argv.extend(int_flag("window-y", self.window_y.map(i64::from)));
        if self.display.mode != DisplayMode::Flex {
            argv.extend(int_flag("window-width", self.window_width.map(i64::from)));
            argv.extend(int_flag("window-height", self.window_height.map(i64::from)));
        }
        Ok(argv)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn argv(args: &EngineArgs) -> Vec<String> {
        args.to_argv("scrcpy").unwrap()
    }

    fn has(argv: &[String], flag: &str) -> bool {
        argv.iter().any(|a| a == flag)
    }

    fn has_prefix(argv: &[String], prefix: &str) -> bool {
        argv.iter().any(|a| a.starts_with(prefix))
    }

    #[test]
    fn adb_pin_never_becomes_an_argv_flag() {
        let mut args = EngineArgs::new("s");
        args.adb_binary = Some("C:\\tools\\adb.exe".into());
        assert!(!has_prefix(&argv(&args), "--adb"));
        assert!(!has_prefix(&argv(&EngineArgs::new("s")), "--adb"));
    }

    #[test]
    fn flex_default_matches_verified_preset() {
        let mut args = EngineArgs::new("4444bd6b");
        args.app_package = Some("cn.com.langeasy.LangEasyLexis".into());
        let argv = argv(&args);
        let joined = argv.join(" ");
        assert!(has(&argv, "--serial=4444bd6b"));
        assert!(has(&argv, "--new-display=1920x1080"));
        assert!(has(&argv, "--no-window-aspect-ratio-lock"));
        assert!(has(&argv, "--flex-display"));
        assert!(has(&argv, "--render-fit=stretched"));
        assert!(!joined.contains("--capture-orientation"));
        assert!(!joined.contains("--no-vd-system-decorations"));
        assert!(has(&argv, "--start-app=+cn.com.langeasy.LangEasyLexis"));
        assert!(has(&argv, "--turn-screen-off"));
        assert!(has(&argv, "--stay-awake"));
        assert!(has(&argv, "--keyboard=uhid"));
        assert!(has(&argv, "--video-codec=h265"));
        assert!(has(&argv, "--video-bit-rate=30M"));
        assert!(has(&argv, "--max-fps=90"));
        assert!(!joined.contains("clipboard"));
    }

    #[test]
    fn start_app_plus_prefix_is_idempotent() {
        let mut args = EngineArgs::new("s");
        args.app_package = Some("+cn.com.langeasy.LangEasyLexis".into());
        let argv = argv(&args);
        assert!(has(&argv, "--start-app=+cn.com.langeasy.LangEasyLexis"));
        assert!(!argv.iter().any(|a| a.contains("++")));
    }

    #[test]
    fn flex_without_dpi_uses_default_size() {
        let argv = argv(&EngineArgs::new("s"));
        assert!(has(&argv, "--new-display=1920x1080"));
        assert!(!argv.iter().any(|a| a.ends_with("/None")));
        assert!(has(&argv, "--flex-display"));
        assert!(!has(&argv, "--no-vd-system-decorations"));
    }

    #[test]
    fn flex_explicit_size_pins_display() {
        let mut args = EngineArgs::new("s");
        args.display = DisplaySpec {
            mode: DisplayMode::Flex,
            width: Some(1120),
            height: Some(1872),
            dpi: Some(313),
        };
        let argv = argv(&args);
        assert!(has(&argv, "--new-display=1120x1872/313"));
        assert!(has(&argv, "--flex-display"));
    }

    #[test]
    fn fixed_display_size_and_dpi() {
        let mut args = EngineArgs::new("s");
        args.display = DisplaySpec {
            mode: DisplayMode::Fixed,
            width: Some(2560),
            height: Some(1440),
            dpi: Some(268),
        };
        let argv = argv(&args);
        assert!(has(&argv, "--new-display=2560x1440/268"));
        assert!(!has(&argv, "--flex-display"));
    }

    #[test]
    fn fixed_display_requires_dimensions() {
        let spec = DisplaySpec {
            mode: DisplayMode::Fixed,
            ..Default::default()
        };
        assert_eq!(
            spec.to_flags().unwrap_err(),
            "fixed display mode requires width and height"
        );
    }

    #[test]
    fn mirror_mode_emits_no_display_flags() {
        let mut args = EngineArgs::new("s");
        args.display = DisplaySpec {
            mode: DisplayMode::Mirror,
            ..Default::default()
        };
        let argv = argv(&args);
        assert!(!has_prefix(&argv, "--new-display"));
        assert!(!has_prefix(&argv, "--flex-display"));
        assert!(!has(&argv, "--no-vd-system-decorations"));
    }

    #[test]
    fn vd_keep_content_flag() {
        assert!(!has(
            &argv(&EngineArgs::new("s")),
            "--no-vd-destroy-content"
        ));
        let mut keep = EngineArgs::new("s");
        keep.vd_keep_content = true;
        assert!(has(&argv(&keep), "--no-vd-destroy-content"));
        let mut mirror_keep = EngineArgs::new("s");
        mirror_keep.vd_keep_content = true;
        mirror_keep.display = DisplaySpec {
            mode: DisplayMode::Mirror,
            ..Default::default()
        };
        assert!(!has(&argv(&mirror_keep), "--no-vd-destroy-content"));
    }

    #[test]
    fn video_encoder_optional() {
        let mut pinned = EngineArgs::new("s");
        pinned.video.encoder = Some("c2.qti.hevc.encoder".into());
        assert!(has(&argv(&pinned), "--video-encoder=c2.qti.hevc.encoder"));
        assert!(!has_prefix(&argv(&EngineArgs::new("s")), "--video-encoder"));
    }

    #[test]
    fn audio_and_title_flags() {
        let mut args = EngineArgs::new("s");
        args.audio = false;
        args.window_title = Some("不背单词".into());
        let out = argv(&args);
        assert!(has(&out, "--no-audio"));
        assert!(has(&out, "--window-title=不背单词"));
        let out = argv(&EngineArgs::new("s"));
        assert!(!has(&out, "--no-audio"));
        assert!(has(&out, "--audio-codec=flac"));
        assert!(has(&out, "--audio-buffer=100"));
    }

    #[test]
    fn screen_off_switchable() {
        let mut args = EngineArgs::new("s");
        args.screen_off = false;
        let out = argv(&args);
        assert!(!has(&out, "--turn-screen-off"));
        assert!(has(&out, "--stay-awake"));
    }

    #[test]
    fn binary_position() {
        let argv = argv(&EngineArgs::new("s"));
        assert_eq!(argv[0], "scrcpy");
        let argv = EngineArgs::new("s").to_argv("/usr/bin/scrcpy.exe").unwrap();
        assert_eq!(argv[0], "/usr/bin/scrcpy.exe");
    }

    #[test]
    fn window_position_emitted_with_flex() {
        let mut args = EngineArgs::new("s");
        args.window_x = Some(100);
        args.window_y = Some(50);
        let argv = argv(&args);
        assert!(has(&argv, "--window-x=100"));
        assert!(has(&argv, "--window-y=50"));
        assert!(!has_prefix(&argv, "--window-width"));
        assert!(!has_prefix(&argv, "--window-height"));
    }

    #[test]
    fn window_size_suppressed_under_flex() {
        let mut args = EngineArgs::new("s");
        args.window_width = Some(800);
        args.window_height = Some(1200);
        assert!(!has_prefix(&argv(&args), "--window-"));
    }

    #[test]
    fn window_size_emitted_for_fixed() {
        let mut args = EngineArgs::new("s");
        args.display = DisplaySpec {
            mode: DisplayMode::Fixed,
            width: Some(1252),
            height: Some(2088),
            dpi: Some(313),
        };
        args.window_x = Some(10);
        args.window_y = Some(20);
        args.window_width = Some(1252);
        args.window_height = Some(2088);
        let argv = argv(&args);
        assert!(has(&argv, "--window-x=10"));
        assert!(has(&argv, "--window-y=20"));
        assert!(has(&argv, "--window-width=1252"));
        assert!(has(&argv, "--window-height=2088"));
    }

    #[test]
    fn borderless_flag() {
        let mut args = EngineArgs::new("s");
        args.borderless = true;
        assert!(has(&argv(&args), "--window-borderless"));
        assert!(!has(&argv(&EngineArgs::new("s")), "--window-borderless"));
    }

    #[test]
    fn print_fps_on_by_default_and_opt_out() {
        assert!(has(&argv(&EngineArgs::new("s")), "--print-fps"));
        let mut mirror = EngineArgs::new("s");
        mirror.display = DisplaySpec {
            mode: DisplayMode::Mirror,
            ..Default::default()
        };
        assert!(has(&argv(&mirror), "--print-fps"));
        let mut off = EngineArgs::new("s");
        off.print_fps = false;
        assert!(!has_prefix(&argv(&off), "--print-fps"));
    }
}

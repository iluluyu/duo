//! `mirror` 子命令编排层（对译 `duo/__main__.py::_run_mirror` + `_resolve_*`；
//! 合同镜像 tests/test_cli_quality.py）。纯裁决/装配逻辑在本文件上半部
//! （可测），进程驱动在下半部。`--chrome` 走 pyduo 真机验证过的路径：
//! scrcpy 无边框窗口 + Windows 侧 C# overlay（chrome.rs）提供胶囊/下巴。

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::apps::run_device_density;
use crate::audio_lock::AudioLock;
use crate::catalog::catalog_by_package;
use crate::chrome::borderless_for;
use crate::codec;
use crate::devices::{run_devices_query, DeviceStates, EXIT_DEVICE_LOST};
use crate::engine::{DisplayMode, DisplaySpec, EngineArgs, VideoSpec};
use crate::monitor::{
    apply_render_scale, primary_work_area, recommend_landscape, recommend_portrait, WorkArea,
    LANDSCAPE_TARGET_DP, PORTRAIT_TARGET_DP,
};
use crate::paths::logs_dir;
use crate::session::{run_session_abortable, SessionSpec};
use crate::settings::{load_settings, resolve_tool, Settings};

// ------------------------------------------------------------ resolvers

/// (forward audio, arbitrate via lock)。显式 --no-audio 恒赢；off 全静音
/// 不仲裁；all 并行不仲裁；latest 转发 + 仲裁。
pub fn resolve_audio(no_audio_flag: bool, audio_policy: &str) -> (bool, bool) {
    if no_audio_flag {
        return (false, false);
    }
    match audio_policy {
        "off" => (false, false),
        "all" => (true, false),
        _ => (true, true),
    }
}

/// --turn-screen-off：显式 --no-screen-off 强制亮屏，否则设置开关决定。
pub fn resolve_screen_off(no_screen_off_flag: bool, turn_screen_off: bool) -> bool {
    if no_screen_off_flag {
        false
    } else {
        turn_screen_off
    }
}

/// --chrome-top/--chrome-bottom：显式旗标赢过设置存值。
pub fn resolve_bar_mode(flag: Option<&str>, setting: &str) -> String {
    match flag {
        Some(flag) => flag.to_string(),
        None => setting.to_string(),
    }
}

/// --glass：显式旗标（"1"/"0"）赢过 glass_enabled 设置。
pub fn resolve_glass(flag: Option<&str>, setting: bool) -> bool {
    match flag {
        None => setting,
        Some(value) => value == "1",
    }
}

/// serial 择一：显式必须在线；无显式时恰一在线才自动（对译 _pick_serial
/// 的报错文案）。
pub fn pick_serial(explicit: Option<&str>, states: &DeviceStates) -> Result<String, String> {
    let online: Vec<&str> = states
        .iter()
        .filter(|(_, state)| state.as_str() == "device")
        .map(|(serial, _)| serial.as_str())
        .collect();
    if let Some(explicit) = explicit {
        if !online.contains(&explicit) {
            let found = if online.is_empty() {
                "none".to_string()
            } else {
                online.join(", ")
            };
            return Err(format!("device {explicit} is not online (found: {found})"));
        }
        return Ok(explicit.to_string());
    }
    match online.len() {
        0 => Err("no device online - connect one and enable USB debugging".into()),
        1 => Ok(online[0].to_string()),
        _ => Err(format!(
            "multiple devices online, pass --serial: {}",
            online.join(", ")
        )),
    }
}

/// 窗口标题：--title > 目录预设名 > 包名（M2 的设备标签 sweep 到位后
/// 再补第三档；元数据恒可选，失败不拦启动——对译 _resolve_app_title 语义）。
pub fn title_for(args: &MirrorArgs) -> String {
    if let Some(title) = &args.title {
        return title.clone();
    }
    match &args.app {
        Some(package) => catalog_by_package(package)
            .map(|preset| preset.label.to_string())
            .unwrap_or_else(|| package.clone()),
        None => String::new(),
    }
}

// ------------------------------------------------------ argv parsing

#[derive(Debug, Clone, PartialEq)]
pub struct MirrorArgs {
    pub app: Option<String>,
    pub serial: Option<String>,
    pub display: DisplayMode,
    pub width: Option<i64>,
    pub height: Option<i64>,
    pub dpi: Option<i64>,
    pub render_scale: Option<f64>,
    pub dp: Option<i64>,
    pub portrait: bool,
    pub fps: Option<i64>,
    pub bitrate: Option<i64>,
    pub no_screen_off: bool,
    pub no_audio: bool,
    pub no_vd_destroy_content: bool,
    pub video_codec: Option<String>,
    pub title: Option<String>,
    pub chrome: bool,
    pub chrome_top: Option<String>,
    pub chrome_bottom: Option<String>,
    pub glass: Option<String>,
    pub bar_theme: Option<String>,
    pub corner_radius: Option<i64>,
    pub session_log: Option<String>,
}

impl Default for MirrorArgs {
    fn default() -> Self {
        Self {
            app: None,
            serial: None,
            display: DisplayMode::Flex,
            width: None,
            height: None,
            dpi: None,
            render_scale: None,
            dp: None,
            portrait: false,
            fps: None,
            bitrate: None,
            no_screen_off: false,
            no_audio: false,
            no_vd_destroy_content: false,
            video_codec: None,
            title: None,
            chrome: false,
            chrome_top: None,
            chrome_bottom: None,
            glass: None,
            bar_theme: None,
            corner_radius: None,
            session_log: None,
        }
    }
}

fn invalid_choice(flag: &str, value: &str, choices: &[&str]) -> String {
    let quoted: Vec<String> = choices.iter().map(|c| format!("'{c}'")).collect();
    format!(
        "argument --{flag}: invalid choice: '{value}' (choose from {})",
        quoted.join(", ")
    )
}

fn parse_int(flag: &str, value: &str) -> Result<i64, String> {
    value
        .parse()
        .map_err(|_| format!("argument --{flag}: invalid int value: '{value}'"))
}

fn parse_float(flag: &str, value: &str) -> Result<f64, String> {
    value
        .parse()
        .map_err(|_| format!("argument --{flag}: invalid float value: '{value}'"))
}

fn one_of(flag: &str, value: &str, choices: &[&str]) -> Result<String, String> {
    if choices.contains(&value) {
        Ok(value.to_string())
    } else {
        Err(invalid_choice(flag, value, choices))
    }
}

/// 手写 argv 解析，语义对齐 argparse：`--flag value` 与 `--flag=value`
/// 同收；布尔旗标无值。非法值返回 Err（main 打到 stderr + rc 2）。
pub fn parse_args(argv: &[String]) -> Result<MirrorArgs, String> {
    let mut args = MirrorArgs::default();
    let mut i = 0usize;
    while i < argv.len() {
        let (name, inline): (String, Option<String>) = match argv[i].strip_prefix("--") {
            Some(rest) => match rest.split_once('=') {
                Some((flag, value)) => (flag.to_string(), Some(value.to_string())),
                None => (rest.to_string(), None),
            },
            None => return Err(format!("unrecognized argument: {}", argv[i])),
        };
        let name_str: &str = &name;
        let value = |i: &mut usize| -> Result<String, String> {
            match &inline {
                Some(v) => Ok(v.clone()),
                None => {
                    *i += 1;
                    argv.get(*i)
                        .cloned()
                        .ok_or_else(|| format!("argument --{name_str}: expected one argument"))
                }
            }
        };
        match name.as_str() {
            "app" => args.app = Some(value(&mut i)?),
            "serial" => args.serial = Some(value(&mut i)?),
            "display" => {
                let v = value(&mut i)?;
                args.display = match one_of("display", &v, &["flex", "fixed", "mirror"])?.as_str() {
                    "fixed" => DisplayMode::Fixed,
                    "mirror" => DisplayMode::Mirror,
                    _ => DisplayMode::Flex,
                };
            }
            "width" => args.width = Some(parse_int("width", &value(&mut i)?)?),
            "height" => args.height = Some(parse_int("height", &value(&mut i)?)?),
            "dpi" => args.dpi = Some(parse_int("dpi", &value(&mut i)?)?),
            "render-scale" => {
                args.render_scale = Some(parse_float("render-scale", &value(&mut i)?)?)
            }
            "dp" => args.dp = Some(parse_int("dp", &value(&mut i)?)?),
            "portrait" => args.portrait = true,
            "fps" => args.fps = Some(parse_int("fps", &value(&mut i)?)?),
            "bitrate" => args.bitrate = Some(parse_int("bitrate", &value(&mut i)?)?),
            "no-screen-off" => args.no_screen_off = true,
            "no-audio" => args.no_audio = true,
            "no-vd-destroy-content" => args.no_vd_destroy_content = true,
            "video-codec" => {
                args.video_codec = Some(one_of(
                    "video-codec",
                    &value(&mut i)?,
                    &["auto", "h264", "h265", "av1"],
                )?)
            }
            "title" => args.title = Some(value(&mut i)?),
            "chrome" => args.chrome = true,
            "chrome-top" => {
                args.chrome_top = Some(one_of(
                    "chrome-top",
                    &value(&mut i)?,
                    &["immersive", "native", "none"],
                )?)
            }
            "chrome-bottom" => {
                args.chrome_bottom = Some(one_of(
                    "chrome-bottom",
                    &value(&mut i)?,
                    &["immersive", "native", "none"],
                )?)
            }
            "glass" => args.glass = Some(one_of("glass", &value(&mut i)?, &["0", "1"])?),
            "bar-theme" => {
                args.bar_theme = Some(one_of(
                    "bar-theme",
                    &value(&mut i)?,
                    &["light", "dark", "system"],
                )?)
            }
            "corner-radius" => {
                args.corner_radius = Some(parse_int("corner-radius", &value(&mut i)?)?)
            }
            "session-log" => args.session_log = Some(value(&mut i)?),
            _ => return Err(format!("unrecognized argument: --{name}")),
        }
        i += 1;
    }
    Ok(args)
}

// ------------------------------------------------------ display planning

/// 显示规划结果：display 进 EngineArgs，window_* 进窗口几何，diag 是
/// 诊断行文本（对译 _run_mirror 的 display: 打印）。
#[derive(Debug, Clone, PartialEq, Default)]
pub struct DisplayPlan {
    pub display: DisplaySpec,
    pub window_x: Option<i32>,
    pub window_y: Option<i32>,
    pub window_width: Option<u32>,
    pub window_height: Option<u32>,
    pub diag: String,
}

/// 密度注入：args.dpi > 设置 dpi > 设备探测（仅 flex 且未钉时）> 160。
/// density_probe 由驱动层注入（None = 探测失败/跳过），保持纯函数可测。
pub fn plan_display(
    args: &MirrorArgs,
    settings: &Settings,
    area: WorkArea,
    density_probe: Option<u32>,
) -> Result<DisplayPlan, String> {
    let render_scale = args.render_scale.unwrap_or(settings.render_scale);
    if !(1.0..=3.0).contains(&render_scale) {
        return Err(format!("render scale {render_scale} out of range 1.0-3.0"));
    }
    if args.display == DisplayMode::Mirror {
        return Ok(DisplayPlan {
            display: DisplaySpec {
                mode: DisplayMode::Mirror,
                ..Default::default()
            },
            ..Default::default()
        });
    }
    let mut dpi = args.dpi.or(settings.dpi);
    if dpi.is_none() && args.display == DisplayMode::Flex {
        dpi = density_probe.or(Some(160)).map(|d| d as i64);
    }
    let mut display = DisplaySpec {
        mode: args.display,
        width: args.width.map(|w| w as u32),
        height: args.height.map(|h| h as u32),
        dpi: dpi.map(|d| d as u32),
    };
    let mut plan = DisplayPlan::default();
    let portrait = args.portrait
        || (args.width.is_some() && args.height.is_some() && args.height > args.width);
    if portrait {
        let rec = recommend_portrait(area, args.dp.unwrap_or(PORTRAIT_TARGET_DP));
        display = DisplaySpec {
            mode: args.display,
            width: args
                .width
                .map(|w| w as u32)
                .or(rec.display_width.map(|w| w as u32)),
            height: args
                .height
                .map(|h| h as u32)
                .or(rec.display_height.map(|h| h as u32)),
            dpi: dpi.or(Some(rec.dpi)).map(|d| d as u32),
        };
        if let Some(window) = rec.window {
            if args.display == DisplayMode::Flex {
                plan.window_x = Some(window.x as i32);
                plan.window_y = Some(window.y as i32);
            } else {
                plan.window_x = Some(window.x as i32);
                plan.window_y = Some(window.y as i32);
                plan.window_width = Some(rec.display_width.unwrap_or(window.width) as u32);
                plan.window_height = Some(rec.display_height.unwrap_or(window.height) as u32);
            }
        }
    } else {
        let rec = recommend_landscape(area, args.dp.unwrap_or(LANDSCAPE_TARGET_DP));
        if dpi.is_none() {
            display = DisplaySpec {
                mode: args.display,
                width: display.width,
                height: display.height,
                dpi: Some(rec.dpi as u32),
            };
        }
    }
    let mut area_text = format!("work area {}x{}", area.width, area.height);
    if render_scale > 1.0 && args.display == DisplayMode::Flex {
        display = apply_render_scale(&display, area, render_scale);
        plan = DisplayPlan {
            window_x: None,
            window_y: None,
            window_width: None,
            window_height: None,
            ..plan
        };
    }
    if render_scale > 1.0 && args.display == DisplayMode::Fixed {
        area_text += &format!(" render÷{}", trim_float(render_scale));
    }
    let new_display = display
        .to_flags()
        .ok()
        .and_then(|flags| {
            flags
                .iter()
                .find(|f| f.starts_with("--new-display="))
                .and_then(|f| f.strip_prefix("--new-display="))
                .map(str::to_string)
        })
        .unwrap_or_else(|| "设备默认".into());
    let mode_name = match display.mode {
        DisplayMode::Flex => "flex",
        DisplayMode::Fixed => "fixed",
        DisplayMode::Mirror => "mirror",
    };
    plan.display = display;
    plan.diag = format!(
        "display: {mode_name} dpi={} new-display={new_display} ({area_text})",
        plan.display
            .dpi
            .map(|d| d.to_string())
            .unwrap_or_else(|| "None".into())
    );
    Ok(plan)
}

// ------------------------------------------------------ session assembly

/// fps/bitrate：CLI 旗标 > 设置 > 60/30 默认。
pub fn effective_fps(args: &MirrorArgs, settings: &Settings) -> u32 {
    args.fps.or(settings.fps).unwrap_or(60).clamp(1, 240) as u32
}

pub fn effective_bitrate(args: &MirrorArgs, settings: &Settings) -> u32 {
    args.bitrate
        .or(settings.bitrate_mbps)
        .unwrap_or(30)
        .clamp(1, 200) as u32
}

/// codec 探测缓存语义对译 _resolve_video：新鲜缓存免探测，过期/换机
/// 重探一次；失败不拦会话（resolve_codec 自降级）。
pub fn effective_video(
    args: &MirrorArgs,
    settings: &Settings,
    scrcpy_path: &str,
    serial: &str,
    base: Option<&std::path::Path>,
) -> (VideoSpec, Option<String>) {
    let setting = args
        .video_codec
        .clone()
        .unwrap_or_else(|| settings.video_codec.clone());
    let cache_path = codec::encoders_cache_path(base);
    let mut encoders = codec::load_cached_encoders(&cache_path, serial, None);
    if encoders.is_none() {
        if let Some(found) = codec::probe_encoders(scrcpy_path, serial, 15.0) {
            let _ = codec::save_encoders_cache(&cache_path, serial, &found, None);
            encoders = Some(found);
        }
    }
    let choice = codec::resolve_codec(&setting, encoders.as_deref());
    (
        VideoSpec {
            codec: choice.codec,
            encoder: choice.encoder,
            bitrate_mbps: effective_bitrate(args, settings),
            max_fps: effective_fps(args, settings),
        },
        if choice.note.is_empty() {
            None
        } else {
            Some(choice.note)
        },
    )
}

/// 装配 EngineArgs（borderless 由 chrome + 上巴模式决定：native 顶要
/// scrcpy 自建带框窗，其余走无边框 + overlay）。
pub fn build_engine_args(
    args: &MirrorArgs,
    settings: &Settings,
    plan: &DisplayPlan,
    video: VideoSpec,
    audio: bool,
    title: &str,
    borderless: bool,
) -> EngineArgs {
    let mut engine = EngineArgs::new(args.serial.clone().unwrap_or_default());
    engine.display = plan.display.clone();
    engine.video = video;
    engine.app_package = args.app.clone();
    engine.screen_off = resolve_screen_off(args.no_screen_off, settings.turn_screen_off);
    engine.vd_keep_content = args.no_vd_destroy_content;
    engine.audio = audio;
    engine.window_title = if title.is_empty() {
        None
    } else {
        Some(title.to_string())
    };
    engine.window_x = plan.window_x;
    engine.window_y = plan.window_y;
    engine.window_width = plan.window_width;
    engine.window_height = plan.window_height;
    engine.borderless = borderless;
    engine
}

/// 会话日志：显式 --session-log 优先；CLI 路径一枚时间戳文件。
/// 时间戳为 UTC（面板路径恒显式传 --session-log，CLI 诊断文件不比较时区）。
pub fn session_log_path(args: &MirrorArgs, base: Option<&std::path::Path>) -> PathBuf {
    if let Some(explicit) = &args.session_log {
        return PathBuf::from(explicit);
    }
    let app = args.app.as_deref().unwrap_or("mirror");
    logs_dir(base).join(format!("{}-{app}.log", stamp_utc()))
}

/// Python ``:g`` 语义（1.0-3.0 域内）：整数尾零剔除。
fn trim_float(x: f64) -> String {
    if x.fract() == 0.0 {
        format!("{}", x as i64)
    } else {
        format!("{x}")
    }
}

/// DisplayMode 的 argv 名（overlay --display-mode 用；仅 Windows 会话接线）。
#[cfg(windows)]
fn display_mode_name(mode: DisplayMode) -> &'static str {
    match mode {
        DisplayMode::Flex => "flex",
        DisplayMode::Fixed => "fixed",
        DisplayMode::Mirror => "mirror",
    }
}

fn stamp_utc() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (y, m, d) = civil_from_days(days);
    format!(
        "{y:04}{m:02}{d:02}-{:02}{:02}{:02}",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

/// Howard Hinnant 的 civil_from_days（无时区依赖的 UTC 年月日）。
pub(crate) fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

// --------------------------------------------------------------- driver

/// PATH 探测（对译 shutil.which 的意图；Windows 补 .exe 后缀）。
pub fn find_on_path(name: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let direct = dir.join(name);
        if direct.is_file() {
            return Some(direct.display().to_string());
        }
        if cfg!(windows) {
            let exe = dir.join(format!("{name}.exe"));
            if exe.is_file() {
                return Some(exe.display().to_string());
            }
        }
    }
    None
}

fn resolve_binaries(settings: &Settings) -> Result<(String, String), String> {
    let adb = resolve_tool("adb", settings, find_on_path("adb").as_deref())
        .ok_or("adb not found (set its path in settings or PATH)")?;
    let scrcpy = resolve_tool("scrcpy", settings, find_on_path("scrcpy").as_deref())
        .ok_or("scrcpy not found (set its path in settings or PATH)")?;
    Ok((adb, scrcpy))
}

fn device_states(adb: &str) -> DeviceStates {
    run_devices_query(adb).unwrap_or_default()
}

/// `mirror` 驱动：设置 → 工具/serial 择一 → 显示规划 → 视频择优 →
/// （Windows chrome）C# overlay 贴窗 / 其余受监督会话；设备拔出返回 2。
pub fn run(argv: &[String]) -> i32 {
    let args = match parse_args(argv) {
        Ok(args) => args,
        Err(err) => {
            eprintln!("{err}");
            return 2;
        }
    };
    let (settings, problems) = load_settings(None);
    for problem in &problems {
        println!("settings: {problem}");
    }
    let (adb_path, scrcpy_path) = match resolve_binaries(&settings) {
        Ok(paths) => paths,
        Err(err) => {
            eprintln!("error: {err}");
            return 1;
        }
    };
    let states = device_states(&adb_path);
    let serial = match pick_serial(args.serial.as_deref(), &states) {
        Ok(serial) => serial,
        Err(err) => {
            eprintln!("error: {err}");
            return 1;
        }
    };
    let density_probe = run_device_density(&adb_path, &serial);
    let plan = match plan_display(&args, &settings, primary_work_area(), density_probe) {
        Ok(plan) => plan,
        Err(err) => {
            eprintln!("error: {err}");
            return 1;
        }
    };
    if !plan.diag.is_empty() {
        println!("{}", plan.diag);
    }
    let (video, note) = effective_video(&args, &settings, &scrcpy_path, &serial, None);
    if let Some(note) = note {
        println!("video codec: {note}");
    }
    let title = title_for(&args);
    if let Some(app) = &args.app {
        println!("app: {title} ({app})");
    }
    let (audio, arbitrate) = resolve_audio(args.no_audio, &settings.audio_policy);
    if settings.audio_policy == "off" && !args.no_audio {
        println!("audio policy: off - muted");
    }
    let mut audio_lock = AudioLock::new(None);
    let mut audio = audio;
    if audio && arbitrate && !audio_lock.acquire() {
        audio = false;
        println!("audio already owned by another duo window - muted");
    }

    // 上巴 native = 真系统标题栏：scrcpy 不得无边框（chrome.rs
    // borderless_for，2026-09-09 真机定稿）；沉浸/无 上巴仍无边框。
    let top_bar_mode = resolve_bar_mode(args.chrome_top.as_deref(), &settings.top_bar_mode);
    let mut engine = build_engine_args(
        &args,
        &settings,
        &plan,
        video,
        audio,
        &title,
        args.chrome && borderless_for(&top_bar_mode),
    );
    engine.adb_binary = Some(adb_path.clone());
    let log_path = session_log_path(&args, None);
    println!("session log: {}", log_path.display());
    let spec = SessionSpec {
        command: match engine.to_argv(&scrcpy_path) {
            Ok(argv) => argv,
            Err(err) => {
                eprintln!("error: {err}");
                audio_lock.release();
                return 1;
            }
        },
        log_path: log_path.clone(),
        max_restarts: 3,
        restart_delay_s: 2.0,
        env: [("ADB".to_string(), adb_path.clone())].into(),
        // flex 会话防旋转乒乓风暴（display 模式在 engine.argv 体现，
        // session 侧按 --flex-display 旗标匹配后再下发）
        orientation_lock: Some(adb_path.clone()),
    };

    // Window chrome：无边框窗口 + Windows 侧 overlay（会话前启动，与
    // pyduo 启动序一致；非 Windows 打印提示后继续纯会话）。
    #[allow(unused_mut)]
    let mut overlay: Option<crate::chrome::ChromeOverlay> = None;
    if args.chrome {
        if title.is_empty() {
            eprintln!("error: chrome needs a window title: pass --app or --title");
            audio_lock.release();
            return 1;
        }
        #[cfg(windows)]
        {
            use crate::chrome::{read_top_pin, top_pin_path, ChromeOverlay, OverlayArgs};
            use crate::settings::corner_radius_dip;
            let bottom_bar_mode =
                resolve_bar_mode(args.chrome_bottom.as_deref(), &settings.bottom_bar_mode);
            // 视频尺寸 seed 只给 fixed（比例锁）；flex 纯自由窗口，
            // 镜像不传（尺寸经 session log 流入）。
            let (vd_w, vd_h) = if plan.display.mode == DisplayMode::Fixed {
                (plan.display.width, plan.display.height)
            } else {
                (None, None)
            };
            // 按应用固定：本次启动读初值，overlay 右键切换回写同一文件；
            // 整机镜像无包名不落盘，固定随会话生灭。
            let pin_file = args.app.as_deref().map(|app| top_pin_path(app, None));
            if let Some(pin) = &pin_file {
                if let Some(parent) = pin.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
            }
            let overlay_args = OverlayArgs {
                title: title.clone(),
                serial: serial.clone(),
                adb_path: adb_path.clone(),
                home: args.app.is_none(),
                display_mode: display_mode_name(args.display).to_string(),
                video_width: vd_w,
                video_height: vd_h,
                session_log: Some(log_path.display().to_string()),
                corner_radius_dip: args
                    .corner_radius
                    .unwrap_or_else(|| corner_radius_dip(&settings)),
                top_bar_mode: top_bar_mode.clone(),
                bottom_bar_mode,
                pin_top: args
                    .app
                    .as_deref()
                    .map(|app| read_top_pin(app, None))
                    .unwrap_or(false),
                pin_file: pin_file.map(|p| p.display().to_string()),
                glass: resolve_glass(args.glass.as_deref(), settings.glass_enabled),
                bar_theme: args
                    .bar_theme
                    .clone()
                    .unwrap_or_else(|| settings.theme.clone()),
                ..OverlayArgs::default()
            };
            match ChromeOverlay::new(None, overlay_args) {
                Ok(mut started) => match started.start() {
                    Ok(overlay_log) => {
                        println!("chrome overlay log: {}", overlay_log.display());
                        overlay = Some(started);
                    }
                    Err(err) => {
                        eprintln!("error: chrome overlay failed to start: {err}");
                        audio_lock.release();
                        return 1;
                    }
                },
                Err(err) => {
                    eprintln!("error: {err}");
                    audio_lock.release();
                    return 1;
                }
            }
        }
        #[cfg(not(windows))]
        {
            println!("chrome requires the Windows host; running a plain session");
        }
    }

    let abort = Arc::new(AtomicBool::new(false));
    let watcher_gone = Arc::new(Mutex::new(false));
    {
        let abort = abort.clone();
        let watcher_gone = watcher_gone.clone();
        let adb_path = adb_path.clone();
        let serial = serial.clone();
        thread::spawn(move || loop {
            if abort.load(Ordering::Acquire) {
                return;
            }
            let states = device_states(&adb_path);
            if !states.is_empty() && states.get(&serial).map(String::as_str) != Some("device") {
                *watcher_gone.lock().expect("device watch lock") = true;
                abort.store(true, Ordering::Release);
                return;
            }
            thread::sleep(Duration::from_secs(2));
        });
    }
    println!("starting engine... (Ctrl+C to stop)");
    let code = run_session_abortable(&spec, &abort, &mut |_| {});
    if let Some(overlay) = overlay.as_mut() {
        overlay.stop();
    }
    audio_lock.release();
    if *watcher_gone.lock().expect("device watch lock") {
        println!("device disconnected - session stopped");
        return EXIT_DEVICE_LOST;
    }
    code
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::DisplayMode;
    use std::collections::BTreeMap;

    fn settings() -> Settings {
        Settings::default()
    }

    fn args() -> MirrorArgs {
        MirrorArgs::default()
    }

    #[test]
    fn audio_policy_three_states() {
        assert_eq!(resolve_audio(false, "off"), (false, false));
        assert_eq!(resolve_audio(false, "all"), (true, false));
        assert_eq!(resolve_audio(false, "latest"), (true, true));
        for policy in ["latest", "all", "off"] {
            assert_eq!(resolve_audio(true, policy), (false, false));
        }
    }

    #[test]
    fn screen_off_flag_beats_setting() {
        assert!(!resolve_screen_off(false, false));
        assert!(resolve_screen_off(false, true));
        assert!(!resolve_screen_off(true, true));
    }

    #[test]
    fn bar_mode_flag_beats_setting() {
        assert_eq!(resolve_bar_mode(None, "native"), "native");
        assert_eq!(resolve_bar_mode(None, "none"), "none");
        assert_eq!(resolve_bar_mode(Some("immersive"), "native"), "immersive");
        assert_eq!(resolve_bar_mode(Some("none"), "immersive"), "none");
    }

    #[test]
    fn glass_flag_beats_setting() {
        assert!(resolve_glass(None, true));
        assert!(!resolve_glass(None, false));
        assert!(resolve_glass(Some("1"), false));
        assert!(!resolve_glass(Some("0"), true));
    }

    fn states(map: &[(&str, &str)]) -> DeviceStates {
        map.iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect::<BTreeMap<String, String>>()
    }

    #[test]
    fn pick_serial_explicit_must_be_online() {
        let s = states(&[("A", "device"), ("B", "offline")]);
        assert_eq!(pick_serial(Some("A"), &s).unwrap(), "A");
        assert_eq!(
            pick_serial(Some("B"), &s).unwrap_err(),
            "device B is not online (found: A)"
        );
        assert_eq!(
            pick_serial(Some("C"), &states(&[])).unwrap_err(),
            "device C is not online (found: none)"
        );
    }

    #[test]
    fn pick_serial_auto_requires_exactly_one() {
        assert_eq!(pick_serial(None, &states(&[("A", "device")])).unwrap(), "A");
        assert_eq!(
            pick_serial(None, &states(&[])).unwrap_err(),
            "no device online - connect one and enable USB debugging"
        );
        assert_eq!(
            pick_serial(None, &states(&[("A", "device"), ("B", "device")])).unwrap_err(),
            "multiple devices online, pass --serial: A, B"
        );
    }

    #[test]
    fn title_flag_then_catalog_then_package() {
        let mut a = args();
        a.title = Some("自定义".into());
        assert_eq!(title_for(&a), "自定义");
        a.title = None;
        a.app = Some("com.tencent.mm".into());
        assert_eq!(title_for(&a), "微信");
        a.app = Some("unknown.pkg".into());
        assert_eq!(title_for(&a), "unknown.pkg");
        a.app = None;
        assert_eq!(title_for(&a), "");
    }

    fn argv(text: &str) -> Vec<String> {
        // parse_args 收的是子命令后的旗标段；剥掉可选的 "mirror" 头
        //（不能全局过滤：--display 的合法取值也叫 mirror）。
        let mut tokens: Vec<String> = text.split_whitespace().map(str::to_string).collect();
        if tokens.first().map(String::as_str) == Some("mirror") {
            tokens.remove(0);
        }
        tokens
    }

    #[test]
    fn parser_defaults_follow_settings() {
        let a = parse_args(&argv("mirror")).unwrap();
        assert_eq!(a.display, DisplayMode::Flex);
        assert!(a.chrome_top.is_none() && a.chrome_bottom.is_none());
        assert!(!a.no_vd_destroy_content && !a.portrait && !a.chrome);
    }

    #[test]
    fn parser_accepts_inline_and_spaced_values() {
        let a = parse_args(&argv("mirror --app=com.tencent.mm --serial ABC")).unwrap();
        assert_eq!(a.app.as_deref(), Some("com.tencent.mm"));
        let b = parse_args(&argv("mirror --app com.tencent.mm --serial ABC")).unwrap();
        assert_eq!(b.app, a.app);
        assert_eq!(b.serial.as_deref(), Some("ABC"));
    }

    #[test]
    fn parser_enums_and_rejections() {
        let a = parse_args(&argv(
            "mirror --chrome-top native --chrome-bottom immersive",
        ))
        .unwrap();
        assert_eq!(a.chrome_top.as_deref(), Some("native"));
        assert_eq!(a.chrome_bottom.as_deref(), Some("immersive"));
        assert!(parse_args(&argv("mirror --chrome-top floating")).is_err());
        assert!(parse_args(&argv("mirror --display bogus")).is_err());
        assert!(parse_args(&argv("mirror --video-codec hevc")).is_err());
        assert!(parse_args(&argv("mirror --glass 2")).is_err());
        assert!(parse_args(&argv("mirror --fps abc")).is_err());
        assert!(parse_args(&argv("mirror --render-scale fast")).is_err());
        assert!(parse_args(&argv("mirror --bogus")).is_err());
    }

    #[test]
    fn parser_mirror_display_mode_and_booleans() {
        let a = parse_args(&argv(
            "mirror --display mirror --portrait --no-audio --no-screen-off \
             --no-vd-destroy-content --chrome",
        ))
        .unwrap();
        assert_eq!(a.display, DisplayMode::Mirror);
        assert!(a.portrait && a.no_audio && a.no_screen_off);
        assert!(a.no_vd_destroy_content && a.chrome);
        // embed 实验已删：旗标不再被接受。
        assert!(parse_args(&argv("mirror --embed")).is_err());
        assert!(parse_args(&argv("mirror --embed-style native")).is_err());
    }

    const AREA: WorkArea = WorkArea {
        width: 3840,
        height: 2054,
    };

    #[test]
    fn plan_display_flex_defaults_density_probe() {
        // 设置默认 dpi=160；仅当用户选「跟随设备」（dpi=None）才探测注入。
        let mut s = settings();
        s.dpi = None;
        let plan = plan_display(&args(), &s, AREA, Some(356)).unwrap();
        assert_eq!(plan.display.mode, DisplayMode::Flex);
        assert_eq!(plan.display.dpi, Some(356));
        assert!(plan.diag.contains("display: flex dpi=356"));
        assert!(plan.diag.contains("work area 3840x2054"));
    }

    #[test]
    fn plan_display_settings_dpi_beats_probe() {
        // 设置页存了密度（默认 160）时不探测：设置 > 设备。
        let plan = plan_display(&args(), &settings(), AREA, Some(356)).unwrap();
        assert_eq!(plan.display.dpi, Some(160));
    }

    #[test]
    fn plan_display_probe_failure_falls_back_160() {
        let mut s = settings();
        s.dpi = None;
        let plan = plan_display(&args(), &s, AREA, None).unwrap();
        assert_eq!(plan.display.dpi, Some(160));
    }

    #[test]
    fn plan_display_explicit_dpi_beats_probe() {
        let mut a = args();
        a.dpi = Some(240);
        let plan = plan_display(&a, &settings(), AREA, Some(356)).unwrap();
        assert_eq!(plan.display.dpi, Some(240));
    }

    #[test]
    fn plan_display_portrait_pins_window_origin_only_for_flex() {
        let mut a = args();
        a.portrait = true;
        let plan = plan_display(&a, &settings(), AREA, Some(160)).unwrap();
        assert_eq!(plan.display.width, Some(1080));
        assert_eq!(plan.display.height, Some(1920));
        assert_eq!(plan.window_x, Some(3840 - 1080));
        assert_eq!(plan.window_width, None, "flex 只摆位不锁尺寸");

        let mut fixed = a.clone();
        fixed.display = DisplayMode::Fixed;
        let plan = plan_display(&fixed, &settings(), AREA, Some(160)).unwrap();
        assert_eq!(plan.window_width, Some(1080));
        assert_eq!(plan.window_height, Some(1920));
    }

    #[test]
    fn plan_display_render_scale_converts_flex_to_fixed() {
        let a = args();
        let mut s = settings();
        s.render_scale = 2.0;
        let plan = plan_display(&a, &s, AREA, Some(160)).unwrap();
        assert_eq!(plan.display.mode, DisplayMode::Fixed);
        assert_eq!(plan.display.width, Some(1920));
        assert_eq!(plan.display.height, Some(1028));
        assert!(plan.diag.contains("work area 3840x2054"));
    }

    #[test]
    fn plan_display_rejects_out_of_range_scale() {
        let mut a = args();
        a.render_scale = Some(4.0);
        assert!(plan_display(&a, &settings(), AREA, None).is_err());
    }

    #[test]
    fn plan_display_mirror_has_no_display_flags() {
        let mut a = args();
        a.display = DisplayMode::Mirror;
        let plan = plan_display(&a, &settings(), AREA, None).unwrap();
        assert_eq!(plan.display.mode, DisplayMode::Mirror);
        assert_eq!(plan.display.to_flags().unwrap(), Vec::<String>::new());
    }

    #[test]
    fn fps_bitrate_flag_setting_default() {
        assert_eq!(effective_fps(&args(), &settings()), 60);
        let mut s = settings();
        s.fps = Some(120);
        assert_eq!(effective_fps(&args(), &s), 120);
        let mut a = args();
        a.fps = Some(90);
        assert_eq!(effective_fps(&a, &s), 90, "CLI 旗标赢设置");
        assert_eq!(effective_bitrate(&args(), &settings()), 30);
        let mut a = args();
        a.bitrate = Some(8);
        assert_eq!(effective_bitrate(&a, &settings()), 8);
    }

    #[test]
    fn engine_args_carry_audio_and_geometry() {
        let mut a = args();
        a.app = Some("com.tencent.mm".into());
        a.serial = Some("ABC".into());
        a.no_vd_destroy_content = true;
        let plan = plan_display(&a, &settings(), AREA, Some(356)).unwrap();
        let engine = build_engine_args(
            &a,
            &settings(),
            &plan,
            default_video(),
            false,
            "微信",
            false,
        );
        let argv = engine.to_argv("scrcpy").unwrap();
        let joined = argv.join(" ");
        assert!(joined.contains("--start-app=+com.tencent.mm"));
        assert!(joined.contains("--no-vd-destroy-content"));
        assert!(joined.contains("--no-audio"));
        assert!(
            !joined.contains("--window-borderless"),
            "非 chrome 保持系统窗"
        );
        assert!(joined.contains("--window-title=微信"));
    }

    fn default_video() -> VideoSpec {
        VideoSpec {
            codec: "h265".into(),
            encoder: None,
            bitrate_mbps: 30,
            max_fps: 60,
        }
    }

    #[test]
    fn engine_args_borderless_surface() {
        let a = args();
        let plan = DisplayPlan::default();
        let engine = build_engine_args(&a, &settings(), &plan, default_video(), true, "T", true);
        let argv = engine.to_argv("scrcpy").unwrap();
        assert!(argv.iter().any(|f| f == "--window-borderless"));
        assert!(argv.iter().any(|f| f == "--audio-codec=flac"));
    }

    #[test]
    fn session_log_explicit_beats_generated() {
        let mut a = args();
        a.session_log = Some("/tmp/x.log".into());
        assert_eq!(session_log_path(&a, None), PathBuf::from("/tmp/x.log"));
        let a = args();
        let path = session_log_path(&a, None);
        let name = path.file_name().unwrap().to_string_lossy().into_owned();
        assert!(name.ends_with("-mirror.log"), "got {name}");
        assert_eq!(name.len(), "20260916-101500-mirror.log".len());
    }

    #[test]
    fn civil_from_days_known_dates() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_723), (2024, 1, 1));
        assert_eq!(civil_from_days(20_653), (2026, 7, 19));
    }

    #[test]
    fn chrome_borderless_follows_top_mode() {
        // 对译 test_cli_borderless_follows_top_mode：--chrome 时 borderless
        // 跟随上巴模式（native = 带框窗），无 chrome 恒带框。
        let mut a = args();
        a.chrome = true;
        let s = settings();
        let plan = DisplayPlan::default();
        let engine = build_engine_args(
            &a,
            &s,
            &plan,
            default_video(),
            true,
            "T",
            a.chrome && borderless_for(&resolve_bar_mode(a.chrome_top.as_deref(), &s.top_bar_mode)),
        );
        assert!(engine
            .to_argv("scrcpy")
            .unwrap()
            .iter()
            .any(|f| f == "--window-borderless"));
        a.chrome_top = Some("native".into());
        let engine = build_engine_args(
            &a,
            &s,
            &plan,
            default_video(),
            true,
            "T",
            a.chrome && borderless_for(&resolve_bar_mode(a.chrome_top.as_deref(), &s.top_bar_mode)),
        );
        assert!(
            !engine
                .to_argv("scrcpy")
                .unwrap()
                .iter()
                .any(|f| f == "--window-borderless"),
            "native 顶 = 真系统标题栏"
        );
    }
}

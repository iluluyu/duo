//! 会话与音频生命周期契约见 docs/mirroring-quality.md §3、docs/window-experience.md §12。

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::Child;
use std::time::{Duration, Instant};

use duo_core::aspects::preset_by_id;
use duo_core::catalog::APP_CATALOG;
use duo_core::paths::logs_dir;
use duo_core::settings::{load_settings, Settings};

use crate::prefs::{
    load_bar_prefs, load_density_prefs, load_portrait_prefs, load_scale_prefs, save_portrait_prefs,
    DisplayChoice,
};
use crate::winproc;

/// 整机镜像的「包名」占位（每台设备一枚镜像会话）。
pub const MIRROR_KEY: &str = "__device_mirror__";

pub fn needs_native_portrait_canvas(package: &str) -> bool {
    package == "com.coloros.calculator"
}

/// 音频 relay 日志的伪包名（一设备一枚 relay 日志）。
pub const AUDIO_RELAY_TAG: &str = "audio-relay";

/// 音频请求优先级，平级按启动顺序裁决。
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord)]
pub enum AudioPriority {
    Off,
    #[default]
    Normal,
    High,
}

impl AudioPriority {
    pub fn as_str(&self) -> &'static str {
        match self {
            AudioPriority::Off => "off",
            AudioPriority::Normal => "normal",
            AudioPriority::High => "high",
        }
    }

    /// 未知值回落 Normal（prefs 兼容）。
    pub fn parse_pref(value: &str) -> Self {
        match value {
            "off" => AudioPriority::Off,
            "high" => AudioPriority::High,
            _ => AudioPriority::Normal,
        }
    }
}

/// 会话复合键：serial::package（serial 含 `:`/`.`/`-`，不含 `::`）。
pub fn session_key(serial: &str, package: &str) -> String {
    format!("{serial}::{package}")
}

/// 运行卡/状态行的会话显示名（按包名）。
pub fn session_label(package: &str) -> String {
    if package == MIRROR_KEY {
        return "设备镜像".into();
    }
    if let Some(preset) = APP_CATALOG.iter().find(|p| p.package == package) {
        return preset.label.to_string();
    }
    package_to_label(package)
}

/// 未收录包的人话回退（最后一段大写开头）。
pub fn package_to_label(package: &str) -> String {
    let tail = package.rsplit('.').next().unwrap_or(package);
    let mut chars = tail.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

/// 每设备、每应用独立日志，serial 的冒号转为 Windows 安全字符。
pub fn panel_log_path(serial: &str, package: &str) -> PathBuf {
    logs_dir(None).join(format!("panel-{}-{package}.log", serial.replace(':', "-")))
}

fn pin_fixed_display(argv: &mut Vec<String>, width: i64, height: i64) {
    argv.extend([
        "--display".into(),
        "fixed".into(),
        "--width".into(),
        width.to_string(),
        "--height".into(),
        height.to_string(),
    ]);
    if height > width {
        argv.push("--portrait".into());
    }
}

fn pin_chrome_bars(argv: &mut Vec<String>, package: Option<&str>) {
    let (settings, _) = load_settings(None);
    let bars = load_bar_prefs();
    let override_top = package.and_then(|p| bars.get(p)).and_then(|c| c.top);
    let override_bottom = package.and_then(|p| bars.get(p)).and_then(|c| c.bottom);
    argv.extend([
        "--chrome-top".into(),
        override_top
            .unwrap_or(settings.top_bar_mode.as_str())
            .to_string(),
        "--chrome-bottom".into(),
        override_bottom
            .unwrap_or(settings.bottom_bar_mode.as_str())
            .to_string(),
    ]);
}

fn pin_glass(argv: &mut Vec<String>) {
    let (settings, _) = load_settings(None);
    argv.extend([
        "--glass".into(),
        if settings.glass_enabled { "1" } else { "0" }.into(),
        "--bar-theme".into(),
        settings.theme.clone(),
    ]);
}

fn pin_density(argv: &mut Vec<String>, package: Option<&str>) {
    let Some(package) = package else { return };
    if let Some(dpi) = load_density_prefs().get(package) {
        argv.extend(["--dpi".into(), dpi.to_string()]);
    }
}

fn pin_render_scale(argv: &mut Vec<String>, package: Option<&str>, settings: &Settings) {
    let Some(package) = package else { return };
    let scale = load_scale_prefs()
        .get(package)
        .copied()
        .unwrap_or(settings.render_scale);
    if scale > 1.0 {
        argv.extend(["--render-scale".into(), format_scale(scale)]);
    }
}

/// Python `format(scale, "g")` 语义（1.0-3.0 域内整数尾零剔除）。
fn format_scale(scale: f64) -> String {
    if scale.fract() == 0.0 {
        format!("{}", scale as i64)
    } else {
        format!("{scale}")
    }
}

/// 记忆的 fixed 显示 → 具体几何；None = flex（body id 由调用方先解析）。
pub fn display_size(choice: Option<&DisplayChoice>) -> Option<(i64, i64)> {
    let choice = choice?;
    if let DisplayChoice::Fixed { aspect } = choice {
        if let Some(preset) = preset_by_id(aspect) {
            return Some((preset.width, preset.height));
        }
    }
    None
}

/// 一次面板启动的参数包（build_launch_argv 的形参面）。
#[derive(Debug, Clone, Default)]
pub struct LaunchParams<'a> {
    pub package: &'a str,
    pub serial: &'a str,
    pub portrait: bool,
    pub size: Option<(i64, i64)>,
    pub display: Option<&'a DisplayChoice>,
    pub keep_vd: bool,
    pub audio_priority: AudioPriority,
    pub native_canvas: bool,
}

/// 视频不带音频；共享 relay 转发整机混音。
pub fn launch_argv(params: &LaunchParams<'_>) -> Vec<String> {
    let (settings, _) = load_settings(None);
    let mut argv = vec![
        "mirror".into(),
        "--app".into(),
        params.package.into(),
        "--serial".into(),
        params.serial.into(),
        "--chrome".into(),
        "--session-log".into(),
        panel_log_path(params.serial, params.package)
            .display()
            .to_string(),
    ];
    let mut fixed = false;
    if let Some((w, h)) = params.size {
        pin_fixed_display(&mut argv, w, h);
        fixed = true;
    } else if let Some((w, h)) = display_size(params.display) {
        pin_fixed_display(&mut argv, w, h);
        fixed = true;
    } else if params.portrait {
        argv.push("--portrait".into());
    }
    pin_density(&mut argv, Some(params.package));
    if params.native_canvas {
        argv.push("--native-canvas".into());
    }
    if !fixed {
        pin_render_scale(&mut argv, Some(params.package), &settings);
    }
    pin_chrome_bars(&mut argv, Some(params.package));
    pin_glass(&mut argv);
    if params.keep_vd {
        argv.push("--no-vd-destroy-content".into());
    }
    argv.push("--no-audio".into());
    argv
}

/// 整机镜像 argv（无包名 → 栏模式走设置默认，恒不注入 per-app 覆盖）。
pub fn device_mirror_argv(serial: &str) -> Vec<String> {
    let mut argv = vec![
        "mirror".into(),
        "--display".into(),
        "mirror".into(),
        "--serial".into(),
        serial.into(),
        "--chrome".into(),
        "--title".into(),
        "平板镜像".into(),
    ];
    pin_chrome_bars(&mut argv, None);
    pin_glass(&mut argv);
    argv.push("--no-audio".into());
    argv
}

/// 独立音频 relay 参数，不创建窗口、虚拟屏或应用。
pub fn relay_argv(serial: &str) -> Vec<String> {
    vec![
        "mirror".into(),
        "--audio-only".into(),
        "--serial".into(),
        serial.into(),
        "--session-log".into(),
        panel_log_path(serial, AUDIO_RELAY_TAG)
            .display()
            .to_string(),
    ]
}

// --------------------------------------------------------------- registry

/// 运行会话按 serial::package 隔离。
#[derive(Debug)]
pub struct SessionEntry {
    pub key: String,
    pub package: String,
    pub serial: String,
    pub label: String,
    pub child: Child,
    /// 固定几何。
    pub size: Option<(i64, i64)>,
    /// 启动序（平级音频请求最新优先）。
    pub seq: u64,
    /// 音频请求优先级（右键菜单可改）。
    pub priority: AudioPriority,
}

/// 一路独立音频 relay（duo-core mirror --audio-only，无窗口无视频）。
#[derive(Debug)]
pub struct AudioRelayEntry {
    pub child: Child,
    pub serial: String,
}

/// 子进程装配点（测试注入 fake）。
pub type SpawnFn = Box<dyn Fn(&str, &[String]) -> Result<Child, String>>;

/// 面板拥有的会话注册表 + 音频 relay 仲裁。
pub struct Sessions {
    pub job: winproc::ChildJob,
    entries: BTreeMap<String, SessionEntry>,
    relays: BTreeMap<String, AudioRelayEntry>,
    portrait_prefs: BTreeMap<String, bool>,
    /// 测试注入：覆盖设置里的 audio_policy（不读盘）。
    policy_override: Option<String>,
    /// relay spawn 用的 duo-core 路径（start 时记录）。
    binary: Option<String>,
    next_seq: u64,
    /// spawn 失败待重试的 relay owner key。
    pending: Vec<String>,
    last_relay_retry: Option<Instant>,
    spawner: SpawnFn,
}

impl Sessions {
    pub fn new() -> Self {
        Self::build(None)
    }

    fn build(policy_override: Option<&str>) -> Self {
        Self {
            job: winproc::ChildJob::new(),
            entries: BTreeMap::new(),
            relays: BTreeMap::new(),
            portrait_prefs: if policy_override.is_some() {
                BTreeMap::new()
            } else {
                load_portrait_prefs()
            },
            policy_override: policy_override.map(str::to_string),
            binary: None,
            next_seq: 0,
            pending: Vec::new(),
            last_relay_retry: None,
            spawner: Box::new(|binary, argv| {
                let mut command = winproc::silent_command(binary, argv);
                if let Some((x, y)) = duo_core::monitor::env_pinned_point()
                    .or_else(duo_core::monitor::cursor_position)
                {
                    command.env("DUO_HOST_X", x.to_string());
                    command.env("DUO_HOST_Y", y.to_string());
                }
                command.spawn().map_err(|e| e.to_string())
            }),
        }
    }

    pub fn running(&self) -> Vec<(String, String)> {
        self.entries
            .values()
            .map(|e| (e.key.clone(), e.label.clone()))
            .collect()
    }

    pub fn is_running(&mut self, serial: &str, package: &str) -> bool {
        let key = session_key(serial, package);
        self.entries
            .get_mut(&key)
            .map(|e| matches!(e.child.try_wait(), Ok(None)))
            .unwrap_or(false)
    }

    /// 回收退出进程并恢复缺失的音频 relay。
    pub fn reap(&mut self) {
        let mut gone: Vec<String> = Vec::new();
        self.entries.retain(|key, entry| {
            let live = matches!(entry.child.try_wait(), Ok(None));
            if !live {
                gone.push(key.clone());
            }
            live
        });
        self.relays
            .retain(|_, relay| matches!(relay.child.try_wait(), Ok(None)));
        if !gone.is_empty() {
            let _ = self.reconcile_relays();
        }
        self.retry_pending_relays();
    }

    #[allow(clippy::too_many_arguments)] // 一次性会话装配，拆组反而难读
    fn spawn(
        &mut self,
        binary: &str,
        argv: Vec<String>,
        serial: &str,
        package: &str,
        label: &str,
        size: Option<(i64, i64)>,
        priority: AudioPriority,
    ) -> Result<(), String> {
        let child = (self.spawner)(binary, &argv)?;
        self.job.add(&child);
        let key = session_key(serial, package);
        let seq = self.next_seq;
        self.next_seq += 1;
        self.entries.insert(
            key,
            SessionEntry {
                key: session_key(serial, package),
                package: package.to_string(),
                serial: serial.to_string(),
                label: label.to_string(),
                child,
                size,
                seq,
                priority,
            },
        );
        Ok(())
    }

    fn effective_policy(&self) -> String {
        self.policy_override
            .clone()
            .unwrap_or_else(|| load_settings(None).0.audio_policy)
    }

    /// 按策略、优先级和启动顺序选择请求者，同设备只保留一路音频。
    fn desired_relays(&mut self) -> Vec<(String, String)> {
        let policy = self.effective_policy();
        if policy == "off" {
            return Vec::new();
        }
        let requester_keys: Vec<String> = self
            .entries
            .iter_mut()
            .filter_map(|(key, e)| {
                (matches!(e.child.try_wait(), Ok(None)) && e.priority != AudioPriority::Off)
                    .then(|| key.clone())
            })
            .collect();
        let mut requesters: Vec<&SessionEntry> =
            requester_keys.iter().map(|k| &self.entries[k]).collect();
        requesters.sort_by(|a, b| b.priority.cmp(&a.priority).then(b.seq.cmp(&a.seq)));
        match policy.as_str() {
            "all" => {
                let mut seen: Vec<String> = Vec::new();
                let mut out = Vec::new();
                for entry in requesters {
                    if !seen.contains(&entry.serial) {
                        seen.push(entry.serial.clone());
                        out.push((entry.key.clone(), entry.serial.clone()));
                    }
                }
                out
            }
            _ => requesters
                .first()
                .map(|entry| vec![(entry.key.clone(), entry.serial.clone())])
                .unwrap_or_default(),
        }
    }

    /// 先释放旧音频锁再启动新 relay，永不修改视频进程。
    fn reconcile_relays(&mut self) -> Vec<String> {
        let mut notes = Vec::new();
        if self.effective_policy() == "off" {
            self.pending.clear();
            let keys: Vec<String> = self.relays.keys().cloned().collect();
            for key in keys {
                if let Some(relay) = self.relays.remove(&key) {
                    Self::kill_relay(relay);
                }
            }
            return notes;
        }
        let desired = self.desired_relays();
        let mut stash: Vec<AudioRelayEntry> = Vec::new();
        let mut dropped: Vec<(String, String)> = Vec::new();
        let keys: Vec<String> = self.relays.keys().cloned().collect();
        for key in keys {
            if desired.iter().any(|(want, _)| want == &key) {
                continue;
            }
            let Some(mut relay) = self.relays.remove(&key) else {
                continue;
            };
            if matches!(relay.child.try_wait(), Ok(None)) {
                dropped.push((key, relay.serial.clone()));
                stash.push(relay);
            } else {
                Self::kill_relay(relay);
            }
        }
        for (key, serial) in &desired {
            if self.relays.contains_key(key) {
                continue;
            }
            if let Some(pos) = stash.iter().position(|r| &r.serial == serial) {
                let relay = stash.remove(pos);
                self.relays.insert(key.clone(), relay);
            }
        }
        for relay in stash {
            Self::kill_relay(relay);
        }
        for (key, serial) in &dropped {
            if !desired.iter().any(|(_, want)| want == serial) {
                if let Some(label) = self.entries.get(key).map(|e| e.label.clone()) {
                    notes.push(format!("{label} 已静音"));
                }
            }
        }
        for (key, serial) in &desired {
            if self.relays.contains_key(key) {
                continue;
            }
            if let Err(note) = self.spawn_relay_owned(serial, key) {
                notes.push(note);
                self.pending.push(key.clone());
            }
        }
        notes
    }

    fn spawn_relay_owned(&mut self, serial: &str, key: &str) -> Result<(), String> {
        let Some(binary) = self.binary.clone() else {
            return Err("音频中继启动失败：duo-core 未解析".into());
        };
        let _ = std::fs::remove_file(panel_log_path(serial, AUDIO_RELAY_TAG));
        let argv = relay_argv(serial);
        let child = (self.spawner)(&binary, &argv)?;
        self.job.add(&child);
        self.relays.insert(
            key.to_string(),
            AudioRelayEntry {
                child,
                serial: serial.to_string(),
            },
        );
        Ok(())
    }

    /// 终止 relay 并只清自己持有的音频锁（防陈锁卡死后续会话）。
    fn kill_relay(relay: AudioRelayEntry) {
        let pid = relay.child.id();
        winproc::terminate_tree_detach(relay.child);
        let lock_path = duo_core::audio_lock::audio_lock_path(None);
        if duo_core::audio_lock::read_owner(&lock_path) == i64::from(pid) {
            let _ = std::fs::remove_file(&lock_path);
        }
    }

    /// relay 失落（spawn 失败 / relay 崩溃）时节流重仲裁，不静默丢音。
    fn retry_pending_relays(&mut self) {
        if self.pending.is_empty() && !self.relay_missing() {
            return;
        }
        if let Some(at) = self.last_relay_retry {
            if at.elapsed() < Duration::from_secs(2) {
                return;
            }
        }
        self.last_relay_retry = Some(Instant::now());
        self.pending.clear();
        let _ = self.reconcile_relays();
    }

    /// 期望 owner 缺 relay（spawn 失败/崩溃后由节流重试补齐）。
    fn relay_missing(&mut self) -> bool {
        if self.effective_policy() == "off" {
            return false;
        }
        let desired = self.desired_relays();
        desired
            .iter()
            .any(|(key, _)| !self.relays.contains_key(key))
    }

    /// 先启动视频，成功后仲裁音频；失败不影响其他窗口。
    pub fn start(
        &mut self,
        binary: &str,
        params: &LaunchParams<'_>,
        exclusive: bool,
    ) -> Result<Vec<String>, String> {
        self.binary = Some(binary.to_string());
        self.reap();
        let package = params.package;
        let keep_vd = params.keep_vd && package != MIRROR_KEY;
        let size = params.size.or_else(|| display_size(params.display));
        let argv = launch_argv(&LaunchParams {
            keep_vd,
            size,
            ..params.clone()
        });
        // 旧 exclusive 旗标兼容：独占 = High 优先级请求。
        let priority = if exclusive {
            AudioPriority::High
        } else {
            params.audio_priority
        };
        let _ = std::fs::remove_file(panel_log_path(params.serial, package));
        let label = session_label(package);
        self.spawn(binary, argv, params.serial, package, &label, size, priority)?;
        Ok(self.reconcile_relays())
    }

    /// 启动整机镜像。
    pub fn start_mirror(&mut self, binary: &str, serial: &str) -> Result<Vec<String>, String> {
        self.binary = Some(binary.to_string());
        self.reap();
        let argv = device_mirror_argv(serial);
        let _ = std::fs::remove_file(panel_log_path(serial, MIRROR_KEY));
        self.spawn(
            binary,
            argv,
            serial,
            MIRROR_KEY,
            "设备镜像",
            None,
            AudioPriority::Normal,
        )?;
        Ok(self.reconcile_relays())
    }

    /// 右键改优先级：只改活会话请求并重仲裁 relay，绝不碰视频进程。
    pub fn set_audio_priority(&mut self, serial: &str, package: &str, priority: AudioPriority) {
        let key = session_key(serial, package);
        if let Some(entry) = self.entries.get_mut(&key) {
            entry.priority = priority;
        }
        let _ = self.reconcile_relays();
    }

    /// 关一枚会话（整树）；relay 按 remaining 请求者交棒，音频不丢。
    pub fn stop(&mut self, key: &str) -> bool {
        if let Some(entry) = self.entries.remove(key) {
            winproc::terminate_tree_detach(entry.child);
            let _ = self.reconcile_relays();
            true
        } else {
            false
        }
    }

    /// 面板退出：全树拖走（含 relay），不留孤儿不清 pending。
    pub fn shutdown(&mut self) {
        let keys: Vec<String> = self.entries.keys().cloned().collect();
        for key in keys {
            if let Some(mut entry) = self.entries.remove(&key) {
                winproc::terminate_tree(&mut entry.child);
            }
        }
        let relays: Vec<String> = self.relays.keys().cloned().collect();
        for key in relays {
            if let Some(mut relay) = self.relays.remove(&key) {
                let pid = relay.child.id();
                winproc::terminate_tree(&mut relay.child);
                let lock_path = duo_core::audio_lock::audio_lock_path(None);
                if duo_core::audio_lock::read_owner(&lock_path) == i64::from(pid) {
                    let _ = std::fs::remove_file(&lock_path);
                }
            }
        }
        self.pending.clear();
        self.job.close();
    }

    pub fn set_portrait(&mut self, package: &str, portrait: bool) {
        self.portrait_prefs.insert(package.to_string(), portrait);
        save_portrait_prefs(&self.portrait_prefs);
    }

    pub fn portrait_of(&self, package: &str) -> bool {
        self.portrait_prefs.get(package).copied().unwrap_or(false)
    }
}

impl Default for Sessions {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_key_and_log_path_scope_per_serial() {
        assert_eq!(session_key("a1b2c3d4", "com.a"), "a1b2c3d4::com.a");
        let usb = panel_log_path("a1b2c3d4", "com.tencent.mm");
        let wifi = panel_log_path("192.168.1.100:5555", "com.tencent.mm");
        assert_ne!(usb, wifi, "同一应用在不同设备各一枚日志");
        let name = wifi.file_name().unwrap().to_string_lossy().into_owned();
        assert_eq!(name, "panel-192.168.1.100-5555-com.tencent.mm.log");
    }

    #[test]
    fn session_label_catalog_mirror_fallback() {
        assert_eq!(session_label(MIRROR_KEY), "设备镜像");
        assert_eq!(session_label("com.tencent.mm"), "微信");
        assert_eq!(session_label("org.unknown.thing"), "Thing");
    }

    #[test]
    fn package_to_label_uppercases_tail() {
        assert_eq!(package_to_label("com.example.weather"), "Weather");
        assert_eq!(package_to_label("a.b"), "B");
    }

    #[test]
    fn audio_priority_strings_roundtrip() {
        for p in [
            AudioPriority::Off,
            AudioPriority::Normal,
            AudioPriority::High,
        ] {
            assert_eq!(AudioPriority::parse_pref(p.as_str()), p);
        }
        assert_eq!(AudioPriority::parse_pref("junk"), AudioPriority::Normal);
        assert_eq!(AudioPriority::default(), AudioPriority::Normal);
        assert!(AudioPriority::High > AudioPriority::Normal);
    }

    #[test]
    fn native_portrait_canvas_is_explicit_and_keeps_manual_density() {
        assert!(needs_native_portrait_canvas("com.coloros.calculator"));
        assert!(!needs_native_portrait_canvas("com.coolapk.market"));
        let params = LaunchParams {
            package: "com.coloros.calculator",
            serial: "ABC",
            size: Some((2400, 3392)),
            native_canvas: true,
            ..Default::default()
        };
        let argv = launch_argv(&params);
        assert!(argv.contains(&"--native-canvas".to_string()));
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--display" && w[1] == "fixed"));
        assert!(argv.contains(&"--portrait".to_string()));
        assert_eq!(argv.last().unwrap(), "--no-audio");
        let ordinary = launch_argv(&LaunchParams {
            native_canvas: false,
            ..params
        });
        assert!(!ordinary.contains(&"--native-canvas".to_string()));
    }

    #[test]
    fn relay_argv_shape() {
        let argv = relay_argv("ABC");
        assert_eq!(argv[0], "mirror");
        assert!(argv.windows(2).any(|w| w[0] == "--audio-only"));
        assert!(argv.windows(2).any(|w| w[0] == "--serial" && w[1] == "ABC"));
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--session-log" && w[1].contains("panel-ABC-audio-relay.log")));
        assert!(!argv.contains(&"--chrome".to_string()));
        assert!(!argv.contains(&"--app".to_string()));
    }

    #[test]
    fn launch_argv_shape_flex_with_pins() {
        let argv = launch_argv(&LaunchParams {
            package: "com.tencent.mm",
            serial: "ABC",
            ..Default::default()
        });
        assert_eq!(argv[0], "mirror");
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--app" && w[1] == "com.tencent.mm"));
        assert!(argv.windows(2).any(|w| w[0] == "--serial" && w[1] == "ABC"));
        assert!(argv.contains(&"--chrome".to_string()));
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--session-log" && w[1].contains("panel-ABC-com.tencent.mm.log")));
        assert!(!argv.contains(&"--portrait".to_string()));
        assert!(argv.windows(2).any(|w| w[0] == "--chrome-top"));
        assert!(argv.windows(2).any(|w| w[0] == "--glass"));
        assert!(argv.windows(2).any(|w| w[0] == "--bar-theme"));
        assert_eq!(argv.last().unwrap(), "--no-audio", "面板视频恒静音");
    }

    #[test]
    fn launch_argv_fixed_geometry_pins_display_and_orientation() {
        let argv = launch_argv(&LaunchParams {
            package: "a.b",
            serial: "S",
            size: Some((1600, 900)),
            ..Default::default()
        });
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--display" && w[1] == "fixed"));
        assert!(argv.windows(2).any(|w| w[0] == "--width" && w[1] == "1600"));
        assert!(argv.windows(2).any(|w| w[0] == "--height" && w[1] == "900"));
        assert!(!argv.contains(&"--portrait".to_string()), "横屏几何不注入");
        let argv = launch_argv(&LaunchParams {
            package: "a.b",
            serial: "S",
            size: Some((900, 1600)),
            ..Default::default()
        });
        assert!(argv.contains(&"--portrait".to_string()), "竖几何跟随几何");
    }

    #[test]
    fn launch_argv_always_muted_and_keep_vd_tail_flags() {
        let argv = launch_argv(&LaunchParams {
            package: "a.b",
            serial: "S",
            keep_vd: true,
            ..Default::default()
        });
        assert_eq!(
            argv.last().unwrap(),
            "--no-audio",
            "视频恒静音（音频走 relay）"
        );
        assert!(argv.contains(&"--no-vd-destroy-content".to_string()));
    }

    #[test]
    fn device_mirror_argv_no_per_app_pins() {
        let argv = device_mirror_argv("S");
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--display" && w[1] == "mirror"));
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--title" && w[1] == "平板镜像"));
        assert!(!argv.windows(2).any(|w| w[0] == "--app"));
        assert_eq!(argv.last().unwrap(), "--no-audio", "镜像视频同样恒静音");
    }

    #[test]
    fn display_size_resolves_frozen_ids_only() {
        assert_eq!(
            display_size(Some(&DisplayChoice::Fixed {
                aspect: "16:9".into()
            })),
            Some((2560, 1440))
        );
        assert_eq!(display_size(Some(&DisplayChoice::Flex)), None);
        assert_eq!(
            display_size(Some(&DisplayChoice::Fixed {
                aspect: "body-l".into()
            })),
            None,
            "机身 id 由调用方解析成具体几何"
        );
    }

    #[test]
    fn format_scale_trims_integral_tail() {
        assert_eq!(format_scale(2.0), "2");
        assert_eq!(format_scale(1.5), "1.5");
    }
}

/// 用隔离的假子进程验证 relay 生命周期，不访问用户偏好。
#[cfg(all(test, target_os = "linux"))]
mod relay_tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::rc::Rc;

    struct Harness {
        sessions: Sessions,
        fail: Rc<Cell<bool>>,
        spawned: Rc<RefCell<Vec<Vec<String>>>>,
    }

    fn harness(policy: &str) -> Harness {
        let fail = Rc::new(Cell::new(false));
        let spawned: Rc<RefCell<Vec<Vec<String>>>> = Rc::new(RefCell::new(Vec::new()));
        let spawner = {
            let fail = fail.clone();
            let spawned = spawned.clone();
            move |_binary: &str, argv: &[String]| {
                spawned.borrow_mut().push(argv.to_vec());
                if fail.get() {
                    return Err("spawn boom".into());
                }
                winproc::silent_command("sleep", &["30".to_string()])
                    .spawn()
                    .map_err(|e| e.to_string())
            }
        };
        Harness {
            sessions: Sessions {
                job: winproc::ChildJob::new(),
                entries: BTreeMap::new(),
                relays: BTreeMap::new(),
                portrait_prefs: BTreeMap::new(),
                policy_override: Some(policy.into()),
                binary: Some("duo-core-fake".into()),
                next_seq: 0,
                pending: Vec::new(),
                last_relay_retry: None,
                spawner: Box::new(spawner),
            },
            fail,
            spawned,
        }
    }

    fn video(h: &mut Harness, serial: &str, package: &str, priority: AudioPriority) -> u32 {
        let label = session_label(package);
        h.sessions
            .spawn("", Vec::new(), serial, package, &label, None, priority)
            .expect("video spawn");
        h.sessions.entries[&session_key(serial, package)].child.id()
    }

    fn relay_pid(h: &Harness) -> Option<u32> {
        h.sessions.relays.values().next().map(|r| r.child.id())
    }

    fn alive(h: &mut Harness, serial: &str, package: &str) -> bool {
        h.sessions.is_running(serial, package)
    }

    #[test]
    fn latest_second_launch_keeps_old_video_pid() {
        let mut h = harness("latest");
        let pid_a = video(&mut h, "S1", "com.a", AudioPriority::Normal);
        assert!(h.sessions.reconcile_relays().is_empty());
        assert_eq!(h.sessions.relays.len(), 1);
        let relay_before = relay_pid(&h);
        let _pid_b = video(&mut h, "S1", "com.b", AudioPriority::Normal);
        let notes = h.sessions.reconcile_relays();
        assert!(notes.is_empty(), "同设备交棒不是夺音: {notes:?}");
        assert_eq!(relay_pid(&h), relay_before, "同 serial 复用 relay 进程");
        assert_eq!(
            h.sessions.entries[&session_key("S1", "com.a")].child.id(),
            pid_a,
            "旧视频会话 PID 不变"
        );
        assert!(alive(&mut h, "S1", "com.a"), "旧视频会话仍活");
        assert!(h.sessions.relays.contains_key(&session_key("S1", "com.b")));
        h.sessions.shutdown();
    }

    #[test]
    fn latest_cross_serial_switches_single_relay_with_note() {
        let mut h = harness("latest");
        let pid_a = video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        let relay_s1 = relay_pid(&h);
        let _pid_b = video(&mut h, "S2", "com.b", AudioPriority::Normal);
        let notes = h.sessions.reconcile_relays();
        assert_eq!(notes, vec!["A 已静音".to_string()]);
        assert_eq!(h.sessions.relays.len(), 1, "latest 恒单路");
        assert!(h.sessions.relays.contains_key(&session_key("S2", "com.b")));
        assert_ne!(relay_pid(&h), relay_s1, "换设备必换 relay 进程");
        assert_eq!(
            h.sessions.entries[&session_key("S1", "com.a")].child.id(),
            pid_a
        );
        assert!(alive(&mut h, "S1", "com.a"));
        h.sessions.shutdown();
    }

    #[test]
    fn off_policy_spawns_no_relay_and_never_touches_video() {
        let mut h = harness("off");
        let pid_a = video(&mut h, "S1", "com.a", AudioPriority::High);
        let _pid_b = video(&mut h, "S1", "com.b", AudioPriority::Normal);
        assert!(h.sessions.reconcile_relays().is_empty());
        assert!(h.sessions.relays.is_empty(), "off 全禁");
        assert_eq!(
            h.sessions.entries[&session_key("S1", "com.a")].child.id(),
            pid_a
        );
        assert!(alive(&mut h, "S1", "com.a") && alive(&mut h, "S1", "com.b"));
        h.sessions.shutdown();
    }

    #[test]
    fn all_policy_one_relay_per_device() {
        let mut h = harness("all");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        video(&mut h, "S1", "com.b", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        assert_eq!(
            h.sessions.relays.len(),
            1,
            "同设备仅一路（防 REMOTE_SUBMIX 重复采集爆音）"
        );
        video(&mut h, "S2", "com.c", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        assert_eq!(h.sessions.relays.len(), 2, "多设备多路");
        assert!(h.sessions.relays.contains_key(&session_key("S2", "com.c")));
        h.sessions.shutdown();
    }

    #[test]
    fn high_priority_beats_normal_and_off_never_requests() {
        let mut h = harness("latest");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        let relay_before = relay_pid(&h);
        video(&mut h, "S1", "com.b", AudioPriority::High);
        let _ = h.sessions.reconcile_relays();
        assert!(h.sessions.relays.contains_key(&session_key("S1", "com.b")));
        assert_eq!(relay_pid(&h), relay_before, "同设备夺权复用进程");
        h.sessions
            .set_audio_priority("S1", "com.b", AudioPriority::Off);
        assert!(
            h.sessions.relays.contains_key(&session_key("S1", "com.a")),
            "High 降 Off 后 Normal 接棒"
        );
        assert!(alive(&mut h, "S1", "com.b"), "改优先级不碰视频");
        h.sessions
            .set_audio_priority("S1", "com.a", AudioPriority::Off);
        assert!(h.sessions.relays.is_empty(), "全员 Off = 无请求");
        h.sessions.shutdown();
    }

    #[test]
    fn set_audio_priority_promotes_without_restarting_relay() {
        let mut h = harness("latest");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        video(&mut h, "S1", "com.b", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        let relay_before = relay_pid(&h);
        h.sessions
            .set_audio_priority("S1", "com.b", AudioPriority::High);
        assert!(h.sessions.relays.contains_key(&session_key("S1", "com.b")));
        assert_eq!(relay_pid(&h), relay_before, "仅切 owner 不换进程");
        h.sessions.shutdown();
    }

    #[test]
    fn closing_owner_same_device_keeps_relay_alive() {
        let mut h = harness("latest");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        video(&mut h, "S1", "com.b", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        let relay_before = relay_pid(&h);
        assert!(h.sessions.stop(&session_key("S1", "com.b")));
        assert!(h.sessions.relays.contains_key(&session_key("S1", "com.a")));
        assert_eq!(
            relay_pid(&h),
            relay_before,
            "owner 关窗、同设备窗仍在 → relay 进程续用，音频零空窗"
        );
        assert!(alive(&mut h, "S1", "com.a"));
        h.sessions.shutdown();
    }

    #[test]
    fn closing_owner_falls_back_to_remaining_requester() {
        let mut h = harness("latest");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        video(&mut h, "S2", "com.b", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        assert!(h.sessions.relays.contains_key(&session_key("S2", "com.b")));
        assert!(h.sessions.stop(&session_key("S2", "com.b")));
        assert!(
            h.sessions.relays.contains_key(&session_key("S1", "com.a")),
            "owner 关窗后剩余窗接棒，音频不丢"
        );
        assert_eq!(h.sessions.relays.len(), 1);
        h.sessions.shutdown();
    }

    #[test]
    fn closing_last_window_stops_relay() {
        let mut h = harness("latest");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        let pid = relay_pid(&h).unwrap();
        assert!(h.sessions.stop(&session_key("S1", "com.a")));
        assert!(h.sessions.relays.is_empty());
        assert!(
            !std::path::Path::new(&format!("/proc/{pid}")).exists(),
            "relay 进程已收走"
        );
        h.sessions.shutdown();
    }

    #[test]
    fn relay_spawn_failure_keeps_video_and_retries() {
        let mut h = harness("latest");
        let pid_a = video(&mut h, "S1", "com.a", AudioPriority::Normal);
        h.fail.set(true);
        let notes = h.sessions.reconcile_relays();
        assert_eq!(notes.len(), 1, "失败要提示: {notes:?}");
        assert!(h.sessions.relays.is_empty());
        assert_eq!(
            h.sessions.entries[&session_key("S1", "com.a")].child.id(),
            pid_a
        );
        assert!(alive(&mut h, "S1", "com.a"), "relay 失败不破坏视频");
        assert!(!h.sessions.pending.is_empty(), "失败进重试队列");
        h.fail.set(false);
        h.sessions.last_relay_retry = None;
        h.sessions.reap();
        assert_eq!(h.sessions.relays.len(), 1, "重试后 relay 补上");
        h.sessions.shutdown();
    }

    #[test]
    fn shutdown_terminates_everything() {
        let mut h = harness("all");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        video(&mut h, "S2", "com.b", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        assert_eq!(h.sessions.relays.len(), 2);
        h.sessions.shutdown();
        assert!(h.sessions.entries.is_empty() && h.sessions.relays.is_empty());
        assert!(h.sessions.pending.is_empty());
    }

    #[test]
    fn relay_argv_recorded_for_fake_spawner() {
        let mut h = harness("latest");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        let argv = h
            .sessions
            .relays
            .values()
            .next()
            .map(|_| h.spawned.borrow().last().unwrap().clone())
            .unwrap();
        assert!(argv.windows(2).any(|w| w[0] == "--audio-only"));
        assert!(argv.windows(2).any(|w| w[0] == "--serial" && w[1] == "S1"));
        h.sessions.shutdown();
    }

    #[test]
    fn cross_serial_switch_kills_old_relay_before_spawning_new() {
        // 新 relay 要拿 AudioLock：旧 relay 若还活着会把 acquire 顶失败，
        // relay 静音退出 → 音频丢失。断言 S2 spawn 那一刻 S1 relay 已死。
        let old_pid: Rc<Cell<u32>> = Rc::new(Cell::new(0));
        let old_alive_at_spawn = Rc::new(Cell::new(true));
        let spawner = {
            let old_pid = old_pid.clone();
            let old_alive = old_alive_at_spawn.clone();
            move |_binary: &str, argv: &[String]| {
                if argv.windows(2).any(|w| w[0] == "--serial" && w[1] == "S2") {
                    let pid = old_pid.get();
                    old_alive.set(std::path::Path::new(&format!("/proc/{pid}")).exists());
                }
                winproc::silent_command("sleep", &["30".to_string()])
                    .spawn()
                    .map_err(|e| e.to_string())
            }
        };
        let mut s = Sessions {
            job: winproc::ChildJob::new(),
            entries: BTreeMap::new(),
            relays: BTreeMap::new(),
            portrait_prefs: BTreeMap::new(),
            policy_override: Some("latest".into()),
            binary: Some("duo-core-fake".into()),
            next_seq: 0,
            pending: Vec::new(),
            last_relay_retry: None,
            spawner: Box::new(spawner),
        };
        let _ = s.spawn(
            "",
            Vec::new(),
            "S1",
            "com.a",
            "A",
            None,
            AudioPriority::Normal,
        );
        let _ = s.reconcile_relays();
        old_pid.set(s.relays.values().next().map(|r| r.child.id()).unwrap());
        let _ = s.spawn(
            "",
            Vec::new(),
            "S2",
            "com.b",
            "B",
            None,
            AudioPriority::Normal,
        );
        let _ = s.reconcile_relays();
        assert!(
            !old_alive_at_spawn.get(),
            "新 relay spawn 时旧 relay 必须已终止（AudioLock 才拿得到）"
        );
        assert_eq!(s.relays.len(), 1);
        assert!(s.relays.contains_key(&session_key("S2", "com.b")));
        s.shutdown();
    }

    #[test]
    fn relay_crash_respawns_throttled() {
        let mut h = harness("latest");
        video(&mut h, "S1", "com.a", AudioPriority::Normal);
        let _ = h.sessions.reconcile_relays();
        let crashed = h
            .sessions
            .relays
            .get_mut(&session_key("S1", "com.a"))
            .map(|r| {
                let pid = r.child.id();
                let _ = r.child.kill();
                let _ = r.child.wait();
                pid
            })
            .unwrap();
        h.sessions.reap();
        assert_eq!(h.sessions.relays.len(), 1, "relay 崩溃后节流重仲裁补齐");
        let revived = relay_pid(&h).unwrap();
        assert_ne!(revived, crashed, "新 relay 进程接替");
        h.sessions.shutdown();
    }
}

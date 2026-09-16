//! 会话管理（controller.py 会话面对译）：argv 装配（按应用 pins 全家桶：
//! portrait/display/bars/density/scale/keep-vd/glass）、`duo-core mirror`
//! spawn、树杀、音频三态仲裁（latest = 新会话夺音频，其余静音重启）、
//! 运行卡模型。设置/prefs 每次启动现读（保存即达下一个窗口）。

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::Child;

use duo_core::aspects::preset_by_id;
use duo_core::catalog::APP_CATALOG;
use duo_core::paths::logs_dir;
use duo_core::settings::{load_settings, Settings};

use crate::prefs::{
    load_bar_prefs, load_behavior_prefs, load_density_prefs, load_portrait_prefs, load_scale_prefs,
    save_portrait_prefs, DisplayChoice,
};
use crate::winproc;

pub const MIRROR_KEY: &str = "__device_mirror__";

/// 运行卡/状态行的会话显示名。
pub fn session_label(key: &str) -> String {
    if key == MIRROR_KEY {
        return "设备镜像".into();
    }
    if let Some(preset) = APP_CATALOG.iter().find(|p| p.package == key) {
        return preset.label.to_string();
    }
    package_to_label(key)
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

/// 会话日志（一包一文件，display-id 从这里回读）。
pub fn panel_log_path(package: &str) -> PathBuf {
    logs_dir(None).join(format!("panel-{package}.log"))
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
    pub muted: bool,
    pub size: Option<(i64, i64)>,
    pub display: Option<&'a DisplayChoice>,
    pub keep_vd: bool,
}

/// 一次面板启动的 duo-core mirror argv（build_launch_argv 对译；进程入口
/// 换成 duo-core 二进制路径，由调用方前插）。
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
        panel_log_path(params.package).display().to_string(),
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
    if !fixed {
        pin_render_scale(&mut argv, Some(params.package), &settings);
    }
    pin_chrome_bars(&mut argv, Some(params.package));
    pin_glass(&mut argv);
    if params.keep_vd {
        argv.push("--no-vd-destroy-content".into());
    }
    if params.muted {
        argv.push("--no-audio".into());
    }
    argv
}

/// 整机镜像 argv（无包名 → 栏模式走设置默认，恒不注入 per-app 覆盖）。
pub fn device_mirror_argv(serial: &str, muted: bool) -> Vec<String> {
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
    if muted {
        argv.push("--no-audio".into());
    }
    argv
}

// --------------------------------------------------------------- registry

/// 一条运行会话。
#[derive(Debug)]
pub struct SessionEntry {
    pub key: String,
    pub label: String,
    pub child: Child,
    /// 固定几何（音频静音重启要重新钉住）。
    pub size: Option<(i64, i64)>,
}

/// 面板拥有的会话注册表 + 音频仲裁。
pub struct Sessions {
    pub job: winproc::ChildJob,
    entries: BTreeMap<String, SessionEntry>,
    audio_keys: Vec<String>,
    portrait_prefs: BTreeMap<String, bool>,
}

impl Sessions {
    pub fn new() -> Self {
        Self {
            job: winproc::ChildJob::new(),
            entries: BTreeMap::new(),
            audio_keys: Vec::new(),
            portrait_prefs: load_portrait_prefs(),
        }
    }

    pub fn running(&self) -> Vec<(String, String)> {
        self.entries
            .values()
            .map(|e| (e.key.clone(), e.label.clone()))
            .collect()
    }

    pub fn is_running(&mut self, key: &str) -> bool {
        self.entries
            .get_mut(key)
            .map(|e| matches!(e.child.try_wait(), Ok(None)))
            .unwrap_or(false)
    }

    /// 回收已退出的会话（每次启动/查询前调）。
    pub fn reap(&mut self) {
        self.entries
            .retain(|_, entry| entry.child.try_wait().map(|w| w.is_none()).unwrap_or(false));
        let live: Vec<String> = self.entries.keys().cloned().collect();
        self.audio_keys.retain(|k| live.contains(k));
    }

    fn spawn(
        &mut self,
        binary: &str,
        argv: Vec<String>,
        key: &str,
        label: &str,
        size: Option<(i64, i64)>,
    ) -> Result<(), String> {
        let child = winproc::silent_command(binary, &argv)
            .spawn()
            .map_err(|e| e.to_string())?;
        self.job.add(&child);
        let has_audio = !argv.iter().any(|a| a == "--no-audio");
        if has_audio {
            self.audio_keys.push(key.to_string());
        } else {
            self.audio_keys.retain(|k| k != key);
        }
        self.entries.insert(
            key.to_string(),
            SessionEntry {
                key: key.to_string(),
                label: label.to_string(),
                child,
                size,
            },
        );
        Ok(())
    }

    /// 音频三态裁决（_apply_audio_policy 对译）：argv 原地可变——off 注入
    /// --no-audio；latest/独占先静音重启其余音频会话。返回被静音重启的
    /// 会话名（状态行用）。
    pub fn apply_audio_policy(
        &mut self,
        binary: &str,
        new_key: &str,
        serial: &str,
        argv: &mut Vec<String>,
        exclusive: bool,
    ) -> Vec<String> {
        if exclusive {
            return self.restart_others_muted(binary, new_key, serial);
        }
        let policy = load_settings(None).0.audio_policy;
        match policy.as_str() {
            "off" => {
                argv.push("--no-audio".into());
                Vec::new()
            }
            "latest" => self.restart_others_muted(binary, new_key, serial),
            _ => Vec::new(),
        }
    }

    /// latest 交棒：其余带音频的活会话 terminate → 静音重启（同几何同
    /// keep-vd 重新钉住）。镜像走镜像 argv。
    fn restart_others_muted(&mut self, binary: &str, new_key: &str, serial: &str) -> Vec<String> {
        let mut restarted = Vec::new();
        let mut keys = Vec::new();
        for (key, entry) in self.entries.iter_mut() {
            if *key != new_key
                && self.audio_keys.contains(key)
                && matches!(entry.child.try_wait(), Ok(None))
            {
                keys.push(key.clone());
            }
        }
        for key in keys {
            let Some(mut entry) = self.entries.remove(&key) else {
                continue;
            };
            self.audio_keys.retain(|k| k != &key);
            winproc::terminate_tree(&mut entry.child);
            let keep_vd =
                key != MIRROR_KEY && load_behavior_prefs().get(&key).copied().unwrap_or(false);
            let argv = if key == MIRROR_KEY {
                device_mirror_argv(serial, true)
            } else {
                launch_argv(&LaunchParams {
                    package: &key,
                    serial,
                    portrait: self.portrait_prefs.get(&key).copied().unwrap_or(false),
                    muted: true,
                    size: entry.size,
                    display: None,
                    keep_vd,
                })
            };
            let _ = std::fs::remove_file(panel_log_path(&key));
            let label = session_label(&key);
            if self.spawn(binary, argv, &key, &label, entry.size).is_ok() {
                restarted.push(label);
            }
        }
        restarted
    }

    /// 启动一枚应用会话（含去重：已运行 → 返回 AlreadyRunning 由调用方
    /// 走 startAppOnDisplay）。
    pub fn start(
        &mut self,
        binary: &str,
        params: &LaunchParams<'_>,
        exclusive: bool,
    ) -> Result<Vec<String>, String> {
        self.reap();
        let package = params.package;
        let keep_vd = params.keep_vd && package != MIRROR_KEY;
        let size = params.size.or_else(|| display_size(params.display));
        let mut argv = launch_argv(&LaunchParams {
            keep_vd,
            size,
            ..params.clone()
        });
        let restarted =
            self.apply_audio_policy(binary, package, params.serial, &mut argv, exclusive);
        let _ = std::fs::remove_file(panel_log_path(package));
        let label = session_label(package);
        self.spawn(binary, argv, package, &label, size)?;
        Ok(restarted)
    }

    /// 启动整机镜像。
    pub fn start_mirror(&mut self, binary: &str, serial: &str) -> Result<Vec<String>, String> {
        self.reap();
        let mut argv = device_mirror_argv(serial, false);
        let restarted = self.apply_audio_policy(binary, MIRROR_KEY, serial, &mut argv, false);
        self.spawn(binary, argv, MIRROR_KEY, "设备镜像", None)?;
        Ok(restarted)
    }

    /// 关一枚会话（整树）。
    pub fn stop(&mut self, key: &str) -> bool {
        if let Some(mut entry) = self.entries.remove(key) {
            winproc::terminate_tree(&mut entry.child);
            self.audio_keys.retain(|k| k != key);
            true
        } else {
            false
        }
    }

    /// 面板退出：全树拖走。
    pub fn shutdown(&mut self) {
        let keys: Vec<String> = self.entries.keys().cloned().collect();
        for key in keys {
            self.stop(&key);
        }
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
            .any(|w| w[0] == "--session-log" && w[1].contains("panel-com.tencent.mm.log")));
        assert!(!argv.contains(&"--portrait".to_string()));
        assert!(argv.windows(2).any(|w| w[0] == "--chrome-top"));
        assert!(argv.windows(2).any(|w| w[0] == "--glass"));
        assert!(argv.windows(2).any(|w| w[0] == "--bar-theme"));
        assert!(!argv.contains(&"--no-audio".to_string()));
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
    fn launch_argv_muted_and_keep_vd_tail_flags() {
        let argv = launch_argv(&LaunchParams {
            package: "a.b",
            serial: "S",
            muted: true,
            keep_vd: true,
            ..Default::default()
        });
        assert_eq!(argv.last().unwrap(), "--no-audio", "静音恒居末位");
        assert!(argv.contains(&"--no-vd-destroy-content".to_string()));
    }

    #[test]
    fn device_mirror_argv_no_per_app_pins() {
        let argv = device_mirror_argv("S", false);
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--display" && w[1] == "mirror"));
        assert!(argv
            .windows(2)
            .any(|w| w[0] == "--title" && w[1] == "平板镜像"));
        assert!(!argv.windows(2).any(|w| w[0] == "--app"));
        let argv = device_mirror_argv("S", true);
        assert_eq!(argv.last().unwrap(), "--no-audio");
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

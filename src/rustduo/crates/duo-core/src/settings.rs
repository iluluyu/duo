//! 用户设置：加载、校验、落盘（data_dir 下的 JSON）。对译自
//! duo/core/settings.py；合同镜像 tests/test_settings.py。
//!
//! 合同（docs/window-experience.md §4）：
//! - 存储：data_dir()/settings.json，透明可手编；
//! - load 永不失败：缺失/损坏/类型错的文件回退默认值，并把问题清单报
//!   给 UI 一次性呈现；
//! - save 先校验再原子替换（tmp 文件 + rename）；
//! - 全局优先级：显式 CLI 参数 > 已存设置 > 内建默认。

use std::path::{Path, PathBuf};

use serde::Serialize;
use serde_json::Value;

use crate::paths;

pub const VALID_CORNER_MODES: [&str; 3] = ["system", "g2", "none"];
pub const VALID_AUDIO_POLICIES: [&str; 3] = ["latest", "all", "off"];
pub const VALID_VIDEO_CODECS: [&str; 4] = ["auto", "h264", "h265", "av1"];
pub const VALID_BAR_MODES: [&str; 3] = ["immersive", "native", "none"];
pub const VALID_THEMES: [&str; 3] = ["light", "dark", "system"];

// 输入范围，不是硬件承诺（docs §4.1）。
pub const FPS_RANGE: (i64, i64) = (1, 240);
pub const BITRATE_RANGE: (i64, i64) = (1, 200);
pub const DPI_RANGE: (i64, i64) = (120, 640);
pub const CORNER_RANGE: (i64, i64) = (0, 96); // 超过 96 是试验田（160 已实测）
                                              // 渲染倍率（2026-09-11）：窗口分辨率 ÷ k = 安卓渲染分辨率（4K 窗 2 倍
                                              // 即 1K 渲染）。范围 1.0–3.0，自由取值（预设 1/1.4/2，微调步进 0.1）。
pub const RENDER_SCALE_RANGE: (f64, f64) = (1.0, 3.0);

/// 虚拟屏密度的出厂默认（桌面 mdpi 基准）。固定横屏的平行视窗保障
/// 以「设置 == 默认值」为介入条件（用户自定义密度不让位）。
pub const DEFAULT_VD_DPI: i64 = 160;

/// 持久化的用户偏好（引擎默认值与外观）。字段顺序即 settings.json 落盘
/// 顺序（Python dataclass asdict 合同）。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Settings {
    pub version: i64,
    pub scrcpy_path: String,
    pub adb_path: String,
    /// 60：120Hz 面板上节拍均匀（90 会 2:1 抖动）；强 PC 可 120 换顺滑。
    pub fps: Option<i64>,
    pub bitrate_mbps: Option<i64>,
    /// 虚拟屏密度（2026-09-11 起默认 160 桌面密度）：160 = mdpi 基准、
    /// 1dp==1px、同屏 dp 数最大化；None = 跟随设备（wm density 探测）。
    pub dpi: Option<i64>,
    /// 渲染倍率：flex 会话的虚拟屏按 窗口÷k 建屏，1.0 = 原生跟随窗口。
    pub render_scale: f64,
    /// system = DWM 默认圆角；g2 = quartic region（长期目标）。
    pub corner_mode: String,
    /// iPhone/iPad 式 squircle 比例。
    pub corner_size_dip: i64,
    pub glass_enabled: bool,
    /// 外观主题（2026-09-12 暗色模式）：light / dark / system。
    pub theme: String,
    /// latest = 新会话带音频时其他音频会话自动重启为 --no-audio；
    /// all = 不做单音频仲裁；off = 全部静音。
    pub audio_policy: String,
    /// auto = 探测设备硬件编码器并择优；显式指定则用之。
    pub video_codec: String,
    /// 窗口栏模式（docs/window-experience.md §10）；默认：上巴沉浸、
    /// 下巴不显示（scrcpy 右键已是返回）。
    pub top_bar_mode: String,
    pub bottom_bar_mode: String,
    /// --turn-screen-off：黑屏防误触，主要对 mirror（整机镜像）有意义。
    pub turn_screen_off: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            version: 1,
            scrcpy_path: String::new(),
            adb_path: String::new(),
            fps: Some(60),
            bitrate_mbps: Some(30),
            dpi: Some(DEFAULT_VD_DPI),
            render_scale: 1.0,
            corner_mode: "system".into(),
            corner_size_dip: 48,
            glass_enabled: true,
            theme: "light".into(),
            audio_policy: "latest".into(),
            video_codec: "auto".into(),
            top_bar_mode: "immersive".into(),
            bottom_bar_mode: "none".into(),
            turn_screen_off: false,
        }
    }
}

/// 设置落点：data_dir()/settings.json（base 可注入，bin 的 --data-dir）。
pub fn settings_path(base: Option<&Path>) -> PathBuf {
    paths::data_dir(base).join("settings.json")
}

/// serde_json Value 的 Python repr 风格字符串（问题清单文案沿用 Python）。
fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => format!("'{s}'"),
        other => other.to_string(),
    }
}

/// Python ``{low:g}`` 的紧凑格式（1.0 → "1"，2.5 → "2.5"）。
fn py_g(x: f64) -> String {
    if x.fract() == 0.0 && x.abs() < 1e15 {
        format!("{}", x as i64)
    } else {
        format!("{x}")
    }
}

/// 元组 repr（枚举问题的 ``不在 (...)`` 文案）。
fn py_tuple_repr(items: &[&str]) -> String {
    let inner = items
        .iter()
        .map(|s| format!("'{s}'"))
        .collect::<Vec<_>>()
        .join(", ");
    format!("({inner})")
}

/// 钳成范围内的整数；缺键/显式 null 用回退值（无问题），其他任何坏值
/// 报问题并逐字段回退。
fn clamp_int_or_none(
    value: Option<&Value>,
    low: i64,
    high: i64,
    field: &str,
    fallback: Option<i64>,
    problems: &mut Vec<String>,
) -> Option<i64> {
    let Some(value) = value.filter(|v| !v.is_null()) else {
        return fallback;
    };
    if let Some(n) = value.as_i64() {
        if !(low..=high).contains(&n) {
            problems.push(format!("{field}: {n} 超出范围 {low}–{high}"));
            return fallback;
        }
        return Some(n);
    }
    if value.as_u64().is_some() {
        // 超出 i64 的巨大整数：Python 侧按超范围报错。
        problems.push(format!("{field}: {} 超出范围 {low}–{high}", py_repr(value)));
        return fallback;
    }
    // 布尔/浮点/字符串等一律不是整数。
    problems.push(format!("{field}: 期望整数，实际为 {}", py_repr(value)));
    fallback
}

/// 钳成范围内的浮点；任何坏值（含布尔/null/非数值）报问题并回退。
fn clamp_float_or_none(
    value: Option<&Value>,
    low: f64,
    high: f64,
    field: &str,
    fallback: f64,
    problems: &mut Vec<String>,
) -> f64 {
    let Some(value) = value else {
        return fallback;
    };
    let Some(number) = value.as_f64() else {
        problems.push(format!("{field}: 期望数值，实际为 {}", py_repr(value)));
        return fallback;
    };
    if !(low..=high).contains(&number) {
        problems.push(format!(
            "{field}: {} 超出范围 {}–{}",
            py_repr(value),
            py_g(low),
            py_g(high)
        ));
        return fallback;
    }
    number
}

/// raw 里的字符串字段；缺键/类型错回退 ""（缺键不报问题，对齐 Python
/// 的 raw.get(field, "")）。
fn text_field(
    raw: &serde_json::Map<String, Value>,
    field: &str,
    problems: &mut Vec<String>,
) -> String {
    match raw.get(field) {
        Some(Value::String(s)) => s.clone(),
        None => String::new(),
        Some(other) => {
            problems.push(format!("{field}: 期望字符串，实际为 {}", py_repr(other)));
            String::new()
        }
    }
}

/// 枚举字段：集合外/缺键之外的任何值回退默认并报问题（缺键走调用方的
/// default，与 Python raw.get(field, defaults.x) 一致）。
fn enum_field(
    raw: &serde_json::Map<String, Value>,
    field: &str,
    valid: &[&str],
    default: &str,
    problems: &mut Vec<String>,
) -> String {
    match raw.get(field) {
        Some(Value::String(s)) if valid.contains(&s.as_str()) => s.clone(),
        Some(other) => {
            problems.push(format!(
                "{field}: {} 不在 {}",
                py_repr(other),
                py_tuple_repr(valid)
            ));
            default.to_string()
        }
        None => default.to_string(),
    }
}

/// 布尔字段：非布尔值回退默认并报问题（缺键不报）。
fn bool_field(
    raw: &serde_json::Map<String, Value>,
    field: &str,
    default: bool,
    problems: &mut Vec<String>,
) -> bool {
    match raw.get(field) {
        Some(Value::Bool(b)) => *b,
        None => default,
        Some(other) => {
            problems.push(format!("{field}: 期望布尔，实际为 {}", py_repr(other)));
            default
        }
    }
}

/// 从 raw 对象构建 Settings，丢弃一切非法值。
///
/// 非法值逐字段回退到字段默认（绝不猜测替代），手编错误无法静默改变
/// 其他行为。只读已知键：历史撤除键（如 flex_resolution）与任何多余
/// 键被无害忽略、不进 problems。问题追加顺序与 Python 一致。
pub fn sanitize(raw: &serde_json::Map<String, Value>, problems: &mut Vec<String>) -> Settings {
    let defaults = Settings::default();

    let version = match raw.get("version") {
        None => 1,
        Some(v) if !v.is_boolean() => match v.as_i64() {
            Some(n) => n,
            None => {
                problems.push(format!("version: 期望整数，实际为 {}", py_repr(v)));
                1
            }
        },
        Some(v) => {
            problems.push(format!("version: 期望整数，实际为 {}", py_repr(v)));
            1
        }
    };

    let scrcpy_path = text_field(raw, "scrcpy_path", problems);
    let adb_path = text_field(raw, "adb_path", problems);

    let fps = clamp_int_or_none(
        raw.get("fps"),
        FPS_RANGE.0,
        FPS_RANGE.1,
        "fps",
        defaults.fps,
        problems,
    );
    let bitrate_mbps = clamp_int_or_none(
        raw.get("bitrate_mbps"),
        BITRATE_RANGE.0,
        BITRATE_RANGE.1,
        "bitrate_mbps",
        defaults.bitrate_mbps,
        problems,
    );
    let mut dpi = clamp_int_or_none(
        raw.get("dpi"),
        DPI_RANGE.0,
        DPI_RANGE.1,
        "dpi",
        defaults.dpi,
        problems,
    );
    // 显式 null = 跟随设备（保存页开关写入的语义），缺键才是新默认 160。
    if matches!(raw.get("dpi"), Some(Value::Null)) {
        dpi = None;
    }
    let render_scale = clamp_float_or_none(
        raw.get("render_scale"),
        RENDER_SCALE_RANGE.0,
        RENDER_SCALE_RANGE.1,
        "render_scale",
        defaults.render_scale,
        problems,
    );
    let corner_size_dip = clamp_int_or_none(
        raw.get("corner_size_dip"),
        CORNER_RANGE.0,
        CORNER_RANGE.1,
        "corner_size_dip",
        Some(defaults.corner_size_dip),
        problems,
    )
    .unwrap_or(0);

    let corner_mode = enum_field(
        raw,
        "corner_mode",
        &VALID_CORNER_MODES,
        &defaults.corner_mode,
        problems,
    );
    let glass_enabled = bool_field(raw, "glass_enabled", defaults.glass_enabled, problems);
    let theme = enum_field(raw, "theme", &VALID_THEMES, &defaults.theme, problems);
    let audio_policy = enum_field(
        raw,
        "audio_policy",
        &VALID_AUDIO_POLICIES,
        &defaults.audio_policy,
        problems,
    );
    let video_codec = enum_field(
        raw,
        "video_codec",
        &VALID_VIDEO_CODECS,
        &defaults.video_codec,
        problems,
    );
    let top_bar_mode = enum_field(
        raw,
        "top_bar_mode",
        &VALID_BAR_MODES,
        &defaults.top_bar_mode,
        problems,
    );
    let bottom_bar_mode = enum_field(
        raw,
        "bottom_bar_mode",
        &VALID_BAR_MODES,
        &defaults.bottom_bar_mode,
        problems,
    );
    let turn_screen_off = bool_field(raw, "turn_screen_off", defaults.turn_screen_off, problems);

    Settings {
        version,
        scrcpy_path,
        adb_path,
        fps,
        bitrate_mbps,
        dpi,
        render_scale,
        corner_mode,
        corner_size_dip,
        glass_enabled,
        theme,
        audio_policy,
        video_codec,
        top_bar_mode,
        bottom_bar_mode,
        turn_screen_off,
    }
}

/// 读 settings.json；永不失败。
///
/// 返回生效设置 + 人类可读的问题清单（缺失/损坏/非法字段）。
pub fn load_settings(base: Option<&Path>) -> (Settings, Vec<String>) {
    let path = settings_path(base);
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            return (Settings::default(), Vec::new());
        }
        Err(err) => {
            return (
                Settings::default(),
                vec![format!("settings.json 无法读取（{err}），已用默认值")],
            );
        }
    };
    match serde_json::from_str::<Value>(&text) {
        Err(err) => (
            Settings::default(),
            vec![format!("settings.json 无法读取（{err}），已用默认值")],
        ),
        Ok(Value::Object(map)) => {
            let mut problems = Vec::new();
            (sanitize(&map, &mut problems), problems)
        }
        Ok(_) => (
            Settings::default(),
            vec!["settings.json 顶层不是对象，已用默认值".into()],
        ),
    }
}

/// 该 Settings 实例的问题；空清单 = 可落盘。
pub fn validate(settings: &Settings) -> Vec<String> {
    let mut problems = Vec::new();
    let asdict = serde_json::to_value(settings)
        .ok()
        .and_then(|v| v.as_object().cloned())
        .expect("Settings 序列化不可失败");
    sanitize(&asdict, &mut problems);
    problems
}

/// 先校验再原子替换 settings.json；校验失败返回 Err（问题以 "; " 连接，
/// 对齐 Python 的 ValueError 文案）。
pub fn save_settings(settings: &Settings, base: Option<&Path>) -> Result<(), String> {
    let problems = validate(settings);
    if !problems.is_empty() {
        return Err(problems.join("; "));
    }
    let path = settings_path(base);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let tmp = path.with_extension("json.tmp");
    let json = serde_json::to_string_pretty(settings).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, json).map_err(|err| format!("settings.json 无法写入（{err}）"))?;
    std::fs::rename(&tmp, &path).map_err(|err| format!("settings.json 无法写入（{err}）"))?;
    Ok(())
}

/// 会话覆盖层的圆角（0 = 不建 region）。
///
/// 只有实验性 ``g2`` 模式请求 region；``system``（默认）与 ``none`` 不动
/// 窗口，让 Windows 自己的 DWM 圆角生效。
pub fn corner_radius_dip(settings: &Settings) -> i64 {
    if settings.corner_mode != "g2" {
        return 0;
    }
    settings.corner_size_dip
}

/// ``name``（scrcpy/adb）的首选二进制：设置覆盖 > 探测发现。
///
/// 显式配置的路径原样生效（用户负责）；空设置回退 PATH 探测。两者皆无
/// 返回 None。
pub fn resolve_tool(name: &str, settings: &Settings, found: Option<&str>) -> Option<String> {
    let configured = if name == "scrcpy" {
        settings.scrcpy_path.as_str()
    } else {
        settings.adb_path.as_str()
    };
    if configured.is_empty() {
        found.map(str::to_string)
    } else {
        Some(configured.to_string())
    }
}

/// 面板 adb：设置覆盖 > 探测发现 > 字面回退。
///
/// 每进程解析一次（GUI 启动或保存设置后）让设备轮询、安装检查与 CLI
/// 会话共用同一 adb——混版本的服务器会互相杀。
pub fn resolve_adb_path(settings: &Settings, discovered: Option<&str>, fallback: &str) -> String {
    resolve_tool("adb", settings, discovered).unwrap_or_else(|| fallback.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// 每个测试一个一次性数据目录（进程内并发互不踩脚）。
    fn temp_base(tag: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("duo-core-settings-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        dir
    }

    fn write_raw(base: &Path, json: &str) {
        fs::write(settings_path(Some(base)), json).unwrap();
    }

    fn raw_object(base: &Path) -> serde_json::Map<String, Value> {
        serde_json::from_str::<Value>(&fs::read_to_string(settings_path(Some(base))).unwrap())
            .unwrap()
            .as_object()
            .cloned()
            .unwrap()
    }

    fn overrides(f: impl FnOnce(&mut Settings)) -> Settings {
        let mut s = Settings::default();
        f(&mut s);
        s
    }

    #[test]
    fn defaults_roundtrip() {
        // 保存→加载保值；文件是人类可读 JSON。
        let base = temp_base("roundtrip");
        let settings = overrides(|s| {
            s.fps = Some(120);
            s.bitrate_mbps = Some(8);
            s.corner_size_dip = 64;
            s.adb_path = r"C:\工具\adb.exe".into();
        });
        save_settings(&settings, Some(base.as_path())).unwrap();
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded, settings);
        assert_eq!(raw_object(&base).get("fps"), Some(&Value::from(120)));
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn missing_file_gives_defaults() {
        let base = temp_base("missing");
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded, Settings::default());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn default_corner_mode_is_system_rounding() {
        // 出厂保持 Windows 自身 DWM 圆角：G2 region 路径保持 opt-in，
        // 直到边缘质量问题解决（长期目标）。
        let fresh = Settings::default();
        assert_eq!(fresh.corner_mode, "system");
        assert_eq!(corner_radius_dip(&fresh), 0);
    }

    #[test]
    fn corrupt_file_falls_back_with_problem() {
        let base = temp_base("corrupt");
        write_raw(&base, "{not json");
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert_eq!(loaded, Settings::default());
        assert_eq!(problems.len(), 1);
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn invalid_values_reported_and_dropped() {
        // 类型错/超范围的字段逐字段回退默认。
        let base = temp_base("invalid");
        write_raw(
            &base,
            r#"{"fps": 999, "bitrate_mbps": "high", "corner_mode": "circle",
                 "corner_size_dip": true, "glass_enabled": "yes"}"#,
        );
        let (loaded, problems) = load_settings(Some(base.as_path()));
        let defaults = Settings::default();
        assert_eq!(loaded.fps, defaults.fps); // 超范围 → 默认
        assert_eq!(loaded.bitrate_mbps, defaults.bitrate_mbps);
        assert_eq!(loaded.corner_mode, defaults.corner_mode);
        assert_eq!(loaded.corner_size_dip, defaults.corner_size_dip);
        assert!(loaded.glass_enabled);
        assert_eq!(problems.len(), 5);
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn save_rejects_invalid() {
        let base = temp_base("reject");
        assert!(save_settings(&overrides(|s| s.fps = Some(0)), Some(base.as_path())).is_err());
        assert!(save_settings(
            &overrides(|s| s.corner_size_dip = 500),
            Some(base.as_path())
        )
        .is_err());
        // 拒绝时什么都不写。
        assert!(!settings_path(Some(&base)).exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn atomic_write_leaves_no_tmp() {
        let base = temp_base("atomic");
        save_settings(&Settings::default(), Some(base.as_path())).unwrap();
        assert!(settings_path(Some(&base)).exists());
        assert!(!base.join("settings.json.tmp").exists());
        let entries: Vec<String> = fs::read_dir(&base)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(entries, vec!["settings.json".to_string()]);
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn corner_radius_mapping() {
        // g2 模式映射到自己的 size；system/none 映射 0（无 region）。
        assert_eq!(
            corner_radius_dip(&overrides(|s| {
                s.corner_mode = "g2".into();
                s.corner_size_dip = 48;
            })),
            48
        );
        assert_eq!(
            corner_radius_dip(&overrides(|s| s.corner_mode = "system".into())),
            0
        );
        assert_eq!(
            corner_radius_dip(&overrides(|s| s.corner_mode = "none".into())),
            0
        );
    }

    #[test]
    fn resolve_tool_settings_win_over_discovery() {
        // 显式设置路径原样生效；空值回退探测。
        let s = overrides(|s| s.scrcpy_path = r"C:\bin\scrcpy.exe".into());
        assert_eq!(
            resolve_tool("scrcpy", &s, Some("/usr/bin/scrcpy")),
            Some(r"C:\bin\scrcpy.exe".into())
        );
        assert_eq!(
            resolve_tool("adb", &s, Some("/usr/bin/adb")),
            Some("/usr/bin/adb".into())
        );
        assert_eq!(resolve_tool("adb", &Settings::default(), None), None);
    }

    #[test]
    fn resolve_adb_path_priority() {
        // 面板 adb：设置覆盖 > PATH 探测 > 字面回退。
        let r#override = overrides(|s| s.adb_path = r"C:\o\adb.exe".into());
        assert_eq!(
            resolve_adb_path(&r#override, Some("/found/adb"), "adb.exe"),
            r"C:\o\adb.exe"
        );
        assert_eq!(
            resolve_adb_path(&Settings::default(), Some("/found/adb"), "adb.exe"),
            "/found/adb"
        );
        assert_eq!(
            resolve_adb_path(&Settings::default(), None, "adb.exe"),
            "adb.exe"
        );
    }

    #[test]
    fn validate_clean_instance() {
        assert!(validate(&Settings::default()).is_empty());
        assert!(validate(&overrides(|s| s.fps = None)).is_empty());
        assert!(validate(&overrides(|s| s.dpi = None)).is_empty());
    }

    // ------------------------------------------------ 投屏质量三字段

    #[test]
    fn quality_fields_roundtrip() {
        // audio_policy / video_codec / turn_screen_off 落盘重读不丢。
        let base = temp_base("quality-rt");
        save_settings(
            &overrides(|s| {
                s.audio_policy = "all".into();
                s.video_codec = "h265".into();
                s.turn_screen_off = true;
            }),
            Some(base.as_path()),
        )
        .unwrap();
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded.audio_policy, "all");
        assert_eq!(loaded.video_codec, "h265");
        assert!(loaded.turn_screen_off);
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn quality_fields_defaults() {
        // latest/auto/false：latest 会话优先音频，auto 走编码器探测，
        // 屏幕默认不关。
        let fresh = Settings::default();
        assert_eq!(fresh.audio_policy, "latest");
        assert_eq!(fresh.video_codec, "auto");
        assert!(!fresh.turn_screen_off);
    }

    #[test]
    fn quality_fields_invalid_reported_and_dropped() {
        // 类型错/集合外值逐字段回退，各报一条问题。
        let base = temp_base("quality-bad");
        write_raw(
            &base,
            r#"{"audio_policy": "loudest", "video_codec": "mpeg2",
                 "turn_screen_off": "yes"}"#,
        );
        let (loaded, problems) = load_settings(Some(base.as_path()));
        let defaults = Settings::default();
        assert_eq!(loaded.audio_policy, defaults.audio_policy);
        assert_eq!(loaded.video_codec, defaults.video_codec);
        assert_eq!(loaded.turn_screen_off, defaults.turn_screen_off);
        assert_eq!(problems.len(), 3);
        assert!(problems.iter().any(|p| p.contains("audio_policy")));
        assert!(problems.iter().any(|p| p.contains("video_codec")));
        assert!(problems.iter().any(|p| p.contains("turn_screen_off")));
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn quality_fields_save_rejects_invalid() {
        let base = temp_base("quality-reject");
        assert!(save_settings(
            &overrides(|s| s.audio_policy = "loudest".into()),
            Some(base.as_path())
        )
        .is_err());
        assert!(save_settings(
            &overrides(|s| s.video_codec = "mpeg2".into()),
            Some(base.as_path())
        )
        .is_err());
        assert!(!settings_path(Some(&base)).exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn stale_flex_resolution_key_is_ignored() {
        // 已撤除的 flex_resolution 键残留在旧 settings.json 里被无害忽略：
        // sanitize 只读已知键，残留键不进 problems、不产生字段。
        let base = temp_base("stale-key");
        write_raw(&base, r#"{"flex_resolution": "1080p", "fps": 90}"#);
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded.fps, Some(90));
        assert!(serde_json::to_value(&loaded)
            .expect("Settings 序列化不可失败")
            .as_object()
            .unwrap()
            .get("flex_resolution")
            .is_none());
        let _ = fs::remove_dir_all(&base);
    }

    // ------------------------------------------- 窗口栏模式（上巴/下巴）

    #[test]
    fn bar_mode_fields_roundtrip() {
        // top_bar_mode / bottom_bar_mode 落盘重读不丢（含 none）。
        let base = temp_base("bar-rt");
        save_settings(
            &overrides(|s| {
                s.top_bar_mode = "native".into();
                s.bottom_bar_mode = "none".into();
            }),
            Some(base.as_path()),
        )
        .unwrap();
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded.top_bar_mode, "native");
        assert_eq!(loaded.bottom_bar_mode, "none");
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn bar_mode_fields_defaults() {
        // 新默认（2026-09-09）：上巴 immersive（无边框 + overlay 悬浮控件），
        // 下巴 none（scrcpy 右键已是返回，下巴对多数用户冗余）。
        let fresh = Settings::default();
        assert_eq!(fresh.top_bar_mode, "immersive");
        assert_eq!(fresh.bottom_bar_mode, "none");
    }

    #[test]
    fn bar_mode_enum_accepts_none_roundtrip() {
        // VALID_BAR_MODES 三枚举全量放行：none 进枚举后往返自动持久化。
        assert_eq!(VALID_BAR_MODES, ["immersive", "native", "none"]);
        for (top, bottom) in [
            ("none", "none"),
            ("none", "immersive"),
            ("immersive", "none"),
        ] {
            let base = temp_base("bar-enum");
            save_settings(
                &overrides(|s| {
                    s.top_bar_mode = top.into();
                    s.bottom_bar_mode = bottom.into();
                }),
                Some(base.as_path()),
            )
            .unwrap();
            let (loaded, problems) = load_settings(Some(base.as_path()));
            assert!(problems.is_empty());
            assert_eq!(
                (
                    loaded.top_bar_mode.as_str(),
                    loaded.bottom_bar_mode.as_str()
                ),
                (top, bottom)
            );
            let _ = fs::remove_dir_all(&base);
        }
    }

    #[test]
    fn bar_mode_invalid_falls_back_with_problem() {
        // 非法值逐字段回退各自默认（上巴 immersive / 下巴 none），各报一条。
        let base = temp_base("bar-bad");
        write_raw(
            &base,
            r#"{"top_bar_mode": "floating", "bottom_bar_mode": 3}"#,
        );
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert_eq!(loaded.top_bar_mode, "immersive");
        assert_eq!(loaded.bottom_bar_mode, "none");
        assert_eq!(problems.len(), 2);
        assert!(problems.iter().any(|p| p.contains("top_bar_mode")));
        assert!(problems.iter().any(|p| p.contains("bottom_bar_mode")));
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn bar_mode_save_rejects_invalid() {
        // save 前校验拒绝非法枚举，不落盘。
        let base = temp_base("bar-reject");
        assert!(save_settings(
            &overrides(|s| s.top_bar_mode = "floating".into()),
            Some(base.as_path())
        )
        .is_err());
        assert!(save_settings(
            &overrides(|s| s.bottom_bar_mode = "titanium".into()),
            Some(base.as_path())
        )
        .is_err());
        assert!(!settings_path(Some(&base)).exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn old_savefile_without_bar_modes_loads_defaults() {
        // from_dict 兼容旧存档：字段缺失 → 各自默认，且不产生问题。
        let base = temp_base("bar-legacy");
        write_raw(
            &base,
            r#"{"version": 1, "fps": 90, "audio_policy": "all",
                 "video_codec": "h264", "turn_screen_off": true,
                 "window_aspect": "free"}"#,
        );
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded.top_bar_mode, "immersive");
        assert_eq!(loaded.bottom_bar_mode, "none");
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn saved_explicit_bar_modes_not_migrated() {
        // 改默认不迁移老用户：已存的显式值原样保留（2026-09-09 决策——
        // 下巴老默认 immersive 的用户不被静默改掉）。
        let base = temp_base("bar-keep");
        write_raw(
            &base,
            r#"{"top_bar_mode": "native", "bottom_bar_mode": "immersive"}"#,
        );
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded.top_bar_mode, "native");
        assert_eq!(loaded.bottom_bar_mode, "immersive");
        let _ = fs::remove_dir_all(&base);
    }

    // ---------------------- DPI 默认值与渲染倍率（2026-09-11）

    #[test]
    fn dpi_defaults_to_desktop_160() {
        // 新默认 = 160 桌面密度（不再是设备密度探测）；倍率默认 1.0 原生。
        let fresh = Settings::default();
        assert_eq!(fresh.dpi, Some(160));
        assert_eq!(fresh.render_scale, 1.0);
    }

    #[test]
    fn dpi_explicit_null_keeps_follow_device() {
        // 显式 "dpi": null = 跟随设备（保存页开关的持久形态），不得被新
        // 默认 160 顶掉——老文件静默翻语义比缺省更糟。
        let base = temp_base("dpi-null");
        write_raw(&base, r#"{"dpi": null}"#);
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded.dpi, None);
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn dpi_missing_key_falls_back_to_default() {
        // 缺键（新装/手删）= 新默认 160。
        let base = temp_base("dpi-missing");
        write_raw(&base, r#"{"fps": 60}"#);
        let (loaded, problems) = load_settings(Some(base.as_path()));
        assert!(problems.is_empty());
        assert_eq!(loaded.dpi, Some(160));
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn render_scale_accepts_numbers() {
        for (raw, expected) in [
            (serde_json::json!(2.5), 2.5),
            (serde_json::json!(2), 2.0),
            (serde_json::json!(1.0), 1.0),
            (serde_json::json!(3.0), 3.0),
        ] {
            let base = temp_base("scale-ok");
            write_raw(
                &base,
                &serde_json::to_string(&serde_json::json!({ "render_scale": raw })).unwrap(),
            );
            let (loaded, problems) = load_settings(Some(base.as_path()));
            assert!(problems.is_empty());
            assert_eq!(loaded.render_scale, expected);
            let _ = fs::remove_dir_all(&base);
        }
    }

    #[test]
    fn render_scale_rejects_junk() {
        // 坏值/超范围回默认 1.0 并进问题清单（保存前校验同路）。
        for raw in [
            serde_json::json!("fast"),
            serde_json::json!(true),
            serde_json::json!(0.5),
            serde_json::json!(3.5),
            serde_json::json!(8.0),
            serde_json::json!(null),
            serde_json::json!([2.0]),
        ] {
            let base = temp_base("scale-bad");
            write_raw(
                &base,
                &serde_json::to_string(&serde_json::json!({ "render_scale": raw })).unwrap(),
            );
            let (loaded, problems) = load_settings(Some(base.as_path()));
            assert_eq!(loaded.render_scale, 1.0);
            assert!(problems.iter().any(|p| p.contains("render_scale")));
            let _ = fs::remove_dir_all(&base);
        }
        let base = temp_base("scale-save-reject");
        assert!(save_settings(&overrides(|s| s.render_scale = 9.0), Some(base.as_path())).is_err());
        assert!(!settings_path(Some(&base)).exists());
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn problem_messages_mirror_python_wording() {
        // 问题文案逐条对齐 Python（字段名 + 中文短语），面板直接透传展示。
        let mut problems = Vec::new();
        sanitize(
            &serde_json::from_str::<Value>(
                r#"{"fps": 999, "bitrate_mbps": "high", "corner_size_dip": true,
                     "corner_mode": "circle", "version": null}"#,
            )
            .unwrap()
            .as_object()
            .unwrap()
            .clone(),
            &mut problems,
        );
        assert_eq!(problems[0], "version: 期望整数，实际为 None".to_string());
        assert_eq!(problems[1], "fps: 999 超出范围 1–240".to_string());
        assert_eq!(
            problems[2],
            "bitrate_mbps: 期望整数，实际为 'high'".to_string()
        );
        assert_eq!(
            problems[3],
            "corner_size_dip: 期望整数，实际为 True".to_string()
        );
        assert_eq!(
            problems[4],
            "corner_mode: 'circle' 不在 ('system', 'g2', 'none')".to_string()
        );
        // 渲染倍率的范围文案用 :g 紧凑格式。
        let mut problems = Vec::new();
        sanitize(
            &serde_json::from_str::<Value>(r#"{"render_scale": 8.0}"#)
                .unwrap()
                .as_object()
                .unwrap()
                .clone(),
            &mut problems,
        );
        assert_eq!(problems, vec!["render_scale: 8.0 超出范围 1–3".to_string()]);
    }
}

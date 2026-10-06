//! gui_prefs.json 各节读写（controller.py load_*/save_* 对译）：单文档
//! 读改写保证节间互不覆盖；坏形状丢弃不硬猜（手改坏文件最多丢一条选择，
//! 不断启动链路）。文件与 Python 面板同格式——两栈可互换使用同一数据。

use std::collections::BTreeMap;
use std::path::PathBuf;

use duo_core::settings::VALID_BAR_MODES;

use crate::pinyin::label_sort_key;

pub fn prefs_path() -> PathBuf {
    duo_core::paths::data_dir(None).join("gui_prefs.json")
}

fn read_doc() -> serde_json::Map<String, serde_json::Value> {
    std::fs::read_to_string(prefs_path())
        .ok()
        .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
        .and_then(|v: serde_json::Value| v.as_object().cloned())
        .unwrap_or_default()
}

fn write_doc(doc: &serde_json::Map<String, serde_json::Value>) {
    let path = prefs_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(text) = serde_json::to_string_pretty(doc) {
        let _ = std::fs::write(path, text);
    }
}

fn update_doc(section: &str, value: serde_json::Value) {
    let mut doc = read_doc();
    doc.insert(section.to_string(), value);
    write_doc(&doc);
}

// --------------------------------------------------------------- portrait

/// 长按切换的按应用方向记忆（空开局：不替 APP 猜方向）。
pub fn load_portrait_prefs() -> BTreeMap<String, bool> {
    let mut merged = BTreeMap::new();
    if let Some(saved) = read_doc().get("portrait").and_then(|v| v.as_object()) {
        for (k, v) in saved {
            if let Some(b) = v.as_bool() {
                merged.insert(k.clone(), b);
            }
        }
    }
    merged
}

pub fn save_portrait_prefs(prefs: &BTreeMap<String, bool>) {
    update_doc("portrait", serde_json::to_value(prefs).unwrap_or_default());
}

// ----------------------------------------------------------------- pinned

/// 置顶集合（排序持久化，diff 稳定）。
pub fn load_pinned_prefs() -> Vec<String> {
    read_doc()
        .get("pinned")
        .and_then(|v| v.as_array())
        .map(|list| {
            list.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

pub fn save_pinned_prefs(pinned: &[String]) {
    let mut sorted = pinned.to_vec();
    sorted.sort();
    update_doc("pinned", serde_json::to_value(sorted).unwrap_or_default());
}

// -------------------------------------------------------------- wireless

/// 上次无线连接目标（设备卡对话框预填；坏形状丢弃回空串）。
pub fn load_wireless_target() -> String {
    read_doc()
        .get("wireless")
        .and_then(|v| v.get("target"))
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string()
}

/// 历史无线地址（最近优先，最多 5 个；设置页设备卡/对话框共用）。
pub fn load_wireless_recent() -> Vec<String> {
    read_doc()
        .get("wireless")
        .and_then(|v| v.get("recent"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// 记住一次成功连接：recent 头插去重裁 5，target 同步刷新。
pub fn save_wireless_target(target: &str) {
    let mut doc = read_doc();
    let mut section = doc
        .get("wireless")
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default();
    let mut recent = load_wireless_recent();
    recent.retain(|t| t != target);
    recent.insert(0, target.to_string());
    recent.truncate(5);
    section.insert("target".into(), serde_json::Value::String(target.into()));
    section.insert(
        "recent".into(),
        serde_json::Value::Array(recent.into_iter().map(serde_json::Value::String).collect()),
    );
    doc.insert("wireless".into(), serde_json::Value::Object(section));
    write_doc(&doc);
}

/// 遗忘一个历史地址；被删的恰为 target 时回退到新的队首（或空）。
pub fn forget_wireless_target(target: &str) {
    let mut doc = read_doc();
    let mut section = doc
        .get("wireless")
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default();
    let mut recent = load_wireless_recent();
    let was_target = load_wireless_target() == target;
    recent.retain(|t| t != target);
    if was_target {
        let head = recent.first().cloned().unwrap_or_default();
        section.insert("target".into(), serde_json::Value::String(head));
    }
    section.insert(
        "recent".into(),
        serde_json::Value::Array(recent.into_iter().map(serde_json::Value::String).collect()),
    );
    doc.insert("wireless".into(), serde_json::Value::Object(section));
    write_doc(&doc);
}

// ---------------------------------------------------------------- devices

/// 设备自定义名（gui_prefs.json devices.names：serial → 名称）。
pub fn load_device_names() -> BTreeMap<String, String> {
    read_doc()
        .get("devices")
        .and_then(|v| v.get("names"))
        .and_then(|v| v.as_object())
        .map(|m| {
            m.iter()
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default()
}

/// 记住/清除（空白删除）一台设备的名字。
pub fn save_device_name(serial: &str, name: &str) {
    let mut doc = read_doc();
    let mut section = doc
        .get("devices")
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default();
    let mut names = section
        .get("names")
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default();
    if name.trim().is_empty() {
        names.remove(serial);
    } else {
        names.insert(
            serial.into(),
            serde_json::Value::String(name.trim().to_string()),
        );
    }
    section.insert("names".into(), serde_json::Value::Object(names));
    doc.insert("devices".into(), serde_json::Value::Object(section));
    write_doc(&doc);
}

// ---------------------------------------------------------------- display

/// 按应用显示模式：`{"mode": "flex"}` 或 `{"mode": "fixed", "aspect": id}`。
#[derive(Debug, Clone, PartialEq)]
pub enum DisplayChoice {
    Flex,
    Fixed { aspect: String },
}

pub fn load_display_prefs() -> BTreeMap<String, DisplayChoice> {
    let mut prefs = BTreeMap::new();
    let doc = read_doc();
    let Some(saved) = doc.get("display").and_then(|v| v.as_object()) else {
        return prefs;
    };
    for (package, choice) in saved {
        let Some(obj) = choice.as_object() else {
            continue;
        };
        match obj.get("mode").and_then(|m| m.as_str()) {
            Some("fixed") => {
                let aspect = obj
                    .get("aspect")
                    .and_then(|a| a.as_str())
                    .unwrap_or_default()
                    .to_string();
                prefs.insert(package.clone(), DisplayChoice::Fixed { aspect });
            }
            _ => {
                prefs.insert(package.clone(), DisplayChoice::Flex);
            }
        }
    }
    prefs
}

pub fn save_display_prefs(prefs: &BTreeMap<String, DisplayChoice>) {
    let obj: serde_json::Map<String, serde_json::Value> = prefs
        .iter()
        .map(|(package, choice)| {
            let value = match choice {
                DisplayChoice::Flex => serde_json::json!({"mode": "flex"}),
                DisplayChoice::Fixed { aspect } => {
                    serde_json::json!({"mode": "fixed", "aspect": aspect})
                }
            };
            (package.clone(), value)
        })
        .collect();
    update_doc("display", serde_json::Value::Object(obj));
}

// ------------------------------------------------------------------- bars

/// 按应用窗口栏覆盖：None = 跟随设置页默认；枚举外值丢弃。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct BarChoice {
    pub top: Option<&'static str>,
    pub bottom: Option<&'static str>,
}

pub fn load_bar_prefs() -> BTreeMap<String, BarChoice> {
    let mut prefs = BTreeMap::new();
    let doc = read_doc();
    let Some(saved) = doc.get("bars").and_then(|v| v.as_object()) else {
        return prefs;
    };
    for (package, choice) in saved {
        let Some(obj) = choice.as_object() else {
            continue;
        };
        let valid = |v: Option<&serde_json::Value>| -> Option<&'static str> {
            let s = v.and_then(|v| v.as_str())?;
            VALID_BAR_MODES.iter().copied().find(|m| m == &s)
        };
        prefs.insert(
            package.clone(),
            BarChoice {
                top: valid(obj.get("top")),
                bottom: valid(obj.get("bottom")),
            },
        );
    }
    prefs
}

pub fn save_bar_prefs(prefs: &BTreeMap<String, BarChoice>) {
    let obj: serde_json::Map<String, serde_json::Value> = prefs
        .iter()
        .map(|(package, choice)| {
            let mut entry = serde_json::Map::new();
            if let Some(top) = choice.top {
                entry.insert("top".into(), serde_json::json!(top));
            }
            if let Some(bottom) = choice.bottom {
                entry.insert("bottom".into(), serde_json::json!(bottom));
            }
            (package.clone(), serde_json::Value::Object(entry))
        })
        .collect();
    update_doc("bars", serde_json::Value::Object(obj));
}

// ------------------------------------------------------------ audio/behavior

pub fn load_flag_prefs(section: &str, key: &str) -> BTreeMap<String, bool> {
    let mut prefs = BTreeMap::new();
    let doc = read_doc();
    let Some(saved) = doc.get(section).and_then(|v| v.as_object()) else {
        return prefs;
    };
    for (package, choice) in saved {
        if let Some(flag) = choice
            .as_object()
            .and_then(|o| o.get(key))
            .and_then(|v| v.as_bool())
        {
            prefs.insert(package.clone(), flag);
        }
    }
    prefs
}

pub fn save_flag_prefs(section: &str, key: &str, prefs: &BTreeMap<String, bool>) {
    let obj: serde_json::Map<String, serde_json::Value> = prefs
        .iter()
        .map(|(package, flag)| (package.clone(), serde_json::json!({ key: flag })))
        .collect();
    update_doc(section, serde_json::Value::Object(obj));
}

/// 音频独占（audio 节）。
pub fn load_audio_prefs() -> BTreeMap<String, bool> {
    load_flag_prefs("audio", "exclusive")
}

pub fn save_audio_prefs(prefs: &BTreeMap<String, bool>) {
    save_flag_prefs("audio", "exclusive", prefs);
}

/// 断开保留画面（behavior 节）。
pub fn load_behavior_prefs() -> BTreeMap<String, bool> {
    load_flag_prefs("behavior", "keep_vd")
}

pub fn save_behavior_prefs(prefs: &BTreeMap<String, bool>) {
    save_flag_prefs("behavior", "keep_vd", prefs);
}

// --------------------------------------------------------- density / scale

pub const DPI_RANGE: (i64, i64) = (120, 640);
pub const SCALE_RANGE: (f64, f64) = (1.0, 3.0);

/// 按应用 DPI 覆盖（density 节，DPI_RANGE 内才收）。
pub fn load_density_prefs() -> BTreeMap<String, i64> {
    let mut prefs = BTreeMap::new();
    let doc = read_doc();
    let Some(saved) = doc.get("density").and_then(|v| v.as_object()) else {
        return prefs;
    };
    for (package, choice) in saved {
        if let Some(dpi) = choice
            .as_object()
            .and_then(|o| o.get("dpi"))
            .and_then(|v| v.as_i64())
        {
            if DPI_RANGE.0 <= dpi && dpi <= DPI_RANGE.1 {
                prefs.insert(package.clone(), dpi);
            }
        }
    }
    prefs
}

pub fn save_density_prefs(prefs: &BTreeMap<String, i64>) {
    let obj: serde_json::Map<String, serde_json::Value> = prefs
        .iter()
        .map(|(package, dpi)| (package.clone(), serde_json::json!({ "dpi": dpi })))
        .collect();
    update_doc("density", serde_json::Value::Object(obj));
}

/// 按应用渲染倍率（scale 节）。
pub fn load_scale_prefs() -> BTreeMap<String, f64> {
    let mut prefs = BTreeMap::new();
    let doc = read_doc();
    let Some(saved) = doc.get("scale").and_then(|v| v.as_object()) else {
        return prefs;
    };
    for (package, choice) in saved {
        if let Some(scale) = choice
            .as_object()
            .and_then(|o| o.get("scale"))
            .and_then(|v| v.as_f64())
        {
            if (SCALE_RANGE.0..=SCALE_RANGE.1).contains(&scale) {
                prefs.insert(package.clone(), scale);
            }
        }
    }
    prefs
}

pub fn save_scale_prefs(prefs: &BTreeMap<String, f64>) {
    let obj: serde_json::Map<String, serde_json::Value> = prefs
        .iter()
        .map(|(package, scale)| (package.clone(), serde_json::json!({ "scale": scale })))
        .collect();
    update_doc("scale", serde_json::Value::Object(obj));
}

/// 排序键随 prefs 走（调用方给 label 用）。
pub fn sort_key_for(label: &str) -> String {
    label_sort_key(label)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, MutexGuard};

    /// HOME 是进程级环境变量：prefs 测试互斥串行。
    static HOME_LOCK: Mutex<()> = Mutex::new(());

    fn lock_home() -> MutexGuard<'static, ()> {
        HOME_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "duo-panel-prefs-{tag}-{}-{}",
            std::process::id(),
            tag
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn with_home<F: FnOnce()>(home: &PathBuf, f: F) {
        // prefs_path 走 data_dir(None) = $HOME/.local/share/duo；测试注入 HOME。
        std::env::set_var("HOME", home);
        std::env::remove_var("USERPROFILE");
        f();
        std::env::remove_var("HOME");
    }

    #[test]
    fn sections_roundtrip_in_one_doc() {
        let _guard = lock_home();
        let home = scratch("roundtrip");
        with_home(&home, || {
            let mut portrait = BTreeMap::new();
            portrait.insert("com.tencent.mm".into(), true);
            save_portrait_prefs(&portrait);
            save_pinned_prefs(&["tv.danmaku.bili".to_string(), "a.b".to_string()]);
            let mut display = BTreeMap::new();
            display.insert(
                "a.b".into(),
                DisplayChoice::Fixed {
                    aspect: "16:9".into(),
                },
            );
            display.insert("c.d".into(), DisplayChoice::Flex);
            save_display_prefs(&display);
            // portrait 节不被后写覆盖。
            assert_eq!(load_portrait_prefs().get("com.tencent.mm"), Some(&true));
            assert_eq!(
                load_pinned_prefs(),
                vec!["a.b".to_string(), "tv.danmaku.bili".to_string()]
            );
            let display = load_display_prefs();
            assert_eq!(
                display.get("a.b"),
                Some(&DisplayChoice::Fixed {
                    aspect: "16:9".into()
                })
            );
            assert_eq!(display.get("c.d"), Some(&DisplayChoice::Flex));
            assert_eq!(display.get("missing"), None);
        });
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn corrupt_shapes_are_dropped_not_guessed() {
        let _guard = lock_home();
        let home = scratch("corrupt");
        with_home(&home, || {
            let mut doc = serde_json::Map::new();
            doc.insert(
                "bars".into(),
                serde_json::json!({
                    "ok.pkg": {"top": "native", "bottom": "bogus"},
                    "bad.pkg": "not a dict",
                }),
            );
            doc.insert("pinned".into(), serde_json::json!({"not":"a list"}));
            doc.insert(
                "density".into(),
                serde_json::json!({
                    "ok.pkg": {"dpi": 240},
                    "huge.pkg": {"dpi": 9999},
                }),
            );
            write_doc(&doc);
            let bars = load_bar_prefs();
            assert_eq!(
                bars.get("ok.pkg"),
                Some(&BarChoice {
                    top: Some("native"),
                    bottom: None
                })
            );
            assert_eq!(bars.get("bad.pkg"), None);
            assert!(load_pinned_prefs().is_empty());
            let density = load_density_prefs();
            assert_eq!(density.get("ok.pkg"), Some(&240));
            assert_eq!(density.get("huge.pkg"), None);
        });
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn flag_sections_share_one_doc_without_clobber() {
        let _guard = lock_home();
        let home = scratch("flags");
        with_home(&home, || {
            let mut audio = BTreeMap::new();
            audio.insert("a.b".into(), true);
            save_audio_prefs(&audio);
            let mut behavior = BTreeMap::new();
            behavior.insert("c.d".into(), true);
            save_behavior_prefs(&behavior);
            assert_eq!(load_audio_prefs().get("a.b"), Some(&true));
            assert_eq!(load_behavior_prefs().get("c.d"), Some(&true));
            let mut scale = BTreeMap::new();
            scale.insert("a.b".into(), 2.0);
            save_scale_prefs(&scale);
            assert_eq!(load_scale_prefs().get("a.b"), Some(&2.0));
            assert_eq!(load_audio_prefs().get("a.b"), Some(&true), "audio 节仍在");
        });
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn wireless_target_roundtrips_and_defaults_empty() {
        let _guard = lock_home();
        let home = scratch("wireless");
        with_home(&home, || {
            assert_eq!(load_wireless_target(), "");
            save_wireless_target("192.168.1.100:5555");
            assert_eq!(load_wireless_target(), "192.168.1.100:5555");
            // 与其他节共存不覆盖
            save_pinned_prefs(&["a.b".to_string()]);
            assert_eq!(load_wireless_target(), "192.168.1.100:5555");
            save_wireless_target("192.168.1.100:40135");
            assert_eq!(load_pinned_prefs(), vec!["a.b".to_string()]);
        });
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn device_names_roundtrip_and_clear() {
        let _guard = lock_home();
        let home = scratch("device-names");
        with_home(&home, || {
            assert!(load_device_names().is_empty());
            save_device_name("4444bd6b", "工作平板");
            save_device_name("192.168.1.100:5555", "客厅平板");
            let names = load_device_names();
            assert_eq!(names.get("4444bd6b").map(String::as_str), Some("工作平板"));
            assert_eq!(names.len(), 2);
            // 空白 = 清除；其他节不受影响
            save_device_name("4444bd6b", "  ");
            assert_eq!(load_device_names().len(), 1);
            save_wireless_target("192.168.1.100:5555");
            assert_eq!(load_device_names().len(), 1);
        });
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn wireless_recent_dedupes_caps_and_forgets() {
        let _guard = lock_home();
        let home = scratch("wireless-recent");
        with_home(&home, || {
            assert!(load_wireless_recent().is_empty());
            save_wireless_target("192.168.1.100:5555");
            save_wireless_target("192.168.1.101:5555");
            save_wireless_target("192.168.1.102:5555");
            // 重复连接前置，不占两格
            save_wireless_target("192.168.1.100:5555");
            let recent = load_wireless_recent();
            assert_eq!(recent[0], "192.168.1.100:5555");
            assert_eq!(recent.len(), 3);
            // 遗忘中间项；target 不受影响
            forget_wireless_target("192.168.1.101:5555");
            assert_eq!(load_wireless_recent().len(), 2);
            assert_eq!(load_wireless_target(), "192.168.1.100:5555");
            // 遗忘 target 本尊 → 回退到新队首
            forget_wireless_target("192.168.1.100:5555");
            assert_eq!(load_wireless_target(), "192.168.1.102:5555");
            // 超 5 个裁剪
            for i in 0..8 {
                save_wireless_target(&format!("10.0.0.{i}:5555"));
            }
            assert_eq!(load_wireless_recent().len(), 5);
        });
        let _ = std::fs::remove_dir_all(&home);
    }
}

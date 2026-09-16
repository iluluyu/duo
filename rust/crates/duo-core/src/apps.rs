//! 已装应用枚举：``pm``/``wm``/aapt2/badging 纯解析层。对译自
//! duo/core/apps.py；合同镜像 tests/test_apps.py。
//!
//! 图标合成留 Python 面板侧，见 duo/core/apps.py。

use std::collections::BTreeMap;
use std::io::Read;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use crate::catalog::{catalog_by_package, APP_CATALOG};

/// Python ``_RUN_TIMEOUT_S`` 合同：一次 ``pm list packages`` 往返的预算。
pub const PM_LIST_TIMEOUT_S: f64 = 60.0;

/// ``pm list packages [-3]`` 输出 -> 包名列表（排序）。两档（``-3`` 与
/// 全量）共用一份行式解析，对译 parse_package_list。
pub fn parse_package_list(packages_output: &str) -> Vec<String> {
    let mut names: Vec<String> = packages_output
        .lines()
        .map(str::trim)
        .filter_map(|line| line.strip_prefix("package:"))
        .map(str::to_string)
        .collect();
    names.sort();
    names
}

/// ``wm density`` 输出 -> 生效密度（Override 优先，缺则 Physical）。
/// 对译 parse_device_density：冒号后取全部数字拼位，无数字则忽略该行。
pub fn parse_device_density(wm_density_output: &str) -> Option<u32> {
    let mut override_density: Option<u32> = None;
    let mut physical: Option<u32> = None;
    for line in wm_density_output.lines() {
        let text = line.trim();
        let (key, rest) = match text.split_once(':') {
            Some(pair) => pair,
            None => continue,
        };
        let digits: String = rest.chars().filter(char::is_ascii_digit).collect();
        let Some(value) = digits.parse().ok() else {
            continue;
        };
        match key {
            "Override density" => override_density = Some(value),
            "Physical density" => physical = Some(value),
            _ => {}
        }
    }
    override_density.or(physical)
}

/// ``pm path <pkg>`` 输出 -> base.apk 设备路径（split-only 安装为 None）。
pub fn parse_base_apk_path(pm_path_output: &str) -> Option<&str> {
    pm_path_output
        .lines()
        .map(str::trim)
        .find(|line| line.starts_with("package:") && line.ends_with("base.apk"))
        .and_then(|line| line.strip_prefix("package:"))
        .map(str::trim)
}

/// ``cmd package resolve-activity --brief`` 输出 -> 可启动组件。
/// 最后一条非空、含 ``/`` 且无空格的行胜出；None = 无可启动项。
pub fn parse_resolve_activity(resolve_output: &str) -> Option<&str> {
    resolve_output
        .lines()
        .map(str::trim)
        .rfind(|line| !line.is_empty() && line.contains('/') && !line.contains(' '))
}

/// ``aapt2 dump badging`` 输出的四个感兴趣字段。
#[derive(Debug, Default, PartialEq, Eq)]
pub struct BadgingInfo {
    pub package: Option<String>,
    pub version_name: Option<String>,
    pub label: Option<String>,
    pub icon: Option<String>,
}

/// ``aapt2 dump badging`` 输出 -> 结构化字段；无可解析行时全 None
/// （Python 合同：返回空 dict 而不是抛异常）。
pub fn parse_badging(badging_output: &str) -> BadgingInfo {
    let mut info = BadgingInfo::default();
    for line in badging_output.lines() {
        if let Some(rest) = line.strip_prefix("package: ") {
            if info.package.is_none() {
                info.package = quoted_field(rest, "name=");
            }
            if info.version_name.is_none() {
                info.version_name = quoted_field(rest, "versionName=");
            }
        } else if info.label.is_none() && line.starts_with("application-label:'") {
            // Python 锚定行尾（`^application-label:'(.*)'$`）：无收尾
            // 引号的行不是标签；首个匹配胜出（re.search 语义）。
            let rest = line.trim_start_matches("application-label:");
            info.label = rest
                .strip_prefix('\'')
                .and_then(|v| v.strip_suffix('\''))
                .map(str::to_string);
        } else if let Some(rest) = line.strip_prefix("application: ") {
            info.icon = quoted_field(rest, "icon=");
        }
    }
    info
}

/// 形如 ``key='value'`` 的引号字段值；无引号、键缺失或空值 -> None
/// （Python 正则 ``(\S+)`` 不收空串）。
fn quoted_field(line: &str, key: &str) -> Option<String> {
    let start = line.find(key)? + key.len();
    let rest = &line[start..];
    let value = rest.strip_prefix('\'')?.split('\'').next()?;
    match value.is_empty() {
        true => None,
        false => Some(value.to_string()),
    }
}

/// 标签清洗：空白修剪后为空则回退包名（对译 app_info 的
/// ``label or package`` 兜底——渲染失败/未打标签的应用也有可读名）。
pub fn clean_label(label: &str, package: &str) -> String {
    let trimmed = label.trim();
    if trimmed.is_empty() {
        package.to_string()
    } else {
        trimmed.to_string()
    }
}

/// 渲染器 labels.txt 的单包元数据（kind/version/version_name/label）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RendererMeta {
    pub kind: String,
    pub version: String,
    pub version_name: String,
    pub label: String,
}

/// 渲染器 labels.txt -> 包名 -> 元数据；不足 5 列的行跳过。
/// 对译 parse_renderer_meta。
pub fn parse_renderer_meta(labels_text: &str) -> BTreeMap<String, RendererMeta> {
    let mut meta = BTreeMap::new();
    for line in labels_text.lines() {
        let mut parts = line.split('\t');
        let (Some(pkg), Some(kind), Some(version), Some(version_name), Some(label)) = (
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
        ) else {
            continue;
        };
        meta.insert(
            pkg.to_string(),
            RendererMeta {
                kind: kind.to_string(),
                version: version.to_string(),
                version_name: version_name.to_string(),
                label: label.to_string(),
            },
        );
    }
    meta
}

/// apps 子命令的一行输出：包名、清洗后的展示标签、是否目录收录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppRow {
    pub package: String,
    pub label: String,
    pub catalog: bool,
}

/// 已装包列表 + 目录合并：目录内应用按冻结播种序在前（catalog.rs 合同：
/// 顺序即网格播种序），其余按包名序殿后。标签取目录预设名，未收录回退
/// 清洗后的包名。
pub fn merge_catalog(packages: &[String]) -> Vec<AppRow> {
    let installed: std::collections::BTreeSet<&str> = packages.iter().map(String::as_str).collect();
    let mut rows: Vec<AppRow> = APP_CATALOG
        .iter()
        .filter(|preset| installed.contains(preset.package))
        .map(|preset| AppRow {
            package: preset.package.to_string(),
            label: preset.label.to_string(),
            catalog: true,
        })
        .collect();
    let extras: Vec<AppRow> = installed
        .iter()
        .filter(|package| catalog_by_package(package).is_none())
        .map(|package| AppRow {
            package: package.to_string(),
            label: clean_label("", package),
            catalog: false,
        })
        .collect();
    rows.extend(extras);
    rows
}

/// 一次 ``wm density`` 查询：生效密度（Override 优先）。失败/无数字回
/// None（调用方回退 160，对译 device_density 的容错语义）。
pub fn run_device_density(adb_binary: &str, serial: &str) -> Option<u32> {
    let output = std::process::Command::new(adb_binary)
        .args(["-s", serial, "shell", "wm", "density"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    parse_device_density(&String::from_utf8_lossy(&output.stdout))
}

/// 一次 ``pm list packages -3`` 查询 + 目录合并。失败（rc≠0/超时/无法
/// 启动）返回 Err——查询失败与"无应用"必须可区分（devices.rs 同合同）。
pub fn run_apps_query(adb_binary: &str, serial: &str) -> Result<Vec<AppRow>, String> {
    let mut child = Command::new(adb_binary)
        .args(["-s", serial, "shell", "pm", "list", "packages", "-3"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("adb pm list failed to launch: {e}"))?;
    let deadline = Instant::now() + Duration::from_secs_f64(PM_LIST_TIMEOUT_S);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("adb pm list timed out".into());
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(e) => return Err(format!("adb pm list wait failed: {e}")),
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
    let _ = child.wait();
    if !status.success() {
        let msg = String::from_utf8_lossy(if stderr_buf.is_empty() {
            &stdout_buf
        } else {
            &stderr_buf
        });
        return Err(format!(
            "adb pm list failed (rc={}): {}",
            status.code().unwrap_or(-1),
            msg.chars().take(120).collect::<String>()
        ));
    }
    Ok(merge_catalog(&parse_package_list(
        &String::from_utf8_lossy(&stdout_buf),
    )))
}

#[cfg(test)]
mod tests {
    use super::*;

    const PM_LIST_OUTPUT: &str = "package:com.android.chrome\npackage:cn.com.langeasy.LangEasyLexis\npackage:tv.danmaku.bili\n";

    const BADGING_OUTPUT: &str = "package: name='cn.com.langeasy.LangEasyLexis' versionCode='368' versionName='5.11.1' platformBuildVersionName='14'\napplication-label:'不背单词'\napplication-label-zh-CN:'不背单词'\napplication: label='不背单词' icon='res/mipmap-anydpi-v26/ic_launcher_app.xml'\nlaunchable-activity: name='cn.com.langeasy.LangEasyLexis.activity.SplashActivity'  label='' icon=''\n";

    #[test]
    fn parse_package_list_sorted() {
        // 去掉 package: 前缀、排序；-3 与全量输出同形，共用此解析。
        assert_eq!(
            parse_package_list(PM_LIST_OUTPUT),
            [
                "cn.com.langeasy.LangEasyLexis",
                "com.android.chrome",
                "tv.danmaku.bili",
            ]
        );
        assert!(parse_package_list("").is_empty());
        // 非 package: 行（stderr 噪声/空行）跳过。
        assert_eq!(
            parse_package_list("noise\n\npackage:a.b\n"),
            vec!["a.b".to_string()]
        );
    }

    #[test]
    fn parse_device_density_prefers_override() {
        // Override 是生效值（display zoom），Physical 只是兜底。
        assert_eq!(
            parse_device_density("Physical density: 420\nOverride density: 356"),
            Some(356)
        );
        assert_eq!(parse_device_density("Physical density: 420"), Some(420));
        assert_eq!(parse_device_density(""), None);
        // 乱序出现仍按优先级裁决；无数字行忽略。
        assert_eq!(
            parse_device_density("Override density: 356\nPhysical density: 420"),
            Some(356)
        );
        assert_eq!(parse_device_density("Physical density: ???"), None);
    }

    #[test]
    fn clean_label_trims_and_falls_back_to_package() {
        // app_info 合同：空标签/纯空白标签回退包名，其余仅修剪。
        assert_eq!(clean_label("  不背单词 ", "a.b"), "不背单词");
        assert_eq!(clean_label("", "a.b"), "a.b");
        assert_eq!(clean_label("  ", "a.b"), "a.b");
    }

    #[test]
    fn parse_base_apk_path_strips_prefix() {
        let out = "package:/data/app/~~pAeeM3oES5guBJhkOYjXAQ==/cn.com.langeasy.LangEasyLexis-6cGM_YvZ4qvNihlkr_4U7Q==/base.apk\n";
        let path = parse_base_apk_path(out).expect("base.apk path");
        assert!(path.starts_with("/data/app/"));
        assert!(path.ends_with("/base.apk"));
        // split-only 安装（无 base.apk）与空输出 -> None。
        assert_eq!(
            parse_base_apk_path("package:/data/app/x/split_config.arm64_v8a.apk\n"),
            None
        );
        assert_eq!(parse_base_apk_path(""), None);
    }

    #[test]
    fn parse_resolve_activity_extracts_component() {
        assert_eq!(
            parse_resolve_activity("\npkg.name/pkg.name.MainActivity\n"),
            Some("pkg.name/pkg.name.MainActivity")
        );
        assert_eq!(
            parse_resolve_activity("pkg.name/pkg.name.MainActivity"),
            Some("pkg.name/pkg.name.MainActivity")
        );
        assert_eq!(parse_resolve_activity(""), None);
        assert_eq!(parse_resolve_activity("\n\n"), None);
        // 带空格的行（日志噪声）不选。
        assert_eq!(
            parse_resolve_activity("this is noise\npkg/a.B"),
            Some("pkg/a.B")
        );
    }

    #[test]
    fn parse_badging_extracts_fields() {
        let info = parse_badging(BADGING_OUTPUT);
        assert_eq!(
            info.package.as_deref(),
            Some("cn.com.langeasy.LangEasyLexis")
        );
        assert_eq!(info.version_name.as_deref(), Some("5.11.1"));
        assert_eq!(info.label.as_deref(), Some("不背单词"));
        assert_eq!(
            info.icon.as_deref(),
            Some("res/mipmap-anydpi-v26/ic_launcher_app.xml")
        );
    }

    #[test]
    fn parse_badging_empty_output() {
        // 不可解析输出 -> 全 None（Python 返回空 dict），不抛异常。
        assert_eq!(parse_badging(""), BadgingInfo::default());
        assert_eq!(
            parse_badging("some random stderr noise"),
            BadgingInfo::default()
        );
    }

    #[test]
    fn parse_renderer_meta_tab_columns() {
        let meta = parse_renderer_meta("a.b\tadaptive\t368\t5.11.1\t不背单词\nshort\tline\n");
        assert_eq!(meta.len(), 1);
        let entry = &meta["a.b"];
        assert_eq!(entry.kind, "adaptive");
        assert_eq!(entry.version, "368");
        assert_eq!(entry.version_name, "5.11.1");
        assert_eq!(entry.label, "不背单词");
    }

    #[test]
    fn merge_catalog_seeds_frozen_order_then_extras() {
        // 目录内按冻结播种序在前（微信/哔哩哔哩），其余按包名序殿后。
        let packages: Vec<String> = ["tv.danmaku.bili", "com.android.chrome", "com.tencent.mm"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let rows = merge_catalog(&packages);
        let got: Vec<(&str, bool)> = rows
            .iter()
            .map(|r| (r.package.as_str(), r.catalog))
            .collect();
        assert_eq!(
            got,
            [
                ("com.tencent.mm", true),
                ("tv.danmaku.bili", true),
                ("com.android.chrome", false)
            ]
        );
        assert_eq!(rows[0].label, "微信");
        // 未收录应用标签回退包名（clean_label 兜底）。
        assert_eq!(rows[2].label, "com.android.chrome");
    }
}

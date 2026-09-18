//! 应用标签/图标 sweep（apps.py render_device_icons / read_device_meta 的
//! 运行时对译）：设备端渲染（duo_icons.dex 内嵌）→ pull → devicon 收尾
//! → icons/<pkg>.r20.png + device_meta.json。标签随 labels.txt 免费带回。
//! 本地 aapt2 慢路径留 Python 作对照（TODO §0）。

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::apps::{parse_renderer_meta, RendererMeta};
use crate::devicon::finish_device_icon;
use crate::quiet::quiet_command;

pub const ICON_CACHE_SUFFIX: &str = ".r20.png";
const DEX_BYTES: &[u8] = include_bytes!("../../../../pyduo/resources/duo_icons.dex");
const DEVICE_OUT: &str = "/data/local/tmp/duo_icons_out";
const DEVICE_LIST: &str = "/data/local/tmp/duo_icons_pkgs.txt";
const DEVICE_DEX: &str = "/data/local/tmp/duo_icons.dex";
/// >30 个待拉包走整目录一次 pull（Python 合同）。
const BULK_PULL_THRESHOLD: usize = 30;

/// adb 传输抽象（测试注入假实现）。
pub trait DeviceTransport {
    fn push(&mut self, local: &Path, remote: &str) -> Result<(), String>;
    fn pull(&mut self, remote: &str, local: &Path) -> Result<(), String>;
    fn shell(&mut self, command: &str) -> Result<String, String>;
}

pub struct AdbTransport {
    adb: String,
    serial: String,
}

impl AdbTransport {
    pub fn new(adb: &str, serial: &str) -> Self {
        Self {
            adb: adb.to_string(),
            serial: serial.to_string(),
        }
    }
}

impl DeviceTransport for AdbTransport {
    fn push(&mut self, local: &Path, remote: &str) -> Result<(), String> {
        run_adb(
            &self.adb,
            &self.serial,
            &["push", &local.display().to_string(), remote],
        )
    }

    fn pull(&mut self, remote: &str, local: &Path) -> Result<(), String> {
        run_adb(
            &self.adb,
            &self.serial,
            &["pull", remote, &local.display().to_string()],
        )
    }

    fn shell(&mut self, command: &str) -> Result<String, String> {
        run_adb_stdout(&self.adb, &self.serial, &["shell", command])
    }
}

fn run_adb(adb: &str, serial: &str, args: &[&str]) -> Result<(), String> {
    let mut argv: Vec<&str> = vec!["-s", serial];
    argv.extend_from_slice(args);
    let status = quiet_command(adb)
        .args(&argv)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map_err(|e| format!("adb failed to launch: {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "adb {} failed (rc={:?})",
            args.first().unwrap_or(&""),
            status.code()
        ))
    }
}

fn run_adb_stdout(adb: &str, serial: &str, args: &[&str]) -> Result<String, String> {
    let mut argv: Vec<&str> = vec!["-s", serial];
    argv.extend_from_slice(args);
    let output = quiet_command(adb)
        .args(&argv)
        .output()
        .map_err(|e| format!("adb failed to launch: {e}"))?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    } else {
        Err(format!("adb {} failed", args.first().unwrap_or(&"")))
    }
}

fn icon_cache_dir(cache_root: Option<&Path>) -> PathBuf {
    match cache_root {
        Some(root) => root.join("icons"),
        None => crate::paths::icons_dir(None),
    }
}

fn icon_cache_png(cache_root: Option<&Path>, package: &str) -> PathBuf {
    icon_cache_dir(cache_root).join(format!("{package}{ICON_CACHE_SUFFIX}"))
}

fn meta_path(cache_root: Option<&Path>) -> PathBuf {
    icon_cache_dir(cache_root).join("device_meta.json")
}

/// 已合并的渲染器元数据（缺失/损坏 → 空）。
pub fn read_device_meta(cache_root: Option<&Path>) -> BTreeMap<String, RendererMeta> {
    let Ok(raw) = std::fs::read_to_string(meta_path(cache_root)) else {
        return BTreeMap::new();
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return BTreeMap::new();
    };
    let Some(obj) = value.as_object() else {
        return BTreeMap::new();
    };
    let mut meta = BTreeMap::new();
    for (package, entry) in obj {
        let Some(entry) = entry.as_object() else {
            continue;
        };
        let get = |key: &str| {
            entry
                .get(key)
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string()
        };
        meta.insert(
            package.clone(),
            RendererMeta {
                kind: get("kind"),
                version: get("version"),
                version_name: get("version_name"),
                label: get("label"),
            },
        );
    }
    meta
}

fn write_device_meta(cache_root: Option<&Path>, meta: &BTreeMap<String, RendererMeta>) {
    let obj: serde_json::Map<String, serde_json::Value> = meta
        .iter()
        .map(|(package, entry)| {
            (
                package.clone(),
                serde_json::json!({
                    "kind": entry.kind,
                    "version": entry.version,
                    "version_name": entry.version_name,
                    "label": entry.label,
                }),
            )
        })
        .collect();
    let _ = std::fs::write(
        meta_path(cache_root),
        serde_json::to_string(&serde_json::Value::Object(obj)).unwrap_or_default(),
    );
}

/// sweep 结果：渲染是否可用 + 每包标签（设备端 label，供面板直接采用）。
#[derive(Debug, Clone, PartialEq)]
pub struct SweepOutcome {
    pub rendered: bool,
    pub labels: BTreeMap<String, String>,
}

fn stale(entry: Option<&RendererMeta>, old: Option<&RendererMeta>, png_exists: bool) -> bool {
    match entry {
        Some(entry) if entry.kind != "error" => match old {
            None => true,
            Some(old) => old.version != entry.version || !png_exists,
        },
        _ => false,
    }
}

/// 本地图标后处理管线版本（蒙版/缩放等本地重绘逻辑变更时 +1）：
/// 设备端渲染器版本只覆盖 dex 变化，本地管线升级后旧缓存靠它判陈。
/// sidecar 不符 → 全量 stale → 重拉重绘。
const ICON_PIPELINE_VERSION: &str = "2-mask-feather";

fn pipeline_stale(cache_root: Option<&Path>) -> bool {
    let path = icon_cache_dir(cache_root).join("pipeline.txt");
    std::fs::read_to_string(path).ok().as_deref() != Some(ICON_PIPELINE_VERSION)
}

/// 设备端批量渲染 + 本地收尾（对译 render_device_icons）。False = 渲染器
/// 不可用/传输失败（面板回退目录标签 + 预设图标，绝不拦启动）。
pub fn render_device_icons(
    transport: &mut dyn DeviceTransport,
    packages: &[String],
    cache_root: Option<&Path>,
) -> SweepOutcome {
    let empty = BTreeMap::new();
    let outcome = |labels: BTreeMap<String, String>| SweepOutcome {
        rendered: false,
        labels,
    };
    if packages.is_empty() {
        return outcome(empty);
    }
    let cache_dir = icon_cache_dir(cache_root);
    if std::fs::create_dir_all(&cache_dir).is_err() {
        return outcome(empty);
    }
    let existing = read_device_meta(cache_root);
    let pipeline_dirty = pipeline_stale(cache_root);
    // 每次调用独立工作目录（同进程并发 sweep 互不踩踏）。
    static SWEEP_N: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
    let work = std::env::temp_dir().join(format!(
        "duo-sweep-{}-{}",
        std::process::id(),
        SWEEP_N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
    ));
    let _ = std::fs::remove_dir_all(&work);
    if std::fs::create_dir_all(&work).is_err() {
        return outcome(empty);
    }
    let cleanup = |work: &Path| {
        let _ = std::fs::remove_dir_all(work);
    };
    let dex = work.join("duo_icons.dex");
    if std::fs::write(&dex, DEX_BYTES).is_err() {
        cleanup(&work);
        return outcome(empty);
    }
    let listing = work.join("pkgs.txt");
    let text = format!("{}\n", packages.join("\n"));
    if std::fs::write(&listing, text).is_err() {
        cleanup(&work);
        return outcome(empty);
    }
    if transport.push(&dex, DEVICE_DEX).is_err()
        || transport.push(&listing, DEVICE_LIST).is_err()
        || transport
            .shell(&format!(
                "rm -rf {DEVICE_OUT}; CLASSPATH={DEVICE_DEX} app_process / DuoIconRenderer {DEVICE_LIST} {DEVICE_OUT}"
            ))
            .is_err()
    {
        cleanup(&work);
        return outcome(empty);
    }
    let labels_file = work.join("labels.txt");
    if transport
        .pull(&format!("{DEVICE_OUT}/labels.txt"), &labels_file)
        .is_err()
    {
        cleanup(&work);
        return outcome(empty);
    }
    let Ok(labels_text) = std::fs::read_to_string(&labels_file) else {
        cleanup(&work);
        return outcome(empty);
    };
    let meta = parse_renderer_meta(&labels_text);
    let pending: Vec<&String> = packages
        .iter()
        .filter(|package| {
            pipeline_dirty
                || stale(
                    meta.get(*package),
                    existing.get(*package),
                    icon_cache_png(cache_root, package).is_file(),
                )
        })
        .collect();
    let labels: BTreeMap<String, String> = meta
        .iter()
        .filter(|(_, entry)| entry.kind != "error" && !entry.label.is_empty())
        .map(|(package, entry)| (package.clone(), entry.label.clone()))
        .collect();
    if pending.is_empty() {
        cleanup(&work);
        return SweepOutcome {
            rendered: true,
            labels,
        };
    }
    let out_dir = work.join("out");
    let pulled = if pending.len() > BULK_PULL_THRESHOLD {
        transport.pull(DEVICE_OUT, &out_dir).is_ok()
    } else if std::fs::create_dir_all(&out_dir).is_ok() {
        // 单文件缺失（legacy 渲染无分层）容忍：逐个尽力拉。
        for package in &pending {
            for suffix in ["", ".fg", ".bg"] {
                let remote = format!("{DEVICE_OUT}/{package}{suffix}.png");
                let local = out_dir.join(format!("{package}{suffix}.png"));
                let _ = transport.pull(&remote, &local);
            }
        }
        true
    } else {
        false
    };
    if !pulled {
        cleanup(&work);
        return outcome(labels.clone());
    }
    let mut merged = existing.clone();
    for package in &pending {
        let Some(entry) = meta.get(package.as_str()) else {
            continue;
        };
        if entry.kind == "error" {
            merged.insert((*package).clone(), entry.clone());
            continue;
        }
        let png = out_dir.join(format!("{package}.png"));
        let layers = if entry.kind == "adaptive" {
            let fg = out_dir.join(format!("{package}.fg.png"));
            let bg = out_dir.join(format!("{package}.bg.png"));
            match (std::fs::read(&fg), std::fs::read(&bg)) {
                (Ok(fg), Ok(bg)) => Some((fg, bg)),
                _ => None,
            }
        } else {
            None
        };
        if let Ok(raw) = std::fs::read(&png) {
            let fg_slice = layers.as_ref().map(|(fg, _)| fg.as_slice());
            let bg_slice = layers.as_ref().map(|(_, bg)| bg.as_slice());
            let layers_ref = fg_slice.zip(bg_slice);
            if let Ok(finished) = finish_device_icon(&raw, &entry.kind, layers_ref) {
                let _ = std::fs::write(icon_cache_png(cache_root, package), finished);
            }
        }
        merged.insert((*package).clone(), entry.clone());
    }
    write_device_meta(cache_root, &merged);
    // 管线版本钉在成功收尾后：中途失败下次仍判陈重拉。
    let _ = std::fs::write(
        icon_cache_dir(cache_root).join("pipeline.txt"),
        ICON_PIPELINE_VERSION,
    );
    cleanup(&work);
    SweepOutcome {
        rendered: true,
        labels,
    }
}

/// 面板查单包：设备渲染标签 + 收尾图标缓存路径（None = 未渲染/无缓存）。
pub fn device_app_info(
    package: &str,
    cache_root: Option<&Path>,
) -> Option<(String, String, PathBuf)> {
    let meta = read_device_meta(cache_root);
    let entry = meta.get(package)?;
    if entry.kind.is_empty() {
        return None;
    }
    let png = icon_cache_png(cache_root, package);
    if !png.is_file() {
        return None;
    }
    Some((entry.label.clone(), entry.version_name.clone(), png))
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::ImageEncoder;
    use std::cell::RefCell;
    use std::sync::atomic::{AtomicU32, Ordering};

    fn artwork_png() -> Vec<u8> {
        let img = image::RgbaImage::from_pixel(432, 432, image::Rgba([18, 184, 104, 255]));
        let mut bytes = Vec::new();
        image::codecs::png::PngEncoder::new(&mut bytes)
            .write_image(img.as_raw(), 432, 432, image::ExtendedColorType::Rgba8)
            .unwrap();
        bytes
    }

    struct FakeAdb {
        packages_pushed: RefCell<Vec<String>>,
        pulled: RefCell<Vec<String>>,
    }

    impl FakeAdb {
        fn new() -> Self {
            Self {
                packages_pushed: RefCell::new(Vec::new()),
                pulled: RefCell::new(Vec::new()),
            }
        }
    }

    impl DeviceTransport for FakeAdb {
        fn push(&mut self, local: &Path, remote: &str) -> Result<(), String> {
            if remote.ends_with("pkgs.txt") {
                let text = std::fs::read_to_string(local).unwrap_or_default();
                *self.packages_pushed.borrow_mut() =
                    text.split_whitespace().map(str::to_string).collect();
            }
            Ok(())
        }

        fn pull(&mut self, remote: &str, local: &Path) -> Result<(), String> {
            self.pulled.borrow_mut().push(remote.to_string());
            if let Some(parent) = local.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            if remote.ends_with("labels.txt") {
                let _ = std::fs::write(
                    local,
                    "a.b.c\tadaptive\t42\t9.9\tApp C\nx.y\terror\t0\t?\tBad\n",
                );
                return Ok(());
            }
            if remote.ends_with("a.b.c.png") {
                let _ = std::fs::write(local, artwork_png());
            }
            Ok(())
        }

        fn shell(&mut self, _command: &str) -> Result<String, String> {
            Ok(String::new())
        }
    }

    fn tmp(tag: &str) -> PathBuf {
        static N: AtomicU32 = AtomicU32::new(0);
        let dir = std::env::temp_dir().join(format!(
            "duo-sweep-{tag}-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn renders_caches_and_feeds_app_info() {
        let root = tmp("full");
        let mut adb = FakeAdb::new();
        let packages = vec!["a.b.c".to_string(), "x.y".to_string()];
        let outcome = render_device_icons(&mut adb, &packages, Some(&root));
        assert!(outcome.rendered);
        assert_eq!(
            outcome.labels.get("a.b.c").map(String::as_str),
            Some("App C")
        );
        assert!(!outcome.labels.contains_key("x.y"), "error 行无标签");
        let cached = icon_cache_png(Some(&root), "a.b.c");
        assert!(cached.is_file());
        let img = image::open(&cached).unwrap().to_rgba8();
        assert_eq!(img.dimensions(), (288, 288));
        let (label, version, png) = device_app_info("a.b.c", Some(&root)).unwrap();
        assert_eq!(label, "App C");
        assert_eq!(version, "9.9");
        assert_eq!(png, cached);
        assert!(device_app_info("missing.pkg", Some(&root)).is_none());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn warm_cache_pulls_no_package_files() {
        let root = tmp("warm");
        let mut adb = FakeAdb::new();
        let packages = vec!["a.b.c".to_string()];
        assert!(render_device_icons(&mut adb, &packages, Some(&root)).rendered);
        let cached = icon_cache_png(Some(&root), "a.b.c");
        let first_meta = std::fs::read_to_string(meta_path(Some(&root))).unwrap();
        let stamp = cached.metadata().unwrap().modified().unwrap();

        let mut second = FakeAdb::new();
        let outcome = render_device_icons(&mut second, &packages, Some(&root));
        assert!(outcome.rendered);
        let pkg_pulls = second
            .pulled
            .borrow()
            .iter()
            .filter(|r| r.ends_with(".png"))
            .count();
        assert_eq!(pkg_pulls, 0, "warm cache pulls no package PNGs");
        assert_eq!(
            second.pulled.borrow().len(),
            1,
            "labels.txt only, got {:?}",
            second.pulled.borrow()
        );
        assert_eq!(cached.metadata().unwrap().modified().unwrap(), stamp);
        assert_eq!(
            std::fs::read_to_string(meta_path(Some(&root))).unwrap(),
            first_meta
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn transport_failure_degrades_to_false() {
        struct DeadAdb;
        impl DeviceTransport for DeadAdb {
            fn push(&mut self, _: &Path, _: &str) -> Result<(), String> {
                Err("no device".into())
            }
            fn pull(&mut self, _: &str, _: &Path) -> Result<(), String> {
                Err("no device".into())
            }
            fn shell(&mut self, _: &str) -> Result<String, String> {
                Err("no device".into())
            }
        }
        let root = tmp("dead");
        let mut dead = DeadAdb;
        let outcome = render_device_icons(&mut dead, &["a.b".to_string()], Some(&root));
        assert!(!outcome.rendered);
        assert!(outcome.labels.is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn version_bump_re_renders() {
        let root = tmp("bump");
        let mut adb = FakeAdb::new();
        assert!(render_device_icons(&mut adb, &["a.b.c".to_string()], Some(&root)).rendered);
        // 篡改缓存版本 → stale → 重新拉取。
        let mut meta = read_device_meta(Some(&root));
        meta.get_mut("a.b.c").unwrap().version = "43".into();
        write_device_meta(Some(&root), &meta);
        std::fs::remove_file(icon_cache_png(Some(&root), "a.b.c")).unwrap();
        let mut again = FakeAdb::new();
        let outcome = render_device_icons(&mut again, &["a.b.c".to_string()], Some(&root));
        assert!(outcome.rendered);
        let pkg_pulls = again
            .pulled
            .borrow()
            .iter()
            .filter(|r| r.ends_with(".png"))
            .count();
        assert!(pkg_pulls >= 1, "version bump re-pulls the artwork");
        assert!(icon_cache_png(Some(&root), "a.b.c").is_file());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn embedded_dex_is_the_renderer() {
        assert_eq!(&DEX_BYTES[..4], b"dex\n", "embedded payload is a dex");
        assert!(DEX_BYTES.len() > 1024);
    }
}

//! 应用网格模型（controller.py _rebuild_apps/_merge_all_apps/_sort_apps/
//! _apply_app_info 对译）。网格只显示设备上真实安装的应用；固定行与网格
//! 共享同一份数据（置顶 = 快捷方式非移动）；扫描期顺序冻结、sweep 收尾
//! 一次落位（磁贴不漂移）。

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use duo_core::catalog::APP_CATALOG;
use duo_core::sweep::{device_app_info, ICON_CACHE_SUFFIX};

use crate::pinyin::label_sort_key;
use crate::sessions::package_to_label;

/// 一颗磁贴。
#[derive(Debug, Clone, PartialEq)]
pub struct AppEntry {
    pub package: String,
    pub label: String,
    /// 排序键（拼音首字母；构造/标签 patch 时算好，比较零重算）。
    pub key: String,
    /// 图标路径：sweep 缓存 .r20.png > 预设 SVG > None（品牌色块兜底）。
    pub icon: Option<PathBuf>,
    pub pinned: bool,
}

impl AppEntry {
    fn fresh(package: &str, label: &str, pinned: bool) -> Self {
        Self {
            package: package.to_string(),
            label: label.to_string(),
            key: label_sort_key(label),
            icon: preset_icon(package),
            pinned,
        }
    }
}

fn preset_icon(package: &str) -> Option<PathBuf> {
    duo_core::icons::preset_icon_path(&duo_core::paths::icons_dir(None), package)
}

/// 网格模型（grid + pinned 同源）。
#[derive(Debug, Default)]
pub struct AppsModel {
    pub apps: Vec<AppEntry>,
}

impl AppsModel {
    pub fn pinned(&self) -> Vec<&AppEntry> {
        self.apps.iter().filter(|e| e.pinned).collect()
    }

    fn sort(&mut self) {
        self.apps
            .sort_by(|a, b| (&a.key, &a.label, &a.package).cmp(&(&b.key, &b.label, &b.package)));
    }

    /// 已装集合重建（幸存者保住已解析的图标/标签；卸载即消失）。
    pub fn rebuild(&mut self, installed: &[String], pinned: &BTreeMap<String, bool>) {
        let previous: BTreeMap<String, AppEntry> = self
            .apps
            .drain(..)
            .map(|e| (e.package.clone(), e))
            .collect();
        let mut entries = Vec::new();
        let installed_set: std::collections::BTreeSet<&str> =
            installed.iter().map(String::as_str).collect();
        for preset in APP_CATALOG {
            let package = preset.package;
            if !installed_set.contains(package) {
                continue;
            }
            let mut entry = previous
                .get(package)
                .cloned()
                .unwrap_or_else(|| AppEntry::fresh(package, preset.label, false));
            entry.pinned = pinned.get(package).copied().unwrap_or(false);
            entries.push(entry);
        }
        for (package, mut entry) in previous {
            if !installed_set.contains(package.as_str()) {
                continue;
            }
            entry.pinned = pinned.get(&package).copied().unwrap_or(false);
            entries.push(entry);
        }
        self.apps = entries;
        self.sort();
    }

    /// 第三方包合并（-3 listing 即已装集合）。
    pub fn merge_third_party(&mut self, packages: &[String], pinned: &BTreeMap<String, bool>) {
        let known: std::collections::BTreeSet<String> =
            self.apps.iter().map(|e| e.package.clone()).collect();
        let mut fresh = false;
        for package in packages {
            if known.contains(package) {
                continue;
            }
            let label = package_to_label(package);
            self.apps.push(AppEntry::fresh(
                package,
                &label,
                pinned.get(package).copied().unwrap_or(false),
            ));
            fresh = true;
        }
        if fresh {
            self.sort();
        }
    }

    /// sweep 元数据落地（标签 + 设备图标）。返回被 patch 的包集合。
    pub fn apply_sweep(&mut self, labels: &BTreeMap<String, String>) -> Vec<String> {
        let mut patched = Vec::new();
        for entry in &mut self.apps {
            let mut dirty = false;
            if let Some(label) = labels.get(&entry.package) {
                if !label.is_empty() && *label != entry.label {
                    entry.label = label.clone();
                    entry.key = label_sort_key(label);
                    dirty = true;
                }
            }
            if entry.icon.is_none()
                || !matches!(&entry.icon, Some(p) if p.file_name()
                    .map(|n| n.to_string_lossy().ends_with(ICON_CACHE_SUFFIX))
                    .unwrap_or(false))
            {
                if let Some((_, _, png)) = device_app_info(&entry.package, None) {
                    entry.icon = Some(png);
                    dirty = true;
                }
            }
            if dirty {
                patched.push(entry.package.clone());
            }
        }
        self.sort();
        patched
    }

    /// 搜索过滤（拼音首字母前缀 + 标签原文不区分大小写包含）。
    pub fn search<'a>(&'a self, query: &str) -> Vec<&'a AppEntry> {
        let needle = query.trim().to_lowercase();
        self.apps
            .iter()
            .filter(|e| {
                needle.is_empty()
                    || e.key.contains(&needle)
                    || e.label.to_lowercase().contains(&needle)
                    || e.package.to_lowercase().contains(&needle)
            })
            .collect()
    }

    pub fn entry(&self, package: &str) -> Option<&AppEntry> {
        self.apps.iter().find(|e| e.package == package)
    }
}

/// 图标缓存路径（磁贴渲染层用：None → 品牌色块）。
pub fn icon_of(entry: &AppEntry) -> Option<&Path> {
    entry.icon.as_deref()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pinned_map(list: &[(&str, bool)]) -> BTreeMap<String, bool> {
        list.iter().map(|(k, v)| (k.to_string(), *v)).collect()
    }

    #[test]
    fn rebuild_filters_uninstalled_and_sorts() {
        let mut model = AppsModel::default();
        model.rebuild(
            &["tv.danmaku.bili".to_string(), "com.tencent.mm".to_string()],
            &BTreeMap::new(),
        );
        let packages: Vec<&str> = model.apps.iter().map(|e| e.package.as_str()).collect();
        assert_eq!(
            packages,
            ["tv.danmaku.bili", "com.tencent.mm"],
            "拼音序 blbl < wx"
        );
        assert!(
            model.entry("cn.com.langeasy.LangEasyLexis").is_none(),
            "未安装不铺灰块"
        );
    }

    #[test]
    fn pinned_row_shares_grid_entries() {
        let mut model = AppsModel::default();
        let pinned = pinned_map(&[("tv.danmaku.bili", true)]);
        model.rebuild(
            &["com.tencent.mm".to_string(), "tv.danmaku.bili".to_string()],
            &pinned,
        );
        assert_eq!(model.pinned().len(), 1);
        assert_eq!(model.pinned()[0].package, "tv.danmaku.bili");
        assert_eq!(model.apps.len(), 2, "置顶不移出网格");
    }

    #[test]
    fn merge_third_party_adds_uncataloged_sorted() {
        let mut model = AppsModel::default();
        model.rebuild(&["com.tencent.mm".to_string()], &BTreeMap::new());
        model.merge_third_party(
            &["zz.last.app".to_string(), "com.android.chrome".to_string()],
            &BTreeMap::new(),
        );
        let labels: Vec<&str> = model.apps.iter().map(|e| e.label.as_str()).collect();
        assert_eq!(
            labels,
            ["App", "Chrome", "微信"],
            "拼音序混合排序（tail 大写）"
        );
    }

    #[test]
    fn apply_sweep_relabels_and_resorts_once() {
        let mut model = AppsModel::default();
        model.rebuild(&["tv.danmaku.bili".to_string()], &BTreeMap::new());
        let mut labels = BTreeMap::new();
        labels.insert("tv.danmaku.bili".to_string(), "阿B站".to_string());
        let patched = model.apply_sweep(&labels);
        assert_eq!(patched, vec!["tv.danmaku.bili".to_string()]);
        assert_eq!(model.apps[0].label, "阿B站");
        assert!(model.apps[0].key.starts_with('a'), "键随标签重算");
    }

    #[test]
    fn search_matches_initial_prefix_and_label() {
        let mut model = AppsModel::default();
        model.rebuild(
            &["com.tencent.mm".to_string(), "tv.danmaku.bili".to_string()],
            &BTreeMap::new(),
        );
        assert_eq!(model.search("wx").len(), 1, "拼音首字母命中微信");
        assert_eq!(model.search("bili").len(), 1);
        assert_eq!(model.search("danmaku").len(), 1, "包名包含");
        assert_eq!(model.search("").len(), 2);
    }

    #[test]
    fn fresh_entry_gets_preset_icon_when_available() {
        let entry = AppEntry::fresh("com.tencent.mm", "微信", false);
        assert!(entry.icon.is_some(), "预设 SVG 落盘即有图标");
        let entry = AppEntry::fresh("no.such.pkg", "X", false);
        assert!(entry.icon.is_none());
    }
}

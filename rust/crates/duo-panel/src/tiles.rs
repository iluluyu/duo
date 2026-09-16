//! 应用网格的纯渲染数据（UI 与渲染分离：本模块零 egui 依赖）。
//!
//! 目录来自 duo-core `catalog::APP_CATALOG`（顺序是合同：网格播种序）。
//! 图标 = icons::preset_icon_path 落盘的 SVG（首调渲染缓存）；无目录
//! 条目 → None（渲染层回退品牌色块 + 单字）。

use std::path::{Path, PathBuf};

use duo_core::catalog::{catalog_by_package, AppPreset, APP_CATALOG};

/// 一颗网格磁贴需要的全部数据。
#[derive(Debug, Clone, PartialEq)]
pub struct TileData {
    pub label: String,
    pub package: String,
    /// 品牌色 #RRGGBB（SVG 加载失败时的色块底色）。
    pub color_hex: String,
    /// 单字（fallback 色块上的字形；亮底品牌色用深墨）。
    pub glyph: String,
    pub glyph_ink: bool,
    /// 预设 SVG 的 file:// URI（无目录条目为 None）。
    pub icon_uri: Option<String>,
}

/// 预设图标 URI（`file://` + preset_icon_path 落盘路径）。
pub fn tile_icon_uri(base: Option<&Path>, package: &str) -> Option<String> {
    let owned;
    let dir: &Path = match base {
        Some(p) => p,
        None => {
            owned = duo_core::paths::data_dir(None);
            &owned
        }
    };
    preset_icon_path_cached(dir, package).map(|p| format!("file://{}", p.display()))
}

fn preset_icon_path_cached(base: &Path, package: &str) -> Option<PathBuf> {
    duo_core::icons::preset_icon_path(base, package)
}

/// 全量网格数据（APP_CATALOG 顺序）。
pub fn tiles(base: Option<&Path>) -> Vec<TileData> {
    APP_CATALOG.iter().map(|p| tile_of(p, base)).collect()
}

fn tile_of(preset: &AppPreset, base: Option<&Path>) -> TileData {
    TileData {
        label: preset.label.into(),
        package: preset.package.into(),
        color_hex: preset.color.into(),
        glyph: preset.glyph.into(),
        glyph_ink: preset.glyph_ink,
        icon_uri: tile_icon_uri(base, preset.package),
    }
}

/// 目录里第一条匹配包名的磁贴（点击会话回查用）。
pub fn tile_for_package(base: Option<&Path>, package: &str) -> Option<TileData> {
    let preset = catalog_by_package(package)?;
    Some(tile_of(&preset, base))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "duo-panel-tiles-{tag}-{}-{}",
            std::process::id(),
            tag
        ));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn tiles_mirror_catalog_order_and_count() {
        let base = tmp("order");
        let list = tiles(Some(&base));
        assert_eq!(list.len(), APP_CATALOG.len());
        assert_eq!(list[0].label, "微信");
        assert_eq!(list[0].package, "com.tencent.mm");
        assert_eq!(list[0].color_hex, "#07C160");
        assert_eq!(list[0].glyph, "微");
        assert!(!list[0].glyph_ink);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn icon_uri_points_at_cached_svg() {
        let base = tmp("icon");
        let uri = tile_icon_uri(Some(&base), "com.tencent.mm");
        assert!(uri.unwrap().ends_with("com.tencent.mm.v6.svg"));
        // 未收录包：无 URI（渲染层走色块 fallback）。
        assert!(tile_icon_uri(Some(&base), "no.such.app").is_none());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn light_brand_uses_ink_glyph() {
        // 不背单词 #F5A623 亮底 → 深墨字形。
        let base = tmp("ink");
        let tile = tile_for_package(Some(&base), "cn.com.langeasy.LangEasyLexis").unwrap();
        assert!(tile.glyph_ink);
        assert_eq!(tile.glyph, "不");
        let _ = std::fs::remove_dir_all(&base);
    }
}

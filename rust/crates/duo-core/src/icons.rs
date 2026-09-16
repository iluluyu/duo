//! 预设应用图标的 SVG 生成（品牌 squircle + 单字）与磁盘缓存。
//! 对译自 duo/core/icon_presets.py + apps.py 的 g2_outline；
//! 合同镜像 tests/test_icon_presets.py。

use std::path::{Path, PathBuf};

use crate::catalog::{catalog_by_package, AppPreset};

/// 模板修订号——SVG 模板变更时递增，缓存文件（带版本后缀）永不回供旧形状。
const TEMPLATE_VERSION: u32 = 6;

/// #RRGGBB 向白色混合：0 = 原色，1 = 纯白（平铺顶部渐变端的“上方打光”）。
pub fn lighten(hex_color: &str, fraction: f64) -> String {
    let b = hex_color.as_bytes();
    let channel =
        |i: usize| u16::from_str_radix(std::str::from_utf8(&b[i..i + 2]).unwrap(), 16).unwrap();
    let toward_white = |c: u16| crate::py_round(c as f64 + (255.0 - c as f64) * fraction) as u16;
    format!(
        "#{:02X}{:02X}{:02X}",
        toward_white(channel(1)),
        toward_white(channel(3)),
        toward_white(channel(5))
    )
}

/// G2 平滑角矩形的轮廓点（apps.py 同款：角内 |u/r|^n + |v/r|^n = 1，
/// n=5，与直边零曲率衔接；顺时针，从左边上顶左角之下开始）。
/// 预设平铺必须与真实抽取图标共用同一蒙版形状。
pub fn g2_outline(width: f64, height: f64, radius: f64, exponent: f64) -> Vec<(f64, f64)> {
    let r = radius.min(width / 2.0).min(height / 2.0);
    let quarter = |cx: f64, cy: f64, sx: f64, sy: f64, swap: bool| -> Vec<(f64, f64)> {
        let steps = 96;
        let mut points = Vec::with_capacity(steps + 1);
        for i in 0..=steps {
            let theta = std::f64::consts::FRAC_PI_2 * i as f64 / steps as f64;
            let mut c = theta.cos().powf(2.0 / exponent);
            let mut s = theta.sin().powf(2.0 / exponent);
            if swap {
                std::mem::swap(&mut c, &mut s);
            }
            points.push((cx + sx * r * c, cy + sy * r * s));
        }
        points
    };
    let mut outline: Vec<(f64, f64)> = Vec::new();
    outline.extend(quarter(r, r, -1.0, -1.0, false));
    outline.push((width - r, 0.0));
    outline.extend(quarter(width - r, r, 1.0, -1.0, true));
    outline.push((width, height - r));
    outline.extend(quarter(width - r, height - r, 1.0, 1.0, false));
    outline.push((r, height));
    outline.extend(quarter(r, height - r, -1.0, 1.0, true));
    outline.push((0.0, r));
    outline
}

fn g2_squircle_path(size: i64, radius: i64) -> String {
    let points = g2_outline(size as f64, size as f64, radius as f64, 5.0);
    let mut path = format!("M{:.2} {:.2}", points[0].0, points[0].1);
    for (x, y) in &points[1..] {
        path.push_str(&format!(" L{x:.2} {y:.2}"));
    }
    path.push_str(" Z");
    path
}

fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            _ => out.push(c),
        }
    }
    out
}

/// 字母基线让单字在 y=30 处光学居中。CJK 全高字形与无尾大写 ≈ 中线
/// +0.36em；独立小写字母按 x 字高（37）；Q 的尾巴拖低包围盒（38）。
fn glyph_baseline(glyph: &str) -> i64 {
    if glyph.is_ascii() && glyph.chars().all(|c| c.is_ascii_lowercase()) {
        return 37;
    } else if glyph == "Q" {
        return 38;
    }
    40
}

/// 一条目录预设的 60×60 squircle 平铺 SVG（竖向渐变 + 居中单字，
/// 亮品牌色深字、否则白字；无描边）。
pub fn render_preset_svg(preset: &AppPreset) -> String {
    let gradient_top = lighten(preset.color, 0.08);
    let ink = if preset.glyph_ink {
        "#1D1D1F"
    } else {
        "#FFFFFF"
    };
    let baseline = glyph_baseline(preset.glyph);
    format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 60 60\">\n<defs><linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">\n<stop offset=\"0\" stop-color=\"{gradient_top}\"/><stop offset=\"1\" stop-color=\"{color}\"/>\n</linearGradient></defs>\n<path d=\"{path}\" fill=\"url(#bg)\"/>\n<text x=\"30\" y=\"{baseline}\" text-anchor=\"middle\"\n      font-family=\"Segoe UI, PingFang SC, Microsoft YaHei, sans-serif\"\n      font-size=\"28\" font-weight=\"600\" fill=\"{ink}\">{glyph}</text>\n</svg>\n",
        gradient_top = gradient_top,
        color = preset.color,
        path = g2_squircle_path(60, 30),
        baseline = baseline,
        ink = ink,
        glyph = escape(preset.glyph),
    )
}

/// 目录包的缓存 SVG 路径（``base/presets/<pkg>.vN.svg``），未收录返回
/// None。首次调用渲染落盘，之后原样返回。
pub fn preset_icon_path(base: &Path, package: &str) -> Option<PathBuf> {
    let preset = catalog_by_package(package)?;
    let dir = base.join("presets");
    let target = dir.join(format!("{package}.v{TEMPLATE_VERSION}.svg"));
    if !target.exists() {
        std::fs::create_dir_all(&dir).ok()?;
        std::fs::write(&target, render_preset_svg(preset)).ok()?;
    }
    Some(target)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::APP_CATALOG;

    #[test]
    fn lighten_blends_each_channel_toward_white() {
        assert_eq!(lighten("#000000", 0.5), "#808080");
        assert_eq!(lighten("#07C160", 0.08), "#1BC66D");
        assert_eq!(lighten("#FF6A00", 0.0), "#FF6A00");
        assert_eq!(lighten("#404040", 1.0), "#FFFFFF");
    }

    #[test]
    fn render_contains_gradient_stops_and_glyph() {
        let svg = render_preset_svg(catalog_by_package("com.tencent.mm").unwrap());
        assert!(svg.trim_start().starts_with("<svg"));
        assert!(svg.trim_end().ends_with("</svg>"));
        assert_eq!(svg.matches("stop-color=").count(), 2);
        assert!(svg.contains(">微</text>"));
        assert!(svg.contains("viewBox=\"0 0 60 60\""));
        assert!(svg.contains("M0.00 30.00"));
        assert!(svg.contains("fill=\"url(#bg)\""));
    }

    #[test]
    fn gradient_uses_lightened_top_and_brand_bottom() {
        let preset = catalog_by_package("tv.danmaku.bili").unwrap();
        let svg = render_preset_svg(preset);
        assert!(svg.contains(&format!("stop-color=\"{}\"", lighten(preset.color, 0.08))));
        assert!(svg.contains(&format!("stop-color=\"{}\"", preset.color)));
    }

    #[test]
    fn glyph_ink_flag_selects_dark_vs_white_fill() {
        let dark = render_preset_svg(catalog_by_package("com.sankuai.meituan").unwrap());
        let white = render_preset_svg(catalog_by_package("com.tencent.mm").unwrap());
        assert!(dark.contains("fill=\"#1D1D1F\""));
        assert!(!white.contains("fill=\"#1D1D1F\""));
        assert!(white.contains("fill=\"#FFFFFF\""));
    }

    #[test]
    fn render_every_catalog_entry() {
        for preset in APP_CATALOG {
            let svg = render_preset_svg(preset);
            assert!(svg.contains(preset.glyph), "{}", preset.package);
        }
    }

    #[test]
    fn preset_icon_path_writes_then_caches() {
        let base = std::env::temp_dir().join(format!("duo-core-icons-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let path = preset_icon_path(&base, "com.tencent.mm").unwrap();
        assert_eq!(path, base.join("presets").join("com.tencent.mm.v6.svg"));
        let content = std::fs::read_to_string(&path).unwrap();
        assert!(content.contains("微"));
        let stamp = std::fs::metadata(&path).unwrap().modified().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(20));
        let again = preset_icon_path(&base, "com.tencent.mm").unwrap();
        assert_eq!(again, path);
        assert_eq!(std::fs::metadata(&path).unwrap().modified().unwrap(), stamp);
        assert!(preset_icon_path(&base, "no.such.app").is_none());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn g2_outline_shape_sanity() {
        // 60×60、r=30：四角盒覆盖整块方形（超椭圆变圆方）。
        let pts = g2_outline(60.0, 60.0, 30.0, 5.0);
        assert_eq!(pts.len(), 4 * 97 + 4);
        let xs: Vec<f64> = pts.iter().map(|p| p.0).collect();
        let ys: Vec<f64> = pts.iter().map(|p| p.1).collect();
        let xmax = xs.iter().cloned().fold(f64::MIN, f64::max);
        let xmin = xs.iter().cloned().fold(f64::MAX, f64::min);
        let ymax = ys.iter().cloned().fold(f64::MIN, f64::max);
        let ymin = ys.iter().cloned().fold(f64::MAX, f64::min);
        assert!((xmax - 60.0).abs() < 1e-6 && xmin.abs() < 1e-6);
        assert!((ymax - 60.0).abs() < 1e-6 && ymin.abs() < 1e-6);
    }
}

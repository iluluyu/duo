//! 冻结的按比例打开预设表（docs/ui/DESIGN.md §3.6）。
//! 对译自 duo/core/aspects.py；合同镜像 tests/test_aspects.py。

use std::sync::OnceLock;

/// 所有预设共享的虚拟屏短边（16:9 保持 2560×1440 应用会话基线）。
pub const BODY_SHORT_SIDE_PX: i64 = 1440;

/// 机身对（设备面板比例，运行时 wm size 探测）的菜单 id。
pub const BODY_LANDSCAPE_ID: &str = "body-l";
pub const BODY_PORTRAIT_ID: &str = "body-p";

/// 机身两条共用的菜单标签。
pub const BODY_LABEL: &str = "机身";

/// 一条可启动的虚拟屏形状。body 预设不在冻结表内（每机不同，运行时派生）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AspectPreset {
    pub id: String,
    pub label: String,
    pub landscape: bool,
    pub width: i64,
    pub height: i64,
}

impl AspectPreset {
    fn frozen(id: &str, landscape: bool, width: i64, height: i64) -> Self {
        Self {
            id: id.to_string(),
            label: id.to_string(),
            landscape,
            width,
            height,
        }
    }
}

/// 冻结比例菜单（DESIGN.md §3.6，横组 21:9 → 1:1 在前，竖组 3:4 → 9:16
/// 在后，短边统一 1440px）。顺序是合同的一部分，勿随意重排。
pub fn aspect_presets() -> &'static [AspectPreset] {
    static TABLE: OnceLock<Vec<AspectPreset>> = OnceLock::new();
    TABLE.get_or_init(|| {
        vec![
            AspectPreset::frozen("21:9", true, 3360, 1440),
            AspectPreset::frozen("16:9", true, 2560, 1440),
            AspectPreset::frozen("4:3", true, 1920, 1440),
            AspectPreset::frozen("1:1", true, 1440, 1440),
            AspectPreset::frozen("3:4", false, 1440, 1920),
            AspectPreset::frozen("2:3", false, 1440, 2160),
            AspectPreset::frozen("5:7", false, 1440, 2016),
            AspectPreset::frozen("9:16", false, 1440, 2560),
        ]
    })
}

/// (w, h) ÷ scale 为偶数整数（渲染倍率的整数契约；4K 窗 ÷2 = 1K 渲染，
/// 2026-09-11 用户定稿语义）。每维四舍五入后奇数上调到偶，最小 2px。
pub fn scaled_size(width: i64, height: i64, scale: f64) -> Result<(i64, i64), String> {
    if width <= 0 || height <= 0 || scale <= 0.0 {
        return Err(format!(
            "invalid scaled_size input: {width}x{height}/{scale}"
        ));
    }
    let w = (crate::py_round(width as f64 / scale)).max(2);
    let h = (crate::py_round(height as f64 / scale)).max(2);
    Ok((w + (w & 1), h + (h & 1)))
}

/// 冻结表内该菜单 id 的预设；None = 不在表内（body id 与未知 id 都
/// 返回 None——机身值住在调用方的探测缓存里）。
pub fn preset_by_id(aspect_id: &str) -> Option<AspectPreset> {
    aspect_presets().iter().find(|p| p.id == aspect_id).cloned()
}

/// 单行 ``wm size`` 输出 → (w, h)；不可解析或非正值返回 None。
fn parse_size_line(text: &str) -> Option<(i64, i64)> {
    let bytes: Vec<char> = text.chars().collect();
    let mut i = 0;
    let n = bytes.len();
    while i < n {
        if !bytes[i].is_ascii_digit() {
            i += 1;
            continue;
        }
        let start = i;
        while i < n && bytes[i].is_ascii_digit() {
            i += 1;
        }
        let first: String = bytes[start..i].iter().collect();
        let mut j = i;
        while j < n && (bytes[j] == ' ' || bytes[j] == '\t') {
            j += 1;
        }
        if j < n && (bytes[j] == 'x' || bytes[j] == 'X' || bytes[j] == '×') {
            j += 1;
            while j < n && (bytes[j] == ' ' || bytes[j] == '\t') {
                j += 1;
            }
            if j < n && bytes[j].is_ascii_digit() {
                let hstart = j;
                while j < n && bytes[j].is_ascii_digit() {
                    j += 1;
                }
                let second: String = bytes[hstart..j].iter().collect();
                let w: i64 = first.parse().ok()?;
                let h: i64 = second.parse().ok()?;
                if w <= 0 || h <= 0 {
                    return None;
                }
                return Some((w, h));
            }
        }
    }
    None
}

/// ``wm size`` 输出 → 机身横屏预设（Override 优先于 Physical）。
/// 面板比例归一化为横屏（wm size 按面板原生轴向报告），短边缩放到
/// BODY_SHORT_SIDE_PX，长边取最近偶数。
pub fn body_aspect_from_wm_size(wm_size_output: &str) -> Option<AspectPreset> {
    let mut override_size: Option<(i64, i64)> = None;
    let mut physical: Option<(i64, i64)> = None;
    for line in wm_size_output.lines() {
        let text = line.trim();
        if text.starts_with("Override size:") {
            if override_size.is_none() {
                override_size = parse_size_line(text);
            }
        } else if text.starts_with("Physical size:") && physical.is_none() {
            physical = parse_size_line(text);
        }
    }
    let size = override_size.or(physical)?;
    let long_side = size.0.max(size.1);
    let short_side = size.0.min(size.1);
    let scaled_long =
        crate::py_round(long_side as f64 * BODY_SHORT_SIDE_PX as f64 / short_side as f64 / 2.0) * 2;
    Some(AspectPreset {
        id: BODY_LANDSCAPE_ID.to_string(),
        label: BODY_LABEL.to_string(),
        landscape: true,
        width: scaled_long,
        height: BODY_SHORT_SIDE_PX,
    })
}

/// 预设的转置孪生：同比例、轴互换。为机身对而生（横竖各一）；
/// 非 body id 原样保留 id（仅轴互换）。
pub fn transposed(preset: &AspectPreset) -> AspectPreset {
    let pair_id = if preset.id == BODY_LANDSCAPE_ID {
        BODY_PORTRAIT_ID
    } else if preset.id == BODY_PORTRAIT_ID {
        BODY_LANDSCAPE_ID
    } else {
        preset.id.as_str()
    };
    AspectPreset {
        id: pair_id.to_string(),
        label: preset.label.clone(),
        landscape: !preset.landscape,
        width: preset.height,
        height: preset.width,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 冻结表逐行（id, landscape, width, height），顺序即合同。
    const FROZEN_ROWS: &[(&str, bool, i64, i64)] = &[
        ("21:9", true, 3360, 1440),
        ("16:9", true, 2560, 1440),
        ("4:3", true, 1920, 1440),
        ("1:1", true, 1440, 1440),
        ("3:4", false, 1440, 1920),
        ("2:3", false, 1440, 2160),
        ("5:7", false, 1440, 2016),
        ("9:16", false, 1440, 2560),
    ];

    #[test]
    fn frozen_table_order_and_values() {
        let presets = aspect_presets();
        for (preset, (id, landscape, width, height)) in presets.iter().zip(FROZEN_ROWS) {
            assert_eq!(
                (
                    preset.id.as_str(),
                    preset.landscape,
                    preset.width,
                    preset.height
                ),
                (*id, *landscape, *width, *height)
            );
        }
        // 横组在前，竖组在后；id 组内唯一；label 回显比例串。
        assert!(presets[..4].iter().all(|p| p.landscape));
        assert!(presets[4..].iter().all(|p| !p.landscape));
        let ids: std::collections::HashSet<&str> = presets.iter().map(|p| p.id.as_str()).collect();
        assert_eq!(ids.len(), presets.len());
        assert!(presets.iter().all(|p| p.label == p.id));
        // 短边统一 1440；无 body 条目。
        for preset in presets {
            assert_eq!(preset.width.min(preset.height), 1440);
        }
        assert!(!ids.contains(BODY_LANDSCAPE_ID));
        assert!(!ids.contains(BODY_PORTRAIT_ID));
    }

    #[test]
    fn frozen_landscape_group_descends_by_ratio() {
        let ratios: Vec<f64> = aspect_presets()[..4]
            .iter()
            .map(|p| p.width as f64 / p.height as f64)
            .collect();
        let mut sorted = ratios.clone();
        sorted.sort_by(|a, b| b.partial_cmp(a).unwrap());
        assert_eq!(ratios, sorted);
        assert!(ratios[0] > 2.0);
    }

    #[test]
    fn preset_by_id_hits_and_misses() {
        for (id, landscape, width, height) in FROZEN_ROWS {
            let preset = preset_by_id(id).unwrap();
            assert_eq!(
                (preset.landscape, preset.width, preset.height),
                (*landscape, *width, *height)
            );
        }
        assert!(preset_by_id(BODY_LANDSCAPE_ID).is_none());
        assert!(preset_by_id(BODY_PORTRAIT_ID).is_none());
        assert!(preset_by_id("16:10").is_none());
        assert!(preset_by_id("").is_none());
    }

    #[test]
    fn body_aspect_prefers_override_over_physical() {
        let preset =
            body_aspect_from_wm_size("Physical size: 1080x2400\nOverride size: 1440x3200\n")
                .unwrap();
        assert_eq!(preset.id, BODY_LANDSCAPE_ID);
        assert_eq!(preset.label, BODY_LABEL);
        assert!(preset.landscape);
        assert_eq!((preset.width, preset.height), (3200, 1440));
    }

    #[test]
    fn body_aspect_falls_back_to_physical() {
        let preset = body_aspect_from_wm_size("Physical size: 1080x2400\n").unwrap();
        assert_eq!((preset.width, preset.height), (3200, 1440));
    }

    #[test]
    fn body_aspect_scales_short_side_and_rounds_even() {
        let preset = body_aspect_from_wm_size("Override size: 2223x1000\n").unwrap();
        assert_eq!(preset.height, 1440);
        // 2223 * 1440 / 1000 = 3201.12 -> 最近偶数 = 3202。
        assert_eq!(preset.width, 3202);
        assert_eq!(preset.width % 2, 0);
        assert!((preset.width as f64 / preset.height as f64 - 2223.0 / 1000.0).abs() < 0.01);
    }

    #[test]
    fn body_aspect_normalizes_to_landscape() {
        let tall = body_aspect_from_wm_size("Override size: 1000x2223\n").unwrap();
        let wide = body_aspect_from_wm_size("Override size: 2223x1000\n").unwrap();
        assert_eq!(tall, wide);
        assert!(tall.landscape);
        assert!(tall.width > tall.height);
    }

    #[test]
    fn body_aspect_invalid_inputs_return_none() {
        assert!(body_aspect_from_wm_size("").is_none());
        assert!(body_aspect_from_wm_size("Physical density: 420\n").is_none());
        assert!(body_aspect_from_wm_size("Physical size: banana\n").is_none());
        assert!(body_aspect_from_wm_size("Physical size: 0x0\n").is_none());
        assert!(body_aspect_from_wm_size("Override size: x2400\n").is_none());
        assert!(body_aspect_from_wm_size("adb: device offline\n").is_none());
    }

    #[test]
    fn transposed_body_pair_math() {
        let body = body_aspect_from_wm_size("Physical size: 1080x2400\n").unwrap();
        let portrait = transposed(&body);
        assert_eq!(portrait.id, BODY_PORTRAIT_ID);
        assert_eq!(portrait.label, BODY_LABEL);
        assert!(!portrait.landscape);
        assert_eq!((portrait.width, portrait.height), (1440, 3200));
        assert_eq!(transposed(&portrait), body);
    }

    #[test]
    fn scaled_size_integer_discipline() {
        assert_eq!(scaled_size(3840, 2160, 2.0).unwrap(), (1920, 1080));
        assert_eq!(scaled_size(3840, 2160, 4.0).unwrap(), (960, 540));
        assert_eq!(scaled_size(3840, 2160, 1.5).unwrap(), (2560, 1440));
        assert_eq!(scaled_size(3840, 2160, 1.25).unwrap(), (3072, 1728));
        assert_eq!(scaled_size(1080, 1920, 2.0).unwrap(), (540, 960));
        // 非整除：round 后偶化（2194.857→2195→2196；1174.29→1174 已偶）。
        assert_eq!(scaled_size(3841, 2055, 1.75).unwrap(), (2196, 1174));
        assert_eq!(scaled_size(64, 32, 4.0).unwrap(), (16, 8));
        assert!(scaled_size(0, 100, 2.0).is_err());
        assert!(scaled_size(100, 100, 0.0).is_err());
    }
}

//! 设备渲染图标的收尾管线（apps.py 归一化启发式族的对译；设计依据
//! docs/ui/RESEARCH-ICONS.md §6/§8）。入口 [`finish_device_icon`]；其余
//! 均为本模块私有步骤。PIL 语义映射：convert("L") 用 601-2 定点舍入、
//! Chops multiply 用 round(a·b/255)、GaussianBlur(r) 的 σ=r、形态学窗
//! 口在边缘钳位。

use image::imageops::FilterType;
use image::{GrayImage, ImageEncoder, Rgb, RgbImage, Rgba, RgbaImage};

use crate::icongen::{apply_g2_mask, ADAPTIVE_CANVAS, ADAPTIVE_VISIBLE};
use crate::py_round;

pub const DEFAULT_RADIUS_RATIO: f32 = 0.50;

const NEAR_WHITE_LUMA: i32 = 224;
const NEAR_NEUTRAL_CHROMA: i32 = 18;
const MIN_NORMALIZE_SIZE: u32 = 48;
const FULL_BLEED_SPAN: f64 = 0.96;
const FULL_BLEED_COVERAGE: f64 = 0.90;
const NEUTRAL_PLATE: (u8, u8, u8) = (217, 221, 227);
const BLOB_COVERAGE: f64 = 0.50;
const PLATE_CONTENT_RATIO: f64 = 0.75;
const BG_TOLERANCE: i32 = 60;
const ADAPTIVE_FG_SPAN: f64 = 0.72;
const ADAPTIVE_FG_TARGET: f64 = 0.66;

type BBox = (u32, u32, u32, u32); // left, top, right, bottom（半开区间）

// ---------------------------------------------------------------- basics

fn luma(rgb: (u8, u8, u8)) -> i32 {
    (i32::from(rgb.0) * 299 + i32::from(rgb.1) * 587 + i32::from(rgb.2) * 114) / 1000
}

fn is_near_white(rgb: (u8, u8, u8)) -> bool {
    let max = i32::from(rgb.0.max(rgb.1).max(rgb.2));
    let min = i32::from(rgb.0.min(rgb.1).min(rgb.2));
    luma(rgb) >= NEAR_WHITE_LUMA && max - min <= NEAR_NEUTRAL_CHROMA
}

fn is_neutral(rgb: (u8, u8, u8)) -> bool {
    let max = i32::from(rgb.0.max(rgb.1).max(rgb.2));
    let min = i32::from(rgb.0.min(rgb.1).min(rgb.2));
    let l = luma(rgb);
    max - min <= NEAR_NEUTRAL_CHROMA && (l >= NEAR_WHITE_LUMA || l <= 40)
}

/// PIL convert("L")（601-2 定点舍入）。
fn to_l(rgb: (u8, u8, u8)) -> u8 {
    (((i32::from(rgb.0) * 19595 + i32::from(rgb.1) * 38470 + i32::from(rgb.2) * 7471 + 32768) >> 16)
        as u32)
        .min(255) as u8
}

fn muldiv255(a: u8, b: u8) -> u8 {
    let t = u32::from(a) * u32::from(b) + 128;
    (((t >> 8) + t) >> 8).min(255) as u8
}

fn alpha_bbox(img: &RgbaImage, threshold: u8) -> Option<BBox> {
    let (w, h) = img.dimensions();
    let mut min_x = u32::MAX;
    let mut min_y = u32::MAX;
    let mut max_x = 0u32;
    let mut max_y = 0u32;
    let mut any = false;
    for y in 0..h {
        for x in 0..w {
            if img.get_pixel(x, y).0[3] > threshold {
                any = true;
                min_x = min_x.min(x);
                min_y = min_y.min(y);
                max_x = max_x.max(x);
                max_y = max_y.max(y);
            }
        }
    }
    any.then(|| (min_x, min_y, max_x + 1, max_y + 1))
}

fn alpha_has_soft(img: &RgbaImage) -> bool {
    img.pixels().any(|p| p.0[3] < 250)
}

fn opaque_mask(img: &RgbaImage) -> GrayImage {
    let (w, h) = img.dimensions();
    let mut mask = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let on = img.get_pixel(x, y).0[3] > 200;
            mask.put_pixel(x, y, image::Luma([if on { 255 } else { 0 }]));
        }
    }
    mask
}

fn alpha_coverage(mask: &GrayImage) -> f64 {
    let (w, h) = mask.dimensions();
    if w == 0 || h == 0 {
        return 0.0;
    }
    let sum: u64 = mask.pixels().map(|p| u64::from(p.0[0])).sum();
    sum as f64 / 255.0 / (w as f64 * h as f64)
}

// --------------------------------------------------------------- filters

/// L 通道形态学（窗口奇数边长，边缘钳位窗）。
fn morph_l(mask: &GrayImage, size: u32, max: bool) -> GrayImage {
    let (w, h) = mask.dimensions();
    let half = (size / 2) as i64;
    let mut out = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let mut acc: Option<u8> = None;
            for dy in -half..=half {
                for dx in -half..=half {
                    let sx = (x as i64 + dx).clamp(0, w as i64 - 1) as u32;
                    let sy = (y as i64 + dy).clamp(0, h as i64 - 1) as u32;
                    let v = mask.get_pixel(sx, sy).0[0];
                    acc = Some(match acc {
                        None => v,
                        Some(cur) => {
                            if max {
                                cur.max(v)
                            } else {
                                cur.min(v)
                            }
                        }
                    });
                }
            }
            out.put_pixel(x, y, image::Luma([acc.unwrap_or(0)]));
        }
    }
    out
}

fn convolve(
    kernel: &[f64],
    radius: i64,
    w: u32,
    h: u32,
    horizontal: bool,
    src: impl Fn(i64, i64) -> [f64; 3],
) -> Vec<[f64; 3]> {
    let mut tmp = vec![[0f64; 3]; (w * h) as usize];
    for y in 0..h as i64 {
        for x in 0..w as i64 {
            let mut acc = [0f64; 3];
            for (k, weight) in kernel.iter().enumerate() {
                let offset = k as i64 - radius;
                let (sx, sy) = if horizontal {
                    ((x + offset).clamp(0, w as i64 - 1), y)
                } else {
                    (x, (y + offset).clamp(0, h as i64 - 1))
                };
                let px = src(sx, sy);
                for c in 0..3 {
                    acc[c] += px[c] * weight;
                }
            }
            tmp[(y * w as i64 + x) as usize] = acc;
        }
    }
    tmp
}

/// 分离式高斯（σ = radius，边缘钳位；对齐 PIL GaussianBlur 语义；f64
/// 累加避免双 pass 漂移）。
fn gaussian_rgb(img: &RgbImage, sigma: f32) -> RgbImage {
    let radius = (f64::from(sigma) * 3.0).ceil() as i64;
    let mut kernel: Vec<f64> = (-radius..=radius)
        .map(|i| (-(i * i) as f64 / (2.0 * f64::from(sigma) * f64::from(sigma))).exp())
        .collect();
    let sum: f64 = kernel.iter().sum();
    for k in &mut kernel {
        *k /= sum;
    }
    let (w, h) = img.dimensions();
    let horiz = convolve(&kernel, radius, w, h, true, |x, y| {
        let px = img.get_pixel(x as u32, y as u32).0;
        [f64::from(px[0]), f64::from(px[1]), f64::from(px[2])]
    });
    let vert = convolve(&kernel, radius, w, h, false, |x, y| {
        horiz[(y * w as i64 + x) as usize]
    });
    let mut out = RgbImage::new(w, h);
    for y in 0..h as usize {
        for x in 0..w as usize {
            let px = vert[y * w as usize + x];
            out.put_pixel(
                x as u32,
                y as u32,
                Rgb([
                    px[0].round() as u8,
                    px[1].round() as u8,
                    px[2].round() as u8,
                ]),
            );
        }
    }
    out
}

/// RGB 中值滤波（窗口奇数边长，边缘钳位窗）。
fn median_rgb(img: &RgbImage, size: u32) -> RgbImage {
    let (w, h) = img.dimensions();
    let half = (size / 2) as i64;
    let mut out = RgbImage::new(w, h);
    let mut window: Vec<u8> = Vec::with_capacity((size * size * 3) as usize);
    for y in 0..h {
        for x in 0..w {
            for c in 0..3 {
                window.clear();
                for dy in -half..=half {
                    for dx in -half..=half {
                        let sx = (x as i64 + dx).clamp(0, w as i64 - 1) as u32;
                        let sy = (y as i64 + dy).clamp(0, h as i64 - 1) as u32;
                        window.push(img.get_pixel(sx, sy).0[c]);
                    }
                }
                let mid = window.len() / 2;
                window.select_nth_unstable(mid);
                let median = window[mid];
                let px = out.get_pixel_mut(x, y);
                px.0[c] = median;
            }
        }
    }
    out
}

fn rgba_to_rgb(img: &RgbaImage) -> RgbImage {
    let (w, h) = img.dimensions();
    let mut out = RgbImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let p = img.get_pixel(x, y).0;
            out.put_pixel(x, y, Rgb([p[0], p[1], p[2]]));
        }
    }
    out
}

// ----------------------------------------------------------- measurements

fn solid_border_color(img: &RgbaImage) -> Option<(u8, u8, u8)> {
    use std::collections::BTreeMap;
    let (w, h) = img.dimensions();
    let step = 1.max((w.min(h) / 36) as i64) as u32;
    let mut border: BTreeMap<(u8, u8, u8), u64> = BTreeMap::new();
    let mut sample = |x: u32, y: u32| {
        let p = img.get_pixel(x, y).0;
        *border.entry((p[0], p[1], p[2])).or_insert(0) += 1;
    };
    for x in (0..w).step_by(step.max(1) as usize) {
        sample(x, 0);
        sample(x, h - 1);
    }
    for y in (0..h).step_by(step.max(1) as usize) {
        sample(0, y);
        sample(w - 1, y);
    }
    if border.is_empty() {
        return None;
    }
    let majority: u64 = border.values().sum();
    let channels_spread = |pick: fn(&(u8, u8, u8)) -> i32| {
        let values = border.keys().map(pick);
        values.clone().max().unwrap_or(0) - values.min().unwrap_or(0)
    };
    if channels_spread(|c| i32::from(c.0)) > 40
        || channels_spread(|c| i32::from(c.1)) > 40
        || channels_spread(|c| i32::from(c.2)) > 40
    {
        return None;
    }
    let whites: Vec<(&(u8, u8, u8), &u64)> =
        border.iter().filter(|(c, _)| is_near_white(**c)).collect();
    let white_count: u64 = whites.iter().map(|(_, n)| **n).sum();
    if white_count >= (majority as f64 * 0.4) as u64 && !whites.is_empty() {
        let total: u64 = whites.iter().map(|(_, n)| **n).sum();
        let weighted = |pick: fn(&(u8, u8, u8)) -> u8| -> u8 {
            (whites
                .iter()
                .map(|(c, n)| u64::from(pick(c)).saturating_mul(**n))
                .sum::<u64>()
                / total) as u8
        };
        return Some((weighted(|c| c.0), weighted(|c| c.1), weighted(|c| c.2)));
    }
    let (color, count) = border.iter().max_by_key(|(_, n)| **n).unwrap();
    (*count >= (majority as f64 * 0.85) as u64).then_some(*color)
}

fn flood_bg_mask(img: &RgbaImage, bg: (u8, u8, u8)) -> Vec<u8> {
    let (w, h) = img.dimensions();
    let mut mask = vec![0u8; (w * h) as usize];
    let close = |i: usize| -> bool {
        let p = img
            .get_pixel((i % w as usize) as u32, (i / w as usize) as u32)
            .0;
        (i32::from(p[0]) - i32::from(bg.0)).abs()
            + (i32::from(p[1]) - i32::from(bg.1)).abs()
            + (i32::from(p[2]) - i32::from(bg.2)).abs()
            <= BG_TOLERANCE
    };
    let wi = w as usize;
    let hi = h as usize;
    let mut stack: Vec<usize> = Vec::new();
    for x in 0..wi {
        stack.push(x);
        stack.push((hi - 1) * wi + x);
    }
    for y in 0..hi {
        stack.push(y * wi);
        stack.push(y * wi + wi - 1);
    }
    while let Some(i) = stack.pop() {
        if mask[i] != 0 || !close(i) {
            continue;
        }
        mask[i] = 1;
        let (x, y) = (i % wi, i / wi);
        if x > 0 {
            stack.push(i - 1);
        }
        if x < wi - 1 {
            stack.push(i + 1);
        }
        if y > 0 {
            stack.push(i - wi);
        }
        if y < hi - 1 {
            stack.push(i + wi);
        }
    }
    mask
}

fn mask_content_stats(mask: &[u8], w: u32, _h: u32) -> (Option<BBox>, f64) {
    let mut min_x = u32::MAX;
    let mut min_y = u32::MAX;
    let mut max_x = 0u32;
    let mut max_y = 0u32;
    let mut count = 0u64;
    for (i, selected) in mask.iter().enumerate() {
        if *selected == 0 {
            let (x, y) = ((i % w as usize) as u32, (i / w as usize) as u32);
            count += 1;
            min_x = min_x.min(x);
            min_y = min_y.min(y);
            max_x = max_x.max(x);
            max_y = max_y.max(y);
        }
    }
    if count == 0 {
        return (None, 0.0);
    }
    let bbox = (min_x, min_y, max_x + 1, max_y + 1);
    let area = (bbox.2 - bbox.0) as f64 * (bbox.3 - bbox.1) as f64;
    (Some(bbox), count as f64 / area)
}

fn dominant_color(img: &RgbaImage) -> Option<(u8, u8, u8)> {
    use std::collections::BTreeMap;
    let mut buckets: BTreeMap<(u8, u8, u8), [u64; 4]> = BTreeMap::new();
    for p in img.pixels() {
        let rgb = (p.0[0], p.0[1], p.0[2]);
        if p.0[3] < 128 || is_neutral(rgb) {
            continue;
        }
        let key = (rgb.0 >> 4, rgb.1 >> 4, rgb.2 >> 4);
        let bucket = buckets.entry(key).or_insert([0, 0, 0, 0]);
        bucket[0] += 1;
        bucket[1] += u64::from(rgb.0);
        bucket[2] += u64::from(rgb.1);
        bucket[3] += u64::from(rgb.2);
    }
    let best = buckets.values().max_by_key(|b| b[0])?;
    (best[0] != 0).then(|| {
        (
            (best[1] / best[0]) as u8,
            (best[2] / best[0]) as u8,
            (best[3] / best[0]) as u8,
        )
    })
}

fn mean_opaque_color(img: &RgbaImage) -> Option<(u8, u8, u8)> {
    let (mut rs, mut gs, mut bs, mut count) = (0u64, 0u64, 0u64, 0u64);
    for p in img.pixels() {
        if p.0[3] > 200 {
            rs += u64::from(p.0[0]);
            gs += u64::from(p.0[1]);
            bs += u64::from(p.0[2]);
            count += 1;
        }
    }
    (count != 0).then(|| ((rs / count) as u8, (gs / count) as u8, (bs / count) as u8))
}

fn edge_ring_color(img: &RgbaImage) -> Option<(u8, u8, u8)> {
    let solid = opaque_mask(img);
    let ring = morph_sub(&solid, &morph_l(&solid, 7, false));
    if ring.pixels().all(|p| p.0[0] == 0) {
        return None;
    }
    let (w, h) = img.dimensions();
    let (mut rs, mut gs, mut bs, mut count) = (0u64, 0u64, 0u64, 0u64);
    for y in 0..h {
        for x in 0..w {
            if ring.get_pixel(x, y).0[0] == 0 {
                continue;
            }
            let rgb = {
                let p = img.get_pixel(x, y).0;
                (p[0], p[1], p[2])
            };
            if is_near_white(rgb) {
                continue;
            }
            rs += u64::from(rgb.0);
            gs += u64::from(rgb.1);
            bs += u64::from(rgb.2);
            count += 1;
        }
    }
    (count != 0).then(|| ((rs / count) as u8, (gs / count) as u8, (bs / count) as u8))
}

/// ImageChops.subtract（a-b，clip 0）。
fn morph_sub(a: &GrayImage, b: &GrayImage) -> GrayImage {
    let (w, h) = a.dimensions();
    let mut out = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let v = a.get_pixel(x, y).0[0].saturating_sub(b.get_pixel(x, y).0[0]);
            out.put_pixel(x, y, image::Luma([v]));
        }
    }
    out
}

fn morph_mul(a: &GrayImage, b: &GrayImage) -> GrayImage {
    let (w, h) = a.dimensions();
    let mut out = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let v = muldiv255(a.get_pixel(x, y).0[0], b.get_pixel(x, y).0[0]);
            out.put_pixel(x, y, image::Luma([v]));
        }
    }
    out
}

fn is_monochrome_art(blob: &RgbaImage, dominant: (u8, u8, u8)) -> bool {
    let data = blob.as_raw();
    let t2: u32 = 48 * 48;
    let mut near = 0u64;
    let mut total = 0u64;
    let mut i = 0usize;
    while i + 3 < data.len() {
        if data[i + 3] >= 200 {
            total += 1;
            let d2 = (u32::from(data[i]) - u32::from(dominant.0)).pow(2)
                + (u32::from(data[i + 1]) - u32::from(dominant.1)).pow(2)
                + (u32::from(data[i + 2]) - u32::from(dominant.2)).pow(2);
            if d2 < t2 {
                near += 1;
            }
        }
        i += 12;
    }
    if total == 0 || (near as f64 / total as f64) < 0.85 {
        return false;
    }
    let sampled = data.len().div_ceil(12);
    (total as f64 / sampled as f64) < 0.55
}

fn is_full_bleed(bbox: BBox, coverage: f64, w: u32, h: u32) -> bool {
    let wide = f64::from(bbox.2 - bbox.0) >= w as f64 * FULL_BLEED_SPAN;
    let tall = f64::from(bbox.3 - bbox.1) >= h as f64 * FULL_BLEED_SPAN;
    wide && tall && coverage >= FULL_BLEED_COVERAGE
}

// ------------------------------------------------------- transforms

/// 预乘空间 LANCZOS 缩放叠不透明底（对译 _resize_over：透明像素的 RGB
/// 不得渗入滤波核）。
fn resize_over(content: &RgbaImage, tw: u32, th: u32, backdrop: (u8, u8, u8)) -> RgbaImage {
    let (w, h) = content.dimensions();
    let mut premult = RgbImage::new(w, h);
    let mut alpha_src = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let p = content.get_pixel(x, y).0;
            premult.put_pixel(
                x,
                y,
                Rgb([
                    muldiv255(p[0], p[3]),
                    muldiv255(p[1], p[3]),
                    muldiv255(p[2], p[3]),
                ]),
            );
            alpha_src.put_pixel(x, y, image::Luma([p[3]]));
        }
    }
    let resized = image::imageops::resize(&premult, tw, th, FilterType::Lanczos3);
    let alpha_resized = image::imageops::resize(&alpha_src, tw, th, FilterType::Lanczos3);
    let mut out = RgbaImage::new(tw, th);
    for y in 0..th {
        for x in 0..tw {
            let a = alpha_resized.get_pixel(x, y).0[0];
            let inv = 255 - a;
            let pr = resized.get_pixel(x, y).0;
            let ch = |pre: u8, back: u8| -> u8 { pre.saturating_add(muldiv255(back, inv)) };
            out.put_pixel(
                x,
                y,
                Rgba([
                    ch(pr[0], backdrop.0),
                    ch(pr[1], backdrop.1),
                    ch(pr[2], backdrop.2),
                    255,
                ]),
            );
        }
    }
    out
}

fn plate_compose(content: &RgbaImage, plate: (u8, u8, u8), cw: u32, ch: u32) -> RgbaImage {
    let mut base = RgbaImage::from_pixel(cw, ch, Rgba([plate.0, plate.1, plate.2, 255]));
    let limit = py_round(f64::from(cw.min(ch)) * PLATE_CONTENT_RATIO).max(1) as u32;
    let max_dim = content.dimensions().0.max(content.dimensions().1);
    let scale = (f64::from(limit) / f64::from(max_dim)).min(1.0);
    let size = (
        py_round(f64::from(content.width()) * scale).max(1) as u32,
        py_round(f64::from(content.height()) * scale).max(1) as u32,
    );
    let positioned = resize_over(content, size.0, size.1, plate);
    let ox = ((cw - size.0) / 2, (ch - size.1) / 2);
    for y in 0..size.1 {
        for x in 0..size.0 {
            let p = positioned.get_pixel(x, y);
            base.put_pixel(ox.0 + x, ox.1 + y, *p);
        }
    }
    base
}

fn crop(img: &RgbaImage, bbox: BBox) -> RgbaImage {
    let mut out = RgbaImage::new(bbox.2 - bbox.0, bbox.3 - bbox.1);
    for y in 0..out.height() {
        for x in 0..out.width() {
            out.put_pixel(x, y, *img.get_pixel(bbox.0 + x, bbox.1 + y));
        }
    }
    out
}

fn solid_canvas(w: u32, h: u32, rgba: [u8; 4]) -> RgbaImage {
    RgbaImage::from_pixel(w, h, Rgba(rgba))
}

/// 预乘 source-over（Image.alpha_composite）。
fn alpha_composite(dst: &RgbaImage, src: &RgbaImage) -> RgbaImage {
    let (w, h) = dst.dimensions();
    let mut out = dst.clone();
    for y in 0..h {
        for x in 0..w {
            let d = out.get_pixel(x, y).0;
            let s = src.get_pixel(x, y).0;
            let sa = u32::from(s[3]);
            let da = u32::from(d[3]);
            let inv = 255 - sa;
            let mix = |sc: u8, dc: u8| -> u8 {
                // 源与目标均为直 alpha：先按各自 alpha 折算再合成（PIL 同式）
                let s_pre = u32::from(sc) * sa / 255;
                let d_pre = u32::from(dc) * da / 255;
                ((s_pre + d_pre * inv / 255) as f64 / ((sa + da * inv / 255).max(1)) as f64 * 255.0)
                    .round() as u8
            };
            let a = (sa + da * inv / 255).min(255) as u8;
            out.put_pixel(
                x,
                y,
                Rgba([mix(s[0], d[0]), mix(s[1], d[1]), mix(s[2], d[2]), a]),
            );
        }
    }
    out
}

fn defringe_to(img: &RgbaImage, colour: (u8, u8, u8)) -> RgbaImage {
    let mut out = img.clone();
    if !alpha_has_soft(img) {
        return out;
    }
    for p in out.pixels_mut() {
        if p.0[3] < 250 {
            p.0[0] = colour.0;
            p.0[1] = colour.1;
            p.0[2] = colour.2;
        }
    }
    out
}

fn repaint_ring_whites(img: &RgbaImage, colour: (u8, u8, u8)) -> RgbaImage {
    let solid = opaque_mask(img);
    let ring = morph_sub(&solid, &morph_l(&solid, 7, false));
    let mut out = img.clone();
    for y in 0..img.height() {
        for x in 0..img.width() {
            if ring.get_pixel(x, y).0[0] == 0 {
                continue;
            }
            let p = out.get_pixel_mut(x, y);
            if is_near_white((p.0[0], p.0[1], p.0[2])) {
                p.0[0] = colour.0;
                p.0[1] = colour.1;
                p.0[2] = colour.2;
            }
        }
    }
    out
}

fn flatten_white(img: &RgbaImage) -> RgbaImage {
    let mut colour = solid_border_color(img);
    if colour.is_none() || colour.is_some_and(|c| i32::from(c.0.max(c.1).max(c.2)) < 40) {
        let coverage = alpha_coverage(&opaque_mask(img));
        colour = Some(if coverage >= 0.70 {
            mean_opaque_color(img).unwrap_or((255, 255, 255))
        } else {
            (255, 255, 255)
        });
    }
    let colour = colour.unwrap_or((255, 255, 255));
    let treated = repaint_ring_whites(&defringe_to(img, colour), colour);
    let plate = solid_canvas(
        img.width(),
        img.height(),
        [colour.0, colour.1, colour.2, 255],
    );
    alpha_composite(&plate, &treated)
}

fn recolor_white_bg(img: &RgbaImage) -> RgbaImage {
    let Some(bg) = solid_border_color(img) else {
        return img.clone();
    };
    if !is_near_white(bg) {
        return img.clone();
    }
    let mask = flood_bg_mask(img, bg);
    let (bbox, _coverage) = mask_content_stats(&mask, img.width(), img.height());
    let Some(_bbox) = bbox else {
        return solid_canvas(
            img.width(),
            img.height(),
            [NEUTRAL_PLATE.0, NEUTRAL_PLATE.1, NEUTRAL_PLATE.2, 255],
        );
    };
    if !alpha_has_soft(img) {
        return img.clone();
    }
    flatten_white(img)
}

// ----------------------------------------------------- normalize_raster

fn normalize_raster(img: &RgbaImage) -> RgbaImage {
    let (w, h) = img.dimensions();
    if w.min(h) < MIN_NORMALIZE_SIZE {
        return img.clone();
    }
    if alpha_has_soft(img) {
        let Some(bbox) = alpha_bbox(img, 8) else {
            return img.clone();
        };
        let blob = crop(img, bbox);
        let blob_opaque = opaque_mask(&blob);
        let coverage = alpha_coverage(&blob_opaque);
        let span = f64::from((bbox.2 - bbox.0).max(bbox.3 - bbox.1)) / f64::from(w.max(h));
        if span >= 0.95 && coverage >= 0.40 {
            let colour = edge_ring_color(img).or_else(|| mean_opaque_color(img));
            if let Some(colour) = colour {
                let l = (0.299 * f64::from(colour.0)
                    + 0.587 * f64::from(colour.1)
                    + 0.114 * f64::from(colour.2))
                    / 255.0;
                if l < 0.45 {
                    let plate = solid_canvas(w, h, [colour.0, colour.1, colour.2, 255]);
                    let treated = repaint_ring_whites(&defringe_to(img, colour), colour);
                    return alpha_composite(&plate, &treated);
                }
            }
        }
        if is_full_bleed(bbox, coverage, w, h) || coverage < BLOB_COVERAGE {
            return recolor_white_bg(&flatten_white(img));
        }
        let Some(dominant) = dominant_color(&blob) else {
            return recolor_white_bg(&flatten_white(img));
        };
        if is_neutral(dominant) {
            return recolor_white_bg(&flatten_white(img));
        }
        if is_monochrome_art(&blob, dominant) {
            return plate_compose(
                &repaint_ring_whites(&blob, (255, 255, 255)),
                (255, 255, 255),
                w,
                h,
            );
        }
        return plate_compose(&repaint_ring_whites(&blob, dominant), dominant, w, h);
    }
    match solid_border_color(img) {
        Some(_) => recolor_white_bg(img),
        None => img.clone(),
    }
}

// ----------------------------------------------------- adaptive compose

fn strip_ground_tone(layer: &RgbaImage, plate: (u8, u8, u8)) -> RgbaImage {
    let (w, h) = layer.dimensions();
    let mut close = GrayImage::new(w, h);
    let mut opaque = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let p = layer.get_pixel(x, y).0;
            let diff = (i32::from(p[0]) - i32::from(plate.0)).abs()
                + (i32::from(p[1]) - i32::from(plate.1)).abs()
                + (i32::from(p[2]) - i32::from(plate.2)).abs();
            close.put_pixel(x, y, image::Luma([if diff < 28 { 255 } else { 0 }]));
            opaque.put_pixel(x, y, image::Luma([if p[3] > 200 { 255 } else { 0 }]));
        }
    }
    let matching = morph_mul(&close, &opaque);
    let matched = matching.pixels().filter(|p| p.0[0] == 255).count();
    let total = opaque.pixels().filter(|p| p.0[0] == 255).count();
    if total == 0 || (matched as f64 / total as f64) < 0.40 {
        return layer.clone();
    }
    let mut stripped = layer.clone();
    for y in 0..h {
        for x in 0..w {
            let m = matching.get_pixel(x, y).0[0];
            if m == 0 {
                continue;
            }
            let p = stripped.get_pixel_mut(x, y);
            p.0[3] = p.0[3].saturating_sub(m);
        }
    }
    stripped
}

fn compose_layers(fg: &RgbaImage, bg: &RgbaImage) -> RgbaImage {
    let offset = (ADAPTIVE_CANVAS - ADAPTIVE_VISIBLE) / 2;
    let box_rect = (
        offset,
        offset,
        ADAPTIVE_CANVAS - offset,
        ADAPTIVE_CANVAS - offset,
    );
    let plate = recolor_white_bg(&crop(bg, box_rect));
    let layer = crop(fg, box_rect);
    if alpha_bbox(&layer, 8).is_none() {
        return plate;
    }
    let plate_empty = plate.pixels().all(|p| p.0[3] == 0);
    let sample = plate.get_pixel(2, 2).0;
    let plate_blank = (sample[0], sample[1], sample[2]) == NEUTRAL_PLATE;
    let plate = if plate_empty || plate_blank {
        solid_canvas(ADAPTIVE_VISIBLE, ADAPTIVE_VISIBLE, [255, 255, 255, 255])
    } else {
        plate
    };
    let centre = plate.get_pixel(plate.width() / 2, plate.height() / 2).0;
    let layer = strip_ground_tone(&layer, (centre[0], centre[1], centre[2]));
    let Some(bbox) = alpha_bbox(&layer, 8) else {
        return plate;
    };
    let span = f64::from((bbox.2 - bbox.0).max(bbox.3 - bbox.1)) / f64::from(ADAPTIVE_VISIBLE);
    if span <= ADAPTIVE_FG_SPAN || span >= 0.95 || plate_empty || plate_blank {
        return alpha_composite(&plate, &layer);
    }
    let scale = ADAPTIVE_FG_TARGET / span;
    let art = crop(&layer, bbox);
    let size = (
        py_round(f64::from(art.width()) * scale).max(1) as u32,
        py_round(f64::from(art.height()) * scale).max(1) as u32,
    );
    let pixel = plate.get_pixel(4, 4).0;
    let plate_rgb = (pixel[0], pixel[1], pixel[2]);
    let positioned = resize_over(&art, size.0, size.1, plate_rgb);
    let mut canvas = plate.clone();
    let ox = (
        (ADAPTIVE_VISIBLE - size.0) / 2,
        (ADAPTIVE_VISIBLE - size.1) / 2,
    );
    for y in 0..size.1 {
        for x in 0..size.0 {
            let p = positioned.get_pixel(x, y);
            canvas.put_pixel(ox.0 + x, ox.1 + y, *p);
        }
    }
    canvas
}

// ------------------------------------------------------------ crisp edges

fn crisp_edges(img: &RgbaImage) -> RgbaImage {
    let rgb = rgba_to_rgb(img);
    let blurred = gaussian_rgb(&rgb, 7.0);
    let rough = median_rgb(&rgb, 5);
    let (w, h) = rgb.dimensions();
    let lum = |p: &[u8; 3]| to_l((p[0], p[1], p[2]));
    let mut content = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let diff =
                i32::from(lum(&rgb.get_pixel(x, y).0)) - i32::from(lum(&rough.get_pixel(x, y).0));
            content.put_pixel(x, y, image::Luma([if diff.abs() > 16 { 255 } else { 0 }]));
        }
    }
    let content_dilated = morph_l(&content, 9, true);
    let mut field = rgb.clone();
    for y in 0..h {
        for x in 0..w {
            if content_dilated.get_pixel(x, y).0[0] != 0 {
                field.put_pixel(x, y, *blurred.get_pixel(x, y));
            }
        }
    }
    let eroded = morph_l(&content, 5, false);
    let edge = morph_sub(&content, &eroded);
    let mut brighter = GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            let d =
                i32::from(lum(&rgb.get_pixel(x, y).0)) - i32::from(lum(&field.get_pixel(x, y).0));
            brighter.put_pixel(x, y, image::Luma([if d > 8 { 255 } else { 0 }]));
        }
    }
    let fix = morph_mul(&edge, &brighter);
    let fixed = fix.pixels().filter(|p| p.0[0] != 0).count() as f64;
    let total = (w * h) as f64;
    if fixed == 0.0 {
        return img.clone();
    }
    if fixed > total * 0.004 {
        let rim = 4.max(w.min(h) / 8);
        let mut near_rim = 0u64;
        for y in 0..h {
            for x in 0..w {
                if fix.get_pixel(x, y).0[0] != 0
                    && (x < rim || y < rim || x >= w - rim || y >= h - rim)
                {
                    near_rim += 1;
                }
            }
        }
        if near_rim as f64 / fixed < 0.5 {
            return img.clone();
        }
    }
    let mut out = img.clone();
    for y in 0..h {
        for x in 0..w {
            if fix.get_pixel(x, y).0[0] != 0 {
                let f = field.get_pixel(x, y).0;
                let p = out.get_pixel_mut(x, y);
                p.0[0] = f[0];
                p.0[1] = f[1];
                p.0[2] = f[2];
            }
        }
    }
    out
}

// ------------------------------------------------------------------ entry

/// 收尾一枚系统渲染图标（apps.py finish_device_icon 对译）。kind 取渲染器
/// labels.txt；layers = adaptive 的 fg/bg 分层渲染（可选）。输出 288 方形
/// G2 圆角 PNG 字节。
pub fn finish_device_icon(
    raw: &[u8],
    kind: &str,
    layers: Option<(&[u8], &[u8])>,
) -> Result<Vec<u8>, String> {
    let base = decode_rgba(raw)?;
    let mut base = base;
    if kind == "adaptive" && base.dimensions() == (ADAPTIVE_CANVAS, ADAPTIVE_CANVAS) {
        if let Some((fg_raw, bg_raw)) = layers {
            let composed = compose_layers(&decode_rgba(fg_raw)?, &decode_rgba(bg_raw)?);
            let treated = if alpha_has_soft(&composed) {
                crisp_edges(&normalize_raster(&composed))
            } else {
                crisp_edges(&composed)
            };
            return encode_png(&apply_g2_mask(&treated, DEFAULT_RADIUS_RATIO));
        }
        let offset = (ADAPTIVE_CANVAS - ADAPTIVE_VISIBLE) / 2;
        let cropped = recolor_white_bg(&crop(
            &base,
            (
                offset,
                offset,
                ADAPTIVE_CANVAS - offset,
                ADAPTIVE_CANVAS - offset,
            ),
        ));
        let treated = if alpha_has_soft(&cropped) {
            crisp_edges(&normalize_raster(&cropped))
        } else {
            crisp_edges(&cropped)
        };
        return encode_png(&apply_g2_mask(&treated, DEFAULT_RADIUS_RATIO));
    }
    if base.dimensions() != (ADAPTIVE_VISIBLE, ADAPTIVE_VISIBLE) {
        base = image::imageops::resize(
            &base,
            ADAPTIVE_VISIBLE,
            ADAPTIVE_VISIBLE,
            FilterType::Lanczos3,
        );
    }
    encode_png(&apply_g2_mask(
        &crisp_edges(&normalize_raster(&base)),
        DEFAULT_RADIUS_RATIO,
    ))
}

fn decode_rgba(bytes: &[u8]) -> Result<RgbaImage, String> {
    image::load_from_memory(bytes)
        .map(|img| img.to_rgba8())
        .map_err(|e| format!("decode: {e}"))
}

fn encode_png(img: &RgbaImage) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    image::codecs::png::PngEncoder::new(&mut out)
        .write_image(
            img.as_raw(),
            img.width(),
            img.height(),
            image::ExtendedColorType::Rgba8,
        )
        .map_err(|e| format!("encode: {e}"))?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn solid(w: u32, h: u32, rgba: [u8; 4]) -> RgbaImage {
        solid_canvas(w, h, rgba)
    }

    fn corner_alphas(img: &RgbaImage) -> [u8; 4] {
        let (w, h) = img.dimensions();
        [
            img.get_pixel(0, 0).0[3],
            img.get_pixel(w - 1, 0).0[3],
            img.get_pixel(0, h - 1).0[3],
            img.get_pixel(w - 1, h - 1).0[3],
        ]
    }

    #[test]
    fn near_white_and_neutral_predicates() {
        assert!(is_near_white((250, 250, 250)));
        assert!(!is_near_white((250, 240, 220)), "chroma too wide");
        assert!(!is_near_white((200, 200, 200)), "luma too low");
        assert!(is_neutral((20, 20, 20)));
        assert!(is_neutral((250, 250, 250)));
        assert!(!is_neutral((30, 30, 200)));
    }

    #[test]
    fn solid_border_color_uniform_and_gradient() {
        // 白/绿分裂边（通道 spread > 40）：与 Python 同样拒绝统一底假设。
        let mut img = solid(100, 100, [240, 240, 240, 255]);
        for y in 50..100 {
            for x in 0..100 {
                img.put_pixel(x, y, Rgba([30, 160, 80, 255]));
            }
        }
        assert_eq!(solid_border_color(&img), None);
        // 同族白（spread ≤ 40）且白占 ≥ 40% → 加权白均值。
        let mut family = solid(60, 60, [235, 236, 238, 255]);
        for y in 30..60 {
            for x in 0..60 {
                family.put_pixel(x, y, Rgba([245, 244, 242, 255]));
            }
        }
        let got = solid_border_color(&family).unwrap();
        assert!(got.0 >= 235 && got.0 <= 245, "weighted white mean: {got:?}");
        // 渐变边（通道 spread > 40）拒绝。
        let mut grad = solid(60, 60, [0, 0, 0, 255]);
        for y in 0..60 {
            let v = (y * 5) as u8;
            for x in 0..60 {
                grad.put_pixel(x, y, Rgba([v, v, v, 255]));
            }
        }
        assert_eq!(solid_border_color(&grad), None);
        // 全图单色 → 该色。
        assert_eq!(
            solid_border_color(&solid(60, 60, [10, 20, 30, 255])),
            Some((10, 20, 30))
        );
    }

    #[test]
    fn flood_mask_spares_interior_ink() {
        // 红环围住一块白：内部白与边框不连通 → 作为内容存活。
        let mut img = solid(60, 60, [255, 255, 255, 255]);
        for i in 20..40 {
            for (x, y) in [(i, 20), (i, 39), (20, i), (39, i)] {
                img.put_pixel(x, y, Rgba([200, 30, 30, 255]));
            }
        }
        let mask = flood_bg_mask(&img, (255, 255, 255));
        let (bbox, _) = mask_content_stats(&mask, 60, 60);
        let bbox = bbox.expect("content exists");
        assert_eq!(bbox, (20, 20, 40, 40), "red ring + interior white survive");
        assert_eq!(mask[0], 1, "corner is border-connected bg");
    }

    #[test]
    fn monochrome_line_art_detected_solid_disc_not() {
        // 稀疏单色（线稿）：透明底 + 细蓝线。
        let mut line = solid(64, 64, [0, 0, 0, 0]);
        for y in 28..36 {
            for x in 8..56 {
                line.put_pixel(x, y, Rgba([40, 120, 230, 255]));
            }
        }
        assert!(is_monochrome_art(&line, (40, 120, 230)));
        // 实心单色圆盘：不判线稿（total/sampled 高）。
        let mut disc = solid(64, 64, [0, 0, 0, 0]);
        for y in 4..60 {
            for x in 4..60 {
                disc.put_pixel(x, y, Rgba([40, 120, 230, 255]));
            }
        }
        assert!(!is_monochrome_art(&disc, (40, 120, 230)));
    }

    #[test]
    fn plate_compose_content_ratio_and_centering() {
        let blob = solid(100, 60, [200, 40, 40, 255]);
        let out = plate_compose(&blob, (240, 240, 240), 200, 200);
        // scale = min(limit/max_dim, 1.0) 恒不放大：100 宽原尺寸居中。
        let content_w = (0..200u32)
            .filter(|&x| out.get_pixel(x, 100).0[0] < 230)
            .count();
        assert!(
            (95..=105).contains(&content_w),
            "no upscale, got {content_w}"
        );
        let first = (0..200u32)
            .find(|&x| out.get_pixel(x, 100).0[0] < 230)
            .unwrap();
        assert!(
            (45..=55).contains(&first),
            "centred: starts ~50, got {first}"
        );
        assert_eq!(out.get_pixel(0, 0).0[..3], [240, 240, 240]);
    }

    #[test]
    fn normalize_passthrough_small_and_opaque_colorful() {
        let small = solid(40, 40, [10, 200, 60, 255]);
        assert_eq!(normalize_raster(&small), small, "<48 原样");
        // 全不透明彩底 + 单色边 → solid_border 命中但非白 → recolor 原样。
        let colorful = solid(64, 64, [30, 144, 255, 255]);
        assert_eq!(normalize_raster(&colorful), colorful);
    }

    #[test]
    fn normalize_plates_floating_blob_on_transparent() {
        // 浮动彩色圆盘 → 同色底盘（plate_compose 路径）。
        let mut img = solid(100, 100, [0, 0, 0, 0]);
        for y in 30..70 {
            for x in 30..70 {
                img.put_pixel(x, y, Rgba([30, 160, 80, 255]));
            }
        }
        let out = normalize_raster(&img);
        let corner = out.get_pixel(2, 2).0;
        assert_eq!(corner[3], 255, "corners plated opaque");
        assert_eq!(
            (corner[0], corner[1], corner[2]),
            (30, 160, 80),
            "plate = blob colour"
        );
        // 内容中心保持原色。
        let centre = out.get_pixel(50, 50).0;
        assert_eq!((centre[0], centre[1], centre[2]), (30, 160, 80));
    }

    #[test]
    fn normalize_opaque_white_base_with_ink_passes_through() {
        // 品牌白策略：全不透明白底 + 红心 → 原样直通（recolor 无软边可补）。
        let mut img = solid(100, 100, [255, 255, 255, 255]);
        for y in 40..60 {
            for x in 40..60 {
                img.put_pixel(x, y, Rgba([220, 40, 40, 255]));
            }
        }
        assert_eq!(normalize_raster(&img), img);
    }

    #[test]
    fn full_bleed_adaptive_render_finishes_288_with_clear_corners() {
        let artwork = solid(ADAPTIVE_CANVAS, ADAPTIVE_CANVAS, [18, 184, 104, 255]);
        let mut png = Vec::new();
        image::codecs::png::PngEncoder::new(&mut png)
            .write_image(artwork.as_raw(), 432, 432, image::ExtendedColorType::Rgba8)
            .unwrap();
        let finished = finish_device_icon(&png, "adaptive", None).unwrap();
        let img = image::load_from_memory(&finished).unwrap().to_rgba8();
        assert_eq!(img.dimensions(), (288, 288));
        assert_eq!(img.get_pixel(144, 144).0[..3], [18, 184, 104]);
        for a in corner_alphas(&img) {
            assert_eq!(a, 0, "G2 mask clears corners");
        }
    }

    #[test]
    fn legacy_render_resizes_to_288() {
        let mut artwork = solid(192, 192, [255, 140, 0, 255]);
        for y in 0..192 {
            for x in 0..192 {
                if (x + y) % 7 == 0 {
                    artwork.put_pixel(x, y, Rgba([20, 20, 200, 255]));
                }
            }
        }
        let mut png = Vec::new();
        image::codecs::png::PngEncoder::new(&mut png)
            .write_image(artwork.as_raw(), 192, 192, image::ExtendedColorType::Rgba8)
            .unwrap();
        let finished = finish_device_icon(&png, "raster", None).unwrap();
        let img = image::load_from_memory(&finished).unwrap().to_rgba8();
        assert_eq!(img.dimensions(), (288, 288));
        assert!(corner_alphas(&img).iter().all(|&a| a == 0));
    }

    #[test]
    fn adaptive_layers_compose_onto_white_when_bg_is_blank() {
        // bg 空白 → 白底；fg 大跨度（span 0.78 > 0.72 且 < 0.95）→ 收进 0.66 带。
        let bg = solid(ADAPTIVE_CANVAS, ADAPTIVE_CANVAS, [255, 255, 255, 255]);
        let mut fg = solid(ADAPTIVE_CANVAS, ADAPTIVE_CANVAS, [0, 0, 0, 0]);
        let offset = (432 - 288) / 2;
        let pad = 40; // 内容宽 288-80=208 → span 0.72…；208/288≈0.722 命中带内
        for y in offset + pad..432 - offset - pad {
            for x in offset + pad..432 - offset - pad {
                fg.put_pixel(x, y, Rgba([230, 60, 60, 255]));
            }
        }
        let composed = compose_layers(&fg, &bg);
        assert_eq!(composed.dimensions(), (288, 288));
        let centre = composed.get_pixel(144, 144).0;
        assert_eq!((centre[0], centre[1], centre[2]), (230, 60, 60));
        let corner = composed.get_pixel(2, 2).0;
        assert_eq!(corner, [255, 255, 255, 255], "blank bg → official white");
    }

    #[test]
    fn strip_ground_tone_removes_matching_fg_patch() {
        // fg 大面积与盘同色 → 剥除（alpha 降）。
        let plate = (240, 98, 146);
        let mut layer = solid(288, 288, [plate.0, plate.1, plate.2, 255]);
        for y in 20..60 {
            for x in 20..60 {
                layer.put_pixel(x, y, Rgba([30, 30, 30, 255]));
            }
        }
        let stripped = strip_ground_tone(&layer, plate);
        let corner = stripped.get_pixel(200, 200).0;
        assert!(
            corner[3] < 200,
            "matching plate tone transparentised, got {corner:?}"
        );
        let ink = stripped.get_pixel(40, 40).0;
        assert_eq!(ink[3], 255, "real ink untouched");
    }

    #[test]
    fn gaussian_blur_preserves_constant_image() {
        let img = RgbImage::from_pixel(32, 32, Rgb([100, 150, 200]));
        let out = gaussian_rgb(&img, 7.0);
        assert_eq!(out.get_pixel(16, 16).0, [100, 150, 200]);
    }

    #[test]
    fn median_filter_removes_salt_noise() {
        let mut img = RgbImage::from_pixel(21, 21, Rgb([50, 50, 50]));
        img.put_pixel(10, 10, Rgb([255, 255, 255]));
        let out = median_rgb(&img, 5);
        assert_eq!(out.get_pixel(10, 10).0, [50, 50, 50]);
    }
}

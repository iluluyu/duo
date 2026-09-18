//! 图标图像合成管线（apps.py 图像部分的对译）：G2 圆角 alpha 蒙版、
//! adaptive 前景/背景层合成、面板尺寸缩放。
//!
//! 解析层（badging/资源表/xmltree）在 [`crate::apps`]；本模块只做像素：
//! 输入输出均为 PNG 字节。三个入口对应 apps.py 的三段图像管线——
//! [`rounded_mask_png`]（apply_rounded_mask + finish 的缩放段）、
//! [`adaptive_icon_png`]（_compose_adaptive：432 画布、bg 全幅拉伸、
//! fg 内容优先贴放、288 可见中心裁剪 + 蒙版）、[`panel_icon_png`]
//! （finish_device_icon 的 legacy 段：单次 LANCZOS 到面板规范尺寸）。
//! 白底重着色等归一化启发式（_recolor_white_bg/normalize_raster）留
//! Python 侧。蒙版形状必须与预设模板共用 [`crate::icons::g2_outline`]。

use image::codecs::png::PngEncoder;
use image::imageops::{self, FilterType};
use image::{ExtendedColorType, GenericImageView, GrayImage, ImageEncoder, Rgba, RgbaImage};

use crate::icons::g2_outline;

/// Adaptive 画布：108 单位 × 4px/单位（apps.py `_ADAPTIVE_CANVAS`）。
/// 4px 档使 72/66 单位的可见中心与安全区都落在整数像素上（512 画布的
/// 341.33 会级联出混叠，RESEARCH-ICONS.md §5 / P2-5）。
pub const ADAPTIVE_CANVAS: u32 = 432;
/// Launcher 可见中心（72 单位）：图标的规范裁剪与缓存尺寸。
pub const ADAPTIVE_VISIBLE: u32 = ADAPTIVE_CANVAS * 72 / 108; // 288
/// 66 单位安全区：前景内容超出即按比例收缩（保证不被蒙版裁掉）。
pub const ADAPTIVE_SAFE: u32 = ADAPTIVE_CANVAS * 66 / 108; // 264

/// 前景内容触及每边 ≥98% 即满幅美术——靠蒙版裁切成形，保持全画布拉伸
/// （apps.py `_ADAPTIVE_FULL_BLEED`）。
const ADAPTIVE_FULL_BLEED: f64 = 0.98;
/// 蒙版超采样倍率（apps.py scale=6）：多边形在 6× 分辨率上光栅化，再
/// LANCZOS 缩回，曲线在面板尺寸下保持平滑。
const MASK_SUPERSCALE: f64 = 6.0;
/// 缩回后 alpha 低于此值清零（apps.py `point(<4→0)`：压掉 Lanczos 负环）。
const MASK_ALPHA_FLOOR: u8 = 4;
/// 可见墨水阈值：alpha > 8 才算内容（apps.py `_alpha_bbox` 默认档）。
const ALPHA_INK_THRESHOLD: u8 = 8;
/// apps.py `apply_rounded_mask` 的默认角半径比。
pub const DEFAULT_RADIUS_RATIO: f32 = 0.50;

// ---------------------------------------------------------------------------
// PNG 字节 <-> RgbaImage
// ---------------------------------------------------------------------------

fn decode_rgba(input: &[u8]) -> Result<RgbaImage, String> {
    image::load_from_memory(input)
        .map(|dynamic| dynamic.to_rgba8())
        .map_err(|error| format!("decode icon PNG: {error}"))
}

fn encode_png(image: &RgbaImage) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    PngEncoder::new(&mut bytes)
        .write_image(
            image.as_raw(),
            image.width(),
            image.height(),
            ExtendedColorType::Rgba8,
        )
        .map_err(|error| format!("encode icon PNG: {error}"))?;
    Ok(bytes)
}

/// PNG 字节进、G2 圆角合成 PNG 字节出（管线主入口）。
///
/// 对译 `apply_rounded_mask` + `finish_device_icon` 的缩放段：先把输入
/// 缩放到 `out_size × out_size`（预乘空间 LANCZOS，直通 alpha 缩放会让
/// 透明像素的隐藏 RGB 泄进边缘），再乘上 G2 蒙版（半径 = min 边 ×
/// `radius_ratio`，形状与预设模板共用 g2_outline）。图片不被裁剪——
/// 只是不透明方形源失去四角。
pub fn rounded_mask_png(input: &[u8], radius_ratio: f32, out_size: u32) -> Result<Vec<u8>, String> {
    if out_size == 0 {
        return Err("out_size must be at least 1".to_string());
    }
    let decoded = decode_rgba(input)?;
    let scaled = resize_premultiplied(&decoded, out_size, out_size);
    let masked = apply_g2_mask(&scaled, radius_ratio);
    encode_png(&masked)
}

/// 面板规范尺寸（288）+ 默认半径比 0.5 的便捷入口：一张任意 PNG 直接
/// 变成可入网格的圆角方图标。
pub fn panel_icon_png(input: &[u8]) -> Result<Vec<u8>, String> {
    rounded_mask_png(input, DEFAULT_RADIUS_RATIO, ADAPTIVE_VISIBLE)
}

/// 面板直载入口：任意 PNG → 显示尺寸的 G2 圆角 RGBA（预乘 LANCZOS）。
/// 288 缓存直缩显示尺寸 + 裸/平角遗留缓存补蒙版，保证上屏圆角一致。
pub fn panel_icon_rgba(input: &[u8], out_size: u32) -> Result<RgbaImage, String> {
    if out_size == 0 {
        return Err("out_size must be at least 1".to_string());
    }
    let decoded = decode_rgba(input)?;
    let scaled = resize_premultiplied(&decoded, out_size, out_size);
    Ok(apply_g2_mask(&scaled, DEFAULT_RADIUS_RATIO))
}

// ---------------------------------------------------------------------------
// ① G2 圆角 alpha 蒙版（apply_rounded_mask）
// ---------------------------------------------------------------------------

/// 把 G2 平滑角蒙版乘进每个像素的 alpha（apply_rounded_mask 对译）。
///
/// 蒙版在 6× 分辨率上光栅化后 LANCZOS 缩回，<4 清零，再与既有 alpha
/// 逐像素相乘。返回新图，不修改输入。
pub fn apply_g2_mask(image: &RgbaImage, radius_ratio: f32) -> RgbaImage {
    let (width, height) = image.dimensions();
    let radius = crate::py_round(f64::from(width.min(height)) * f64::from(radius_ratio));
    let mask = g2_mask(width, height, radius);
    let mut out = image.clone();
    for (x, y, pixel) in out.enumerate_pixels_mut() {
        let mut value = mask.get_pixel(x, y)[0];
        if value < MASK_ALPHA_FLOOR {
            value = 0;
        }
        let alpha = (u16::from(pixel[3]) * u16::from(value) + 127) / 255;
        *pixel = Rgba([pixel[0], pixel[1], pixel[2], alpha as u8]);
    }
    out
}

/// G2 蒙版的 alpha 场：超采样光栅化 + LANCZOS 缩回。
fn g2_mask(width: u32, height: u32, radius: i64) -> GrayImage {
    let outline = g2_outline(f64::from(width), f64::from(height), radius as f64, 5.0);
    let supersampled: Vec<(f64, f64)> = outline
        .iter()
        .map(|(x, y)| (x * MASK_SUPERSCALE, y * MASK_SUPERSCALE))
        .collect();
    let hi_width = width.saturating_mul(MASK_SUPERSCALE as u32) as i64;
    let hi_height = height.saturating_mul(MASK_SUPERSCALE as u32) as i64;
    let filled = fill_polygon(&supersampled, hi_width, hi_height);
    imageops::resize(&filled, width, height, FilterType::Lanczos3)
}

/// 简单多边形的扫描线偶奇填充（等价 PIL ImageDraw.polygon 的 255 填充，
/// 无抗锯齿——抗锯齿由超采样 + 缩回完成）。
fn fill_polygon(points: &[(f64, f64)], width: i64, height: i64) -> GrayImage {
    let mut mask = GrayImage::new(width as u32, height as u32);
    let count = points.len();
    for row in 0..height {
        let sample_y = row as f64 + 0.5;
        let mut crossings: Vec<f64> = Vec::with_capacity(8);
        for i in 0..count {
            let (x1, y1) = points[i];
            let (x2, y2) = points[(i + 1) % count];
            if (y1 <= sample_y && y2 > sample_y) || (y2 <= sample_y && y1 > sample_y) {
                let t = (sample_y - y1) / (y2 - y1);
                crossings.push(x1 + t * (x2 - x1));
            }
        }
        crossings.sort_unstable_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        for pair in crossings.chunks(2) {
            if pair.len() < 2 {
                continue;
            }
            let start = (pair[0] - 0.5).ceil().max(0.0) as i64;
            let end = (pair[1] - 0.5).floor().min(width as f64 - 1.0) as i64;
            for x in start..=end {
                mask.put_pixel(x as u32, row as u32, image::Luma([255]));
            }
        }
    }
    mask
}

// ---------------------------------------------------------------------------
// ② Adaptive 前景/背景层合成（_compose_adaptive / _paste_foreground）
// ---------------------------------------------------------------------------

/// 两层 adaptive 图标合成出 288px 圆角 PNG（_compose_adaptive 对译）。
///
/// `fg`/`bg` 为 PNG 字节（None 表示该层缺失）；`bg_color` 在背景层是
/// 纯色资源时替代位图。背景整幅拉伸铺满 432 画布，前景按内容轮廓
/// 贴放（保留作者尺寸、超安全区才收缩、居中），最后裁 288 可见中心
/// 并过 G2 蒙版。
pub fn adaptive_icon_png(
    fg: Option<&[u8]>,
    bg: Option<&[u8]>,
    bg_color: Option<[u8; 3]>,
) -> Result<Vec<u8>, String> {
    let fg_image = fg.map(decode_rgba).transpose()?;
    let bg_image = bg.map(decode_rgba).transpose()?;
    encode_png(&compose_adaptive(
        fg_image.as_ref(),
        bg_image.as_ref(),
        bg_color,
    ))
}

/// [`adaptive_icon_png`] 的像素层：返回已裁剪、已蒙版的 288×288。
pub fn compose_adaptive(
    fg: Option<&RgbaImage>,
    bg: Option<&RgbaImage>,
    bg_color: Option<[u8; 3]>,
) -> RgbaImage {
    let plate = bg_color.unwrap_or([255, 255, 255]);
    let mut base = solid_image(ADAPTIVE_CANVAS, ADAPTIVE_CANVAS, plate);
    if let Some(bg) = bg {
        let backdrop = solid_image(ADAPTIVE_CANVAS, ADAPTIVE_CANVAS, plate);
        base = resize_over(bg, ADAPTIVE_CANVAS, ADAPTIVE_CANVAS, &backdrop);
    }
    if let Some(fg) = fg {
        paste_foreground(&mut base, fg);
    }
    let offset = (ADAPTIVE_CANVAS - ADAPTIVE_VISIBLE) / 2;
    let visible = base
        .view(offset, offset, ADAPTIVE_VISIBLE, ADAPTIVE_VISIBLE)
        .to_image();
    apply_g2_mask(&visible, DEFAULT_RADIUS_RATIO)
}

/// 前景层贴放（_paste_foreground 对译）：内容优先，不整幅拉伸。
///
/// 前景画在完整 108 单位层上，logo 悬在 66 单位安全区内、四周透明。
/// 直接拉伸会复现不对称的留白；这里裁 alpha 包围盒，保留作者尺寸
/// （不放大），只在内容会溢出安全区时收缩，再居中——72 单位可见
/// 裁剪始终光学居中。两个退化出口：全透明层跳过（透出背景）；内容
/// 已铺满画布（≥98%）是满幅美术，靠蒙版裁切成形，原样拉伸。
fn paste_foreground(base: &mut RgbaImage, fg: &RgbaImage) {
    let Some((bx0, by0, bx1, by1)) = alpha_bbox(fg) else {
        return;
    };
    let (fg_w, fg_h) = fg.dimensions();
    let base_w = base.width();
    let base_h = base.height();
    let bleed_x = f64::from(fg_w) * (1.0 - ADAPTIVE_FULL_BLEED);
    let bleed_y = f64::from(fg_h) * (1.0 - ADAPTIVE_FULL_BLEED);
    let full_bleed = f64::from(bx0) <= bleed_x
        && f64::from(by0) <= bleed_y
        && f64::from(bx1) >= f64::from(fg_w) - bleed_x
        && f64::from(by1) >= f64::from(fg_h) - bleed_y;
    if full_bleed {
        let layer = resize_over(fg, base_w, base_h, base);
        *base = layer;
        return;
    }
    let content_w = bx1 - bx0;
    let content_h = by1 - by0;
    let content_max = f64::from(content_w.max(content_h));
    let mut scale = f64::from(base_w) / f64::from(fg_w.max(fg_h));
    if content_max * scale > f64::from(ADAPTIVE_SAFE) {
        scale = f64::from(ADAPTIVE_SAFE) / content_max;
    }
    let size_w = crate::py_round(f64::from(content_w) * scale).max(1) as u32;
    let size_h = crate::py_round(f64::from(content_h) * scale).max(1) as u32;
    let x0 = (base_w - size_w) / 2;
    let y0 = (base_h - size_h) / 2;
    let content = fg.view(bx0, by0, content_w, content_h).to_image();
    let backdrop = base.view(x0, y0, size_w, size_h).to_image();
    let layer = resize_over(&content, size_w, size_h, &backdrop);
    for y in 0..size_h {
        for x in 0..size_w {
            base.put_pixel(x0 + x, y0 + y, *layer.get_pixel(x, y));
        }
    }
}

/// alpha > [`ALPHA_INK_THRESHOLD`] 的包围盒（x0, y0, x1, y1，端点不含）。
/// 不可见 fringe（alpha 1..8，有损再导出的常见产物）不算墨水；全透明
/// 返回 None。
fn alpha_bbox(image: &RgbaImage) -> Option<(u32, u32, u32, u32)> {
    let (width, height) = image.dimensions();
    let mut x0 = u32::MAX;
    let mut y0 = u32::MAX;
    let mut x1 = 0_u32;
    let mut y1 = 0_u32;
    let mut any = false;
    for y in 0..height {
        for x in 0..width {
            if image.get_pixel(x, y)[3] > ALPHA_INK_THRESHOLD {
                any = true;
                x0 = x0.min(x);
                y0 = y0.min(y);
                x1 = x1.max(x + 1);
                y1 = y1.max(y + 1);
            }
        }
    }
    if any {
        Some((x0, y0, x1, y1))
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// ③ 缩放（_resize_over / finish 的 LANCZOS 段）
// ---------------------------------------------------------------------------

/// 预乘空间 LANCZOS 缩放后压到不透明底上（_resize_over 对译）。
///
/// 直通 alpha 的逐通道缩放会让透明像素的隐藏 RGB 泄进滤波（黑边）；
/// 这里在预乘空间缩放再展开为标准 over 合成。`backdrop` 须已是目标
/// 尺寸，按不透明处理（结果 alpha 恒 255，同 PIL 版语义）。
fn resize_over(content: &RgbaImage, width: u32, height: u32, backdrop: &RgbaImage) -> RgbaImage {
    let resized = resize_premultiplied(content, width, height);
    let mut out = RgbaImage::new(width, height);
    for (x, y, pixel) in resized.enumerate_pixels() {
        let inverse = 255_u32 - u32::from(pixel[3]);
        let over = |premultiplied: u8, back: u8| {
            (u32::from(premultiplied) + (u32::from(back) * inverse + 127) / 255).min(255) as u8
        };
        let back = backdrop.get_pixel(x, y);
        out.put_pixel(
            x,
            y,
            Rgba([
                over(pixel[0], back[0]),
                over(pixel[1], back[1]),
                over(pixel[2], back[2]),
                255,
            ]),
        );
    }
    out
}

/// 预乘空间 LANCZOS 缩放（保留 alpha 语义）。
///
/// 先 rgb·α 缩放、α 同步缩放，再反预乘还原直通 alpha——透明区不引入
/// 底色，也不产生边缘 fringe。尺寸相同则原样克隆。
fn resize_premultiplied(content: &RgbaImage, width: u32, height: u32) -> RgbaImage {
    if content.dimensions() == (width, height) {
        return content.clone();
    }
    let mut premultiplied = RgbaImage::new(content.width(), content.height());
    for (x, y, pixel) in content.enumerate_pixels() {
        let alpha = u32::from(pixel[3]);
        let mul = |channel: u8| ((u32::from(channel) * alpha + 127) / 255) as u8;
        premultiplied.put_pixel(
            x,
            y,
            Rgba([mul(pixel[0]), mul(pixel[1]), mul(pixel[2]), pixel[3]]),
        );
    }
    let resized = imageops::resize(&premultiplied, width, height, FilterType::Lanczos3);
    let mut out = RgbaImage::new(width, height);
    for (x, y, pixel) in resized.enumerate_pixels() {
        let alpha = u32::from(pixel[3]);
        let unmul =
            |channel: u8| ((u32::from(channel) * 255 + alpha / 2) / alpha.max(1)).min(255) as u8;
        out.put_pixel(
            x,
            y,
            Rgba([unmul(pixel[0]), unmul(pixel[1]), unmul(pixel[2]), pixel[3]]),
        );
    }
    out
}

fn solid_image(width: u32, height: u32, rgb: [u8; 3]) -> RgbaImage {
    RgbaImage::from_pixel(width, height, Rgba([rgb[0], rgb[1], rgb[2], 255]))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 程序化生成纯色 PNG（测试不依赖磁盘资源）。
    fn solid_png(width: u32, height: u32, rgba: [u8; 4]) -> Vec<u8> {
        encode_png(&RgbaImage::from_pixel(width, height, Rgba(rgba))).expect("encode solid PNG")
    }

    fn decode(bytes: &[u8]) -> RgbaImage {
        decode_rgba(bytes).expect("decode masked PNG")
    }

    #[test]
    fn rounded_mask_cuts_corners_keeps_center() {
        let out = rounded_mask_png(&solid_png(120, 120, [255, 0, 0, 255]), 0.5, 100)
            .expect("mask solid icon");
        let masked = decode(&out);
        assert_eq!(masked.dimensions(), (100, 100));
        for (x, y) in [(0, 0), (99, 0), (0, 99), (99, 99)] {
            assert_eq!(masked.get_pixel(x, y)[3], 0, "corner ({x},{y}) must be cut");
        }
        let center = masked.get_pixel(50, 50);
        assert_eq!(*center, Rgba([255, 0, 0, 255]), "center keeps solid colour");
        for (x, y) in [(50, 0), (50, 99), (0, 50), (99, 50)] {
            assert_eq!(
                masked.get_pixel(x, y)[3],
                255,
                "edge midpoint ({x},{y}) kept"
            );
        }
    }

    #[test]
    fn rounded_mask_edge_transition_is_monotonic() {
        let out = rounded_mask_png(&solid_png(120, 120, [30, 90, 240, 255]), 0.5, 100).unwrap();
        let masked = decode(&out);
        let diagonal: Vec<u8> = (0..50)
            .map(|step| masked.get_pixel(50 + step, 50 + step)[3])
            .collect();
        assert_eq!(diagonal[0], 255, "diagonal starts opaque at the centre");
        assert_eq!(*diagonal.last().unwrap(), 0, "diagonal ends transparent");
        for pair in diagonal.windows(2) {
            // 中心 → 角落 alpha 只允许下降（+3 容纳 Lanczos 极小正环）。
            assert!(
                i16::from(pair[1]) <= i16::from(pair[0]) + 3,
                "alpha must not rise toward the corner: {pair:?}"
            );
        }
    }

    #[test]
    fn rounded_mask_multiplies_existing_alpha() {
        let out = rounded_mask_png(&solid_png(80, 80, [0, 200, 0, 128]), 0.5, 80).unwrap();
        let masked = decode(&out);
        assert_eq!(masked.get_pixel(40, 40)[3], 128, "mask 255 × source 128");
        assert_eq!(masked.get_pixel(0, 0)[3], 0, "mask 0 wins over source 128");
    }

    #[test]
    fn rounded_mask_resizes_to_out_size() {
        for source in [37_u32, 200] {
            let out =
                rounded_mask_png(&solid_png(source, source, [9, 9, 9, 255]), 0.25, 64).unwrap();
            assert_eq!(decode(&out).dimensions(), (64, 64), "source {source}");
        }
    }

    #[test]
    fn rounded_mask_rejects_bad_input() {
        assert!(rounded_mask_png(b"not a png", 0.5, 64).is_err());
        assert!(rounded_mask_png(&solid_png(8, 8, [1, 2, 3, 255]), 0.5, 0).is_err());
    }

    fn layer_png(size: u32, content: [u8; 4], inset: u32) -> Vec<u8> {
        let mut layer = RgbaImage::from_pixel(size, size, Rgba([0, 0, 0, 0]));
        for y in inset..size - inset {
            for x in inset..size - inset {
                layer.put_pixel(x, y, Rgba(content));
            }
        }
        encode_png(&layer).expect("encode layer PNG")
    }

    #[test]
    fn adaptive_compose_stacks_bg_plate_and_fg_content() {
        let fg = layer_png(200, [255, 0, 0, 255], 70); // 60×60 red in centre
        let bg = solid_png(432, 432, [0, 60, 200, 255]);
        let out = adaptive_icon_png(Some(&fg), Some(&bg), None).expect("compose adaptive layers");
        let icon = decode(&out);
        assert_eq!(icon.dimensions(), (288, 288));
        assert_eq!(icon.get_pixel(0, 0)[3], 0, "G2 mask still cuts corners");
        assert_eq!(
            *icon.get_pixel(144, 144),
            Rgba([255, 0, 0, 255]),
            "fg content"
        );
        assert_eq!(
            *icon.get_pixel(144, 20),
            Rgba([0, 60, 200, 255]),
            "bg plate outside the fg silhouette"
        );
    }

    #[test]
    fn adaptive_compose_solid_bg_color_without_layers() {
        let transparent = layer_png(100, [255, 255, 255, 255], 50); // alpha 0 outside
        let out = adaptive_icon_png(Some(&transparent), None, Some([0, 200, 0]))
            .expect("compose bg colour only");
        let icon = decode(&out);
        assert_eq!(icon.dimensions(), (288, 288));
        assert_eq!(*icon.get_pixel(144, 144), Rgba([0, 200, 0, 255]));
        assert_eq!(icon.get_pixel(287, 0)[3], 0, "corners masked");
    }

    #[test]
    fn adaptive_fg_keeps_authored_scale_when_inside_safe_zone() {
        // 60×60 内容在 200 层上：贴放尺寸 = 60 × 432/200 ≈ 130px，居中后
        // 落在 288 裁剪的 [79, 209)。
        let fg = layer_png(200, [255, 0, 0, 255], 70);
        let composed = compose_adaptive(Some(&decode(&fg)), None, Some([0, 60, 200]));
        assert_eq!(
            composed.get_pixel(79, 79)[0],
            255,
            "fg square starts near 79"
        );
        assert_eq!(
            composed.get_pixel(78, 144)[0],
            0,
            "bg plate just outside the fg square"
        );
    }

    #[test]
    fn adaptive_fg_shrinks_to_safe_zone() {
        // 300×300 内容 > 264 安全区：收缩到 264px，居中于 432 画布后在
        // 288 裁剪内占 [12, 276)。
        let fg = layer_png(432, [255, 200, 0, 255], 66);
        let composed = compose_adaptive(Some(&decode(&fg)), None, Some([0, 0, 30]));
        assert_eq!(composed.dimensions(), (288, 288));
        assert_eq!(
            *composed.get_pixel(144, 144),
            Rgba([255, 200, 0, 255]),
            "fg centre"
        );
        assert_eq!(*composed.get_pixel(5, 144), Rgba([0, 0, 30, 255]), "bg rim");
        assert_eq!(composed.get_pixel(280, 144)[0], 0, "fg ends before 276");
    }

    #[test]
    fn adaptive_full_bleed_fg_stretches_whole_canvas() {
        let fg = solid_png(432, 432, [255, 210, 0, 255]);
        let composed = compose_adaptive(Some(&decode(&fg)), None, Some([10, 10, 10]));
        assert_eq!(*composed.get_pixel(144, 144), Rgba([255, 210, 0, 255]));
        assert_eq!(
            *composed.get_pixel(4, 144),
            Rgba([255, 210, 0, 255]),
            "full canvas"
        );
        assert_eq!(composed.get_pixel(0, 0)[3], 0, "corners still masked");
    }

    #[test]
    fn panel_icon_png_is_canonical_288() {
        let out = panel_icon_png(&solid_png(64, 64, [7, 8, 9, 255])).expect("panel icon");
        let icon = decode(&out);
        assert_eq!(icon.dimensions(), (ADAPTIVE_VISIBLE, ADAPTIVE_VISIBLE));
        assert_eq!(icon.get_pixel(0, 0)[3], 0);
        assert_eq!(icon.get_pixel(143, 143)[3], 255);
    }

    #[test]
    fn panel_icon_rgba_masks_at_display_size() {
        // 平角源（341 见角 alpha>0 的遗留产物）→ 蒙版后四角归零
        let mut flat = RgbaImage::from_pixel(341, 341, Rgba([255, 0, 0, 255]));
        flat.put_pixel(0, 0, Rgba([255, 0, 0, 10]));
        let bytes = encode_png(&flat).unwrap();
        let icon = panel_icon_rgba(&bytes, 60).expect("rgba icon");
        assert_eq!(icon.dimensions(), (60, 60));
        assert_eq!(icon.get_pixel(0, 0)[3], 0);
        assert_eq!(icon.get_pixel(30, 30)[3], 255);
        // 已蒙版的 288 缓存再走一遍 = 幂等（角仍为 0）
        let again = panel_icon_rgba(&encode_png(&icon).unwrap(), 60).unwrap();
        assert_eq!(again.get_pixel(0, 0)[3], 0);
        assert!(panel_icon_rgba(&bytes, 0).is_err());
    }
}

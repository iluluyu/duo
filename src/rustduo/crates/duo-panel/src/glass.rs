//! 菜单毛玻璃（egui 实装）：快照裁剪 → 3×box blur（高斯近似）→ 光学
//! 增益 → 圆角蒙版 → 贴图。配方、参数与移植基线见 docs/ui/glass-recipe.md §8。

use eframe::egui::{Color32, ColorImage, Context, Pos2, Rect, TextureHandle, TextureOptions, Vec2};

/// 快照裁剪相对菜单矩形的边距（逻辑 px；> 3σ，最大 σ16 → 48px）。
pub(crate) const SNAPSHOT_MARGIN: f32 = 56.0;
/// 蒙版圆角（逻辑 px，QML MenuGlassPlate radius 12）。
pub(crate) const MASK_RADIUS: f32 = 12.0;

/// 高光/阴影软膝半宽（gamma 空间）：±13 级过渡带，C¹ 连续消 banding。
const HIGHLIGHT_KNEE: f32 = 0.05;

/// 把逻辑矩形各边量化到设备像素网格（ppp 可为小数，如 1.25）：不量化时
/// 布局尾差会让逐帧矩形按位不等，误判"矩形变了"→ 清贴图重拍（真机
/// 表现为菜单持续"晃动"）；量化后同格即视为未变，绝不重拍。
pub(crate) fn snap_rect_device_px(r: Rect, ppp: f32) -> Rect {
    if ppp <= 0.0 {
        return r;
    }
    let snap = |v: f32| (v * ppp).round() / ppp;
    Rect::from_min_max(
        Pos2::new(snap(r.min.x), snap(r.min.y)),
        Pos2::new(snap(r.max.x), snap(r.max.y)),
    )
}

/// 容差吸收：ppp=1.25 下 1 逻辑像素抖动 = 1.25 设备像素，量化后仍落
/// 在相邻两格（如 89/91）。四边都在 tol 内视为同一矩形，调用方冻结在
/// 旧值上，彻底停掉「尾差→清贴图→作废快照→跳画菜单」的闪烁链。
pub(crate) fn rect_within_tol(a: Rect, b: Rect, tol: f32) -> bool {
    (a.min.x - b.min.x).abs() <= tol
        && (a.min.y - b.min.y).abs() <= tol
        && (a.max.x - b.max.x).abs() <= tol
        && (a.max.y - b.max.y).abs() <= tol
}

/// 玻璃参数（sigma 虚化、光学增益与暗色压暗 tint）。
pub(crate) struct GlassParams {
    pub(crate) sigma: f32,
    pub(crate) brightness: f32,
    pub(crate) contrast: f32,
    pub(crate) pivot: f32,
    pub(crate) saturation: f32,
    pub(crate) tint: Option<(Color32, f32)>,
    pub(crate) highlight_ceiling: Option<f32>,
    pub(crate) highlight_slope: f32,
    /// 阴影地板（亮色档护墨字）：暗底内容向 floor 收拢，墨字对比
    /// 构造性保证（暗色档天花膝的镜像）。
    pub(crate) shadow_floor: Option<f32>,
    pub(crate) shadow_slope: f32,
    /// 纵向光泽渐变（顶 +sheen、底 −sheen，Liquid Glass/ColorOS 光感）。
    pub(crate) sheen: f32,
}

const DARK_SHEEN: f32 = 0.045;

fn dark_unit(sigma: f32, bright: f32, cont: f32, ceiling: Option<f32>, slope: f32) -> GlassParams {
    GlassParams {
        sigma,
        brightness: bright,
        contrast: cont,
        pivot: 0.11,
        saturation: 1.65,
        tint: None,
        highlight_ceiling: ceiling,
        highlight_slope: slope,
        shadow_floor: None,
        shadow_slope: 0.0,
        sheen: DARK_SHEEN,
    }
}

pub(crate) fn main_params(is_dark: bool) -> GlassParams {
    if !is_dark {
        return match std::env::var("DUO_GLASS_UNIT_LIGHT").as_deref() {
            Ok("0") => GlassParams {
                // 旧亮色配方对照：无膝无 sheen
                sigma: 6.5,
                brightness: 0.02,
                contrast: 0.06,
                pivot: 0.5,
                saturation: 1.45,
                tint: None,
                highlight_ceiling: None,
                highlight_slope: 0.35,
                shadow_floor: None,
                shadow_slope: 0.0,
                sheen: 0.0,
            },
            Ok("1") => light_unit(0.62, 0.15, None, 0.0),
            Ok("2") => light_unit(0.66, 0.15, Some((0.96, 0.30)), 0.0),
            Ok("4") => light_unit(0.72, 0.12, Some((0.975, 0.35)), 0.0),
            // sheen 0.02：0.045 会让顶行 251×1.045 钔白 7 级、底行 240
            // 跌破底板；0.02 顶行仅 1 级钳位、底行 246 仍亮于底板（agy 裁决）
            _ => light_unit(0.66, 0.15, Some((0.975, 0.35)), 0.02),
        };
    }
    match std::env::var("DUO_GLASS_UNIT").as_deref() {
        Ok("0") => dark_unit(8.0, 0.025, 0.04, None, 0.35),
        Ok("1") => GlassParams {
            sigma: 8.0,
            brightness: 0.02,
            contrast: 0.04,
            pivot: 0.11,
            saturation: 1.65,
            tint: Some((Color32::from_rgb(36, 36, 40), 0.04)),
            highlight_ceiling: Some(0.42),
            highlight_slope: 0.12,
            shadow_floor: None,
            shadow_slope: 0.0,
            sheen: DARK_SHEEN,
        },
        Ok("2") => dark_unit(8.0, 0.025, 0.04, Some(0.36), 0.12),
        Ok("4") => dark_unit(8.0, 0.035, 0.03, Some(0.44), 0.10),
        _ => dark_unit(8.0, 0.025, 0.04, Some(0.40), 0.15),
    }
}

/// 亮色单位档（§8.4 亮表）：pivot 0.5、×1.45、sheen 同暗档；地板护墨
/// 字（#1D1D1F ≥4.5:1 构造性保证），天花抗冲白并把平画布落点压回
/// QML 假玻璃锚点 (250,250,252) 邻域。
fn light_unit(
    floor: f32,
    floor_slope: f32,
    ceiling: Option<(f32, f32)>,
    sheen: f32,
) -> GlassParams {
    GlassParams {
        sigma: 8.0,
        brightness: 0.02,
        contrast: 0.06,
        pivot: 0.5,
        saturation: 1.45,
        tint: None,
        highlight_ceiling: ceiling.map(|(c, _)| c),
        highlight_slope: ceiling.map(|(_, s)| s).unwrap_or(0.35),
        shadow_floor: Some(floor),
        shadow_slope: floor_slope,
        sheen,
    }
}

/// 从全窗设备像素快照裁出菜单区域（含边距），模糊 + 增益 + 蒙版后上传
/// 贴图。返回（贴图，屏幕矩形 = 贴图覆盖区域）。裁剪退化时返回 None。
pub(crate) fn build_texture(
    ctx: &Context,
    snapshot: &ColorImage,
    ppp: f32,
    menu_rect: Rect,
    params: &GlassParams,
    tex_name: &str,
) -> Option<(TextureHandle, Rect)> {
    let (iw, ih) = (snapshot.width(), snapshot.height());
    if iw == 0 || ih == 0 || ppp <= 0.0 {
        return None;
    }
    let crop = menu_rect.expand(SNAPSHOT_MARGIN);
    let x0 = ((crop.left() * ppp).floor() as usize).min(iw - 1);
    let y0 = ((crop.top() * ppp).floor() as usize).min(ih - 1);
    let x1 = ((crop.right() * ppp).ceil() as usize).clamp(x0 + 1, iw);
    let y1 = ((crop.bottom() * ppp).ceil() as usize).clamp(y0 + 1, ih);
    let w = x1 - x0;
    let h = y1 - y0;

    let mut buf = vec![0.0_f32; w * h * 3];
    for row in 0..h {
        for col in 0..w {
            let c = snapshot.pixels[(y0 + row) * iw + (x0 + col)];
            let base = (row * w + col) * 3;
            buf[base] = f32::from(c.r()) / 255.0;
            buf[base + 1] = f32::from(c.g()) / 255.0;
            buf[base + 2] = f32::from(c.b()) / 255.0;
        }
    }
    for r in boxes_for_gauss(params.sigma, 3) {
        box_pass(&mut buf, w, h, r, true);
        box_pass(&mut buf, w, h, r, false);
    }
    apply_gains(&mut buf, params);

    // 圆角蒙版：菜单矩形落在裁剪内的设备坐标，r 与羽化随 ppp 缩放。
    let m_left = menu_rect.left() * ppp - x0 as f32;
    let m_top = menu_rect.top() * ppp - y0 as f32;
    let half_w = menu_rect.width() * ppp / 2.0;
    let half_h = menu_rect.height() * ppp / 2.0;
    let radius = MASK_RADIUS * ppp;
    let mut pixels = Vec::with_capacity(w * h);
    for row in 0..h {
        // 纵向光泽：顶部略亮底部略暗（玻璃被上方环境照亮的真实感）
        let sheen = 1.0 + params.sheen * (0.5 - row as f32 / h as f32) * 2.0;
        for col in 0..w {
            let px = col as f32 + 0.5 - (m_left + half_w);
            let py = row as f32 + 0.5 - (m_top + half_h);
            let dist = sd_rounded_rect(px, py, half_w, half_h, radius);
            let alpha = ((0.5 - dist).clamp(0.0, 1.0) * 255.0).round() as u8;
            let base = (row * w + col) * 3;
            pixels.push(Color32::from_rgba_unmultiplied(
                (buf[base] * sheen * 255.0).round().clamp(0.0, 255.0) as u8,
                (buf[base + 1] * sheen * 255.0).round().clamp(0.0, 255.0) as u8,
                (buf[base + 2] * sheen * 255.0).round().clamp(0.0, 255.0) as u8,
                alpha,
            ));
        }
    }
    let draw_rect_dbg = Rect::from_min_max(
        Pos2::new(x0 as f32 / ppp, y0 as f32 / ppp),
        Pos2::new(x1 as f32 / ppp, y1 as f32 / ppp),
    );
    if std::env::var_os("DUO_GLASS_DEBUG").is_some() {
        eprintln!("[glass-debug] menu_rect={menu_rect:?} crop_px=({x0},{y0})-({x1},{y1}) draw={draw_rect_dbg:?} ppp={ppp}");
        let mut dbg = Vec::with_capacity(w * h * 3);
        for p in &pixels {
            dbg.extend_from_slice(&[p.r(), p.g(), p.b()]);
        }
        let _ = image::save_buffer(
            "/tmp/glass_tex_DEBUG.png",
            &dbg,
            w as u32,
            h as u32,
            image::ColorType::Rgb8,
        );
    }
    let tex = ctx.load_texture(
        tex_name,
        ColorImage {
            size: [w, h],
            pixels,
        },
        TextureOptions::LINEAR,
    );
    let draw_rect = Rect::from_min_max(
        Pos2::new(x0 as f32 / ppp, y0 as f32 / ppp),
        Pos2::new(x1 as f32 / ppp, y1 as f32 / ppp),
    );
    Some((tex, draw_rect))
}

/// 圆角矩形 SDF（内负外正，px 为相对矩形中心的偏移）。
fn sd_rounded_rect(px: f32, py: f32, half_w: f32, half_h: f32, r: f32) -> f32 {
    let qx = px.abs() - (half_w - r);
    let qy = py.abs() - (half_h - r);
    let outside = Vec2::new(qx.max(0.0), qy.max(0.0)).length();
    outside + qx.max(qy).min(0.0) - r
}

/// 高光天花软膝（三次 Hermite）：拐点两侧值与斜率双连续，保证单调无
/// banding；膝外输出与硬拐点公式完全一致（尾线斜率同 slope）。
fn highlight_soft_knee(l: f32, ceiling: f32, slope: f32, half: f32) -> f32 {
    let a = ceiling - half;
    let b = ceiling + half;
    if l >= b {
        return ceiling + (l - ceiling) * slope;
    }
    if l <= a {
        return l;
    }
    let r_b = ceiling + half * slope;
    let x = (l - a) / (b - a);
    let x2 = x * x;
    let x3 = x2 * x;
    let h00 = 2.0 * x3 - 3.0 * x2 + 1.0;
    let h10 = x3 - 2.0 * x2 + x;
    let h01 = -2.0 * x3 + 3.0 * x2;
    let h11 = x3 - x2;
    h00 * a + h10 * (b - a) + h01 * r_b + h11 * (b - a) * slope
}

/// 阴影地板软膝（天花膝的镜像）：暗底向 floor 收拢，尾线斜率同 slope，
/// 膝上回归恒等；值+斜率双连续同 highlight_soft_knee。
fn shadow_soft_knee(l: f32, floor: f32, slope: f32, half: f32) -> f32 {
    let a = floor - half;
    let b = floor + half;
    if l >= b {
        return l;
    }
    if l <= a {
        return floor - (floor - l) * slope;
    }
    let l_a = floor - (floor - a) * slope;
    let x = (l - a) / (b - a);
    let x2 = x * x;
    let x3 = x2 * x;
    let h00 = 2.0 * x3 - 3.0 * x2 + 1.0;
    let h10 = x3 - 2.0 * x2 + x;
    let h01 = -2.0 * x3 + 3.0 * x2;
    let h11 = x3 - x2;
    h00 * l_a + h10 * (b - a) * slope + h01 * b + h11 * (b - a)
}

/// QML MultiEffect 光学增益的线性近似：brightness 加、contrast 绕 0.5
/// 光学增益与暗色压暗 tint 处理。
fn apply_gains(buf: &mut [f32], params: &GlassParams) {
    let contrast_k = 1.0 + params.contrast;
    for px in buf.chunks_exact_mut(3) {
        for c in px.iter_mut() {
            let delta = *c - params.pivot;
            let expanded = if delta > 0.0 {
                delta * contrast_k
            } else {
                delta
            };
            *c = expanded + params.pivot + params.brightness;
        }
        let luma = 0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2];
        for c in px.iter_mut() {
            *c = luma + (*c - luma) * params.saturation;
        }
        if let Some(ceiling) = params.highlight_ceiling {
            let cur_luma = (0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2]).max(0.001);
            if cur_luma > ceiling - HIGHLIGHT_KNEE {
                let out =
                    highlight_soft_knee(cur_luma, ceiling, params.highlight_slope, HIGHLIGHT_KNEE);
                let scale = out / cur_luma;
                for c in px.iter_mut() {
                    *c *= scale;
                }
            }
        }
        if let Some(floor) = params.shadow_floor {
            let cur_luma = (0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2]).max(0.001);
            if cur_luma < floor + HIGHLIGHT_KNEE {
                let out = shadow_soft_knee(cur_luma, floor, params.shadow_slope, HIGHLIGHT_KNEE);
                let scale = out / cur_luma;
                for c in px.iter_mut() {
                    *c *= scale;
                }
            }
        }
        if let Some((tint, alpha)) = params.tint {
            let tr = f32::from(tint.r()) / 255.0;
            let tg = f32::from(tint.g()) / 255.0;
            let tb = f32::from(tint.b()) / 255.0;
            px[0] = px[0] * (1.0 - alpha) + tr * alpha;
            px[1] = px[1] * (1.0 - alpha) + tg * alpha;
            px[2] = px[2] * (1.0 - alpha) + tb * alpha;
        }
        for c in px.iter_mut() {
            *c = c.clamp(0.0, 1.0);
        }
    }
}

/// 3×box 近似高斯的盒宽序列（box blur → σ 换算经典式）。
fn boxes_for_gauss(sigma: f32, n: u32) -> Vec<usize> {
    let sigma = sigma.max(0.2);
    let ideal = (12.0 * sigma * sigma / n as f32 + 1.0).sqrt();
    let mut lower = ideal.floor();
    if lower % 2.0 == 0.0 {
        lower -= 1.0;
    }
    let upper = lower + 2.0;
    let m = ((12.0 * sigma * sigma
        - n as f32 * lower * lower
        - 4.0 * n as f32 * lower
        - 3.0 * n as f32)
        / (-4.0 * lower - 4.0))
        .round();
    (0..n)
        .map(|i| {
            let box_w = if (i as f32) < m { lower } else { upper };
            ((box_w - 1.0) / 2.0).max(0.0) as usize
        })
        .collect()
}

/// 单向盒式模糊（滑动窗口和，边缘 clamp；O(n)，debug 构建也无压力）。
fn box_pass(buf: &mut [f32], w: usize, h: usize, r: usize, horizontal: bool) {
    if w == 0 || h == 0 {
        return;
    }
    let (line_len, lines, step, line_step) = if horizontal {
        (w, h, 3, 3 * w)
    } else {
        (h, w, 3 * w, 3)
    };
    let window = (2 * r + 1) as f32;
    let mut out = vec![0.0_f32; buf.len()];
    for line in 0..lines {
        let base = line * line_step;
        let at = |i: usize| base + i.min(line_len - 1) * step;
        let mut sum = [0.0_f32; 3];
        for k in 0..=r {
            for c in 0..3 {
                sum[c] += buf[at(k) + c];
            }
        }
        for c in 0..3 {
            sum[c] += buf[at(0) + c] * r as f32;
        }
        for i in 0..line_len {
            for c in 0..3 {
                out[base + i * step + c] = sum[c] / window;
            }
            let add = at(i + r + 1);
            let sub = at(i.saturating_sub(r));
            for c in 0..3 {
                sum[c] += buf[add + c] - buf[sub + c];
            }
        }
    }
    buf.copy_from_slice(&out);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snap_rect_quantizes_to_device_grid_and_absorbs_tail_drift() {
        let r = Rect::from_min_max(Pos2::new(10.03, 20.47), Pos2::new(110.51, 220.94));
        let q = snap_rect_device_px(r, 1.25);
        // 各边落在 ppp=1.25 的设备像素网格上（乘 ppp 后为整数）
        for v in [q.min.x, q.min.y, q.max.x, q.max.y] {
            assert!((v * 1.25).fract().abs() < 1e-4, "{v} 不在设备像素网格");
        }
        // 布局尾差（末位比特级抖动）量化到同一格 → 不得触发重拍
        let r2 = Rect::from_min_max(
            Pos2::new(10.03 + 3e-4, 20.47 - 3e-4),
            Pos2::new(110.51 + 3e-4, 220.94 - 3e-4),
        );
        assert_eq!(q, snap_rect_device_px(r2, 1.25), "尾差应落同格");
        // 真实换位（≥1 设备像素）必须仍被识别为变化
        let moved = Rect::from_min_max(Pos2::new(10.03, 20.47), Pos2::new(110.51, 220.94 + 0.8));
        assert_ne!(
            q,
            snap_rect_device_px(moved, 1.25),
            "真实位移不得被量化吞掉"
        );
    }

    #[test]
    fn sdf_mask_is_rounded_with_feather() {
        let (half_w, half_h, r) = (50.0, 50.0, 12.0);
        assert!(
            sd_rounded_rect(0.0, 0.0, half_w, half_h, r) < -20.0,
            "中心深负"
        );
        assert!(
            sd_rounded_rect(half_w, half_h, half_w, half_h, r) > 0.0,
            "方角在外"
        );
        // 圆角弧上一点：45° 方向圆心 (half−r) + r·(1/√2, 1/√2) 在边界上
        let d = sd_rounded_rect(
            half_w - r + r * std::f32::consts::FRAC_1_SQRT_2,
            half_h - r + r * std::f32::consts::FRAC_1_SQRT_2,
            half_w,
            half_h,
            r,
        );
        assert!(d.abs() < 0.5, "圆角边界处 ≈ 0，实际 {d}");
    }

    #[test]
    fn blur_reduces_variance_and_keeps_mean() {
        let w = 32;
        let h = 32;
        let mut buf = vec![0.0_f32; (w * h * 3) as usize];
        for (i, v) in buf.iter_mut().enumerate() {
            *v = if (i / 3) % 2 == 0 { 0.1 } else { 0.9 };
        }
        let mean_before: f32 = buf.iter().sum::<f32>() / buf.len() as f32;
        for r in boxes_for_gauss(8.0, 3) {
            box_pass(&mut buf, w as usize, h as usize, r, true);
            box_pass(&mut buf, w as usize, h as usize, r, false);
        }
        let mean_after: f32 = buf.iter().sum::<f32>() / buf.len() as f32;
        let var: f32 = buf.iter().map(|v| (v - mean_after).powi(2)).sum::<f32>() / buf.len() as f32;
        assert!(var < 0.01, "棋盘应被抹平，方差 {var}");
        assert!((mean_before - mean_after).abs() < 0.02, "均值守恒");
    }

    #[test]
    fn gains_brighten_and_clamp() {
        let mut buf = vec![0.5_f32; 3];
        apply_gains(
            &mut buf,
            &GlassParams {
                sigma: 7.5,
                brightness: 0.02,
                contrast: 0.06,
                pivot: 0.5,
                saturation: 1.45,
                tint: None,
                highlight_ceiling: None,
                highlight_slope: 0.35,
                shadow_floor: None,
                shadow_slope: 0.0,
                sheen: 0.0,
            },
        );
        assert!(buf[0] > 0.5, "灰底应变亮");
        let mut white = vec![1.0_f32; 3];
        apply_gains(
            &mut white,
            &GlassParams {
                sigma: 7.5,
                brightness: 0.04,
                contrast: 0.10,
                pivot: 0.5,
                saturation: 1.50,
                tint: None,
                highlight_ceiling: None,
                highlight_slope: 0.35,
                shadow_floor: None,
                shadow_slope: 0.0,
                sheen: 0.0,
            },
        );
        assert_eq!(white[0], 1.0, "钳到 1.0");

        let mut dark = vec![0.11_f32; 3];
        apply_gains(&mut dark, &main_params(true));
        assert!(dark[0] > 0.11, "暗色模式底板应微抬略亮于底板，消除冷峻感");

        let mut bright = vec![0.9_f32; 3];
        apply_gains(&mut bright, &main_params(true));
        assert!(
            bright[0] <= 0.52,
            "暗色模式高光图标应被滚降收进可读带（≤~132 级）"
        );

        let mut white_bg = vec![1.0_f32; 3];
        apply_gains(&mut white_bg, &main_params(true));
        assert!(
            white_bg[0] <= 0.52 && white_bg[0] >= 0.46,
            "纯白亮底压至 ≈128 级（≈4:1），非死灰板"
        );

        let mut mid = vec![0.5_f32; 3];
        apply_gains(&mut mid, &main_params(true));
        assert!(mid[0] > 0.40, "中间调保持透明结构，不压向死底");
    }

    #[test]
    fn highlight_knee_blends_smoothly() {
        // 软膝过渡带内输出单调无跳变（banding 防护）
        let params = main_params(true);
        let mut prev = 0.0_f32;
        for i in 0..20 {
            let l = 0.34 + i as f32 * 0.005;
            let mut px = vec![l; 3];
            apply_gains(&mut px, &params);
            assert!(px[0] > prev, "膝部输出应单调递增 @{l:.3}");
            prev = px[0];
        }
    }

    /// sRGB 通道值 → 线性光（WCAG 相对亮度定义）。
    fn srgb_to_linear(c: f32) -> f32 {
        if c <= 0.04045 {
            c / 12.92
        } else {
            ((c + 0.055) / 1.055).powf(2.4)
        }
    }

    /// 墨字（亮主题 #1D1D1F）在玻璃输出亮度 L 上的 WCAG 对比度。
    fn ink_contrast(l: f32) -> f32 {
        let glass =
            0.2126 * srgb_to_linear(l) + 0.7152 * srgb_to_linear(l) + 0.0722 * srgb_to_linear(l);
        let ink = (29.0 * 0.2126 + 29.0 * 0.7152 + 31.0 * 0.0722) / 255.0;
        let ink_lin = srgb_to_linear(ink);
        let (hi, lo) = if glass > ink_lin {
            (glass, ink_lin)
        } else {
            (ink_lin, glass)
        };
        (hi + 0.05) / (lo + 0.05)
    }

    #[test]
    fn light_unit_floors_dark_backdrops_for_ink() {
        // 亮色档构造性保证：黑/深底透上来不再成「脏黑斑」，墨字 ≥4.5:1
        for (name, l) in [("black", 0.0_f32), ("deep", 0.1), ("mid", 0.5)] {
            let mut px = vec![l; 3];
            apply_gains(&mut px, &main_params(false));
            assert!(
                px[0] >= 0.54,
                "{name} 底应抬进地板带（≥138 级），实得 {}",
                px[0] * 255.0
            );
            assert!(
                ink_contrast(px[0]) >= 4.5,
                "{name} 底墨字对比不足：{:.2}:1",
                ink_contrast(px[0])
            );
        }
    }

    #[test]
    fn light_unit_keeps_flat_canvas_anchor_and_caps_wash() {
        // 平画布 #F5F5F7 落点 ≈ QML 假玻璃锚点 (250,250,252)：玻璃仍亮于
        // 底板，且不再把 B 通道硬钳到白；纯白与亮彩顶端同样收进 ≤255。
        let mut flat = vec![245.0 / 255.0, 245.0 / 255.0, 247.0 / 255.0];
        apply_gains(&mut flat, &main_params(false));
        assert!(
            (flat[0] * 255.0 - 251.0).abs() <= 2.0,
            "平画布落点偏离锚点：{}",
            flat[0] * 255.0
        );
        assert!(flat[2] < 0.999, "B 通道不应硬钳白：{}", flat[2] * 255.0);
        let mut white = vec![1.0_f32; 3];
        apply_gains(&mut white, &main_params(false));
        assert!(white[0] <= 1.0, "纯白不溢出");
    }

    #[test]
    fn shadow_knee_blends_smoothly_and_continuous() {
        // 地板膝过渡带单调，且膝点两侧值连续（C¹ 由构造保证，此处验值）
        let (floor, slope, half) = (0.66_f32, 0.15_f32, HIGHLIGHT_KNEE);
        let a = floor - half;
        let b = floor + half;
        let tail = shadow_soft_knee(a - 1e-4, floor, slope, half);
        let knee = shadow_soft_knee(a + 1e-4, floor, slope, half);
        assert!((tail - knee).abs() < 1e-3, "膝下沿值连续 {tail} vs {knee}");
        let t2 = shadow_soft_knee(b + 1e-4, floor, slope, half);
        assert!((t2 - (b + 1e-4)).abs() < 1e-3, "膝上沿回归恒等");
        let mut prev = 0.0_f32;
        for i in 0..80 {
            let l = i as f32 * 0.01;
            let out = shadow_soft_knee(l, floor, slope, half);
            assert!(out > prev, "地板膝应单调递增 @{l:.2}");
            prev = out;
        }
    }

    /// 出图 probe（DUO_GLASS_PROBE=1 cargo test probe_dump_units）：
    /// 暗色/亮色默认档对合成背景（平画布 + 色斑 + 黑白灰验垫）各烘
    /// 一块菜单玻璃并合成落盘 /tmp/glass_probe/，供参数目验。
    /// 原理与 build_texture 同链（blur→gains→sheen+蒙版），无 GPU。
    #[test]
    fn probe_dump_units() {
        if std::env::var_os("DUO_GLASS_PROBE").is_none() {
            return;
        }
        let (bw, bh) = (420usize, 300usize);
        let mut bg = vec![0.0_f32; bw * bh * 3];
        for row in 0..bh {
            for col in 0..bw {
                let i = (row * bw + col) * 3;
                let v = 245.0 / 255.0;
                bg[i] = v;
                bg[i + 1] = v;
                bg[i + 2] = 247.0 / 255.0;
            }
        }
        // 色斑（蓝左上 / 绿右下，同心三层近似）+ 验垫带（黑/白/灰/棋盘）
        paint_spot(&mut bg, bw, bh, 0.22, 0.24, 130.0, [0.40, 0.70, 1.00]);
        paint_spot(&mut bg, bw, bh, 0.78, 0.72, 120.0, [0.30, 0.90, 0.55]);
        for row in 220..260 {
            for col in 12..bw - 12 {
                let band = col / 52;
                let v = match band {
                    0 => 0.0,
                    1 => 1.0,
                    2 => 0.5,
                    _ => {
                        if (col / 4) % 2 == 0 {
                            1.0
                        } else {
                            0.0
                        }
                    }
                };
                let i = (row * bw + col) * 3;
                bg[i] = v;
                bg[i + 1] = v;
                bg[i + 2] = v;
            }
        }
        let _ = std::fs::create_dir_all("/tmp/glass_probe");
        for (name, params) in [("dark", main_params(true)), ("light", main_params(false))] {
            // 菜单矩形 (40,8)-(240,276) 外扩 56 裁剪 → blur → gains
            let (mx0, my0, mx1, my1) = (40.0_f32, 8.0_f32, 240.0_f32, 276.0_f32);
            let (cx0, cy0) = (
                (mx0 - 56.0).max(0.0) as usize,
                (my0 - 56.0).max(0.0) as usize,
            );
            let (cx1, cy1) = (
                (mx1 + 56.0).min(bw as f32) as usize,
                (my1 + 56.0).min(bh as f32) as usize,
            );
            let (w, h) = (cx1 - cx0, cy1 - cy0);
            let mut buf = vec![0.0_f32; w * h * 3];
            for row in 0..h {
                for col in 0..w {
                    let src = ((cy0 + row) * bw + cx0 + col) * 3;
                    let dst = (row * w + col) * 3;
                    buf[dst..dst + 3].copy_from_slice(&bg[src..src + 3]);
                }
            }
            for r in boxes_for_gauss(params.sigma, 3) {
                box_pass(&mut buf, w, h, r, true);
                box_pass(&mut buf, w, h, r, false);
            }
            apply_gains(&mut buf, &params);
            let mut out = vec![0u8; bw * bh * 3];
            for row in 0..bh {
                for col in 0..bw {
                    let i = (row * bw + col) * 3;
                    let inside = (col as f32) >= mx0
                        && (col as f32) < mx1
                        && (row as f32) >= my0
                        && (row as f32) < my1;
                    let (r, g, b2) = if inside {
                        let grow = row as f32 - my0;
                        let sheen = 1.0 + params.sheen * (0.5 - grow / (my1 - my0)) * 2.0;
                        let gc = ((row - cy0) * w + (col - cx0)) * 3;
                        let c = |v: f32| (v * sheen * 255.0).round().clamp(0.0, 255.0) as u8;
                        (c(buf[gc]), c(buf[gc + 1]), c(buf[gc + 2]))
                    } else {
                        let c = |v: f32| (v * 255.0).round() as u8;
                        (c(bg[i]), c(bg[i + 1]), c(bg[i + 2]))
                    };
                    out[i] = r;
                    out[i + 1] = g;
                    out[i + 2] = b2;
                }
            }
            image::save_buffer(
                format!("/tmp/glass_probe/{name}.png"),
                &out,
                bw as u32,
                bh as u32,
                image::ColorType::Rgb8,
            )
            .unwrap();
        }
    }

    /// probe 用：同心三层矩形逼近径向色斑（近似 paint::canvas_spots）。
    fn paint_spot(
        bg: &mut [f32],
        bw: usize,
        bh: usize,
        cx: f32,
        cy: f32,
        radius: f32,
        rgb: [f32; 3],
    ) {
        let (px, py) = (cx * bw as f32, cy * bh as f32);
        for layer in 0..3 {
            let r = radius * (1.0 - 0.28 * layer as f32);
            let alpha = 0.14 - 0.03 * layer as f32;
            for row in 0..bh {
                for col in 0..bw {
                    let d =
                        ((col as f32 + 0.5 - px).powi(2) + (row as f32 + 0.5 - py).powi(2)).sqrt();
                    if d < r {
                        let i = (row * bw + col) * 3;
                        for c in 0..3 {
                            bg[i + c] = bg[i + c] * (1.0 - alpha) + rgb[c] * alpha;
                        }
                    }
                }
            }
        }
    }
}

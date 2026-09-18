//! Main.qml 视觉原语的 painter 对译（P1 渲染基建）。
//!
//! 每个函数头部注明 QML 出处（文件 + 行为），数值一律照抄不发明。
//! 纯绘制无状态；交互命中由调用方 allocate_rect 完成。

use crate::theme::Tokens;
use egui::{Color32, CornerRadius, FontId, Pos2, Rect, Shape, Stroke, Vec2};

/// 卡片：fill + 1px cardBorder 亮边（Main.qml 各卡 Rectangle；零阴影）。
pub fn card(painter: &egui::Painter, rect: Rect, t: &Tokens) {
    painter.rect_filled(rect, CornerRadius::same(16), t.card);
    painter.rect_stroke(
        rect,
        CornerRadius::same(16),
        Stroke::new(1.0_f32, t.card_border),
        egui::StrokeKind::Inside,
    );
}

/// 圆角矩形填充（QML Rectangle{radius} 直译）。
pub fn rounded_fill(painter: &egui::Painter, rect: Rect, radius: f32, color: Color32) {
    painter.rect_filled(rect, CornerRadius::same(radius as u8), color);
}

/// 状态点：dotSize 点核 + ringWidth 白环（Main.qml component Dot）。
pub fn dot(painter: &egui::Painter, center: Pos2, dot_size: f32, ring: f32, color: Color32) {
    if ring > 0.0 {
        let outer = dot_size + 2.0 * ring;
        painter.circle_filled(center, outer / 2.0, Color32::WHITE);
    }
    painter.circle_filled(center, dot_size / 2.0, color);
}

/// 字体档：DemiBold 用独立粗体 family（"duo-bold"，fonts.rs 注册）。
pub fn font_id(px: f32, strong: bool) -> FontId {
    if strong {
        FontId::new(px, egui::FontFamily::Name("duo-bold".into()))
    } else {
        FontId::proportional(px)
    }
}

/// 居中文本（QML Text anchors.centerIn 对译）。
pub fn text_centered(
    painter: &egui::Painter,
    center: Pos2,
    s: &str,
    px: f32,
    strong: bool,
    color: Color32,
) {
    let galley = painter.ctx().fonts(|f| {
        f.layout_job(egui::text::LayoutJob::simple(
            s.to_owned(),
            font_id(px, strong),
            color,
            f32::INFINITY,
        ))
    });
    let size = galley.size();
    let pos = center - Vec2::new(size.x / 2.0, size.y / 2.0);
    painter.galley(pos, galley, Color32::WHITE);
}

/// 左对齐文本（QML Text 默认对齐）。
pub fn text_left(painter: &egui::Painter, pos: Pos2, s: &str, px: f32, color: Color32) {
    text_left_weight(painter, pos, s, px, color, false);
}

/// 圆角描边（1px，Inside 语义）。
pub fn rounded_stroke(painter: &egui::Painter, rect: Rect, radius: f32, color: Color32) {
    painter.rect_stroke(
        rect,
        egui::CornerRadius::same(radius as u8),
        egui::Stroke::new(1.0_f32, color),
        egui::StrokeKind::Inside,
    );
}

/// 圆描边（1px）。
pub fn circle_stroke(painter: &egui::Painter, center: Pos2, radius: f32, color: Color32) {
    painter.circle_stroke(center, radius, egui::Stroke::new(1.0_f32, color));
}

pub fn text_left_weight(
    painter: &egui::Painter,
    pos: Pos2,
    s: &str,
    px: f32,
    color: Color32,
    bold: bool,
) {
    let galley = painter.ctx().fonts(|f| {
        f.layout_job(egui::text::LayoutJob::simple(
            s.to_owned(),
            font_id(px, bold),
            color,
            f32::INFINITY,
        ))
    });
    painter.galley(pos, galley, Color32::WHITE);
}

/// 左对齐、垂直居中于 pos.y（QML anchors.verticalCenter 对译；设置页
/// 标签/小节头用，消除 egui/Qt 字体度量差导致的行内偏移）。
pub fn text_left_at_center(painter: &egui::Painter, pos: Pos2, s: &str, px: f32, color: Color32) {
    let galley = painter.ctx().fonts(|f| {
        f.layout_job(egui::text::LayoutJob::simple(
            s.to_owned(),
            FontId::proportional(px),
            color,
            f32::INFINITY,
        ))
    });
    let pos = Pos2::new(pos.x, pos.y - galley.size().y / 2.0);
    painter.galley(pos, galley, Color32::WHITE);
}

/// 半透明色叠在不透明底上（theme::over 的本地别名，语义同 QML）。
fn blend_over(base: Color32, rgb: Color32, a: f32) -> Color32 {
    crate::theme::over(base, rgb, a)
}

/// 画布色斑：六枚同心衰减圆（Main.qml bgLayer 照抄）。三层 alpha 预合成
/// 为不透明色——egui 增量重绘不擦除，半透明逐帧叠加会饱和（实测教训）。
pub fn canvas_spots(painter: &egui::Painter, t: &Tokens, canvas: Rect) {
    let stack = |layers: [(Color32, f32); 3]| {
        let c0 = blend_over(t.bg, layers[0].0, layers[0].1);
        let c1 = blend_over(c0, layers[1].0, layers[1].1);
        let c2 = blend_over(c1, layers[2].0, layers[2].1);
        [c0, c1, c2]
    };
    let blues = stack(t.spot_blue);
    let greens = stack(t.spot_green);
    for (i, (x, y, d)) in crate::theme::SPOTS_BLUE.iter().enumerate() {
        let r = Rect::from_min_size(canvas.left_top() + Vec2::new(*x, *y), Vec2::splat(*d));
        painter.circle_filled(r.center(), d / 2.0, blues[i]);
    }
    for (i, (x, y, d)) in crate::theme::SPOTS_GREEN.iter().enumerate() {
        let r = Rect::from_min_size(canvas.left_top() + Vec2::new(*x, *y), Vec2::splat(*d));
        painter.circle_filled(r.center(), d / 2.0, greens[i]);
    }
}

/// G2 squircle 路径（QML AppGlyph.g2SquircleSource 同构：n=2/5 超椭圆，
/// r = size/2；duo-core icons::g2_outline 提供同一份数学）。
pub fn g2_squircle(rect: Rect, color: Color32) -> Shape {
    let size = rect.width().min(rect.height());
    let pts =
        duo_core::icons::g2_outline(f64::from(size), f64::from(size), f64::from(size / 2.0), 5.0);
    let to_screen = |p: (f64, f64)| {
        Pos2::new(
            (rect.left() + p.0 as f32).round(),
            (rect.top() + p.1 as f32).round(),
        )
    };
    let path: Vec<Pos2> = pts.iter().map(|p| to_screen(*p)).collect();
    Shape::convex_polygon(path, color, Stroke::new(1.0_f32, color))
}

/// 标签像素宽自适应截断（AppTile label 的 slice(0,6)+"…" 换像素规则）：
/// 整串放得下则原样返回（窄字母长名不再被字数规则误截）；放不下时保留
/// 最长前缀使 前缀宽 + "…" 宽 ≤ max_w，再补 "…"——"…" 宽度计入预算，
/// 装不下就逐字回退。宽度函数由调用方注入（真字形宽 =
/// fonts(|f| f.glyph_width(font_id, ch))，字体栈内含 CJK 兜底），纯逻辑可测。
pub fn elide_to_width(s: &str, max_w: f32, char_w: &dyn Fn(char) -> f32) -> String {
    let ell_w = char_w('…');
    let mut chars: Vec<(char, f32)> = s.chars().map(|ch| (ch, char_w(ch))).collect();
    let mut used: f32 = chars.iter().map(|(_, w)| w).sum();
    if used <= max_w {
        return s.to_owned(); // 整串放得下：不截
    }
    // 放不下：为 "…" 留位，超宽就退字
    while used + ell_w > max_w {
        let Some((_, w)) = chars.pop() else { break };
        used -= w;
    }
    if chars.is_empty() {
        // 一个字都装不下：装得下 "…" 就只画 "…"（QML ElideRight 同款兜底），
        // 连 "…" 都超宽则留空，宁缺勿溢出。
        return if ell_w <= max_w {
            "…".to_owned()
        } else {
            String::new()
        };
    }
    let mut head = String::new();
    head.extend(chars.iter().map(|(ch, _)| ch));
    head.push('…');
    head
}

/// 清空钮/停止 ✕ 的双斜线（QML 两条 10×1.6 旋转矩形；线帽圆头）。
pub fn x_mark(painter: &egui::Painter, center: Pos2, color: Color32) {
    let half = 5.0_f32;
    for dir in [1.0, -1.0] {
        let a = center + Vec2::new(-half, -half * dir);
        let b = center + Vec2::new(half, half * dir);
        painter.line_segment([a, b], Stroke::new(1.6_f32, color));
    }
}

/// 放大镜（搜索胶囊内联 SVG 直译：r4.5 圆 + 提柄，线宽 1.5，ink2 定色）。
pub fn magnifier(painter: &egui::Painter, center: Pos2, color: Color32) {
    let c = center + Vec2::new(-1.0, -1.0);
    painter.circle_stroke(c, 4.5, Stroke::new(1.5_f32, color));
    let a = c + Vec2::new(3.6, 3.6);
    painter.line_segment(
        [a, center + Vec2::new(7.0, 7.0)],
        Stroke::new(1.5_f32, color),
    );
}

/// 扬声器单体矢量（镜像卡 Canvas onPaint 直译：ink2，线宽 1.4）。
pub fn speaker(painter: &egui::Painter, top_left: Pos2, color: Color32) {
    let p = |dx: f32, dy: f32| top_left + Vec2::new(dx, dy);
    // 箱体多边形 (1,5)(4,5)(8,1)(8,13)(4,9)(1,9)
    let body = vec![
        p(1.0, 5.0),
        p(4.0, 5.0),
        p(8.0, 1.0),
        p(8.0, 13.0),
        p(4.0, 9.0),
        p(1.0, 9.0),
    ];
    painter.add(Shape::convex_polygon(body, color, Stroke::NONE));
    let hub = p(8.5, 7.0);
    for r in [3.0_f32, 5.5] {
        // 圆弧（-0.85..0.85 rad）：折线近似 8 段（QML Canvas.arc 直译）
        let steps = 8;
        let mut prev: Option<Pos2> = None;
        for i in 0..=steps {
            let th = -0.85 + (1.7 * i as f32 / steps as f32);
            let pt = Pos2::new(hub.x + r * th.cos(), hub.y + r * th.sin());
            if let Some(a) = prev {
                painter.line_segment([a, pt], Stroke::new(1.4_f32, color));
            }
            prev = Some(pt);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::theme::{fallback_color, FALLBACK_PALETTE};

    #[test]
    fn fallback_color_is_deterministic_and_in_palette() {
        let a = fallback_color("tv.danmaku.bili");
        let b = fallback_color("tv.danmaku.bili");
        assert_eq!(a, b);
        assert!(FALLBACK_PALETTE.contains(&a));
        assert_ne!(
            fallback_color("tv.danmaku.bili"),
            fallback_color("com.tencent.mm")
        );
    }

    /// 像素宽自适应截断（elide_to_width）的合成宽函数：ASCII 与 "…"
    /// 记 1px，其余（CJK 全宽）记 2px，方便手算边界。
    fn synth_w(ch: char) -> f32 {
        if ch == '…' || ch.is_ascii() {
            1.0
        } else {
            2.0
        }
    }

    #[test]
    fn elide_fits_exactly_or_packs_by_pixel() {
        // 恰好放下：整串宽 == 预算 → 原样，不加 "…"
        assert_eq!(elide_to_width("abcdef", 6.0, &synth_w), "abcdef");
        // 恰好放不下：截后 前缀+"…" 恰好 == 预算（“…” 计入预算的边界）
        assert_eq!(elide_to_width("abcdefg", 6.0, &synth_w), "abcde…");
        // 前缀+"…" 会超预算时退字：4.9 只装得下 3 字 + "…"（ab.. 由 2+1）
        assert_eq!(elide_to_width("abcdefg", 3.9, &synth_w), "ab…");
    }

    #[test]
    fn elide_empty_and_fullwidth_and_nothing_fits() {
        // 空串
        assert_eq!(elide_to_width("", 10.0, &synth_w), "");
        // 全宽字符：预算 2 恰好装一个 CJK
        assert_eq!(elide_to_width("中", 2.0, &synth_w), "中");
        assert_eq!(elide_to_width("中", 1.9, &synth_w), "…"); // 一字装不下→只画省略号
        assert_eq!(elide_to_width("中", 0.5, &synth_w), ""); // 连 "…" 都超宽→留空
                                                             // CJK+拉丁混合按各自字形宽计（2px CJK 顶得掉 1px 拉丁）：
                                                             // 7px 全串 > 预算 5 → 退到 "ab中" + "…" = 5
        assert_eq!(elide_to_width("ab中cde", 5.0, &synth_w), "ab中…");
    }

    #[test]
    fn elide_keeps_full_name_that_old_rule_mistruncated() {
        // 修复动机：FlClash/ChatGPT 这类 >6 字符的窄字母名，旧 6 字规则
        // 截成 "Flclas…"；像素规则下放得下就完整显示。
        assert_eq!(elide_to_width("FlClash", 7.0, &synth_w), "FlClash");
        assert_eq!(elide_to_width("ChatGPT", 7.0, &synth_w), "ChatGPT");
        // 7 个 CJK（14px）放不下 13px 预算：截尾 + "…" 恰好 13
        assert_eq!(
            elide_to_width("哔哩哔哩动画集", 13.0, &synth_w),
            "哔哩哔哩动画…"
        );
    }
}

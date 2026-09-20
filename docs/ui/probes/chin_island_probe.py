"""chin island probe - 定量验收浮动岛四象限截图。

用法: python3 chin_island_probe.py <dir-with-chin-*.png>
量测:
  1) 岛 bounding box(贴底区域非背景行) + 左右/底边距(DIP 换算)
  2) 四角圆角检测(岛 bbox 角点 vs 背景色)
  3) 岛内部材质(纯色 vs 有色彩方差的亚克力; 条纹区色相渗入)
  4) 药丸存在性(中心小胶囊, 行内亮度峰)
"""
import sys, os
from PIL import Image

BG = (245, 245, 247)


def near(c, ref, tol=6):
    return all(abs(a - b) <= tol for a, b in zip(c[:3], ref))


def lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def analyze(path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    print(f"\n== {os.path.basename(path)}  {w}x{h} ==")

    # 下 30% 找岛: 每行统计显著偏离背景的像素数
    rows = {}
    for y in range(int(h * 0.70), h):
        cnt = sum(0 if near(px[x, y], BG, 10) else 1 for x in range(0, w, 2))
        rows[y] = cnt
    active = [y for y, c in rows.items() if c > w * 0.25 / 2]
    if not active:
        print("  !! 底部未检出岛 (宽 >=25% 的非背景行)")
        return
    top, bot = min(active), max(active)
    print(f"  island rows: y {top}..{bot}  height={bot - top + 1}px")

    # 列范围 (岛内中间行)
    ymid = (top + bot) // 2
    xs = [x for x in range(w) if not near(px[x, ymid], BG, 10)]
    if not xs:
        print("  !! 中间行无岛像素")
        return
    l, r = min(xs), max(xs)
    print(f"  island cols: x {l}..{r}  width={r - l + 1}px  "
          f"margins L={l} R={w - 1 - r} B={h - 1 - bot}")

    # 四角圆角: 岛 bbox 四角 3x3 是否为背景(圆)还是实心(方)
    for name, cx, cy in (("TL", l, top), ("TR", r, top),
                         ("BL", l, bot), ("BR", r, bot)):
        c = px[cx, cy]
        print(f"  corner {name}: rgb={c}  {'BG(圆角/透明)' if near(c, BG, 10) else 'SOLID(方角!)'}")

    # 内部材质: 条纹区(x 0..40% 岛内) 与 文字区(x 40..100%) 的均值/方差
    def region_stats(x0, x1, y0, y1):
        vals, lums = [], []
        for y in range(y0, y1):
            for x in range(x0, x1, 2):
                c = px[x, y]
                vals.append(c)
                lums.append(lum(c))
        n = len(vals)
        mean = tuple(sum(v[i] for v in vals) // n for i in range(3))
        spread = max(lums) - min(lums)
        # 色相方差 (每通道方差的均值)
        var = sum(sum((v[i] - mean[i]) ** 2 for v in vals) / n for i in range(3)) / 3
        return mean, spread, var

    inner_top, inner_bot = top + 4, bot - 4
    inner_l, inner_r = l + 16, r - 16
    mean_a, spread_a, var_a = region_stats(
        inner_l, int(inner_l + (inner_r - inner_l) * 0.4), inner_top, inner_bot)
    mean_b, spread_b, var_b = region_stats(
        int(inner_l + (inner_r - inner_l) * 0.6), inner_r, inner_top, inner_bot)
    print(f"  interior(stripe zone): mean={mean_a} lum_spread={spread_a:.0f} var={var_a:.0f}")
    print(f"  interior(text zone)  : mean={mean_b} lum_spread={spread_b:.0f} var={var_b:.0f}")
    verdict = "亚克力(有色差/渗入)" if var_a > 120 or spread_a > 40 else "近平板纯色"
    print(f"  材质判定: {verdict}")

    # 药丸: 中心 ±10px 行内亮度峰(相对岛均值)
    cx0 = w // 2 - 80
    cx1 = w // 2 + 80
    peak, base = 0, None
    means = []
    for y in range(top + 2, bot - 2):
        row = [lum(px[x, y]) for x in range(cx0, cx1, 2)]
        means.append(sum(row) / len(row))
    if means:
        base = sorted(means)[len(means) // 2]
        peak = max(abs(m - base) for m in means)
    print(f"  pill: center rows lum base={base:.0f} peak_dev={peak:.0f} "
          f"{'检出' if peak > 18 else '未检出!?'}")

    # 裁剪岛区保存(供视觉评审)
    crop = im.crop((max(0, l - 24), max(0, top - 24), min(w, r + 24), min(h, bot + 24)))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    out = os.path.join(os.path.dirname(path),
                       "crop-" + os.path.basename(path))
    crop.save(out)
    print(f"  crop -> {out}")


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    for name in ("chin-glass-dark", "chin-plain-dark",
                 "chin-glass-light", "chin-plain-light"):
        p = os.path.join(d, name + ".png")
        if os.path.exists(p):
            analyze(p)

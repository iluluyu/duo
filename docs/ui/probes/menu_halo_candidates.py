"""菜单文字保底光晕：决策图 + 参数扫描 + 实装态 before/after。

字重铁律（本文件场景同 text_halo_probe.py）：1px 硬描边（Text.Outline）把
墨迹量抬高 +18% 且与 alpha 无关——用户回报"变粗 + 廉价"。软光晕（模糊字形
剪影垫在清晰文字后面）不碰字形轮廓，字重恒定。

文字遮罩由 Qt 软件后端真渲染（plain / outline 两张，白字黑底 = 覆盖率）；
材质按 glass-recipe 配方在真面板截图上算；合成在 PIL 里做。

模式（仓库根目录）::

    .venv/bin/python docs/ui/probes/menu_halo_candidates.py            # 决策图
    .venv/bin/python docs/ui/probes/menu_halo_candidates.py --sweep    # σ×α 扫描
    .venv/bin/python docs/ui/probes/menu_halo_candidates.py --shipped  # before/after
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

ROWS = ["打开", "置顶到固定栏", "自适应窗口", "固定比例", "窗口栏",
        "音频独占", "断开保留画面"]
ROW_H, PAD = 32, 4
MENU_W = 128
MENU_H = PAD * 2 + ROW_H * len(ROWS)
STYLE = REPO / "duo" / "ui" / "qml" / "Style.qml"
OUT = REPO / "docs" / "validation" / "assets"
FONT = "/usr/share/fonts/noto/NotoSans-Regular.ttf"

# (代号, 说明, 类型, halo alpha, 模糊 sigma)
VARIANTS = [
        ("A", "outline 85%", "outline", 0.85, 0.0),
        ("B", "outline 40%", "outline", 0.40, 0.0),
        ("C", "soft 2.5px 55%", "soft", 0.55, 2.5),
        ("D", "soft 5px 40%", "soft", 0.40, 5.0),
        ("E", "none (control)", "none", 0.0, 0.0),
]

MASK_QML = """import QtQuick
Window {
    id: root
    width: __W__; height: __H__; visible: true; color: "#000000"
    property var labels: __LABELS__
    property bool outline: __OUTLINE__
    Column {
        x: 4; y: 4
        Repeater {
            model: __N__
            delegate: Item {
                required property int index
                width: 120; height: 32
                Text {
                    x: 6; anchors.verticalCenter: parent.verticalCenter
                    text: root.labels[index]
                    font.pixelSize: 13
                    color: "#FFFFFF"
                    style: root.outline ? Text.Outline : Text.Normal
                    styleColor: "#FFFFFF"
                }
            }
        }
    }
}
"""


# ---------------------------------------------------------------- 令牌读取
def tokens() -> dict[str, tuple[str, str]]:
        src = STYLE.read_text(encoding="utf-8")
        out: dict[str, tuple[str, str]] = {}
        for name in ("bg", "ink", "ink2", "menuInkHalo", "menuBorder",
                     "menuBright", "menuContrast", "menuSat", "menuBlur",
                     "menuHaloBlur", "menuHaloBlurMax"):
                m = re.search(rf'property \w+ {name}: (.+)', src)
                if m is None:
                        raise SystemExit(f"Style.qml: 缺令牌 {name}")
                value = m.group(1).strip()
                if value.startswith("root.dark ?"):
                        dark, light = value[len("root.dark ?"):].split(" : ", 1)
                else:
                        light = dark = value
                out[name] = (light.strip().strip('"'), dark.strip().strip('"'))
        return out


def _lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def luma_hex(hex_color: str) -> float:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def rgb(hex_color: str) -> tuple[int, int, int]:
        body = hex_color[3:] if len(hex_color) == 9 else hex_color[1:]
        return tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def blur_sigma(tok) -> float:
        """Style 的 blur × blurMax（px 半径）→ 等效高斯 σ。"""
        return float(tok["menuHaloBlur"][0]) * int(tok["menuHaloBlurMax"][0]) / 2


# ---------------------------------------------------------------- 材质
def glass_level(level: float, light: bool, tok) -> float:
        """配方后的灰阶：(v - 0.5) * (1 + contrast) + 0.5 + brightness。"""
        theme = 0 if light else 1
        bright = float(tok["menuBright"][theme])
        contrast = 1.0 + float(tok["menuContrast"][theme])
        return min(255.0, max(0.0, ((level / 255 - 0.5) * contrast + 0.5
                                    + bright) * 255))


def worst_crop(src: Path, ink_luma: float, light: bool,
               tok) -> tuple[Image.Image, tuple[int, int], float]:
        """找"玻璃化后与墨色最同调"的落点：在整图预模糊上滑窗，取文字行带
        亮度分布中最靠近墨色的一侧，换算成玻璃色后取 ink:glass 最低的一处。"""
        img = Image.open(src).convert("RGB")
        sigma = float(tok["menuBlur"][0]) * 32 / 2
        blurred = img.filter(ImageFilter.GaussianBlur(sigma)).convert("L")
        band = int(MENU_H * 0.15), int(MENU_H * 0.85)
        ink_level = ink_luma * 255.0
        dark_ink = ink_level < 128
        best = None
        for y in range(0, img.height - MENU_H, 2):
                for x in range(0, img.width - MENU_W, 2):
                        hist = blurred.crop((x, y + band[0], x + MENU_W,
                                             y + band[1])).histogram()
                        total = sum(hist)
                        acc, level, target = 0, 0.0, (0.10 if dark_ink else 0.90)
                        for i, count in enumerate(hist):
                                acc += count
                                if acc >= total * target:
                                        level = float(i)
                                        break
                        ratio = _contrast(ink_luma, _lin(glass_level(level, light,
                                                                     tok) / 255))
                        if best is None or ratio < best[0]:
                                best = (ratio, x, y)
        assert best is not None
        ratio, bx, by = best
        m = 28      # 外扩：让模糊核吃到真实内容（glass-recipe §2-2 的 28px 边距）
        crop = img.crop((max(0, bx - m), max(0, by - m),
                         min(img.width, bx + MENU_W + m),
                         min(img.height, by + MENU_H + m)))
        return crop, (bx, by), ratio


def glassify(crop: Image.Image, light: bool, tok) -> Image.Image:
        theme = 0 if light else 1
        sigma = float(tok["menuBlur"][theme]) * 32 / 2
        sat = 1.0 + float(tok["menuSat"][theme])
        bright = float(tok["menuBright"][theme])
        contrast = 1.0 + float(tok["menuContrast"][theme])
        img = crop.filter(ImageFilter.GaussianBlur(sigma))
        px_in, out = img.load(), img.copy()
        px_out = out.load()
        for y in range(img.height):
                for x in range(img.width):
                        r, g, b = px_in[x, y]
                        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                        vals = []
                        for v in (r, g, b):
                                v = lum + (v - lum) * sat
                                v = (v / 255 - 0.5) * contrast + 0.5 + bright
                                vals.append(min(255, max(0, round(v * 255))))
                        px_out[x, y] = tuple(vals)
        return out


def _contrast(l1: float, l2: float) -> float:
        return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def plate_at(light: bool, theme_index: int, tok) -> Image.Image:
        """最坏落点处的玻璃块，裁回菜单大小。"""
        src = "qml-main.png" if light else "qml-main-dark.png"
        ink = tok["ink"][theme_index]
        crop, spot, ratio = worst_crop(OUT / src, luma_hex(ink), light, tok)
        print(f"[spot] {'light' if light else 'dark'} x={spot[0]} y={spot[1]} "
              f"ink:glass~{ratio:.2f}:1")
        glass = glassify(crop, light, tok)
        mx, my = spot[0] - max(0, spot[0] - 28), spot[1] - max(0, spot[1] - 28)
        return glass.crop((mx, my, mx + MENU_W, my + MENU_H))


def flat_plate(theme_index: int, tok, backdrop: int) -> Image.Image:
        level = glass_level(backdrop, theme_index == 0, tok)
        return Image.new("RGB", (MENU_W, MENU_H), (int(level),) * 3)


# ---------------------------------------------------------------- 遮罩与合成
def render_masks() -> tuple[Image.Image, Image.Image]:
        """Qt 渲染字形覆盖率遮罩（白字黑底）：fill-only 与 fill+stroke 两张。"""
        from PyQt6 import sip
        from PyQt6.QtCore import QTimer, QUrl
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtQml import QQmlComponent, QQmlEngine
        from PyQt6.QtQuick import QQuickWindow

        app = QGuiApplication(["halo_candidates"])
        engine = QQmlEngine()
        labels = "[" + ",".join(f'"{r}"' for r in ROWS) + "]"
        shots: list[Image.Image] = []
        for outline in (False, True):
                comp = QQmlComponent(engine)
                source = (MASK_QML
                          .replace("__W__", str(MENU_W))
                          .replace("__H__", str(MENU_H))
                          .replace("__N__", str(len(ROWS)))
                          .replace("__LABELS__", labels)
                          .replace("__OUTLINE__", "true" if outline else "false"))
                comp.setData(source.encode("utf-8"),
                             QUrl(f"file:///mask-{outline}.qml"))
                if comp.status() != QQmlComponent.Status.Ready:
                        raise SystemExit(comp.errorString())
                obj = comp.create()
                win = sip.cast(obj, QQuickWindow)
                box: list = []
                QTimer.singleShot(600, lambda: (box.append(win.grabWindow()),
                                                app.quit()))
                app.exec()
                tmp = Path(f"/tmp/duo_mask_{outline}.png")
                box[0].save(str(tmp))
                shots.append(Image.open(tmp).convert("L"))
        return shots[0], shots[1]


def compose(plate: Image.Image, fill: Image.Image, stroke: Image.Image,
            variant, halo: str, theme_index: int, tok) -> Image.Image:
        """按变体合成：清晰文字永远取 fill 遮罩（字形零改动）。"""
        kind, alpha, sigma = variant[2], variant[3], variant[4]
        ink = Image.new("RGB", plate.size, rgb(tok["ink"][theme_index]))
        if kind == "none":
                return Image.composite(ink, plate, fill)
        if kind == "outline":
                stroke_cov = Image.frombytes(
                        "L", stroke.size,
                        bytes(max(0, s - f) for s, f in
                              zip(stroke.tobytes(), fill.tobytes())))
                halo_c = Image.blend(plate, Image.new("RGB", plate.size, rgb(halo)),
                                     alpha)
                return Image.composite(ink, Image.composite(halo_c, plate, stroke_cov),
                                       fill)
        glow = fill.filter(ImageFilter.GaussianBlur(sigma)).point(
                lambda v: min(255, round(v * alpha)))
        halo_c = Image.new("RGB", plate.size, rgb(halo))
        return Image.composite(ink, Image.composite(halo_c, plate, glow), fill)


def ink_mass(panel: Image.Image) -> float:
        """墨迹量代理：白底版上字形覆盖率积分（字重指标）。"""
        flat = Image.new("RGB", panel.size, (255, 255, 255))
        px_in, px_flat = panel.convert("L").load(), flat.convert("L").load()
        mass = 0.0
        for y in range(MENU_H):
                for x in range(MENU_W):
                        ink_l = _lin(px_in[x, y] / 255)
                        mass += max(0.0, 1.0 - ink_l) if ink_l < 1.0 else 0.0
        return mass


# ---------------------------------------------------------------- 指标
def cushion(panel: Image.Image, baseline: Image.Image, mask: Image.Image,
            ink_hex: str) -> tuple[float, int]:
        """光晕本身的贡献（与"无保底"版差分隔离）：被光晕改变的边缘像素上
        墨色对局部底色的对比度中位数 + 被改变像素数（光晕宽度）。"""
        px, base, m = (panel.convert("L").load(), baseline.convert("L").load(),
                       mask.load())
        ink_l = luma_hex(ink_hex)
        ratios: list[float] = []
        width = 0
        for y in range(MENU_H):
                for x in range(MENU_W):
                        if m[x, y] >= 60 or not _near_glyph(m, x, y):
                                continue
                        if abs(px[x, y] - base[x, y]) >= 3:
                                width += 1
                                ratios.append(_contrast(ink_l, _lin(px[x, y] / 255)))
        if not ratios:
                return 0.0, 0
        ratios.sort()
        return ratios[len(ratios) // 2], width


def visibility(panel: Image.Image, baseline: Image.Image,
               mask: Image.Image) -> float:
        """良性底景上光晕的可见度（对比度中位数，1.0 = 看不见）。"""
        px, base, m = (panel.convert("L").load(), baseline.convert("L").load(),
                       mask.load())
        ratios: list[float] = []
        for y in range(MENU_H):
                for x in range(MENU_W):
                        if m[x, y] >= 60 or not _near_glyph(m, x, y):
                                continue
                        if abs(px[x, y] - base[x, y]) < 3:
                                continue
                        ratios.append(_contrast(_lin(base[x, y] / 255),
                                                _lin(px[x, y] / 255)))
        if not ratios:
                return 1.0
        ratios.sort()
        return ratios[len(ratios) // 2]


def edge_max(panel: Image.Image) -> float:
        """与 text_halo_probe 同口径：文字带内相邻像素最大亮度步长（WCAG）。"""
        px = panel.convert("L").load()
        best = 0.0
        for y in range(MENU_H):
                for x in range(MENU_W - 1):
                        a, b = _lin(px[x, y] / 255), _lin(px[x + 1, y] / 255)
                        best = max(best, _contrast(a, b))
        return best


def _near_glyph(mask, x: int, y: int) -> bool:
        for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                        x1, y1 = x + dx, y + dy
                        if 0 <= x1 < MENU_W and 0 <= y1 < MENU_H and mask[x1, y1] >= 200:
                                return True
        return False


# ---------------------------------------------------------------- 模式
def mode_sheet(fill, stroke, tok) -> int:
        panels: list[Image.Image] = []
        print(f"{'theme':6} {'variant':16} {'halo contrast':>13} {'halo px':>8}"
              f"   (AA 4.5:1)")
        for light, theme_index in ((True, 0), (False, 1)):
                plate = plate_at(light, theme_index, tok)
                halo = tok["menuInkHalo"][theme_index]
                baseline = compose(plate, fill, stroke, VARIANTS[-1], halo,
                                   theme_index, tok)
                for variant in VARIANTS:
                        panel = compose(plate, fill, stroke, variant, halo,
                                        theme_index, tok)
                        panels.append(panel)
                        st, wd = cushion(panel, baseline, fill,
                                         tok["ink"][theme_index])
                        print(f"{'light' if light else 'dark':6} "
                              f"{variant[0] + ' ' + variant[1]:16} "
                              f"{st:13.2f} {wd:8d}")
        cols = len(VARIANTS)
        font = ImageFont.truetype(FONT, 14)
        sheet = Image.new("RGB", (cols * (MENU_W + 14) + 14, 2 * (MENU_H + 46) + 40),
                          (250, 250, 252))
        draw = ImageDraw.Draw(sheet)
        for c, variant in enumerate(VARIANTS):
                draw.text((14 + c * (MENU_W + 14), 12), f"{variant[0]} {variant[1]}",
                          fill=(60, 60, 66), font=font)
        for r, tag in enumerate(("light theme menu", "dark theme menu")):
                draw.text((14, 30 + r * (MENU_H + 46)),
                          f"{tag} over worst backdrop", fill=(120, 120, 128),
                          font=font)
        for i, panel in enumerate(panels):
                r, c = divmod(i, cols)
                sheet.paste(panel, (14 + c * (MENU_W + 14),
                                    30 + r * (MENU_H + 46) + 22))
        zw, zh = MENU_W * 3, 150
        zoom = Image.new("RGB", (cols * (zw + 14) + 14, 2 * (zh + 34) + 14),
                         (250, 250, 252))
        zdraw = ImageDraw.Draw(zoom)
        for c, variant in enumerate(VARIANTS):
                zdraw.text((14 + c * (zw + 14), 12), f"{variant[0]} {variant[1]}",
                           fill=(60, 60, 66), font=font)
                zdraw.text((14 + c * (zw + 14), zh + 34 + 12),
                           f"{variant[0]} (dark theme)", fill=(60, 60, 66), font=font)
        for i, panel in enumerate(panels):
                r, c = divmod(i, cols)
                zoom.paste(panel.crop((0, 60, MENU_W, 110)).resize((zw, zh),
                                                                   Image.LANCZOS),
                           (14 + c * (zw + 14), 34 + r * (zh + 34)))
        OUT.mkdir(parents=True, exist_ok=True)
        sheet.save(OUT / "menu-halo-candidates.png")
        zoom.save(OUT / "menu-halo-candidates-zoom.png")
        print("[cols] " + " | ".join(f"{v[0]}={v[1]}" for v in VARIANTS))
        print(f"[shot] {OUT / 'menu-halo-candidates.png'}")
        print(f"[shot] {OUT / 'menu-halo-candidates-zoom.png'}（文字 3x 放大，看字重）")
        return 0


def mode_sweep(fill, stroke, tok) -> int:
        print(f"{'theme':6} {'sigma':>6} {'alpha':>6} {'halo contrast':>13} "
              f"{'halo px':>8} {'canvas visibility':>17}")
        for light, theme_index in ((True, 0), (False, 1)):
                plate = plate_at(light, theme_index, tok)
                halo = tok["menuInkHalo"][theme_index]
                baseline = compose(plate, fill, stroke, VARIANTS[-1], halo,
                                   theme_index, tok)
                canvas = flat_plate(theme_index, tok,
                                    0xF5 if light else 0x21)
                canvas_base = compose(canvas, fill, stroke, VARIANTS[-1], halo,
                                      theme_index, tok)
                for sigma in (2.0, 2.5, 3.0, 3.5):
                        for alpha in (0.30, 0.35, 0.45):
                                variant = ("S", "sweep", "soft", alpha, sigma)
                                st, wd = cushion(
                                        compose(plate, fill, stroke, variant, halo,
                                                theme_index, tok),
                                        baseline, fill, tok["ink"][theme_index])
                                vis = visibility(
                                        compose(canvas, fill, stroke, variant, halo,
                                                theme_index, tok),
                                        canvas_base, fill)
                                print(f"{'light' if light else 'dark':6} "
                                      f"{sigma:6.1f} {alpha:6.2f} {st:13.2f} "
                                      f"{wd:8d} {vis:17.2f}")
        return 0


def mode_shipped(fill, stroke, tok) -> int:
        """实装态 before/after：左无保底 / 右实装软光晕（σ 由 Style 令牌推）。"""
        sigma = blur_sigma(tok)
        font = ImageFont.truetype(FONT, 13)
        panels: list[tuple[str, list[Image.Image]]] = []
        for light, theme_index in ((True, 0), (False, 1)):
                plate = plate_at(light, theme_index, tok)
                halo = tok["menuInkHalo"][theme_index]
                alpha = int(halo[1:3], 16) / 255
                bare = compose(plate, fill, stroke, VARIANTS[-1], halo,
                               theme_index, tok)
                shipped = compose(plate, fill, stroke,
                                  ("S", "shipped", "soft", alpha, sigma),
                                  halo, theme_index, tok)
                st, wd = cushion(shipped, bare, fill, tok["ink"][theme_index])
                print(f"[shipped] {'light' if light else 'dark'} sigma={sigma:.1f} "
                      f"alpha={alpha:.2f}  edge(max step): bare "
                      f"{edge_max(bare):.2f} -> shipped {edge_max(shipped):.2f}  "
                      f"halo contrast {st:.2f} over {wd}px")
                panels.append(("light theme menu" if light else "dark theme menu",
                               [bare, shipped]))
        scale = 2      # 出图给用户/视觉评审看，2× 放大
        pw, ph = MENU_W * scale, MENU_H * scale
        cw, ch = pw * 2, ph + 46 * scale
        img = Image.new("RGB", (cw + 48 * scale, len(panels) * ch + 20 * scale),
                        (250, 250, 252))
        draw = ImageDraw.Draw(img)
        big = ImageFont.truetype(FONT, 13 * scale)
        for i, (tag, pair) in enumerate(panels):
                top = 10 * scale + i * ch
                draw.text((24 * scale, top),
                          f"{tag} - worst placement in the app grid, recipe applied",
                          fill=(90, 90, 96), font=big)
                for j, panel in enumerate(pair):
                        x = 24 * scale + j * pw
                        img.paste(panel.resize((pw, ph), Image.LANCZOS),
                                  (x, top + 24 * scale))
                        label = ("before: no cushion" if j == 0
                                 else "shipped: soft halo")
                        draw.text((x + 2, top + (24 + MENU_H) * scale), label,
                                  fill=(120, 120, 128), font=big)
        OUT.mkdir(parents=True, exist_ok=True)
        img.save(OUT / "menu-legibility-before-after.png")
        print(f"[shot] {OUT / 'menu-legibility-before-after.png'}")
        return 0


def main() -> int:
        tok = tokens()
        fill, stroke = render_masks()
        if "--sweep" in sys.argv:
                return mode_sweep(fill, stroke, tok)
        if "--shipped" in sys.argv:
                return mode_shipped(fill, stroke, tok)
        return mode_sheet(fill, stroke, tok)


if __name__ == "__main__":
        raise SystemExit(main())

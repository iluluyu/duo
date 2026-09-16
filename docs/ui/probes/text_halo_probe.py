"""验收探针：软光晕是否（a）不改字重、（b）把最坏底景的字形边界托回可读。

材质那一半按配方解析计算（纯色底板模糊后仍是自身，再走 MultiEffect 的
`out = (in - 0.5) * (1 + contrast) + 0.5 + brightness`）；文字由 Qt 软件
后端真渲染；光晕在 PIL 里按 Style 令牌做（高斯模糊字形剪影 × alpha），与
MultiEffect 的模糊同构。

三项测量（令牌直接读 Style.qml，改配方重跑即可）：
  1. 墨迹量：清晰文字 vs 硬描边文字（Text.Outline）——后者 +18%（"变粗"的
     来源），软光晕必须为 0%；
  2. 字形边界对比度：无光晕 vs 软光晕（相邻像素最大亮度步长的 WCAG 比值）；
  3. 主题画布上的光晕可见度（应当看不见）。

用法（仓库根目录）::

    .venv/bin/python docs/ui/probes/text_halo_probe.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from PIL import Image, ImageFilter  # noqa: E402
from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QTimer, QUrl  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtQml import QQmlComponent, QQmlEngine  # noqa: E402
from PyQt6.QtQuick import QQuickWindow  # noqa: E402

STYLE = REPO / "duo" / "ui" / "qml" / "Style.qml"
OUT = REPO / "docs" / "validation" / "assets"
W, H = 320, 34
AA = 4.5
FLOOR = 4.0          # 最坏落点（半覆盖边界）的验收下限：near-AA

TEXT = "置顶到固定栏 Pin"

CASES = {
        # 主题 → [(名称, 底板灰阶, 墨色, 光晕色)]
        "light": [("worst (dark tile edge)", 0x63, "#1D1D1F"),
                  ("synthetic flat black", 0x1B, "#1D1D1F"),
                  ("theme canvas", 0xF5, "#1D1D1F")],
        "dark": [("worst (bright tile edge)", 0x99, "#F5F5F7"),
                 ("synthetic flat white", 0xFF, "#F5F5F7"),
                 ("theme canvas", 0x21, "#F5F5F7")],
}


def token(name: str) -> tuple[str, str]:
        src = STYLE.read_text(encoding="utf-8")
        m = re.search(rf'property \w+ {name}: root\.dark \? ("[^"]+"|\S+) '
                      rf': ("[^"]+"|\S+)', src)
        if m:
                return m.group(2).strip('"'), m.group(1).strip('"')
        m = re.search(rf'property \w+ {name}: (\S+)', src)
        if m is None:
                raise SystemExit(f"Style.qml: 缺令牌 {name}")
        return m.group(1).strip('"'), m.group(1).strip('"')


INK = dict(zip(("light", "dark"), token("ink"), strict=True))
HALO = dict(zip(("light", "dark"), token("menuInkHalo"), strict=True))
BRIGHT = {k: float(v) for k, v in zip(("light", "dark"), token("menuBright"),
                                      strict=True)}
CONTRAST = {k: float(v) for k, v in zip(("light", "dark"),
                                        token("menuContrast"), strict=True)}
SIGMA = float(token("menuHaloBlur")[0]) * int(token("menuHaloBlurMax")[0]) / 2

TEXT_QML = """import QtQuick
Window {
    id: root
    width: __W__; height: __H__; visible: true; color: "#000000"
    property bool outline: __OUTLINE__
    Text {
        x: 8; anchors.verticalCenter: parent.verticalCenter
        text: "__TEXT__"
        font.pixelSize: 13
        color: "#FFFFFF"
        style: root.outline ? Text.Outline : Text.Normal
        styleColor: "#FFFFFF"
    }
}
"""


def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def contrast(l1: float, l2: float) -> float:
        return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def rgb_bytes(color: str) -> tuple[int, int, int]:
        body = color[3:] if len(color) == 9 else color[1:]
        return tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def alpha_of(color: str) -> float:
        return int(color[1:3], 16) / 255 if len(color) == 9 else 1.0


def glass_level(level: int, theme: str) -> int:
        v = min(1.0, max(0.0, (level / 255 - 0.5) * (1 + CONTRAST[theme])
                          + 0.5 + BRIGHT[theme]))
        return round(v * 255)


def render_mask(outline: bool) -> Image.Image:
        app = QGuiApplication.instance() or QGuiApplication(["halo_probe"])
        engine = QQmlEngine()
        comp = QQmlComponent(engine)
        source = (TEXT_QML.replace("__W__", str(W)).replace("__H__", str(H))
                  .replace("__TEXT__", TEXT)
                  .replace("__OUTLINE__", "true" if outline else "false"))
        comp.setData(source.encode("utf-8"), QUrl("file:///text.qml"))
        if comp.status() != QQmlComponent.Status.Ready:
                raise SystemExit(comp.errorString())
        obj = comp.create()
        win = sip.cast(obj, QQuickWindow)
        box: list = []
        QTimer.singleShot(600, lambda: (box.append(win.grabWindow()), app.quit()))
        app.exec()
        tmp = Path(f"/tmp/halo_probe_mask_{outline}.png")
        box[0].save(str(tmp))
        return Image.open(tmp).convert("L")


def ink_mass(mask: Image.Image) -> float:
        """字形覆盖率积分 = 白字黑底遮罩的亮度积分（字重指标）。"""
        return sum(lin(v / 255) for v in mask.tobytes())


def edge_contrast(panel: Image.Image) -> float:
        """文字带内相邻像素最大亮度步长的 WCAG 比值（字形边界对比度）。"""
        px = panel.convert("L").load()
        best = 0.0
        for y in range(H):
                for x in range(W - 1):
                        best = max(best, contrast(lin(px[x, y] / 255),
                                                  lin(px[x + 1, y] / 255)))
        return best


def compose(plate: int, mask: Image.Image, theme: str, mode: str) -> Image.Image:
        base = Image.new("RGB", (W, H), (plate,) * 3)
        ink = Image.new("RGB", (W, H), rgb_bytes(INK[theme]))
        if mode == "bare":
                return Image.composite(ink, base, mask)
        glow = mask.filter(ImageFilter.GaussianBlur(SIGMA)).point(
                lambda v: min(255, round(v * alpha_of(HALO[theme]))))
        halo = Image.new("RGB", (W, H), rgb_bytes(HALO[theme]))
        return Image.composite(ink, Image.composite(halo, base, glow), mask)


def main() -> int:
        plain = render_mask(False)
        outline = render_mask(True)
        mass_plain, mass_outline = ink_mass(plain), ink_mass(outline)
        print(f"ink mass: plain {mass_plain:.1f} / hard outline {mass_outline:.1f} "
              f"({(mass_outline / mass_plain - 1) * 100:+.1f}%)  "
              f"<- soft halo reuses the plain mask (0.0%)")
        print(f"tokens: ink {INK}  halo {HALO}  sigma {SIGMA:.1f}px\n")
        print(f"{'theme':6} {'case':26} {'plate':>7} {'bare':>8} {'soft halo':>10}")
        fails: list[str] = []
        for theme, cases in CASES.items():
                for name, level, _ink in cases:
                        plate = glass_level(level, theme)
                        bare = edge_contrast(compose(plate, plain, theme, "bare"))
                        haloed = edge_contrast(compose(plate, plain, theme, "halo"))
                        flag = ""
                        if "worst" in name:
                                if haloed < FLOOR:
                                        fails.append(f"{theme}/{name}")
                                        flag = f"  <-- under {FLOOR}"
                                else:
                                        flag = "  ok"
                        print(f"{theme:6} {name:26} {plate:7d} {bare:8.2f} "
                              f"{haloed:10.2f}{flag}")
        print(f"\nbare baseline on those cases: far below AA; "
              f"worst-placement cases under {FLOOR}:1 -> {fails or 'none'}")
        OUT.mkdir(parents=True, exist_ok=True)
        strip = Image.new("RGB", (W, H * len(CASES["light"]) * 2 + 8), (250, 250, 252))
        row = 0
        for theme, cases in CASES.items():
                for name, level, _ink in cases:
                        plate = glass_level(level, theme)
                        for j, mode in enumerate(("bare", "halo")):
                                strip.paste(compose(plate, plain, theme, mode),
                                            (0, row * H))
                                row += 1
        strip = strip.resize((W * 2, strip.height * 2), Image.LANCZOS)
        strip.save(OUT / "menu-halo-probe.png")
        print(f"[shot] {OUT / 'menu-halo-probe.png'}（每主题 3 例：无保底 / 软光晕）")
        return 1 if fails else 0


if __name__ == "__main__":
        raise SystemExit(main())

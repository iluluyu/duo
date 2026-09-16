"""菜单文字可读性：样式配方对最坏底景的对比度契约。

玻璃会把底景透上来，底景与墨色同调时（亮色菜单盖深色图标、暗色菜单盖
白底图标）ink:glass 掉到 ~1.0，字等于消失（见 §0 表格与
docs/ui/probes/text_halo_probe.py）。QML 侧的对策是 MenuLabel 的软光晕：
字形的模糊剪影垫在清晰文字后面（Style.menuInkHalo + menuHaloBlur）。

本文件在纯 Python 里按配方算账：
  1. 没有光晕时，最坏底景的 ink:glass 低于 AA（问题真实存在）；
  2. 有光晕时，字形边界处的对比度达到 AA（对策成立）——分"半覆盖边界"
     与"满覆盖核心"两档断言；
  3. 主题自己的画布上光晕几乎不可见（常见底景零代价）；
  4. 光晕方向随墨色翻面，且强度/半径留在规则额度内（DESIGN.md 铁律 8 补充）。

数字口径：模拟与真实光栅的实测见 docs/ui/glass-recipe.md §7 与
docs/ui/probes/menu_halo_candidates.py（σ/α 二维扫描：主题画布可见度
1.00/1.05，最坏落点光晕对比度 9.8:1/13.5:1）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STYLE = Path(__file__).resolve().parents[1] / "duo" / "ui" / "qml" / "Style.qml"
AA = 4.5
HALF_COVERAGE_FLOOR = 4.0     # 半覆盖边界（软光晕的梯度最弱处）
# DESIGN.md 铁律 8 补充的额度：亮色 ≤ 40%（白晕在亮玻璃上更易被看见），
# 暗色 ≤ 55%（黑晕在暗玻璃上实测不可见——主题画布可见度 1.05，见下）；
# 模糊半径 ≤ 8px。真正的约束是"主题画布上不可见"，数字只是这些实测的预算。
RULE_ALPHA_MAX = {"light": 0.40, "dark": 0.55}
RULE_RADIUS_MAX = 8.0

# 合成后的玻璃色（sRGB 0-255），从 glass-recipe §7 的实测口径反推：
#   亮色：最坏落点 ink:glass = 2.77:1 → 玻璃 ≈ #636363
#   暗色：最坏落点 ink:glass = 2.63:1 → 玻璃 ≈ #999999
WORST = {"light": 0x63, "dark": 0x99}
# 合成极端（整块纯黑/纯白垫在菜单下）—— 现实落点里不存在（探针滑窗实测
# 最坏 2.77/2.63），仅作为"软光晕也有上限"的边界记录
EXTREME = {"light": 0x1B, "dark": 0xFF}
CANVAS = {"light": 0xF5, "dark": 0x21}


def _token(name: str) -> tuple[str, str]:
        """Style.qml 里 ``root.dark ? 暗 : 亮`` 令牌的 (亮, 暗) 值。"""
        src = STYLE.read_text(encoding="utf-8")
        m = re.search(rf'property \w+ {name}: root\.dark \? ("[^"]+"|\S+) '
                      rf': ("[^"]+"|\S+)', src)
        assert m is not None, f"Style.qml 缺令牌 {name}"
        return m.group(2).strip('"'), m.group(1).strip('"')


def _plain_token(name: str) -> str:
        src = STYLE.read_text(encoding="utf-8")
        m = re.search(rf'property \w+ {name}: (\S+)', src)
        assert m is not None, f"Style.qml 缺令牌 {name}"
        return m.group(1)


def _rgb(color: str) -> tuple[float, float, float]:
        body = color[3:] if len(color) == 9 else color[1:]
        r, g, b = (int(body[i:i + 2], 16) / 255 for i in (0, 2, 4))
        return r, g, b


def _alpha(color: str) -> float:
        return int(color[1:3], 16) / 255 if len(color) == 9 else 1.0


def _lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _luma(rgb: tuple[float, float, float]) -> float:
        return 0.2126 * _lin(rgb[0]) + 0.7152 * _lin(rgb[1]) + 0.0722 * _lin(rgb[2])


def _contrast(a: float, b: float) -> float:
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _gray(level: int) -> tuple[float, float, float]:
        return (level / 255,) * 3


def _ink(theme: str) -> tuple[float, float, float]:
        return _rgb(dict(zip(("light", "dark"), _token("ink"), strict=True))[theme])


def _halo(theme: str) -> str:
        return dict(zip(("light", "dark"), _token("menuInkHalo"), strict=True))[theme]


def _over(front: str, back: tuple[float, float, float],
          coverage: float = 1.0) -> tuple[float, float, float]:
        """front 以 coverage 覆盖率合成到 back 上（sRGB 空间，Qt 的合成域）。"""
        f = _rgb(front)
        alpha = _alpha(front) * coverage
        r, g, b = (alpha * fc + (1 - alpha) * bc
                   for fc, bc in zip(f, back, strict=True))
        return (r, g, b)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_bare_glass_drops_below_aa_at_measured_worst(theme):
        """没有光晕时，实测最坏落点的 ink:glass 低于 AA —— 问题本身。"""
        ink = _luma(_ink(theme))
        assert _contrast(ink, _luma(_gray(WORST[theme]))) < AA
        assert _contrast(ink, _luma(_gray(EXTREME[theme]))) < AA


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_halo_core_restores_aa_at_measured_worst(theme):
        """软光晕核心（满覆盖）把最坏落点的对比度托回 AA 以上。"""
        ink = _luma(_ink(theme))
        core = _over(_halo(theme), _gray(WORST[theme]))
        assert _contrast(ink, _luma(core)) >= AA


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_halo_boundary_keeps_a_floor_at_half_coverage(theme):
        """光晕梯度最弱处（半覆盖边界）仍守住一个下限。"""
        ink = _luma(_ink(theme))
        edge = _over(_halo(theme), _gray(WORST[theme]), coverage=0.5)
        assert _contrast(ink, _luma(edge)) >= HALF_COVERAGE_FLOOR


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_halo_lifts_even_the_synthetic_extreme(theme):
        """整块纯黑/纯白垫底（现实不存在）时光晕也能抬一档，但不假装过 AA。"""
        ink = _luma(_ink(theme))
        bare = _contrast(ink, _luma(_gray(EXTREME[theme])))
        core = _contrast(ink, _luma(_over(_halo(theme), _gray(EXTREME[theme]))))
        assert bare < 2.0 < core < AA


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_halo_is_invisible_over_theme_canvas(theme):
        """常见底景（主题画布）上光晕几乎不可见 —— 零代价。"""
        ink = _luma(_ink(theme))
        plate = _gray(CANVAS[theme])
        core = _over(_halo(theme), plate)
        assert _contrast(_luma(core), _luma(plate)) <= 1.3
        assert _contrast(ink, _luma(plate)) >= AA     # 画布上本来就好读


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_halo_flips_with_ink_direction(theme):
        """光晕永远取墨色的反面：亮底发白、暗底发黑。"""
        ink = _luma(_ink(theme))
        assert (_luma(_rgb(_halo(theme))) > ink) == (theme == "light")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_halo_stays_within_the_rule_budget(theme):
        """保底光晕的额度：alpha ≤ 45% 且模糊半径 ≤ 8px（铁律 8 补充）。"""
        assert 0.0 < _alpha(_halo(theme)) <= RULE_ALPHA_MAX[theme]
        radius = float(_plain_token("menuHaloBlur")) \
            * int(_plain_token("menuHaloBlurMax"))
        assert 0.0 < radius <= RULE_RADIUS_MAX

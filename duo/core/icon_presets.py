"""Preset app icons as generated SVGs (brand squircle + glyph).

Catalog tiles should not wait on an APK pull and an aapt2 icon-extraction
round to look finished: every preset already carries a brand color and
glyph, and this module turns those into a small squircle SVG cached under
``data_dir()/presets``. Deliberately Qt-free string templating - no SVG
library, no raster step - and safe to scale because the art is expressed
in a 60x60 viewBox that QML renders at 60 DIP (crisp at any DPI).
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from duo.core.catalog import AppPreset, catalog_by_package
from duo.core.paths import data_dir

#: Template revision - bump when the SVG template changes so cached files
#: (version-suffixed) never serve the old shape.
_TEMPLATE_VERSION = 2


def lighten(hex_color: str, fraction: float) -> str:
        """Blend an ``#RRGGBB`` color toward white: 0 = unchanged, 1 = white.

        Used for the subtle top-of-tile gradient stop: a tint of the brand
        color reads as "lit from above" without changing the tile's identity.
        """

        def toward_white(channel: int) -> int:
                return round(channel + (255 - channel) * fraction)

        values = [toward_white(int(hex_color[i : i + 2], 16)) for i in (1, 3, 5)]
        return "#{:02X}{:02X}{:02X}".format(*values)


def render_preset_svg(preset: AppPreset) -> str:
        """60x60 squircle tile SVG for one catalog preset.

        Vertical gradient (lightened top -> brand bottom) and the centered
        glyph - dark ink where the brand color is light, white otherwise.
        No rim, no outer stroke: the 1px top highlight read as a dark
        line on light tiles under Qt's SVG rasteriser (rgba() support is
        shaky there), and a clean gradient squircle needs no crutch.

        ``y`` is an explicit alphabetic baseline: Qt's SVG renderer ignores
        ``dominant-baseline``, so optical centering is baked in per glyph
        class (see ``_glyph_baseline``).
        """
        gradient_top = lighten(preset.color, 0.08)
        ink = "#1D1D1F" if preset.glyph_ink else "#FFFFFF"
        baseline = _glyph_baseline(preset.glyph)
        return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60">
<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="{gradient_top}"/><stop offset="1" stop-color="{preset.color}"/>
</linearGradient></defs>
<rect x="0" y="0" width="60" height="60" rx="14" fill="url(#bg)"/>
<text x="30" y="{baseline}" text-anchor="middle"
      font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif"
      font-size="28" font-weight="600" fill="{ink}">{escape(preset.glyph)}</text>
</svg>
"""


def _glyph_baseline(glyph: str) -> int:
        """Alphabetic baseline that optically centres one glyph at y=30.

        CJK full-height glyphs and tailless capitals centre around
        baseline = centre + 0.36em; a lone lowercase letter centres on its
        x-height (0.26em below centre), and ``Q`` gets a nudge up because
        its descender tail drags the bounding box down. Measured against
        Qt's own rasteriser (the renderer the panel ships with).
        """
        if glyph.isascii() and glyph.islower():
                return 37
        if glyph == "Q":
                return 38
        return 40


def preset_icon_path(package: str) -> Path | None:
        """Cached SVG path for a catalog package, ``None`` if not cataloged.

        Rendered once per package per TEMPLATE VERSION into
        ``data_dir()/presets`` (``.v2`` suffix - the rim/stroke-free
        template); a template change bumps the version so stale caches
        are ignored instead of served.
        """
        preset = catalog_by_package().get(package)
        if preset is None:
                return None
        target = data_dir() / "presets" / f"{package}.v{_TEMPLATE_VERSION}.svg"
        if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(render_preset_svg(preset), encoding="utf-8")
        return target

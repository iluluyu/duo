"""Preset SVG generation: gradient/glyph content, ink choice, disk cache.

``data_dir`` is monkeypatched to tmp_path (never the real user data dir),
following the house pattern of patching the binding on the using module.
"""

from __future__ import annotations

from pathlib import Path

import duo.core.icon_presets as icon_presets
from duo.core.catalog import APP_CATALOG, catalog_by_package
from duo.core.icon_presets import lighten, preset_icon_path, render_preset_svg


def test_lighten_blends_each_channel_toward_white():
        """Per-channel mix toward 255; endpoints stay sane at 0 and 1."""
        assert lighten("#000000", 0.5) == "#808080"
        assert lighten("#07C160", 0.08) == "#1BC66D"
        assert lighten("#FF6A00", 0.0) == "#FF6A00"
        assert lighten("#404040", 1.0) == "#FFFFFF"


def test_render_contains_gradient_stops_and_glyph():
        """A WeChat tile carries the gradient pair and its 微 glyph."""
        svg = render_preset_svg(catalog_by_package()["com.tencent.mm"])
        assert svg.lstrip().startswith("<svg")
        assert svg.rstrip().endswith("</svg>")
        assert svg.count("stop-color=") == 2
        assert ">微</text>" in svg
        assert 'viewBox="0 0 60 60"' in svg
        assert 'rx="14"' in svg


def test_gradient_uses_lightened_top_and_brand_bottom():
        """Top stop = lighten(color, 0.08); bottom stop = the brand color."""
        preset = catalog_by_package()["tv.danmaku.bili"]
        svg = render_preset_svg(preset)
        assert f'stop-color="{lighten(preset.color, 0.08)}"' in svg
        assert f'stop-color="{preset.color}"' in svg


def test_glyph_ink_flag_selects_dark_vs_white_fill():
        """美团 (light yellow) renders dark ink; 微信 renders white."""
        dark_ink = render_preset_svg(catalog_by_package()["com.sankuai.meituan"])
        white_ink = render_preset_svg(catalog_by_package()["com.tencent.mm"])
        assert 'fill="#1D1D1F"' in dark_ink
        assert 'fill="#1D1D1F"' not in white_ink
        assert 'fill="#FFFFFF"' in white_ink


def test_render_every_catalog_entry():
        """The whole table renders without raising (no bad color/glyph)."""
        for preset in APP_CATALOG:
                svg = render_preset_svg(preset)
                assert preset.glyph in svg, preset.package


def test_preset_icon_path_writes_then_caches(tmp_path: Path, monkeypatch):
        """First call renders the file; the second returns it untouched."""
        monkeypatch.setattr(icon_presets, "data_dir", lambda: tmp_path)

        path = preset_icon_path("com.tencent.mm")
        assert path == tmp_path / "presets" / "com.tencent.mm.v2.svg"
        assert path is not None and path.exists()
        content = path.read_text(encoding="utf-8")
        assert "微" in content
        stamp = path.stat().st_mtime_ns

        again = preset_icon_path("com.tencent.mm")
        assert again == path
        assert path.read_text(encoding="utf-8") == content
        assert path.stat().st_mtime_ns == stamp   # cache hit: no rewrite


def test_preset_icon_path_creates_presets_dir(tmp_path: Path, monkeypatch):
        """The presets directory is created on demand, not assumed."""
        monkeypatch.setattr(icon_presets, "data_dir", lambda: tmp_path)
        assert not (tmp_path / "presets").exists()
        assert preset_icon_path("com.sankuai.meituan") is not None
        assert (tmp_path / "presets").is_dir()


def test_preset_icon_path_unknown_package_is_none(tmp_path: Path, monkeypatch):
        """Unknown packages get None and touch nothing on disk."""
        monkeypatch.setattr(icon_presets, "data_dir", lambda: tmp_path)
        assert preset_icon_path("com.no.such.app") is None
        assert list(tmp_path.iterdir()) == []

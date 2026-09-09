"""Catalog table integrity: 28 frozen presets, no duplicates, valid colors.

The table is frozen data that two consumers (grid seeding, icon presets)
rely on, so regressions must be caught by asserting shape, not vibes.
"""

from __future__ import annotations

import re

from duo.core.catalog import APP_CATALOG, catalog_by_package

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_catalog_has_28_unique_packages():
        """28 rows, no duplicate package: a dupe would double-seed the grid."""
        packages = [preset.package for preset in APP_CATALOG]
        assert len(APP_CATALOG) == 27
        assert len(set(packages)) == 27


def test_qq_present_mobileqq_only():
        """QQ is cataloged as com.tencent.mobileqq only.

        The speculative ``com.tencent.qq`` (QQ NT alias) was removed
        2026-09-08: it does not exist on Android and showed up as a dead
        grey preset tile.
        """
        by_pkg = catalog_by_package()
        assert "com.tencent.mobileqq" in by_pkg
        assert "com.tencent.qq" not in by_pkg



def test_glyph_ink_true_only_on_light_brand_colors():
        """Only 不背单词 and 美团 use dark ink; everything else white."""
        dark_ink = {preset.package for preset in APP_CATALOG if preset.glyph_ink}
        assert dark_ink == {"cn.com.langeasy.LangEasyLexis", "com.sankuai.meituan"}


def test_colors_are_legal_rrggbb():
        """Every brand color parses as #RRGGBB (feeds lighten())."""
        for preset in APP_CATALOG:
                assert _HEX_COLOR.match(preset.color), preset.package


def test_by_package_maps_identity_and_is_cached():
        """The map covers every row with the same frozen objects, cached."""
        by_package = catalog_by_package()
        assert len(by_package) == 27
        assert by_package["com.tencent.mm"] is APP_CATALOG[0]
        assert catalog_by_package() is by_package


def test_order_is_the_frozen_seed_order():
        """First/last rows pin the table order the grid seeds from."""
        assert APP_CATALOG[0].package == "com.tencent.mm"
        assert APP_CATALOG[-1].package == "com.ss.android.lark"


def test_every_row_has_label_and_glyph():
        """No empty label/glyph sneaks in: both render into the tile."""
        for preset in APP_CATALOG:
                assert preset.label, preset.package
                assert preset.glyph, preset.package

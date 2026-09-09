"""duo.core.aspects: the frozen ratio menu + wm size parsing (Qt-free).

The table is a frozen interop contract (DESIGN.md §3.6): the QML context
menu renders ASPECT_PRESETS in exactly this order, so these tests pin
both the values and the enumeration - landscape group first, portrait
group after, entries in the doc's listed order.
"""

from __future__ import annotations

from duo.core.aspects import (
        ASPECT_PRESETS,
        BODY_LABEL,
        BODY_LANDSCAPE_ID,
        BODY_PORTRAIT_ID,
        body_aspect_from_wm_size,
        preset_by_id,
        transposed,
)

# (id, landscape, width, height) rows in frozen order - DESIGN.md §3.6.
FROZEN_ROWS = [
        ("21:9", True, 3360, 1440),
        ("16:9", True, 2560, 1440),
        ("4:3", True, 1920, 1440),
        ("1:1", True, 1440, 1440),
        ("3:4", False, 1440, 1920),
        ("2:3", False, 1440, 2160),
        ("5:7", False, 1440, 2016),
        ("9:16", False, 1440, 2560),
]


# ------------------------------------------------------------------- frozen


def test_frozen_table_order_and_values():
        """The table matches DESIGN.md §3.6 verbatim, ids unique, dims sane."""
        assert [(p.id, p.landscape, p.width, p.height) for p in ASPECT_PRESETS] == FROZEN_ROWS
        # Landscape group first, portrait group after (menu 小节 order).
        assert [p.landscape for p in ASPECT_PRESETS] == [True] * 4 + [False] * 4
        # Group-unique ids, labels echo the ratio string.
        assert len({p.id for p in ASPECT_PRESETS}) == len(ASPECT_PRESETS)
        assert all(p.label == p.id for p in ASPECT_PRESETS)
        # Short side pinned at 1440 (16:9 stays the app-session baseline).
        for preset in ASPECT_PRESETS:
                assert min(preset.width, preset.height) == 1440
                assert preset.width > 0 and preset.height > 0
        # No body entries: those are per-device and probed at runtime.
        assert BODY_LANDSCAPE_ID not in {p.id for p in ASPECT_PRESETS}
        assert BODY_PORTRAIT_ID not in {p.id for p in ASPECT_PRESETS}


def test_frozen_landscape_group_descends_by_ratio():
        """Landscape ratios strictly descend 21:9 → 1:1 (the menu's wide-to-square order)."""
        ratios = [p.width / p.height for p in ASPECT_PRESETS[:4]]
        assert ratios == sorted(ratios, reverse=True)
        assert ASPECT_PRESETS[0].width / ASPECT_PRESETS[0].height > 2   # 21:9 is ultrawide


# --------------------------------------------------------------- preset_by_id


def test_preset_by_id_hits_and_misses():
        """Every frozen id resolves to its own row; body/unknown ids miss."""
        for aspect_id, landscape, width, height in FROZEN_ROWS:
                preset = preset_by_id(aspect_id)
                assert preset is not None
                assert (preset.landscape, preset.width, preset.height) == (landscape, width, height)
        # Body ids live in the controller's probe cache, not the table.
        assert preset_by_id(BODY_LANDSCAPE_ID) is None
        assert preset_by_id(BODY_PORTRAIT_ID) is None
        assert preset_by_id("16:10") is None
        assert preset_by_id("") is None


# ------------------------------------------------- body preset from wm size


def test_body_aspect_prefers_override_over_physical():
        """Override size wins when present (the effective panel size)."""
        output = "Physical size: 1080x2400\nOverride size: 1440x3200\n"
        preset = body_aspect_from_wm_size(output)
        assert preset is not None
        assert preset.id == BODY_LANDSCAPE_ID
        assert preset.label == BODY_LABEL
        assert preset.landscape is True
        # Short side already 1440: the override ratio passes through 1:1.
        assert (preset.width, preset.height) == (3200, 1440)


def test_body_aspect_falls_back_to_physical():
        """No override line: the physical size decides."""
        preset = body_aspect_from_wm_size("Physical size: 1080x2400\n")
        assert preset is not None
        # 2400 * 1440 / 1080 = 3200 exactly.
        assert (preset.width, preset.height) == (3200, 1440)


def test_body_aspect_scales_short_side_and_rounds_even():
        """Short side lands on 1440; the long side rounds to the nearest even."""
        preset = body_aspect_from_wm_size("Override size: 2223x1000\n")
        assert preset is not None
        assert preset.height == 1440
        # 2223 * 1440 / 1000 = 3201.12 -> nearest even = 3202.
        assert preset.width == 3202
        assert preset.width % 2 == 0
        # The ratio survives the rounding (well under one pixel of drift).
        assert abs(preset.width / preset.height - 2223 / 1000) < 0.01


def test_body_aspect_normalizes_to_landscape():
        """wm size axes follow the panel's native orientation; we normalize."""
        tall = body_aspect_from_wm_size("Override size: 1000x2223\n")
        wide = body_aspect_from_wm_size("Override size: 2223x1000\n")
        assert tall == wide
        assert tall is not None and tall.landscape is True
        assert tall.width > tall.height


def test_body_aspect_invalid_inputs_return_none():
        """Unparseable or non-positive output is a None preset, never a guess."""
        assert body_aspect_from_wm_size("") is None
        assert body_aspect_from_wm_size("Physical density: 420\n") is None
        assert body_aspect_from_wm_size("Physical size: banana\n") is None
        assert body_aspect_from_wm_size("Physical size: 0x0\n") is None
        assert body_aspect_from_wm_size("Override size: x2400\n") is None
        assert body_aspect_from_wm_size("adb: device offline\n") is None


# ------------------------------------------------------------- transposition


def test_transposed_body_pair_math():
        """body-l/body-p are the same ratio with axes swapped; ids flip."""
        body = body_aspect_from_wm_size("Physical size: 1080x2400\n")
        assert body is not None
        portrait = transposed(body)
        assert portrait.id == BODY_PORTRAIT_ID
        assert portrait.label == BODY_LABEL
        assert portrait.landscape is False
        assert (portrait.width, portrait.height) == (1440, 3200)
        # Same ratio, transposed.
        assert portrait.width / portrait.height == body.height / body.width
        # Transposing twice is the identity (the menu pair round-trips).
        assert transposed(portrait) == body

"""Frozen aspect-ratio presets for fixed-size app sessions (Qt-free).

docs/ui/DESIGN.md §3.6 (右键上下文菜单 - 按比例打开) freezes an
eight-entry ratio menu plus one per-device pair derived from the body
panel itself (「机身」 = the physical `wm size` ratio, transposed into
both orientations). Selecting a preset launches the session in the CLI's
fixed virtual-display mode (``--display fixed --width W --height H``):
a locked display physically cannot rotate, apps letterbox inside it like
on a tablet, and the chrome overlay aspect-locks the window - exactly
the "this window is a 16:9 tablet" contract.

Pure data + pure parsers, no Qt: the QML menu renders the frozen table,
the controller (duo.ui.controller) resolves menu ids - including the
lazily probed body pair - into launch argv. 真机行为待回填（TODO.md
「机身比例预设」「按比例打开」）：logic ships first, Windows 实测后补。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

#: Virtual-display short side shared by every preset (px). Long sides
#: follow the frozen ratios; 1440 keeps 16:9 at the established
#: 2560×1440 app-session baseline instead of inventing a second one.
BODY_SHORT_SIDE_PX = 1440

#: Menu ids of the body pair (device-panel ratio, probed at runtime via
#: ``wm size``; not part of the frozen table because they differ per
#: device). QML passes exactly these strings to
#: ``startSessionWithAspect``.
BODY_LANDSCAPE_ID = "body-l"
BODY_PORTRAIT_ID = "body-p"

#: Menu label both body entries share (横屏/竖屏小节各一行).
BODY_LABEL = "机身"

# ``wm size`` prints "Physical size: 1080x2400" plus - when a size
# override is in effect - "Override size: 1080x2340". Digits on both
# sides of the separator; tolerate the × glyph some shells emit.
_SIZE_RE = re.compile(r"(\d+)\s*[xX×]\s*(\d+)")


@dataclass(frozen=True)
class AspectPreset:
        """One launchable virtual-display shape.

        ``id`` is unique within its group: frozen presets use the ratio
        string ("16:9"), body presets use :data:`BODY_LANDSCAPE_ID` /
        :data:`BODY_PORTRAIT_ID`. ``width``/``height`` are virtual-display
        pixels; body presets are never in the frozen table (their values
        are runtime-derived - 0 stands for "not derived yet" should the
        UI need a placeholder row before a device is probed).
        """

        id: str
        label: str
        landscape: bool
        width: int
        height: int


#: The frozen ratio menu (DESIGN.md §3.6, 2026-09 冻结比例集), verbatim
#: enumeration: landscape group (21:9 → 1:1) first, portrait group
#: (3:4 → 9:16) after, each in the doc's listed order. Short side pinned
#: at 1440px throughout. Body presets are NOT here - per-device, probed
#: lazily (see :func:`body_aspect_from_wm_size` and
#: ``PanelController._body_aspect_preset``).
ASPECT_PRESETS: list[AspectPreset] = [
        AspectPreset("21:9", "21:9", True, 3360, 1440),
        AspectPreset("16:9", "16:9", True, 2560, 1440),
        AspectPreset("4:3", "4:3", True, 1920, 1440),
        AspectPreset("1:1", "1:1", True, 1440, 1440),
        AspectPreset("3:4", "3:4", False, 1440, 1920),
        AspectPreset("2:3", "2:3", False, 1440, 2160),
        AspectPreset("5:7", "5:7", False, 1440, 2016),
        AspectPreset("9:16", "9:16", False, 1440, 2560),
]


def preset_by_id(aspect_id: str) -> AspectPreset | None:
        """The frozen preset with this menu id; None = not in the table.

        Body ids deliberately return None here: their values are
        per-device and live in the controller's probe cache, not in the
        frozen table.
        """
        for preset in ASPECT_PRESETS:
                if preset.id == aspect_id:
                        return preset
        return None


def _parse_size_line(text: str) -> tuple[int, int] | None:
        """(w, h) from one ``wm size`` line, e.g. "Override size: 1440x3200".

        None for anything unparseable or non-positive - the caller treats
        a missing map as "no preset" rather than guessing.
        """
        match = _SIZE_RE.search(text)
        if match is None:
                return None
        width, height = int(match.group(1)), int(match.group(2))
        if width <= 0 or height <= 0:
                return None
        return width, height


def body_aspect_from_wm_size(wm_size_output: str) -> AspectPreset | None:
        """``wm size`` output → the landscape body preset (Override > Physical).

        Parses in the same spirit as ``parse_device_density``
        (duo.core.apps): the Override line is the effective size (display
        zoom / ``wm size`` override), Physical the fallback; a miss on
        both returns None. The panel ratio is orientation-normalized to
        landscape (``wm size`` reports the panel's native axes, which
        vary) and scaled so the SHORT side is
        :data:`BODY_SHORT_SIDE_PX`, long side rounded to the nearest even
        value - odd display heights break encoder/window configs more
        often than they help.

        >>> body_aspect_from_wm_size("Physical size: 1080x2400")
        AspectPreset(id='body-l', label='机身', landscape=True, width=3200, height=1440)
        >>> body_aspect_from_wm_size("") is None
        True
        """
        override: tuple[int, int] | None = None
        physical: tuple[int, int] | None = None
        for line in wm_size_output.splitlines():
                text = line.strip()
                if text.startswith("Override size:"):
                        override = override or _parse_size_line(text)
                elif text.startswith("Physical size:"):
                        physical = physical or _parse_size_line(text)
        size = override or physical
        if size is None:
                return None
        long_side, short_side = max(size), min(size)
        scaled_long = round(long_side * BODY_SHORT_SIDE_PX / short_side / 2) * 2
        return AspectPreset(
                id=BODY_LANDSCAPE_ID,
                label=BODY_LABEL,
                landscape=True,
                width=scaled_long,
                height=BODY_SHORT_SIDE_PX,
        )


def transposed(preset: AspectPreset) -> AspectPreset:
        """The twin orientation of a preset: same ratio, axes swapped.

        Built for the body pair - 「机身」横竖各一（同比例转置）, so the
        landscape probe yields the portrait entry for free. Non-body ids
        pass through unchanged (generic axes swap).

        >>> transposed(body_aspect_from_wm_size("Physical size: 1080x2400"))
        AspectPreset(id='body-p', label='机身', landscape=False, width=1440, height=3200)
        """
        if preset.id == BODY_LANDSCAPE_ID:
                pair_id = BODY_PORTRAIT_ID
        elif preset.id == BODY_PORTRAIT_ID:
                pair_id = BODY_LANDSCAPE_ID
        else:
                pair_id = preset.id
        return replace(
                preset,
                id=pair_id,
                landscape=not preset.landscape,
                width=preset.height,
                height=preset.width,
        )

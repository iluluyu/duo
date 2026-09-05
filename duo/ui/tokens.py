"""Shared visual tokens for the Duo Qt surfaces (docs/window-experience.md §5).

Plain constants only - no theme engine, no runtime switching. The contract:
one neutral canvas, one accent colour, hairline separators, smoky translucent
glass on control capsules and settings containers, light shadows. Alpha
values are Qt-QSS integers (0-255), not CSS fractions.
"""

#: Neutral canvas behind every surface.
BG = "#F3F3F5"
#: Ink: primary text, secondary captions, tertiary placeholders/disabled.
INK = "#1D1D1F"
INK_2 = "#86868B"
INK_3 = "#C7C7CC"
#: The single accent (Windows system blue) plus its hover/press steps.
ACCENT = "#0067C0"
ACCENT_HOVER = "#1A76C6"
ACCENT_PRESS = "#0F5EA8"
#: Semantic status colours (device state, probe results) - not decoration.
SUCCESS = "#30D158"
WARN = "#FF9F0A"
DANGER = "#FF3B30"
#: Glass surface: smoky translucent white, brighter on top so the top edge
#: reads as a highlight. GLASS_SOLID is the opaque high-contrast fallback.
GLASS_TOP = "rgba(255, 255, 255, 224)"
GLASS_BOTTOM = "rgba(255, 255, 255, 163)"
GLASS_BORDER = "rgba(255, 255, 255, 217)"
GLASS_SOLID = "#FBFBFD"
#: Hairline separators and the hover/press washes shared by all controls.
HAIRLINE = "rgba(0, 0, 0, 22)"
HOVER_WASH = "rgba(0, 0, 0, 12)"
PRESS_WASH = "rgba(0, 0, 0, 26)"
#: Corner radii (px): containers vs buttons/fields; pills use height / 2.
RADIUS_CARD = 16
RADIUS_CONTROL = 10

#: Substitution table for the %-style QSS templates in the UI modules.
QSS_TOKENS: dict[str, str] = {
        "bg": BG,
        "ink": INK,
        "ink2": INK_2,
        "ink3": INK_3,
        "accent": ACCENT,
        "accentHover": ACCENT_HOVER,
        "accentPress": ACCENT_PRESS,
        "success": SUCCESS,
        "warn": WARN,
        "danger": DANGER,
        "glassTop": GLASS_TOP,
        "glassBottom": GLASS_BOTTOM,
        "glassBorder": GLASS_BORDER,
        "glassSolid": GLASS_SOLID,
        "hairline": HAIRLINE,
        "hoverWash": HOVER_WASH,
        "pressWash": PRESS_WASH,
        "radiusCard": str(RADIUS_CARD),
        "radiusControl": str(RADIUS_CONTROL),
}

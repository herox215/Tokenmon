"""Type-driven palette for move buttons.

Pure module — no AppKit imports — so it can be unit-tested headlessly the
way ``battle/types.py`` is. The popover's ``move_button.py`` view layer
consumes these tuples and converts them to ``NSColor`` at draw time.

Colours are the canonical Pokémon-series type palette (per Bulbapedia)
mapped from hex to 0..1 RGB. They're tuned for full-saturation button
backgrounds, separate from the brighter, often translucent particle
palette in ``battle_fx.py``.
"""
from __future__ import annotations

from typing import Final

# 17 Gen-3 types — keys are lowercase to line up with ``Move.type`` values
# emitted by the engine and PokeAPI loader.
TYPE_COLORS: Final[dict[str, tuple[float, float, float]]] = {
    "normal":   (0.66, 0.66, 0.47),
    "fighting": (0.75, 0.19, 0.16),
    "flying":   (0.66, 0.56, 0.94),
    "poison":   (0.63, 0.25, 0.63),
    "ground":   (0.88, 0.75, 0.41),
    "rock":     (0.72, 0.63, 0.22),
    "bug":      (0.66, 0.72, 0.13),
    "ghost":    (0.44, 0.34, 0.60),
    "steel":    (0.72, 0.72, 0.82),
    "fire":     (0.94, 0.50, 0.19),
    "water":    (0.41, 0.56, 0.94),
    "grass":    (0.47, 0.78, 0.31),
    "electric": (0.97, 0.82, 0.19),
    "psychic":  (0.97, 0.35, 0.53),
    "ice":      (0.60, 0.85, 0.85),
    "dragon":   (0.44, 0.22, 0.97),
    "dark":     (0.44, 0.34, 0.28),
}

# Used when ``Move.type`` is empty or an unrecognised string. Neutral
# enough to read as "untyped move" without colliding with any real type.
_FALLBACK_RGB: Final[tuple[float, float, float]] = (0.55, 0.55, 0.58)

# Title colours — slight tints rather than pure black/white look softer
# against saturated backgrounds and match the popover's overall feel.
_DARK_TEXT: Final[tuple[float, float, float]] = (0.10, 0.10, 0.12)
_LIGHT_TEXT: Final[tuple[float, float, float]] = (0.98, 0.98, 1.00)


def type_color(type_str: str | None) -> tuple[float, float, float]:
    """Return the (r, g, b) background colour for a type string.

    Lookup is case-insensitive; unknown / empty types fall back to a
    neutral gray so the button still renders rather than crashing.
    """
    if not type_str:
        return _FALLBACK_RGB
    return TYPE_COLORS.get(type_str.lower(), _FALLBACK_RGB)


def text_color_for_type(type_str: str | None) -> tuple[float, float, float]:
    """Pick a title colour that stays legible on top of ``type_color``.

    Uses the standard YIQ luminance approximation
    (0.299·R + 0.587·G + 0.114·B). Bright types (Electric, Ice, Ground)
    get dark text; deep ones (Ghost, Dragon, Dark) get light text.
    """
    r, g, b = type_color(type_str)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return _DARK_TEXT if luminance >= 0.5 else _LIGHT_TEXT


def darken(rgb: tuple[float, float, float], amount: float = 0.18) -> tuple[float, float, float]:
    """Return ``rgb`` shifted toward black by ``amount`` (0..1).

    Used to draw the 1px button border so it reads as a darker outline
    of the same hue rather than a separate accent colour.
    """
    factor = max(0.0, 1.0 - amount)
    r, g, b = rgb
    return (r * factor, g * factor, b * factor)


def lighten(rgb: tuple[float, float, float], amount: float = 0.18) -> tuple[float, float, float]:
    """Return ``rgb`` shifted toward white by ``amount`` (0..1).

    Used as the top stop of the move-button gradient so each button has
    a subtle sheen instead of looking like a flat fill.
    """
    amount = max(0.0, min(1.0, amount))
    r, g, b = rgb
    return (
        r + (1.0 - r) * amount,
        g + (1.0 - g) * amount,
        b + (1.0 - b) * amount,
    )

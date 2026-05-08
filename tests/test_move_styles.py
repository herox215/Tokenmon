"""Pure-Python tests for the type-driven move-button palette.

No AppKit needed — ``move_styles`` is a plain dict + helpers so we can
exercise it on any platform.
"""
from __future__ import annotations

import pytest

from tokenmon.battle.types import TYPES
from tokenmon.popover.panes.move_styles import (
    TYPE_COLORS,
    darken,
    lighten,
    text_color_for_type,
    type_color,
)


def test_every_canonical_type_has_a_color():
    """Every Gen-3 type from ``battle/types.py`` must have a palette
    entry — otherwise some moves would silently fall back to gray."""
    missing = [t for t in TYPES if t not in TYPE_COLORS]
    assert missing == [], f"missing palette entries: {missing}"


@pytest.mark.parametrize("type_name", list(TYPE_COLORS.keys()))
def test_color_components_in_unit_range(type_name):
    rgb = TYPE_COLORS[type_name]
    assert len(rgb) == 3
    assert all(0.0 <= c <= 1.0 for c in rgb), rgb


def test_type_color_is_case_insensitive():
    assert type_color("Water") == type_color("water") == TYPE_COLORS["water"]


def test_type_color_unknown_returns_neutral_fallback():
    rgb = type_color("fairy")        # excluded from Gen-3
    assert rgb != TYPE_COLORS["normal"]
    assert all(0.0 <= c <= 1.0 for c in rgb)


def test_type_color_handles_none_and_empty():
    fallback = type_color("xyz")
    assert type_color(None) == fallback
    assert type_color("") == fallback


def test_text_color_dark_on_bright_types():
    """Yellow/cyan/beige backgrounds need dark text for legibility."""
    for bright in ("electric", "ice", "ground", "normal"):
        r, _, _ = text_color_for_type(bright)
        assert r < 0.3, f"{bright} should pick dark text, got {r}"


def test_text_color_light_on_deep_types():
    """Dark/saturated backgrounds need light text."""
    for deep in ("ghost", "dragon", "dark", "fighting"):
        r, _, _ = text_color_for_type(deep)
        assert r > 0.9, f"{deep} should pick light text, got {r}"


def test_darken_moves_toward_black():
    base = (0.6, 0.6, 0.6)
    out = darken(base, 0.5)
    assert all(o < b for o, b in zip(out, base))


def test_darken_zero_is_identity():
    base = (0.4, 0.5, 0.6)
    assert darken(base, 0.0) == base


def test_lighten_moves_toward_white():
    base = (0.4, 0.5, 0.6)
    out = lighten(base, 0.5)
    assert all(o > b for o, b in zip(out, base))
    assert all(o <= 1.0 for o in out)


def test_lighten_zero_is_identity():
    base = (0.4, 0.5, 0.6)
    assert lighten(base, 0.0) == base


def test_lighten_full_clamps_to_white():
    out = lighten((0.2, 0.3, 0.4), 1.0)
    assert out == (1.0, 1.0, 1.0)

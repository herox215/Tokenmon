"""Tests for the companion mood-modifier helpers."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tokenmon.companion.mood import (
    is_night, mood_modifiers, RAIN_WMO_CODES,
)

BERLIN = ZoneInfo("Europe/Berlin")


def _t(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 6, hour, minute, tzinfo=BERLIN)


@pytest.mark.parametrize("hour", [22, 23, 0, 3, 5])
def test_is_night_during_night_hours(hour):
    assert is_night(_t(hour))


@pytest.mark.parametrize("hour", [6, 8, 12, 18, 21])
def test_is_night_false_during_day(hour):
    assert not is_night(_t(hour))


def test_mood_modifiers_day_no_weather():
    m = mood_modifiers(_t(12))
    assert m.alpha_multiplier == 1.0
    assert m.is_night is False
    assert m.show_umbrella is False


def test_mood_modifiers_night_dims_alpha():
    m = mood_modifiers(_t(23))
    assert m.alpha_multiplier == pytest.approx(0.85)
    assert m.is_night is True


def test_mood_modifiers_rain_sets_umbrella():
    m = mood_modifiers(_t(12), wmo_code=63)  # moderate rain
    assert m.show_umbrella is True


def test_mood_modifiers_drizzle_sets_umbrella():
    m = mood_modifiers(_t(12), wmo_code=51)
    assert m.show_umbrella is True


def test_mood_modifiers_clear_skies_no_umbrella():
    m = mood_modifiers(_t(12), wmo_code=0)
    assert m.show_umbrella is False


def test_mood_modifiers_thunderstorm_no_umbrella():
    """95+ are thunderstorms, not rain — they get their own treatment
    elsewhere; umbrella is rain-specific."""
    m = mood_modifiers(_t(12), wmo_code=95)
    assert m.show_umbrella is False


def test_rain_codes_cover_drizzle_and_showers():
    # Spot-check a few canonical WMO codes.
    assert 51 in RAIN_WMO_CODES  # light drizzle
    assert 63 in RAIN_WMO_CODES  # moderate rain
    assert 80 in RAIN_WMO_CODES  # rain showers
    assert 0 not in RAIN_WMO_CODES  # clear
    assert 95 not in RAIN_WMO_CODES  # thunderstorm


def test_overlay_set_mood_alpha_clamps_and_applies():
    pytest.importorskip("AppKit", reason="AppKit unavailable")
    from tokenmon.overlay import PokemonOverlay

    class _W:
        def __init__(self):
            self.alpha = 1.0
        def setAlphaValue_(self, a):
            self.alpha = a

    o = PokemonOverlay()
    fake = _W()
    o._window = fake  # type: ignore[assignment]
    o.set_mood_alpha(0.85)
    assert fake.alpha == pytest.approx(0.85, rel=1e-3)
    # Mood clamps below 0.5
    o.set_mood_alpha(0.1)
    assert fake.alpha == pytest.approx(0.5, rel=1e-3)
    # Mood clamps above 1.0
    o.set_mood_alpha(2.0)
    assert fake.alpha == pytest.approx(1.0, rel=1e-3)

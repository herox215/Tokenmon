"""WMO code + temperature → Pokémon type-weight bias (pure data tests)."""
from __future__ import annotations

from datetime import datetime, timezone

from tokenmon.weather import WeatherSnapshot, emoji_label, type_weights
from tokenmon.weather.bias import BOOST_FACTOR


def _snap(wmo: int, temp_c: float = 15.0) -> WeatherSnapshot:
    return WeatherSnapshot(
        wmo=wmo, temp_c=temp_c, city="Test",
        fetched_at=datetime.now(timezone.utc),
    )


def test_rain_boosts_water_and_bug():
    weights = type_weights(_snap(61))
    assert weights["water"] == BOOST_FACTOR
    assert weights["bug"] == BOOST_FACTOR


def test_thunderstorm_boosts_electric():
    weights = type_weights(_snap(95))
    assert weights == {"electric": BOOST_FACTOR}


def test_snow_boosts_ice():
    weights = type_weights(_snap(73))
    assert weights == {"ice": BOOST_FACTOR}


def test_fog_boosts_ghost_and_psychic():
    weights = type_weights(_snap(45))
    assert set(weights) == {"ghost", "psychic"}


def test_clear_boosts_fire_and_normal():
    weights = type_weights(_snap(0))
    assert set(weights) == {"fire", "normal"}


def test_cloudy_has_no_boost():
    """WMO 2 (partly cloudy) and 3 (overcast) are intentionally neutral."""
    assert type_weights(_snap(2)) == {}
    assert type_weights(_snap(3)) == {}


def test_cold_temperature_adds_ice():
    """Below-zero temp adds Ice on top of any WMO bias."""
    weights = type_weights(_snap(0, temp_c=-5.0))
    assert "ice" in weights
    assert "fire" in weights  # WMO 0 still contributes fire
    assert "normal" in weights


def test_hot_temperature_adds_fire():
    """Above 28°C adds Fire even when the WMO code wouldn't."""
    weights = type_weights(_snap(2, temp_c=32.0))
    assert weights == {"fire": BOOST_FACTOR}


def test_emoji_label_contains_condition_and_favored_types():
    label = emoji_label(_snap(61, temp_c=10.0))
    assert "Rain" in label
    assert "Water" in label or "Bug" in label


def test_emoji_label_neutral_weather_shows_temperature():
    """When there's no boost, the label falls back to weather + temp."""
    label = emoji_label(_snap(2, temp_c=15.0))
    assert "15" in label  # temperature shown
    assert "favored" not in label  # no type-favored phrasing


def test_unknown_wmo_label_does_not_crash():
    """Defensive: an unmapped WMO code returns a safe fallback label."""
    label = emoji_label(_snap(404, temp_c=20.0))
    assert label  # non-empty

"""WMO code → ParticleSpec mapping (pure data tests)."""
from __future__ import annotations

from datetime import datetime, timezone

from tokenmon.weather import WeatherSnapshot, particles_for
from tokenmon.weather.particles import ParticleSpec


def _snap(wmo: int, temp_c: float = 15.0) -> WeatherSnapshot:
    return WeatherSnapshot(
        wmo=wmo, temp_c=temp_c, city="Test",
        fetched_at=datetime.now(timezone.utc),
    )


def test_rain_returns_rain_spec():
    spec = particles_for(_snap(61))
    assert spec is not None
    assert spec.kind == "rain"
    assert spec.count > 0


def test_snow_returns_snow_spec_with_drift():
    spec = particles_for(_snap(73))
    assert spec is not None
    assert spec.kind == "snow"
    # Snowflakes should drift sideways, rain doesn't.
    assert spec.drift_amplitude > 0


def test_thunderstorm_returns_thunder_spec_with_flash():
    spec = particles_for(_snap(95))
    assert spec is not None
    assert spec.kind == "thunder"
    assert spec.flash_interval is not None and spec.flash_interval > 0


def test_fog_returns_static_overlay():
    spec = particles_for(_snap(45))
    assert spec is not None
    assert spec.kind == "fog"
    assert spec.static_overlay is True
    assert spec.count == 0


def test_clear_returns_warm_glow_overlay():
    spec = particles_for(_snap(0))
    assert spec is not None
    assert spec.kind == "sun"
    assert spec.static_overlay is True


def test_cloudy_returns_drifting_clouds():
    spec = particles_for(_snap(2))
    assert spec is not None
    assert spec.kind == "cloud"
    # Clouds rise (negative speed_y) so they drift over the popover.
    assert spec.speed_min < 0


def test_unmapped_code_returns_none():
    """Defensive: an unknown WMO code → no effect."""
    assert particles_for(_snap(404)) is None


def test_showers_more_intense_than_drizzle():
    """Heavier weather should have more / faster particles than lighter."""
    drizzle = particles_for(_snap(51))
    shower = particles_for(_snap(82))
    assert drizzle is not None and shower is not None
    assert shower.count > drizzle.count
    assert shower.speed_max > drizzle.speed_max


def test_freezing_rain_uses_lighter_blue():
    """Freezing rain has its own (icy) tint — distinct from regular rain."""
    rain = particles_for(_snap(61))
    freezing = particles_for(_snap(66))
    assert rain is not None and freezing is not None
    assert rain.color_rgba != freezing.color_rgba

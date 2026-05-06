"""Wind → particle horizontal-drift conversion (pure math)."""
from __future__ import annotations

import math

import pytest

# Skip on platforms where AppKit (and therefore the animator module) is
# unavailable. The conversion helper itself is pure and could in theory
# be moved to weather/, but for now it lives next to the only consumer.
pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


def test_wind_from_north_no_horizontal_drift():
    from tokenmon.popover.weather_layer import _wind_drift_x
    # Wind from north (0°) blows southward — no x component.
    assert math.isclose(_wind_drift_x(20.0, 0.0), 0.0, abs_tol=1e-9)


def test_wind_from_south_no_horizontal_drift():
    from tokenmon.popover.weather_layer import _wind_drift_x
    # Wind from south (180°) blows northward — no x component.
    assert math.isclose(_wind_drift_x(20.0, 180.0), 0.0, abs_tol=1e-9)


def test_wind_from_west_drifts_particles_east():
    """Meteorological 270° = wind blows from west to east → +x drift."""
    from tokenmon.popover.weather_layer import _wind_drift_x
    drift = _wind_drift_x(30.0, 270.0)
    assert drift > 0


def test_wind_from_east_drifts_particles_west():
    """Meteorological 90° = wind blows from east to west → -x drift."""
    from tokenmon.popover.weather_layer import _wind_drift_x
    drift = _wind_drift_x(30.0, 90.0)
    assert drift < 0


def test_zero_wind_zero_drift():
    from tokenmon.popover.weather_layer import _wind_drift_x
    assert _wind_drift_x(0.0, 270.0) == 0.0


def test_drift_scales_linearly_with_speed():
    from tokenmon.popover.weather_layer import _wind_drift_x
    a = _wind_drift_x(10.0, 270.0)
    b = _wind_drift_x(20.0, 270.0)
    assert math.isclose(b, 2 * a)

"""Pure tests for the cursor-proximity → alpha fade curve."""
from __future__ import annotations

import pytest

from tokenmon.companion.proximity import (
    DEFAULT_INNER, DEFAULT_OUTER, MIN_ALPHA, proximity_alpha,
)


def test_inside_inner_returns_min_alpha():
    assert proximity_alpha(0) == MIN_ALPHA
    assert proximity_alpha(DEFAULT_INNER - 0.1) == MIN_ALPHA
    assert proximity_alpha(DEFAULT_INNER) == MIN_ALPHA


def test_outside_outer_returns_full_alpha():
    assert proximity_alpha(DEFAULT_OUTER) == 1.0
    assert proximity_alpha(DEFAULT_OUTER + 100) == 1.0
    assert proximity_alpha(99999) == 1.0


def test_midway_yields_midway_alpha():
    """Halfway between inner and outer should give the midpoint alpha."""
    midway = (DEFAULT_INNER + DEFAULT_OUTER) / 2
    expected = MIN_ALPHA + 0.5 * (1.0 - MIN_ALPHA)
    assert proximity_alpha(midway) == pytest.approx(expected, rel=1e-6)


def test_alpha_monotonically_increases_with_distance():
    last = -1.0
    for d in range(0, 250, 5):
        a = proximity_alpha(d)
        assert a >= last - 1e-9, f"alpha decreased at distance={d}"
        last = a


def test_custom_inner_outer_range():
    # Tight range: 0..50 inner, 50..100 ramp, 100+ full.
    assert proximity_alpha(0, inner=50, outer=100) == MIN_ALPHA
    assert proximity_alpha(75, inner=50, outer=100) == pytest.approx(
        MIN_ALPHA + 0.5 * (1.0 - MIN_ALPHA), rel=1e-6,
    )
    assert proximity_alpha(150, inner=50, outer=100) == 1.0


def test_degenerate_zero_range_acts_as_cliff():
    """If inner == outer, no ramp — sharp transition."""
    assert proximity_alpha(50, inner=100, outer=100) == MIN_ALPHA
    assert proximity_alpha(100, inner=100, outer=100) == MIN_ALPHA
    assert proximity_alpha(101, inner=100, outer=100) == 1.0


def test_inverted_range_handled_gracefully():
    """outer < inner shouldn't crash; treated as a cliff."""
    a = proximity_alpha(50, inner=200, outer=100)
    # Shouldn't NaN or raise
    assert 0.0 <= a <= 1.0


def test_min_alpha_override():
    a = proximity_alpha(0, min_alpha=0.0)
    assert a == 0.0

"""Tests for PokemonOverlay.set_proximity_alpha + idempotent _apply_alpha."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeContent:
    def addSubview_(self, _v):
        pass


class _FakeWin:
    def __init__(self):
        self.alpha_calls: list[float] = []
        self._content = _FakeContent()
    def setAlphaValue_(self, a):
        self.alpha_calls.append(a)
    def contentView(self):
        return self._content


def test_set_proximity_alpha_combines_with_mood():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    # mood night 0.85, proximity 0.15 → 0.1275
    o.set_mood_alpha(0.85)
    o.set_proximity_alpha(0.15)
    expected = 0.85 * 0.15
    assert fake.alpha_calls[-1] == pytest.approx(expected, rel=1e-3)


def test_set_proximity_alpha_clamps_out_of_range():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o._image_view = object()
    o.set_proximity_alpha(2.5)
    assert o._proximity_alpha == 1.0
    o.set_proximity_alpha(-1.0)
    assert o._proximity_alpha == 0.0


def test_apply_alpha_idempotent_skips_redundant_set():
    """Calling setAlphaValue_ 20 times/sec with the same value would spam
    the compositor. _apply_alpha must short-circuit on no-change."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o._image_view = object()
    o.set_proximity_alpha(0.5)
    n_after_first = len(fake.alpha_calls)
    # Re-applying the same value many times shouldn't issue more sets.
    for _ in range(20):
        o.set_proximity_alpha(0.5)
    assert len(fake.alpha_calls) == n_after_first


def test_set_proximity_alpha_skips_tiny_deltas():
    """5e-3 is the threshold — sub-threshold changes shouldn't trigger
    a window redisplay."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o._image_view = object()
    o.set_proximity_alpha(0.5)
    n_after_first = len(fake.alpha_calls)
    o.set_proximity_alpha(0.502)  # within 5e-3
    assert len(fake.alpha_calls) == n_after_first
    o.set_proximity_alpha(0.6)  # well past threshold
    assert len(fake.alpha_calls) > n_after_first

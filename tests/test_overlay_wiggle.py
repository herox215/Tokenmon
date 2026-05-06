"""Tests for the companion wiggle (item-drop announce) animation."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeWin:
    def __init__(self):
        from Foundation import NSMakeRect
        self._frame = NSMakeRect(100.0, 200.0, 128.0, 128.0)
        self.origin_calls: list[tuple[float, float]] = []

    def frame(self):
        return self._frame

    def setFrameOrigin_(self, point):
        self.origin_calls.append((float(point[0]), float(point[1])))


def test_wiggle_noop_without_window():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o.wiggle()
    assert o._wiggling is False
    assert o._wiggle_handler is None


def test_wiggle_starts_handler_and_sets_flag():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o.wiggle()
    assert o._wiggling is True
    assert o._wiggle_handler is not None


def test_wiggle_alternates_origin_and_decays():
    """Drive the handler through its frames manually and verify the
    setFrameOrigin sequence alternates direction with linear damping."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o.wiggle(amplitude_px=10, frames=4)
    handler = o._wiggle_handler
    assert handler is not None
    # Frame 1: +amp * 3/4 = +7.5
    handler.fire_(None)
    assert fake.origin_calls[-1] == pytest.approx((107.5, 200.0))
    # Frame 2: -amp * 2/4 = -5.0
    handler.fire_(None)
    assert fake.origin_calls[-1] == pytest.approx((95.0, 200.0))
    # Frame 3: +amp * 1/4 = +2.5
    handler.fire_(None)
    assert fake.origin_calls[-1] == pytest.approx((102.5, 200.0))
    # Frame 4 (final): snap to original origin, clear flag
    handler.fire_(None)
    assert fake.origin_calls[-1] == pytest.approx((100.0, 200.0))
    assert o._wiggling is False
    assert o._wiggle_handler is None


def test_wiggle_replaces_in_flight_handler():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o.wiggle(frames=4)
    first = o._wiggle_handler
    o.wiggle(frames=4)
    assert o._wiggle_handler is not first
    # The old handler's fire_ should early-exit since it's no longer
    # the registered one — driving it doesn't move the window.
    fake.origin_calls.clear()
    first.fire_(None)
    assert fake.origin_calls == []


def test_wiggling_property_reflects_state():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    assert o.wiggling is False
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o.wiggle()
    assert o.wiggling is True
    # Drive to completion: with default 6 frames + setFrameOrigin steps.
    handler = o._wiggle_handler
    assert handler is not None
    for _ in range(7):  # one extra to land on the final-frame snap
        if o._wiggle_handler is None:
            break
        handler.fire_(None)
    assert o.wiggling is False

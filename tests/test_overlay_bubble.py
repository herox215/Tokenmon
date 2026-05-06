"""Tests for the companion text bubble.

Click on the sprite → bubble opens. Click again → closes. Hide-overlay
must tear down a dangling bubble. set_clickable toggles the underlying
ignoresMouseEvents flag.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeWin:
    def __init__(self):
        self.ignores_mouse_events = True
        # Realistic frame so the bubble has somewhere to anchor.
        from Foundation import NSMakeRect
        self._frame = NSMakeRect(100.0, 200.0, 128.0, 128.0)
    def setIgnoresMouseEvents_(self, v):
        self.ignores_mouse_events = bool(v)
    def frame(self):
        return self._frame


def test_set_clickable_toggles_ignore_mouse_events():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o.set_clickable(True)
    assert fake.ignores_mouse_events is False
    o.set_clickable(False)
    assert fake.ignores_mouse_events is True


def test_set_clickable_safe_when_no_window():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o.set_clickable(True)  # no crash, no-op


def test_on_sprite_clicked_toggles_bubble():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    assert o._bubble_window is None
    o._on_sprite_clicked()
    assert o._bubble_window is not None
    assert o._bubble_field is not None
    o._on_sprite_clicked()
    assert o._bubble_window is None
    assert o._bubble_field is None


def test_open_bubble_positions_to_right_of_sprite():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o._open_bubble()
    win = o._bubble_window
    assert win is not None
    f = win.frame()
    # Sprite frame origin x=100, width=128 → bubble left edge at 100+128+6=234
    assert f.origin.x == pytest.approx(234.0, abs=0.5)
    # Vertically centred on sprite: sprite y=200, h=128, bubble h=40
    # → bubble y = 200 + (128-40)/2 = 244
    assert f.origin.y == pytest.approx(244.0, abs=0.5)
    o._close_bubble()


def test_close_bubble_idempotent():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._close_bubble()  # no crash with no window
    assert o._bubble_window is None


def test_bubble_open_property_reflects_state():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    assert o.bubble_open is False
    o._open_bubble()
    assert o.bubble_open is True
    o._close_bubble()
    assert o.bubble_open is False


def test_close_bubble_uninstalls_dismisser():
    """The global event monitor must be removed so we don't leak it
    after the bubble closes."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o._open_bubble()
    assert o._bubble_dismisser is not None
    o._close_bubble()
    assert o._bubble_dismisser is None


def test_open_bubble_wires_enter_to_close():
    """NSTextField target/action is set up so pressing Enter closes the
    bubble."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]
    o._open_bubble()
    assert o._bubble_submit_handler is not None
    field = o._bubble_field
    assert field is not None
    # Simulate Enter: the field's target should be our handler, action fire:
    assert field.target() is o._bubble_submit_handler
    # Invoke as Cocoa would — the handler must close the bubble.
    o._bubble_submit_handler.fire_(field)
    assert o._bubble_window is None
    assert o._bubble_submit_handler is None


def test_hide_closes_bubble():
    """When the overlay is hidden (e.g. companion toggled off), any open
    text bubble should vanish along with it."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    fake = _FakeWin()
    o._window = fake  # type: ignore[assignment]

    # Make hide() not crash on the fake window — it calls orderOut_.
    fake.orderOut_ = lambda _v: None
    o._open_bubble()
    assert o._bubble_window is not None
    o.hide()
    assert o._bubble_window is None

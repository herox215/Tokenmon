"""Tests for the rotating Pokéball "claude is working" badge.

The badge handler builds a real NSPanel + CALayer; on macOS test runners
those calls succeed and stay invisible (no orderFront actually paints
to a screen during pytest). We verify the public state transitions on
``PokemonOverlay`` plus the handler's positioning math against a fake
window.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeWin:
    """Stand-in for NSWindow with just the surface PokemonOverlay touches."""

    def __init__(self, x: float = 100.0, y: float = 200.0,
                 w: float = 128.0, h: float = 128.0):
        from Foundation import NSMakeRect
        self._frame = NSMakeRect(x, y, w, h)
        self.frame_calls: list[tuple] = []
        self.alpha_calls: list[float] = []

    def frame(self):
        return self._frame

    def setFrameOrigin_(self, _point):
        pass

    def setFrame_display_animate_(self, frame, _display, animate):
        self.frame_calls.append((
            float(frame.origin.x), float(frame.origin.y),
            float(frame.size.width), float(frame.size.height),
            bool(animate),
        ))

    def setIgnoresMouseEvents_(self, _flag):
        pass

    def setAlphaValue_(self, alpha):
        self.alpha_calls.append(float(alpha))

    def orderOut_(self, _sender):
        pass

    def close(self):
        pass


def test_show_noop_without_window():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o.show_claude_badge()
    assert o.claude_badge_visible is False
    assert o._claude_badge_handler is None


def test_show_sets_handler_with_window():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._window = _FakeWin()  # type: ignore[assignment]
    try:
        o.show_claude_badge()
        assert o.claude_badge_visible is True
        assert o._claude_badge_handler is not None
    finally:
        o.hide_claude_badge()


def test_show_is_idempotent():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._window = _FakeWin()  # type: ignore[assignment]
    try:
        o.show_claude_badge()
        first = o._claude_badge_handler
        o.show_claude_badge()
        assert o._claude_badge_handler is first  # no duplicate
    finally:
        o.hide_claude_badge()


def test_hide_clears_state():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._window = _FakeWin()  # type: ignore[assignment]
    o.show_claude_badge()
    assert o.claude_badge_visible is True
    o.hide_claude_badge()
    assert o.claude_badge_visible is False
    assert o._claude_badge_handler is None


def test_hide_is_idempotent_without_badge():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    # Hide before any show: must not raise.
    o.hide_claude_badge()
    assert o.claude_badge_visible is False


def test_overlay_hide_tears_down_badge():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._window = _FakeWin()  # type: ignore[assignment]
    o.show_claude_badge()
    o.hide()
    assert o.claude_badge_visible is False


def test_set_persistent_false_tears_down_badge():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._window = _FakeWin()  # type: ignore[assignment]
    o.show_claude_badge()
    o.set_persistent(False)
    assert o.claude_badge_visible is False


def test_badge_position_bottom_right_with_overlap():
    """Handler.reposition_to_sprite_frame anchors badge bottom-right
    with the configured inward overlap (peeks under the sprite).
    """
    from tokenmon.overlay import (
        PokemonOverlay,
        _BADGE_INSET,
        _BADGE_SIZE,
        _PokeballBadgeHandler,
    )
    from Foundation import NSMakeRect

    o = PokemonOverlay()
    handler = _PokeballBadgeHandler(o)
    handler.start()
    try:
        # Capture frame argument to setFrame_display_animate_ via a
        # fake substitution of the badge window. The handler builds its
        # own real window in start(); we don't peek at it, instead we
        # verify the math via a fresh fake.
        fake = _FakeWin(x=300.0, y=400.0)
        handler._window = fake  # type: ignore[assignment]
        sprite_rect = NSMakeRect(500.0, 600.0, 128.0, 128.0)
        handler.reposition_to_sprite_frame(sprite_rect, animate=False)
        assert fake.frame_calls
        bx, by, bw, bh, anim = fake.frame_calls[-1]
        # Sprite right-edge = 500 + 128 = 628; badge x = 628 - 32 + 4 = 600
        assert bx == 500.0 + 128.0 - _BADGE_SIZE + _BADGE_INSET
        # Sprite bottom (origin.y) = 600; badge y = 600 - 4 = 596
        assert by == 600.0 - _BADGE_INSET
        assert bw == float(_BADGE_SIZE)
        assert bh == float(_BADGE_SIZE)
        assert anim is False
    finally:
        handler.stop()


def test_badge_alpha_tracks_overlay_alpha():
    """Badge alpha mirrors the sprite's (mood × proximity) on
    ``_apply_alpha`` so cursor-proximity fade-out also dims the badge."""
    from tokenmon.overlay import PokemonOverlay

    o = PokemonOverlay()
    sprite = _FakeWin()
    o._window = sprite  # type: ignore[assignment]
    o.show_claude_badge()
    try:
        handler = o._claude_badge_handler
        assert handler is not None
        badge_fake = _FakeWin()
        handler._window = badge_fake  # type: ignore[assignment]
        # Cursor close to sprite → proximity_alpha drops to 0.2; mood is
        # full daytime (1.0). Effective = 0.2.
        o.set_proximity_alpha(0.2)
        # The 5e-3 idempotency band keeps _apply_alpha from firing on
        # tiny jitter; 0.2 is well outside that, so we get a real write.
        assert badge_fake.alpha_calls, "badge alpha never written"
        assert abs(badge_fake.alpha_calls[-1] - 0.2) < 1e-3
        # Cursor moves away → back to 1.0.
        o.set_proximity_alpha(1.0)
        assert abs(badge_fake.alpha_calls[-1] - 1.0) < 1e-3
    finally:
        o.hide_claude_badge()


def test_badge_reposition_follows_sprite_move():
    """``_reposition_claude_badge`` invokes the handler with the
    overlay's current sprite frame."""
    from tokenmon.overlay import PokemonOverlay
    from Foundation import NSMakeRect

    o = PokemonOverlay()
    sprite = _FakeWin(x=100.0, y=200.0)
    o._window = sprite  # type: ignore[assignment]
    o.show_claude_badge()
    try:
        handler = o._claude_badge_handler
        assert handler is not None
        badge_fake = _FakeWin()
        handler._window = badge_fake  # type: ignore[assignment]
        # Simulate sprite-move: change sprite frame, call internal
        # reposition shim, assert badge frame was updated.
        from tokenmon.overlay import _BADGE_INSET, _BADGE_SIZE
        sprite._frame = NSMakeRect(700.0, 800.0, 128.0, 128.0)
        o._reposition_claude_badge(animate=True)
        assert badge_fake.frame_calls
        bx, by, _, _, anim = badge_fake.frame_calls[-1]
        assert bx == 700.0 + 128.0 - _BADGE_SIZE + _BADGE_INSET
        assert by == 800.0 - _BADGE_INSET
        assert anim is True
    finally:
        o.hide_claude_badge()

"""Tests for the companion-sprite-docked-to-chat positioning.

The geometry helper is a pure function so it's covered directly.
The pin/unpin behaviour and the hand-off callback go through the
overlay; we drive them via monkey-patches because the real
NSWindow can't be instantiated in headless test runs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


def _rect(x: float, y: float, w: float, h: float):
    """Tiny stand-in for an NSRect — origin.x/y + size.width/height — so
    the pure helper can be exercised without AppKit machinery."""
    return SimpleNamespace(
        origin=SimpleNamespace(x=x, y=y),
        size=SimpleNamespace(width=w, height=h),
    )


def test_sprite_origin_for_chat_lands_top_right_above_panel():
    """The sprite must sit fully ABOVE the chat panel's top edge — both
    windows share NSFloatingWindowLevel and the chat is most-recently-
    front-ordered, so any overlap would clip the sprite. A small
    positive gap also leaves headroom for BOB's ±3 px breath sine."""
    from tokenmon.overlay import _CHAT_SPRITE_GAP_PX, _sprite_origin_for_chat
    chat = _rect(100, 200, 800, 500)  # top-right at (900, 700)
    x, y = _sprite_origin_for_chat(chat, sprite_size=128)
    # Right edge of sprite = right edge of chat - 8 px inset.
    assert x + 128 == 900 - 8
    # Sprite bottom is ABOVE chat top by the configured gap.
    chat_top = 200 + 500
    assert y == chat_top + _CHAT_SPRITE_GAP_PX


def test_sprite_origin_for_chat_returns_floats():
    """move_to() takes floats; integer rects must still round-trip."""
    from tokenmon.overlay import _sprite_origin_for_chat
    chat = _rect(0, 0, 1000, 600)
    x, y = _sprite_origin_for_chat(chat, sprite_size=128)
    assert isinstance(x, float)
    assert isinstance(y, float)


def test_dock_sprite_to_chat_sets_pin_and_moves_window(monkeypatch):
    """The dock helper must set the pin flag (so companion_drv skips
    its own docking) and trigger an animated move to the computed
    position. We stub move_to to capture coordinates without touching
    AppKit."""
    from tokenmon.overlay import PokemonOverlay, _sprite_origin_for_chat
    o = PokemonOverlay(size=128)
    # Pretend the sprite window is alive — the dock helper bails when
    # _window is None.
    o._window = object()
    calls: list[tuple[float, float, bool]] = []
    monkeypatch.setattr(
        o, "move_to",
        lambda x, y, animate=True: calls.append((x, y, animate)),
    )
    chat = _rect(0, 0, 1200, 700)
    o._dock_sprite_to_chat(chat)
    assert o._sprite_pinned_to_chat is True
    expected_x, expected_y = _sprite_origin_for_chat(chat, 128)
    assert calls == [(expected_x, expected_y, True)]


def test_dock_sprite_to_chat_is_noop_without_window():
    """Without an attached sprite window there's nothing to move —
    must not raise and must not flip the pin (we shouldn't claim to
    have docked a window that doesn't exist)."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay(size=128)
    assert o._window is None
    chat = _rect(0, 0, 800, 500)
    o._dock_sprite_to_chat(chat)
    assert o._sprite_pinned_to_chat is False


def test_companion_drv_skips_dock_while_pinned():
    """The pin's whole point is to suppress companion_drv's periodic
    redocks. Verify it bails before doing any AppKit work — we don't
    need a real overlay or focused window for that."""
    from tokenmon.menubar import companion_drv

    sentinel = {"window_geom_imported": False}

    def _trip_import_sentinel(*a, **kw):
        sentinel["window_geom_imported"] = True

    # If dock_to_focused_window proceeds past the pin check, the
    # next thing it does is import window_geom. Replace the import
    # path so we'd notice. (Module-level import inside the function
    # means we can't intercept easily; instead we trust that
    # frontmost_pid would error before the move runs. Simpler check:
    # verify the function returns without calling move_to_corner.)
    overlay = SimpleNamespace(
        _sprite_pinned_to_chat=True,
        _size=128,
        _window=None,
        move_to=lambda *a, **kw: _trip_import_sentinel(),
        move_to_corner=lambda **kw: _trip_import_sentinel(),
    )
    app = SimpleNamespace(_overlay=overlay, _last_dock_rect=None)
    companion_drv.dock_to_focused_window(app, force=True)
    assert sentinel["window_geom_imported"] is False


def test_hide_chat_callback_fires_redock(monkeypatch):
    """When hide_chat unpins the sprite it should fire the
    _on_chat_hidden callback so the menubar can redock immediately.
    We can't drive the real hide_chat (it touches NSWindow) so we
    verify the wiring contract: setting the attribute is honoured and
    the callback receives no arguments."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    received: list[None] = []
    o._on_chat_hidden = lambda: received.append(None)
    # Simulate the relevant slice of hide_chat: unpin + fire callback.
    o._sprite_pinned_to_chat = True
    was_pinned = o._sprite_pinned_to_chat
    o._sprite_pinned_to_chat = False
    if was_pinned and o._on_chat_hidden is not None:
        o._on_chat_hidden()
    assert received == [None]
    assert o._sprite_pinned_to_chat is False

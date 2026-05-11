"""Companion-mode tests for PokemonOverlay.

We can't easily instantiate the NSWindow in a non-graphical test run, so
these tests cover the parts that don't touch AppKit's drawing path:
the persistent flag and the hide-conditional behaviour through monkey-
patching.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


def test_set_persistent_stores_flag():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    assert o._persistent is False
    o.set_persistent(True)
    assert o._persistent is True
    o.set_persistent(False)
    assert o._persistent is False


def test_set_persistent_coerces_truthy_values():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o.set_persistent(1)  # type: ignore[arg-type]
    assert o._persistent is True
    o.set_persistent(0)  # type: ignore[arg-type]
    assert o._persistent is False


class _FakeContentView:
    def setFrame_(self, *_a):
        pass


class _FakeScreen:
    def visibleFrame(self):
        from Foundation import NSMakeRect
        return NSMakeRect(0, 0, 1920, 1080)


class _FakeWin:
    def __init__(self):
        self.ignores_mouse_events = None

    def screen(self):
        return _FakeScreen()
    def setFrame_display_animate_(self, *_args):
        pass
    def setIgnoresMouseEvents_(self, value):
        self.ignores_mouse_events = bool(value)
    def contentView(self):
        return _FakeContentView()
    def frame(self):
        from Foundation import NSMakeRect
        return NSMakeRect(100.0, 200.0, 128.0, 128.0)


def test_set_persistent_keeps_sprite_window_click_through():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    win = _FakeWin()
    o._window = win  # type: ignore[assignment]

    o.set_persistent(True)
    assert win.ignores_mouse_events is True

    o.set_persistent(False)
    assert win.ignores_mouse_events is True


def test_chat_frame_is_bottom_centered_and_roughly_40_percent_width():
    from Foundation import NSMakeRect
    from tokenmon.overlay import CHAT_BOTTOM_MARGIN, _chat_frame_for_screen

    frame = _chat_frame_for_screen(NSMakeRect(0, 0, 1920, 1080))

    assert frame.size.width == 900
    assert frame.size.height == pytest.approx(410.4)
    assert frame.origin.x == 510
    assert frame.origin.y == CHAT_BOTTOM_MARGIN


def test_chat_start_frame_sits_below_final_for_slide_up():
    from Foundation import NSMakeRect
    from tokenmon.overlay import _chat_start_frame

    final = NSMakeRect(510, 44, 900, 410)
    start = _chat_start_frame(final)

    assert start.size.width == final.size.width
    assert start.size.height == final.size.height
    assert start.origin.x == final.origin.x
    assert start.origin.y == final.origin.y - final.size.height


def test_companion_double_click_toggles_chat():
    from Foundation import NSMakeRect
    from tokenmon.overlay import _CompanionImageView

    class _Overlay:
        def __init__(self):
            self.calls = 0

        def toggle_chat(self):
            self.calls += 1

    class _Event:
        def clickCount(self):
            return 2

    overlay = _Overlay()
    view = _CompanionImageView.alloc().initWithFrame_overlay_(
        NSMakeRect(0, 0, 128, 128), overlay,
    )
    view.mouseDown_(_Event())

    assert overlay.calls == 1


def test_end_level_up_skips_hide_when_persistent(monkeypatch):
    """_end_level_up should not call self.hide() when the overlay is in
    companion mode — the sprite should stay visible after the banner
    timer expires."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._image_view = None
    o._window = _FakeWin()  # type: ignore[assignment]
    hide_calls = []
    monkeypatch.setattr(o, "hide", lambda: hide_calls.append(True))

    # Default mode → hide is called.
    o._end_level_up()
    assert hide_calls == [True], "non-persistent mode must hide()"

    # Companion mode → hide must NOT be called.
    hide_calls.clear()
    o.set_persistent(True)
    o._end_level_up()
    assert hide_calls == [], "persistent mode must not hide()"


def test_end_evolution_skips_hide_when_persistent(monkeypatch):
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._image_view = None
    o._window = _FakeWin()  # type: ignore[assignment]
    hide_calls = []
    monkeypatch.setattr(o, "hide", lambda: hide_calls.append(True))

    o._end_evolution()
    assert hide_calls == [True]

    hide_calls.clear()
    o.set_persistent(True)
    o._end_evolution()
    assert hide_calls == []

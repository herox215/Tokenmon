"""Companion-mode tests for PokemonOverlay.

We can't easily instantiate the NSWindow in a non-graphical test run, so
these tests cover the parts that don't touch AppKit's drawing path:
the persistent flag and the hide-conditional behaviour through monkey-
patching.
"""
from __future__ import annotations

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
    def screen(self):
        return _FakeScreen()
    def setFrame_display_animate_(self, *_args):
        pass
    def contentView(self):
        return _FakeContentView()
    def frame(self):
        from Foundation import NSMakeRect
        return NSMakeRect(100.0, 200.0, 128.0, 128.0)


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

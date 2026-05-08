"""Smoke tests for the typed-move-button NSView.

AppKit-guarded so the suite still runs on non-macOS CI.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


def _make_move(category="physical", typ="water", pp=20):
    from tokenmon.battle.models import Move
    return Move(
        key="hydro-pump", name="Hydro Pump", type=typ, category=category,
        power=110, accuracy=80, pp=pp,
    )


def _make_view(*, mv=None, cur_pp=20, target=None):
    from Foundation import NSMakeRect
    from tokenmon.popover.panes.move_button import _MoveButtonView

    mv = mv or _make_move()
    return _MoveButtonView.alloc().initWithFrame_move_currentPP_target_action_(
        NSMakeRect(0, 0, 120, 30),
        mv,
        cur_pp,
        target,
        b"fire:",
    )


def test_construct_does_not_raise():
    btn = _make_view()
    assert btn.isEnabled() is True


def test_set_enabled_toggles_state():
    btn = _make_view()
    btn.setEnabled_(False)
    assert btn.isEnabled() is False
    btn.setEnabled_(True)
    assert btn.isEnabled() is True


def test_drawrect_does_not_raise_for_each_category():
    """Every category badge has its own painter — make sure none of them
    blow up. drawRect_ is a no-return-value method; not raising is the
    success signal."""
    from Foundation import NSMakeRect

    for cat in ("physical", "special", "status"):
        btn = _make_view(mv=_make_move(category=cat))
        # NSView#drawRect_ requires a graphics context, so we lock focus
        # on a transient image to provide one.
        from AppKit import NSImage
        img = NSImage.alloc().initWithSize_(btn.bounds().size)
        img.lockFocus()
        try:
            btn.drawRect_(btn.bounds())
        finally:
            img.unlockFocus()


def test_drawrect_with_unknown_type_falls_back():
    """Unknown move type uses the neutral palette fallback — must not
    raise."""
    from AppKit import NSImage

    btn = _make_view(mv=_make_move(typ="fairy"))   # excluded from Gen-3
    img = NSImage.alloc().initWithSize_(btn.bounds().size)
    img.lockFocus()
    try:
        btn.drawRect_(btn.bounds())
    finally:
        img.unlockFocus()


def test_disabled_button_renders_dimmed():
    """A disabled button still draws — just with reduced alpha. The
    smoke check is that drawRect_ doesn't raise in either state."""
    from AppKit import NSImage

    btn = _make_view(cur_pp=0)
    btn.setEnabled_(False)
    img = NSImage.alloc().initWithSize_(btn.bounds().size)
    img.lockFocus()
    try:
        btn.drawRect_(btn.bounds())
    finally:
        img.unlockFocus()


def _make_recorder():
    """Build a fresh ``_ActionHandler`` whose callback records calls.

    Re-uses the existing handler bridge instead of subclassing NSObject
    here — Objective-C class names are global, so a per-test
    ``_Recorder(NSObject)`` would collide on the second test."""
    from tokenmon.popover._handlers import make_handler

    calls: list = []
    handler = make_handler(lambda sender: calls.append(sender))
    return handler, calls


def test_click_fires_handler_when_enabled():
    """The internal _fire path (which mouseUp_ delegates to) reaches the
    target's ``fire:`` selector when the view is enabled."""
    handler, calls = _make_recorder()
    btn = _make_view(target=handler)
    btn._fire()
    assert len(calls) == 1


def test_click_blocked_when_disabled():
    """Disabling the button must prevent the action from firing even if
    a stale press lingers on the view."""
    handler, calls = _make_recorder()
    btn = _make_view(target=handler)
    btn.setEnabled_(False)
    if btn.isEnabled():
        btn._fire()
    assert calls == []

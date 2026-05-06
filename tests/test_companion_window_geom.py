"""Tests for window_geom — coordinate-flip + filter logic.

The Quartz call is monkeypatched so we don't need any actual on-screen
windows during testing.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


def _patch_screen_height(monkeypatch, h: float) -> None:
    from tokenmon.companion import window_geom as wg
    monkeypatch.setattr(wg, "_primary_screen_height", lambda: h)


def _patch_window_list(monkeypatch, entries: list[dict]) -> None:
    from tokenmon.companion import window_geom as wg
    monkeypatch.setattr(
        wg, "CGWindowListCopyWindowInfo", lambda *a, **kw: entries,
    )


def test_focused_window_bounds_returns_none_for_unknown_pid(monkeypatch):
    from tokenmon.companion.window_geom import focused_window_bounds
    _patch_screen_height(monkeypatch, 1080.0)
    _patch_window_list(monkeypatch, [])
    assert focused_window_bounds(9999) is None


def _patch_intersects_always_true(monkeypatch):
    """Bypass the on-screen verification so tests don't depend on the
    machine's display configuration."""
    from tokenmon.companion import window_geom as wg
    monkeypatch.setattr(wg, "_intersects_any_screen", lambda *a, **kw: True)


def test_focused_window_bounds_picks_first_match_with_layer_zero(monkeypatch):
    from tokenmon.companion.window_geom import focused_window_bounds
    _patch_screen_height(monkeypatch, 1080.0)
    _patch_intersects_always_true(monkeypatch)
    _patch_window_list(monkeypatch, [
        # Different PID — skipped
        {
            "kCGWindowOwnerPID": 1,
            "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 400, "Height": 300},
        },
        # Wrong layer (panel) — skipped
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 25,
            "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 400, "Height": 300},
        },
        # The match
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            "kCGWindowNumber": 4242,
            "kCGWindowBounds": {"X": 100, "Y": 200, "Width": 800, "Height": 600},
        },
        # Another match (later in z-order) — should not override the first
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            "kCGWindowNumber": 4243,
            "kCGWindowBounds": {"X": 50, "Y": 50, "Width": 1000, "Height": 800},
        },
    ])
    rect = focused_window_bounds(42)
    assert rect is not None
    # CG: x=100 y=200 w=800 h=600. AppKit y_bottom = 1080 - 200 - 600 = 280.
    assert rect.x == 100
    assert rect.y == 280
    assert rect.width == 800
    assert rect.height == 600
    assert rect.window_id == 4242
    assert rect.top == 280 + 600


def test_focused_window_bounds_skips_tiny_windows(monkeypatch):
    from tokenmon.companion.window_geom import focused_window_bounds
    _patch_screen_height(monkeypatch, 1080.0)
    _patch_intersects_always_true(monkeypatch)
    _patch_window_list(monkeypatch, [
        # Tiny palette
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 199, "Height": 100},
        },
        # Real window
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 50, "Y": 50, "Width": 600, "Height": 400},
        },
    ])
    rect = focused_window_bounds(42)
    assert rect is not None
    assert rect.width == 600


def test_focused_window_bounds_skips_low_alpha(monkeypatch):
    """A fade-out sheet at alpha 0.1 must not be chosen over the real
    window underneath at alpha 1.0."""
    from tokenmon.companion.window_geom import focused_window_bounds
    _patch_screen_height(monkeypatch, 1080.0)
    _patch_intersects_always_true(monkeypatch)
    _patch_window_list(monkeypatch, [
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            "kCGWindowAlpha": 0.1,
            "kCGWindowBounds": {"X": 100, "Y": 100, "Width": 800, "Height": 600},
        },
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            "kCGWindowAlpha": 1.0,
            "kCGWindowBounds": {"X": 200, "Y": 150, "Width": 500, "Height": 400},
        },
    ])
    rect = focused_window_bounds(42)
    assert rect is not None
    # The faded window is skipped; we get the second one.
    assert rect.width == 500


def test_focused_window_bounds_skips_off_screen_ghosts(monkeypatch):
    """Minimized windows often live at large negative coordinates. The
    intersection check must reject them so we don't dock to nothing."""
    from tokenmon.companion.window_geom import focused_window_bounds
    from tokenmon.companion import window_geom as wg
    _patch_screen_height(monkeypatch, 1080.0)
    # Simulate a single screen at CG (0, 0, 1920, 1080).
    monkeypatch.setattr(
        wg, "_cg_screen_rects",
        lambda: [(0.0, 0.0, 1920.0, 1080.0)],
    )
    _patch_window_list(monkeypatch, [
        # Off-screen ghost (minimized)
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": -29000, "Y": -29000, "Width": 800, "Height": 600},
        },
        # Real on-screen window
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 100, "Y": 100, "Width": 600, "Height": 400},
        },
    ])
    rect = focused_window_bounds(42)
    assert rect is not None
    assert rect.x == 100


def test_focused_window_bounds_secondary_screen_above_primary(monkeypatch):
    """Window on a secondary monitor stacked above the primary has a
    negative CG y. The conversion must yield an AppKit y > primary_h."""
    from tokenmon.companion.window_geom import focused_window_bounds
    from tokenmon.companion import window_geom as wg
    _patch_screen_height(monkeypatch, 1080.0)
    monkeypatch.setattr(
        wg, "_cg_screen_rects",
        lambda: [
            (0.0, 0.0, 1920.0, 1080.0),       # primary
            (0.0, -720.0, 1280.0, 720.0),     # secondary above
        ],
    )
    _patch_window_list(monkeypatch, [
        {
            "kCGWindowOwnerPID": 42,
            "kCGWindowLayer": 0,
            # CG: top-left at (100, -500), 800×600 → top=-500, bottom=100
            "kCGWindowBounds": {"X": 100, "Y": -500, "Width": 800, "Height": 600},
        },
    ])
    rect = focused_window_bounds(42)
    assert rect is not None
    # AppKit y_bottom = 1080 - (-500) - 600 = 980
    assert rect.x == 100
    assert rect.y == 980


def test_focused_window_bounds_handles_empty_window_list(monkeypatch):
    from tokenmon.companion.window_geom import focused_window_bounds
    _patch_screen_height(monkeypatch, 1080.0)
    _patch_window_list(monkeypatch, None)
    assert focused_window_bounds(42) is None


def test_focused_window_bounds_returns_none_when_no_screen(monkeypatch):
    from tokenmon.companion.window_geom import focused_window_bounds
    _patch_screen_height(monkeypatch, None)  # type: ignore[arg-type]
    assert focused_window_bounds(42) is None


def test_screen_containing_point_returns_a_real_screen_for_origin(monkeypatch):
    """At least one connected screen contains the point (10, 10) on any
    reasonable test machine — checking that the helper returns *some*
    screen rather than the exact one (which depends on real hardware)."""
    from tokenmon.companion.window_geom import screen_containing_point
    s = screen_containing_point(10, 10)
    # Test machines with no displays would return None — that's OK.
    if s is not None:
        f = s.frame()
        assert f.origin.x <= 10 < f.origin.x + f.size.width


def test_screen_containing_point_returns_none_for_implausible_coords():
    from tokenmon.companion.window_geom import screen_containing_point
    # No real display extends to ±1e6 px.
    assert screen_containing_point(-1_000_000, -1_000_000) is None
    assert screen_containing_point(1_000_000, 1_000_000) is None


def test_intersects_any_screen_simple():
    from tokenmon.companion import window_geom as wg
    # Mock screens
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(wg, "_primary_screen_height", lambda: 1080.0)
    # Single screen at (0,0,1920,1080)
    mp.setattr(
        wg, "_cg_screen_rects",
        lambda: [(0.0, 0.0, 1920.0, 1080.0)],
    )
    try:
        assert wg._intersects_any_screen(0, 0, 100, 100) is True
        assert wg._intersects_any_screen(1900, 1000, 100, 100) is True  # corner clip
        assert wg._intersects_any_screen(-29000, -29000, 800, 600) is False
        assert wg._intersects_any_screen(2000, 0, 100, 100) is False  # off right
    finally:
        mp.undo()


def test_overlay_move_to_calls_set_frame_animate(tmp_path):
    """move_to forwards into NSWindow.setFrame_display_animate_."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    captured: list = []

    class _W:
        def setFrame_display_animate_(self, rect, display, animate):
            captured.append((rect, display, animate))
        def screen(self):
            return None

    o._window = _W()  # type: ignore[assignment]
    o.move_to(123, 456, animate=True)
    assert len(captured) == 1
    rect, display, animate = captured[0]
    # rect.origin.x/y, .size.width/height — pyobjc NSRect has tuple-like access
    assert animate is True
    assert display is True


def test_overlay_move_to_swallows_exceptions(monkeypatch):
    """A bad NSWindow ref shouldn't crash the menubar app."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()

    class _BrokenWin:
        def setFrame_display_animate_(self, *a, **kw):
            raise RuntimeError("simulated AppKit hiccup")

    o._window = _BrokenWin()  # type: ignore[assignment]
    o.move_to(0, 0)  # no exception escapes

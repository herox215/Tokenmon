"""Tests for PokemonOverlay.set_sprite_orientation."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


def test_set_orientation_uses_back_when_present(tmp_path, monkeypatch):
    from tokenmon.overlay import PokemonOverlay
    front = tmp_path / "front.gif"
    back = tmp_path / "back.gif"
    front.write_bytes(b"GIF89a front")
    back.write_bytes(b"GIF89a back")
    o = PokemonOverlay()
    captured: list = []
    monkeypatch.setattr(o, "update_sprite", lambda p: captured.append(p))
    o.set_sprite_orientation(front_path=front, back_path=back)
    assert captured == [back]


def test_set_orientation_falls_back_to_front_when_back_missing(
    tmp_path, monkeypatch
):
    from tokenmon.overlay import PokemonOverlay
    front = tmp_path / "front.gif"
    front.write_bytes(b"GIF89a front")
    o = PokemonOverlay()
    captured: list = []
    monkeypatch.setattr(o, "update_sprite", lambda p: captured.append(p))
    o.set_sprite_orientation(front_path=front, back_path=None)
    assert captured == [front]


def test_set_orientation_falls_back_when_back_path_does_not_exist(
    tmp_path, monkeypatch
):
    from tokenmon.overlay import PokemonOverlay
    front = tmp_path / "front.gif"
    front.write_bytes(b"GIF89a front")
    nonexistent_back = tmp_path / "back.gif"  # never written
    o = PokemonOverlay()
    captured: list = []
    monkeypatch.setattr(o, "update_sprite", lambda p: captured.append(p))
    o.set_sprite_orientation(front_path=front, back_path=nonexistent_back)
    assert captured == [front]


def test_set_orientation_silently_skips_when_front_missing(
    tmp_path, monkeypatch
):
    """Both paths missing — log a warning, no crash, no update_sprite call."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    captured: list = []
    monkeypatch.setattr(o, "update_sprite", lambda p: captured.append(p))
    o.set_sprite_orientation(
        front_path=tmp_path / "missing.gif", back_path=None,
    )
    assert captured == []


def test_set_orientation_passes_mirror_flag_through(tmp_path, monkeypatch):
    """The mirrored kwarg is forwarded to set_sprite_mirror after the
    sprite path is updated."""
    from tokenmon.overlay import PokemonOverlay
    front = tmp_path / "front.gif"
    back = tmp_path / "back.gif"
    front.write_bytes(b"GIF89a")
    back.write_bytes(b"GIF89a")
    o = PokemonOverlay()
    monkeypatch.setattr(o, "update_sprite", lambda _p: None)
    mirror_calls: list[bool] = []
    monkeypatch.setattr(o, "set_sprite_mirror", lambda v: mirror_calls.append(v))

    o.set_sprite_orientation(front_path=front, back_path=back, mirrored=True)
    assert mirror_calls == [True]
    o.set_sprite_orientation(front_path=front, back_path=back, mirrored=False)
    assert mirror_calls == [True, False]


def test_set_sprite_mirror_safe_when_no_image_view():
    """No image view yet (window not built) — mirror call must not crash."""
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o.set_sprite_mirror(True)  # no crash
    o.set_sprite_mirror(False)  # no crash


def test_animate_sprite_turn_starts_handler(tmp_path):
    """animate_sprite_turn installs a turn handler so the next
    NSTimer-driven frame can find it via ``overlay._turn_handler``."""
    from tokenmon.overlay import PokemonOverlay
    front = tmp_path / "front.gif"
    back = tmp_path / "back.gif"
    front.write_bytes(b"GIF89a")
    back.write_bytes(b"GIF89a")
    o = PokemonOverlay()
    assert o._turn_handler is None
    o.animate_sprite_turn(front_path=front, back_path=back, mirrored=True)
    assert o._turn_handler is not None
    # _end_turn clears the handler — simulate the animation finishing.
    o._end_turn(-1.0, 1.0)
    assert o._turn_handler is None


def test_animate_sprite_turn_replaces_in_flight_handler(tmp_path):
    """Calling animate_sprite_turn while a turn is already running
    swaps in a fresh handler (the old one's fire_ early-exits)."""
    from tokenmon.overlay import PokemonOverlay
    front = tmp_path / "front.gif"
    back = tmp_path / "back.gif"
    front.write_bytes(b"GIF89a")
    back.write_bytes(b"GIF89a")
    o = PokemonOverlay()
    o.animate_sprite_turn(front_path=front, back_path=back, mirrored=True)
    first = o._turn_handler
    o.animate_sprite_turn(front_path=front, back_path=back, mirrored=False)
    assert o._turn_handler is not first
    o._end_turn(1.0, 1.0)


def test_reset_sprite_state_clears_turn_and_resets_scale(tmp_path):
    """reset_sprite_state cancels any in-flight turn and snaps the layer
    transform back to identity — used on active-Pokémon change so the
    new species doesn't inherit the previous engaged-state transform."""
    from tokenmon.overlay import PokemonOverlay
    front = tmp_path / "front.gif"
    back = tmp_path / "back.gif"
    front.write_bytes(b"GIF89a")
    back.write_bytes(b"GIF89a")
    o = PokemonOverlay()
    # Simulate engaged state: turn animation in flight + scale at -zoom.
    o.animate_sprite_turn(
        front_path=front, back_path=back, mirrored=True, zoom=1.15,
    )
    assert o._turn_handler is not None
    o._apply_scale(-1.15, 1.15)  # simulate end-of-turn
    assert o._current_scale == (-1.15, 1.15)
    o.reset_sprite_state()
    assert o._turn_handler is None
    assert o._current_scale == (1.0, 1.0)


def test_animate_sprite_turn_applies_zoom_to_end_state(tmp_path):
    """Zoom > 1 should compose with mirror sign — engaged-back gets
    x_end = -zoom, idle-front gets x_end = +1.0."""
    from tokenmon.overlay import PokemonOverlay
    front = tmp_path / "front.gif"
    back = tmp_path / "back.gif"
    front.write_bytes(b"GIF89a")
    back.write_bytes(b"GIF89a")
    o = PokemonOverlay()
    o.animate_sprite_turn(
        front_path=front, back_path=back, mirrored=True, zoom=1.4,
    )
    handler = o._turn_handler
    assert handler is not None
    assert handler._x_end == pytest.approx(-1.4)
    assert handler._y_end == pytest.approx(1.4)
    o._end_turn(-1.4, 1.4)


def test_animate_sprite_turn_skips_when_target_missing(tmp_path):
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o.animate_sprite_turn(
        front_path=tmp_path / "missing.gif", back_path=None,
    )
    assert o._turn_handler is None

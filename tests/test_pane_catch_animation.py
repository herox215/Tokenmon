"""Tests for CatchAnimationController + ball-position math (Phase 3f3)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


# ---- compute_ball_position (pure) -----------------------------------


def _make_geom(rest_x=100, rest_y=80):
    return {
        "rest_x": rest_x,
        "rest_y": rest_y,
        "absorb_x": rest_x + 10,
        "absorb_y": rest_y + 30,
        "arc_frames": [(200, 50), (150, 30), (100, 60)],
        "ball_size": 40,
    }


def test_compute_ball_position_rest_returns_rest_coords(db_path):
    from tokenmon.popover.panes.catch_animation import compute_ball_position
    geom = _make_geom(100, 80)
    pos = compute_ball_position("rest", geom)
    assert pos == (100, 80)


def test_compute_ball_position_ball_drop_matches_rest(db_path):
    """ball_drop is the post-arc landing — same coordinates as rest."""
    from tokenmon.popover.panes.catch_animation import compute_ball_position
    geom = _make_geom(100, 80)
    assert compute_ball_position("ball_drop", geom) == (100, 80)


def test_compute_ball_position_shake_left_offsets_x(db_path):
    from tokenmon.popover.panes.catch_animation import compute_ball_position
    from tokenmon.popover.animation import CATCH_WOBBLE_DX
    geom = _make_geom(100, 80)
    pos = compute_ball_position("shake_left_0", geom)
    assert pos == (100 - CATCH_WOBBLE_DX, 82)


def test_compute_ball_position_shake_right_offsets_x(db_path):
    from tokenmon.popover.panes.catch_animation import compute_ball_position
    from tokenmon.popover.animation import CATCH_WOBBLE_DX
    geom = _make_geom(100, 80)
    pos = compute_ball_position("shake_right_2", geom)
    assert pos == (100 + CATCH_WOBBLE_DX, 82)


def test_compute_ball_position_shake_centre_at_rest(db_path):
    from tokenmon.popover.panes.catch_animation import compute_ball_position
    geom = _make_geom(100, 80)
    assert compute_ball_position("shake_centre_1", geom) == (100, 80)


def test_compute_ball_position_unknown_action_returns_none(db_path):
    from tokenmon.popover.panes.catch_animation import compute_ball_position
    geom = _make_geom()
    assert compute_ball_position("done", geom) is None
    assert compute_ball_position("burst", geom) is None
    assert compute_ball_position("throw_arc_1", geom) is None


def test_compute_ball_position_absorb_flash_uses_absorb_coords(db_path):
    from tokenmon.popover.panes.catch_animation import compute_ball_position
    geom = _make_geom(100, 80)
    assert compute_ball_position("absorb_flash", geom) == (110, 110)


# ---- step dispatcher (mocked views) ---------------------------------


class _FakeFrame:
    class _Origin:
        x = 0.0
    class _Size:
        width = 0.0
        height = 0.0
    origin = _Origin()
    size = _Size()


class _FakeBall:
    def __init__(self):
        self.frames = []
        self.hidden = True

    def setHidden_(self, hidden):  # noqa: N802
        self.hidden = bool(hidden)

    def setFrame_(self, f):  # noqa: N802
        self.frames.append(f)

    def frame(self):
        return _FakeFrame()


class _FakeSilhouette:
    def __init__(self):
        self.hidden = False

    def setHidden_(self, h):  # noqa: N802
        self.hidden = bool(h)


class _FakePopover:
    def __init__(self):
        self._animated_image_views: list = []
        self._begin_reveal_calls = 0
        self._show_pane_calls: list[int] = []
        self._encounter_bag_open = False
        self._catch_anim_handler = None

    def _begin_catch_reveal(self):
        self._begin_reveal_calls += 1

    def _show_pane(self, idx):
        self._show_pane_calls.append(idx)


def test_step_throw_start_unhides_ball(db_path):
    from tokenmon.popover.panes.catch_animation import CatchAnimationController
    pop = _FakePopover()
    ctrl = CatchAnimationController(pop, {"item_key": "pokeball",
                                          "species_dex_id": 1,
                                          "caught": False, "shakes": 0,
                                          "encounter_id": 1, "hint": None})
    ctrl._ball = _FakeBall()
    ctrl._silhouette = _FakeSilhouette()
    ctrl._geom = _make_geom()
    ctrl.step("throw_start", ctrl._payload)
    assert ctrl._ball.hidden is False


def test_step_done_caught_triggers_reveal(db_path):
    from tokenmon.popover.panes.catch_animation import CatchAnimationController
    pop = _FakePopover()
    payload = {"caught": True}
    ctrl = CatchAnimationController(pop, payload)
    # No view setup — end() should still run cleanly.
    ctrl.step("done", payload)
    assert pop._begin_reveal_calls == 1


def test_step_done_failure_re_enters_bag_open(db_path):
    from tokenmon.popover.panes.catch_animation import CatchAnimationController
    pop = _FakePopover()
    payload = {"caught": False}
    ctrl = CatchAnimationController(pop, payload)
    ctrl.step("done", payload)
    assert pop._encounter_bag_open is True
    assert pop._show_pane_calls != []

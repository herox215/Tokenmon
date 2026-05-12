"""Tests for the chat-panel idle state machine.

The pure ``IdleStateMachine`` is exercised here with a seeded RNG and
hand-driven clock, so we cover transitions, action math, and the
PACE/BOB anchor tracking without touching AppKit. The NSObject shell
``ChatIdleAnimator`` is covered indirectly by the existing chat-dock
tests (its only side-effects are NSTimer scheduling and
setFrameOrigin, both untestable headlessly).
"""
from __future__ import annotations

import math
import random

from tokenmon.companion.chat_idle import (
    BOB_AMPLITUDE_PX,
    BOB_PERIOD_S,
    FIRST_ACTION_MAX_S,
    FIRST_ACTION_MIN_S,
    HOP_DURATION_S,
    HOP_HEIGHT_PX,
    NEXT_ACTION_MAX_S,
    NEXT_ACTION_MIN_S,
    PACE_DURATION_S,
    SHAKE_AMPLITUDE_PX,
    SHAKE_DURATION_S,
    Action,
    IdleStateMachine,
)


def _make(anchor=(100.0, 200.0), x_range=(0.0, 1000.0), seed=42):
    sm = IdleStateMachine(
        anchor_x=anchor[0],
        anchor_y=anchor[1],
        x_range=x_range,
        rng=random.Random(seed),
    )
    sm.reset(now=0.0)
    return sm


def test_bob_centres_on_anchor_at_period_boundaries():
    """At t=0 and t=BOB_PERIOD the sine is 0 — sprite sits exactly on
    anchor y. This catches off-by-one phase bugs that would leave the
    sprite slightly offset all the time."""
    sm = _make()
    x0, y0 = sm.tick(0.0)
    assert x0 == 100.0
    assert y0 == 200.0
    x1, y1 = sm.tick(BOB_PERIOD_S)
    assert math.isclose(y1, 200.0, abs_tol=1e-9)
    assert math.isclose(x1, 100.0, abs_tol=1e-9)


def test_bob_amplitude_peaks_at_quarter_period():
    sm = _make()
    _, y = sm.tick(BOB_PERIOD_S / 4.0)
    assert math.isclose(y, 200.0 + BOB_AMPLITUDE_PX, abs_tol=1e-9)


def test_next_action_scheduled_within_bounds():
    """The *first* one-shot lands inside the FIRST_ACTION_* window —
    sooner than steady-state so the sprite kicks into HOP/SHAKE/PACE
    right after the chat panel finishes its dock slide, rather than
    sitting through a long BOB-only stretch the user reads as 'frozen'.
    """
    sm = _make()
    assert FIRST_ACTION_MIN_S <= sm._next_action_t <= FIRST_ACTION_MAX_S


def test_one_shot_action_fires_on_schedule():
    """Advance past _next_action_t and we should see the state leave
    BOB. The exact action depends on the seeded RNG."""
    sm = _make()
    # Stay in BOB just before the deadline.
    sm.tick(sm._next_action_t - 0.01)
    assert sm._state == Action.BOB
    # Cross the deadline.
    sm.tick(sm._next_action_t + 0.001)
    assert sm._state != Action.BOB
    assert sm._state in (Action.HOP, Action.SHAKE, Action.PACE)


def test_hop_returns_to_anchor_y_at_end():
    """A half-sine from 0 to π lands at 0. Without that, BOB would
    resume from an offset and feel like a step."""
    sm = _make(seed=1)
    # Force HOP regardless of RNG roll so we test the action math
    # directly, not the selector.
    sm._start_one_shot(0.0, Action.HOP)
    # Peak at the midpoint.
    _, peak_y = sm.tick(HOP_DURATION_S / 2.0)
    assert math.isclose(peak_y, 200.0 + HOP_HEIGHT_PX, abs_tol=1e-9)
    # End of the arc lands back on anchor.
    _, end_y = sm.tick(HOP_DURATION_S - 1e-6)
    assert math.isclose(end_y, 200.0, abs_tol=1e-3)


def test_hop_clears_after_duration():
    """When the hop finishes, state must transition back to BOB and
    schedule the next one-shot."""
    sm = _make(seed=1)
    sm._start_one_shot(0.0, Action.HOP)
    sm.tick(HOP_DURATION_S + 0.01)
    assert sm._state == Action.BOB
    # And a new deadline must be scheduled, far enough out.
    assert sm._next_action_t >= HOP_DURATION_S + 0.01 + NEXT_ACTION_MIN_S


def test_shake_decays_to_anchor():
    """SHAKE's linear damping must hit exactly zero at the end so BOB
    resumes from anchor_x, not from a stuck odd-direction offset."""
    sm = _make(seed=2)
    sm._start_one_shot(0.0, Action.SHAKE)
    # Peak deflection somewhere mid-shake — must be within ±amplitude.
    x_mid, _ = sm.tick(SHAKE_DURATION_S * 0.2)
    assert abs(x_mid - 100.0) <= SHAKE_AMPLITUDE_PX + 1e-6
    # At the boundary the decay factor is 0 → x returns to anchor.
    x_end, _ = sm.tick(SHAKE_DURATION_S - 1e-9)
    assert math.isclose(x_end, 100.0, abs_tol=1e-6)


def test_pace_updates_anchor_continuously():
    """Continuous anchor mutation is the contract that makes BOB
    centre on the *current* paced position instead of snapping at the
    end of the slide. Verify the anchor moves DURING the pace, not
    just at the end."""
    sm = _make(seed=7, x_range=(0.0, 1000.0))
    sm._start_one_shot(0.0, Action.PACE)
    start_anchor = sm.anchor_x
    # Halfway through, anchor must have moved roughly halfway.
    sm.tick(PACE_DURATION_S * 0.5)
    mid_anchor = sm.anchor_x
    assert mid_anchor != start_anchor
    target = sm._pace_target_x
    expected_mid = start_anchor + (target - start_anchor) * 0.5
    assert math.isclose(mid_anchor, expected_mid, abs_tol=1e-6)


def test_pace_lands_exactly_on_target():
    """Floating-point drift over a 0.8 s lerp could leave us a fraction
    of a pixel off. The end-of-PACE snap fixes that."""
    sm = _make(seed=7)
    sm._start_one_shot(0.0, Action.PACE)
    target = sm._pace_target_x
    # Cross the end so the completion path fires.
    sm.tick(PACE_DURATION_S + 0.01)
    assert math.isclose(sm.anchor_x, target, abs_tol=1e-9)
    assert sm._state == Action.BOB


def test_pace_target_far_enough_from_current_anchor():
    """The selector rejects tiny paces (<20 px) so the motion is
    actually visible. Verify across many seeds."""
    for seed in range(20):
        sm = _make(seed=seed, x_range=(0.0, 1000.0))
        sm._start_one_shot(0.0, Action.PACE)
        assert abs(sm._pace_target_x - sm._pace_start_x) >= 20.0


def test_pace_target_within_range():
    """PACE must keep the sprite over the chat panel. Any target
    outside x_range would push the sprite off the panel edge."""
    lo, hi = 50.0, 300.0
    for seed in range(20):
        sm = _make(seed=seed, x_range=(lo, hi), anchor=(150.0, 200.0))
        sm._start_one_shot(0.0, Action.PACE)
        assert lo <= sm._pace_target_x <= hi


def test_pace_in_degenerate_range_is_noop():
    """A chat panel barely wider than the sprite leaves no room to
    pace. Falling back to the current anchor avoids a divide-by-zero
    or out-of-range slide."""
    sm = _make(seed=3, x_range=(100.0, 100.0))
    sm._start_one_shot(0.0, Action.PACE)
    assert sm._pace_target_x == sm.anchor_x


def test_stop_position_returns_current_anchor():
    """stop() snaps to the current anchor — which may have moved if
    PACE updated it. We accept the new position rather than
    teleporting back to the original anchor."""
    sm = _make(seed=7)
    sm._start_one_shot(0.0, Action.PACE)
    sm.tick(PACE_DURATION_S * 0.5)
    x, y = sm.stop_position()
    assert x == sm.anchor_x
    assert y == sm.anchor_y


def test_one_shot_selector_never_re_picks_during_action():
    """While a one-shot is running, the selector must not start
    another action — concurrent HOP+SHAKE would produce nonsense."""
    sm = _make(seed=5)
    sm._start_one_shot(0.0, Action.HOP)
    # Even crossing the (stale) next-action deadline mid-HOP must not
    # transition to another one-shot.
    sm._next_action_t = 0.1  # force the deadline into our window
    sm.tick(0.2)
    assert sm._state == Action.HOP


def test_reset_restores_clean_state():
    """A second reset (called by ChatIdleAnimator.start on reattach)
    must drop any in-flight action and start a fresh BOB."""
    sm = _make()
    sm._start_one_shot(0.0, Action.PACE)
    assert sm._state == Action.PACE
    sm.reset(now=10.0)
    assert sm._state == Action.BOB
    assert sm._t0 == 10.0
    # Next-action deadline must be in the future relative to the new t0.
    assert sm._next_action_t > 10.0

"""Tests for the chat-guest spawn/despawn scheduler.

The pure ``GuestScheduler`` is exercised here with a seeded RNG and
hand-driven clock — same headless pattern used for ``IdleStateMachine``
in ``test_companion_chat_idle.py``. The AppKit shell
``ChatGuestDriver`` is only smoke-touched (it spins NSTimers and
opens NSWindows, both untestable without a running run-loop).
"""
from __future__ import annotations

import random

from tokenmon.companion.chat_guests import (
    COOLDOWN_MAX_S,
    COOLDOWN_MIN_S,
    LIFESPAN_MAX_S,
    LIFESPAN_MIN_S,
    GuestScheduler,
)


def _make(seed: int = 42) -> GuestScheduler:
    return GuestScheduler(rng=random.Random(seed))


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------


def test_reset_places_first_spawn_inside_cooldown_range():
    """After ``reset(t0)`` the next spawn must land between
    ``t0 + COOLDOWN_MIN_S`` and ``t0 + COOLDOWN_MAX_S`` — without the
    initial cooldown the first guest would pop in instantly every time
    the chat opens, which would feel scripted instead of spontaneous."""
    sched = _make()
    sched.reset(now=1000.0)
    # tick at t = COOLDOWN_MIN_S - 1 → no spawn yet
    assert sched.tick(1000.0 + COOLDOWN_MIN_S - 1.0) is None
    # tick at t = COOLDOWN_MAX_S + 1 → must have spawned by now
    assert sched.tick(1000.0 + COOLDOWN_MAX_S + 1.0) == "spawn"


def test_reset_clears_active_guest_state():
    """A scheduler in the middle of a guest cameo must reset cleanly —
    no leftover ``_active_until_t`` from before. This catches a bug
    where chat-reattach would inherit a stale despawn deadline."""
    sched = _make()
    sched.reset(now=0.0)
    sched.note_spawn(now=10.0, lifespan=10.0)
    assert sched.is_guest_active()
    sched.reset(now=100.0)
    assert not sched.is_guest_active()


# ---------------------------------------------------------------------------
# spawn / despawn cycle
# ---------------------------------------------------------------------------


def test_tick_returns_spawn_once_cooldown_elapsed():
    """The first ``tick()`` past the cooldown deadline returns
    ``"spawn"``. Before the deadline it must return ``None`` — the
    driver relies on this to decide whether to even try picking a
    Pokémon (a blocking DB read)."""
    sched = _make(seed=7)
    sched.reset(now=0.0)
    # Force-set the deadline so we don't have to depend on the RNG
    # for this assertion. ``_next_spawn_t`` is a clear contract.
    sched._next_spawn_t = 30.0
    assert sched.tick(0.0) is None
    assert sched.tick(29.9) is None
    assert sched.tick(30.0) == "spawn"
    assert sched.tick(45.0) == "spawn"  # still "spawn" until note_spawn fires


def test_tick_returns_despawn_after_lifespan():
    """Once a guest is active, ``tick()`` ignores the spawn deadline
    entirely and watches the despawn deadline instead. Both deadlines
    are mutually exclusive states."""
    sched = _make()
    sched.reset(now=0.0)
    sched.note_spawn(now=100.0, lifespan=10.0)
    assert sched.tick(105.0) is None
    assert sched.tick(110.0) == "despawn"
    assert sched.tick(150.0) == "despawn"  # idempotent until note_despawn


def test_pick_lifespan_inside_range():
    """Every lifespan roll must land inside the configured range so
    the driver never schedules a 0 s guest (instant despawn flash) or
    a multi-minute guest (the chat-cameo intent breaks)."""
    sched = _make(seed=13)
    for _ in range(50):
        lifespan = sched.pick_lifespan()
        assert LIFESPAN_MIN_S <= lifespan <= LIFESPAN_MAX_S


# ---------------------------------------------------------------------------
# cooldown after despawn
# ---------------------------------------------------------------------------


def test_note_despawn_pushes_next_spawn_into_cooldown():
    """Cooldown starts at *despawn*, not spawn — otherwise two cameos
    could land back-to-back if a guest happened to leave just after
    the cooldown timer expired. This is the regression test for that."""
    sched = _make()
    sched.reset(now=0.0)
    sched.note_spawn(now=100.0, lifespan=10.0)
    sched.note_despawn(now=110.0)
    assert not sched.is_guest_active()
    # No spawn during cooldown
    assert sched.tick(110.0 + COOLDOWN_MIN_S - 1.0) is None
    # Must have spawned by COOLDOWN_MAX_S after despawn
    assert sched.tick(110.0 + COOLDOWN_MAX_S + 1.0) == "spawn"


def test_note_spawn_failed_rolls_new_cooldown():
    """If the driver can't actually open the guest (empty box, sprite
    fetch failed, …) the scheduler must NOT keep returning
    ``"spawn"`` on every following tick — that would busy-loop the
    pick-and-fail path. ``note_spawn_failed`` rolls a fresh cooldown."""
    sched = _make()
    sched.reset(now=0.0)
    sched._next_spawn_t = 30.0
    assert sched.tick(50.0) == "spawn"
    sched.note_spawn_failed(now=50.0)
    # Right after the failure, no spawn
    assert sched.tick(50.0) is None
    # Still no spawn before MIN cooldown
    assert sched.tick(50.0 + COOLDOWN_MIN_S - 1.0) is None
    # Spawn within MAX
    assert sched.tick(50.0 + COOLDOWN_MAX_S + 1.0) == "spawn"
    # And no guest is active in the meantime
    assert not sched.is_guest_active()


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_same_seed_produces_same_schedule():
    """Two schedulers with the same seed driven by the same clock
    must produce identical spawn timing — important so CI is
    deterministic and behaviour can be reproduced from logs."""
    a = _make(seed=99)
    b = _make(seed=99)
    a.reset(now=0.0)
    b.reset(now=0.0)
    assert a._next_spawn_t == b._next_spawn_t
    a.note_spawn(now=a._next_spawn_t, lifespan=a.pick_lifespan())
    b.note_spawn(now=b._next_spawn_t, lifespan=b.pick_lifespan())
    assert a._active_until_t == b._active_until_t

"""Tests for the FREEZE non-volatile status handler."""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

from tokenmon.battle.engine import (
    AttackEvent,
    StatusInflictedEvent,
    StatusPreventedEvent,
    StatusTickEvent,
    plan_turn,
)
from tokenmon.battle.models import BattleStats, Move
from tokenmon.battle.status import (
    NON_VOLATILE_REGISTRY,
    NonVolatileStatus,
    StatusState,
)
from tokenmon.battle.status_handlers import freeze as freeze_handler


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
ICE_BEAM = Move(
    key="ice-beam", name="Ice Beam", type="ice", category="special",
    power=90, accuracy=100, pp=10,
    ailment="freeze", ailment_chance=10,
)
FREEZE_POWDER = Move(
    key="freeze-powder", name="Freeze Powder", type="grass", category="status",
    power=None, accuracy=100, pp=10,
    ailment="freeze", ailment_chance=0,
)
FLAMETHROWER = Move(
    key="flamethrower", name="Flamethrower", type="fire", category="special",
    power=90, accuracy=100, pp=15,
)


def _mon(
    *, name="Mon", speed=100, hp=100, types=("normal",),
    attack=100, defense=100, sp_attack=100, sp_defense=100,
    moves=(TACKLE,), status=None,
) -> BattleStats:
    return BattleStats(
        species_dex_id=1, level=20, types=types,
        hp_max=hp, hp_current=hp,
        attack=attack, defense=defense,
        sp_attack=sp_attack, sp_defense=sp_defense,
        speed=speed,
        moves=moves, move_pps=tuple(m.pp for m in moves),
        name=name,
        status=status if status is not None else StatusState(),
    )


def _handlers():
    return NON_VOLATILE_REGISTRY[NonVolatileStatus.FREEZE]


def _frozen(stats: BattleStats) -> BattleStats:
    return replace(stats, status=StatusState(non_volatile=NonVolatileStatus.FREEZE))


# --- can_inflict ---------------------------------------------------------


def test_can_inflict_rejects_ice_type():
    target = _mon(types=("ice",))
    assert _handlers().can_inflict(target) is False


def test_can_inflict_rejects_dual_type_with_ice():
    target = _mon(types=("water", "ice"))
    assert _handlers().can_inflict(target) is False


def test_can_inflict_rejects_already_statused():
    target = _mon(
        types=("normal",),
        status=StatusState(non_volatile=NonVolatileStatus.BURN),
    )
    assert _handlers().can_inflict(target) is False


def test_can_inflict_accepts_healthy_non_ice():
    target = _mon(types=("normal",))
    assert _handlers().can_inflict(target) is True


# --- on_inflict ----------------------------------------------------------


def test_on_inflict_applies_freeze_status():
    target = _mon(name="Foe", types=("normal",))
    attacker = _mon(name="P")
    rng = random.Random(0)
    new_target, events = _handlers().on_inflict(
        target, attacker=attacker, move=ICE_BEAM,
        actor="player", target_side="opp", rng=rng,
    )
    assert new_target.status.non_volatile == NonVolatileStatus.FREEZE
    assert len(events) == 1
    assert isinstance(events[0], StatusInflictedEvent)
    assert events[0].side == "opp"
    assert events[0].status == "freeze"
    assert events[0].message == "Foe was frozen solid!"


# --- pre_action ----------------------------------------------------------


def test_pre_action_thaw_rate_is_20_percent():
    base = _frozen(_mon(name="F"))
    thaws = 0
    trials = 2000
    for seed in range(trials):
        result = _handlers().pre_action(base, "player", rng=random.Random(seed))
        if result.can_act:
            thaws += 1
    rate = thaws / trials
    assert 0.16 < rate < 0.24, f"thaw rate {rate} out of band"


def test_pre_action_no_thaw_emits_status_prevented():
    base = _frozen(_mon(name="F"))
    saw_block = False
    for seed in range(200):
        rng = random.Random(seed)
        result = _handlers().pre_action(base, "player", rng=rng)
        if not result.can_act:
            saw_block = True
            assert len(result.events) == 1
            ev = result.events[0]
            assert isinstance(ev, StatusPreventedEvent)
            assert ev.side == "player"
            assert ev.status == "freeze"
            assert ev.message == "F is frozen solid!"
            assert result.new_stats.status.non_volatile == NonVolatileStatus.FREEZE
            break
    assert saw_block, "never saw a block across 200 seeds"


def test_pre_action_thaw_emits_status_tick_and_clears_status():
    base = _frozen(_mon(name="F"))
    saw_thaw = False
    for seed in range(200):
        rng = random.Random(seed)
        result = _handlers().pre_action(base, "player", rng=rng)
        if result.can_act:
            saw_thaw = True
            assert len(result.events) == 1
            ev = result.events[0]
            assert isinstance(ev, StatusTickEvent)
            assert ev.side == "player"
            assert ev.status == "freeze"
            assert ev.damage == 0
            assert ev.hp_before == ev.hp_after == base.hp_current
            assert ev.message == "F thawed out!"
            assert result.new_stats.status.non_volatile == NonVolatileStatus.HEALTHY
            assert result.new_stats.status.nv_counter == 0
            break
    assert saw_thaw, "never saw a thaw across 200 seeds"


# --- engine integration --------------------------------------------------


def test_frozen_mon_usually_cannot_act_for_many_turns():
    """A freshly frozen mon should be blocked on most turns; over 50 turns
    we expect somewhere between 0 and ~30 attacks (mean ≈ 10 at 20%)."""
    p = _mon(name="P", speed=200, status=StatusState(non_volatile=NonVolatileStatus.FREEZE))
    o = _mon(name="O", speed=20, hp=99999, defense=999)

    state_p = p
    state_o = o
    p_attacks = 0
    p_blocks = 0
    rng = random.Random(0)
    for _turn in range(50):
        events = plan_turn(
            state_p, state_o,
            player_move=TACKLE, opp_move=TACKLE,
            rng=rng,
        )
        for ev in events:
            if isinstance(ev, AttackEvent) and ev.actor == "player":
                p_attacks += 1
            elif isinstance(ev, StatusPreventedEvent) and ev.side == "player":
                p_blocks += 1

    assert p_blocks > p_attacks, (
        f"expected blocks ({p_blocks}) > attacks ({p_attacks}) over 50 turns"
    )
    assert p_blocks >= 30, f"only {p_blocks} blocks in 50 turns — too few"


def test_status_move_with_zero_chance_always_freezes_non_ice_target():
    p = _mon(name="P", speed=200)
    o = _mon(name="O", speed=20, types=("normal",))
    saw_inflict = False
    for seed in range(20):
        events = plan_turn(
            p, o,
            player_move=FREEZE_POWDER, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        inflicts = [
            e for e in events
            if isinstance(e, StatusInflictedEvent) and e.status == "freeze"
        ]
        assert len(inflicts) == 1, (
            f"seed {seed}: expected 1 freeze inflict, got {len(inflicts)}"
        )
        saw_inflict = True
    assert saw_inflict


# --- fire-thaw scope gap (TODO marker) ------------------------------------


@pytest.mark.xfail(strict=True, reason="fire-thaw needs an engine hook")
def test_fire_move_thaws_frozen_defender():
    """When a frozen defender is hit by a fire-typed move it should thaw
    (Gen-3 canon). Currently the engine has no post-receive-attack hook,
    so this is a known scope gap. Marked xfail strict so a future engine
    integration that wires ``thaw_on_fire_hit`` flips this to pass and
    forces removal of the marker.

    The player is faster and one-shots the defender, so the opp never
    runs its own ``pre_action`` thaw roll — any ``thawed out!`` tick we
    see here must come from the fire-on-frozen receive path."""
    p = _mon(name="P", speed=200, attack=999, moves=(FLAMETHROWER,))
    o = _mon(
        name="O", speed=20, hp=1, defense=1,
        status=StatusState(non_volatile=NonVolatileStatus.FREEZE),
    )
    events = plan_turn(
        p, o,
        player_move=FLAMETHROWER, opp_move=TACKLE,
        rng=random.Random(0),
    )
    thaw_ticks = [
        e for e in events
        if isinstance(e, StatusTickEvent)
        and e.status == "freeze"
        and "thawed" in e.message
        and e.side == "opp"
    ]
    assert len(thaw_ticks) == 1


# --- helper stub still callable ------------------------------------------


def test_thaw_on_fire_hit_helper_clears_freeze_for_fire_move():
    """The ``thaw_on_fire_hit`` helper itself works in isolation — only
    the engine integration is missing."""
    o = _mon(name="O", status=StatusState(non_volatile=NonVolatileStatus.FREEZE))
    new_o, events = freeze_handler.thaw_on_fire_hit(o, FLAMETHROWER)
    assert new_o.status.non_volatile == NonVolatileStatus.HEALTHY
    assert len(events) == 1
    assert isinstance(events[0], StatusTickEvent)
    assert events[0].message == "O thawed out!"


def test_thaw_on_fire_hit_helper_noop_for_non_fire_move():
    o = _mon(name="O", status=StatusState(non_volatile=NonVolatileStatus.FREEZE))
    new_o, events = freeze_handler.thaw_on_fire_hit(o, TACKLE)
    assert new_o.status.non_volatile == NonVolatileStatus.FREEZE
    assert events == []


def test_thaw_on_fire_hit_helper_noop_for_non_frozen_target():
    o = _mon(name="O")
    new_o, events = freeze_handler.thaw_on_fire_hit(o, FLAMETHROWER)
    assert new_o.status.non_volatile == NonVolatileStatus.HEALTHY
    assert events == []

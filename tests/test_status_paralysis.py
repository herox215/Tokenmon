"""Tests for the paralysis non-volatile status handler."""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

from tokenmon.battle.engine import (
    AttackEvent,
    StatusInflictedEvent,
    StatusPreventedEvent,
    plan_turn,
    turn_order,
)
from tokenmon.battle.models import BattleStats, Move
from tokenmon.battle.status import (
    NON_VOLATILE_REGISTRY,
    NonVolatileStatus,
    StatusState,
    speed_after_status,
)


# --- Move fixtures -------------------------------------------------------
# Real PokeAPI ailment metadata: paralysis-causing moves carry
# ailment="paralysis"; chance==0 means "guaranteed if it lands".

THUNDER_WAVE = Move(
    key="thunder-wave", name="Thunder Wave", type="electric",
    category="status", power=None, accuracy=90, pp=20,
    ailment="paralysis", ailment_chance=0,
)
GLARE = Move(
    key="glare", name="Glare", type="normal",
    category="status", power=None, accuracy=100, pp=30,
    ailment="paralysis", ailment_chance=0,
)
STUN_SPORE = Move(
    key="stun-spore", name="Stun Spore", type="grass",
    category="status", power=None, accuracy=75, pp=30,
    ailment="paralysis", ailment_chance=0,
)
TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
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
    return NON_VOLATILE_REGISTRY[NonVolatileStatus.PARALYSIS]


# --- can_inflict ---------------------------------------------------------


def test_electric_type_cannot_be_paralyzed():
    pikachu = _mon(types=("electric",))
    assert _handlers().can_inflict(pikachu) is False


def test_already_statused_cannot_be_paralyzed():
    burned = _mon(status=StatusState(non_volatile=NonVolatileStatus.BURN))
    assert _handlers().can_inflict(burned) is False


def test_already_paralyzed_cannot_be_paralyzed_again():
    plzd = _mon(status=StatusState(non_volatile=NonVolatileStatus.PARALYSIS))
    assert _handlers().can_inflict(plzd) is False


def test_normal_healthy_mon_can_be_paralyzed():
    mon = _mon(types=("normal",))
    assert _handlers().can_inflict(mon) is True


# --- on_inflict ----------------------------------------------------------


def test_healthy_mon_gets_paralyzed_by_paralysis_move():
    target = _mon(name="Pidgey", types=("normal", "flying"))
    attacker = _mon(name="Atk")
    new_target, events = _handlers().on_inflict(
        target,
        attacker=attacker,
        move=GLARE,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_target.status.non_volatile == NonVolatileStatus.PARALYSIS
    assert len(events) == 1
    assert isinstance(events[0], StatusInflictedEvent)
    assert events[0].side == "opp"
    assert events[0].status == "paralysis"
    assert "Pidgey was paralyzed!" == events[0].message


def test_ground_type_immune_to_thunder_wave():
    """Ground type immunity to Electric-typed status moves."""
    diglett = _mon(name="Diglett", types=("ground",))
    attacker = _mon(name="Pikachu", types=("electric",))
    new_target, events = _handlers().on_inflict(
        diglett,
        attacker=attacker,
        move=THUNDER_WAVE,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_target is diglett
    assert new_target.status.non_volatile == NonVolatileStatus.HEALTHY
    assert events == []


def test_ground_type_can_be_paralyzed_by_glare():
    """Glare is Normal-typed; Ground immunity must not over-apply."""
    diglett = _mon(name="Diglett", types=("ground",))
    attacker = _mon(name="Atk")
    new_target, events = _handlers().on_inflict(
        diglett,
        attacker=attacker,
        move=GLARE,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_target.status.non_volatile == NonVolatileStatus.PARALYSIS
    assert len(events) == 1
    assert isinstance(events[0], StatusInflictedEvent)


def test_ground_type_can_be_paralyzed_by_stun_spore():
    """Stun Spore is Grass-typed; Ground immunity must not over-apply."""
    diglett = _mon(name="Diglett", types=("ground",))
    attacker = _mon(name="Atk")
    new_target, events = _handlers().on_inflict(
        diglett,
        attacker=attacker,
        move=STUN_SPORE,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_target.status.non_volatile == NonVolatileStatus.PARALYSIS
    assert len(events) == 1


# --- modify_speed --------------------------------------------------------


def test_modify_speed_quarters_speed():
    mon = _mon(speed=200)
    assert _handlers().modify_speed(mon, 200) == 50


def test_modify_speed_floor_division():
    mon = _mon(speed=7)
    assert _handlers().modify_speed(mon, 7) == 1


def test_speed_after_status_applies_paralysis():
    plzd = _mon(
        speed=400,
        status=StatusState(non_volatile=NonVolatileStatus.PARALYSIS),
    )
    assert speed_after_status(plzd) == 100


# --- pre_action ----------------------------------------------------------


def test_pre_action_skip_rate_around_25_percent():
    stats = _mon(
        status=StatusState(non_volatile=NonVolatileStatus.PARALYSIS),
    )
    skips = 0
    trials = 1000
    for seed in range(trials):
        result = _handlers().pre_action(stats, "player", rng=random.Random(seed))
        if not result.can_act:
            skips += 1
    rate = skips / trials
    assert 0.18 < rate < 0.32, f"skip rate {rate} outside expected band"


def test_pre_action_emits_prevented_event_on_skip():
    stats = _mon(
        name="Bulba",
        status=StatusState(non_volatile=NonVolatileStatus.PARALYSIS),
    )
    saw_skip = False
    for seed in range(200):
        result = _handlers().pre_action(stats, "player", rng=random.Random(seed))
        if not result.can_act:
            saw_skip = True
            assert result.new_stats is stats
            assert len(result.events) == 1
            ev = result.events[0]
            assert isinstance(ev, StatusPreventedEvent)
            assert ev.side == "player"
            assert ev.status == "paralysis"
            assert "Bulba is paralyzed and can't move!" == ev.message
            break
    assert saw_skip, "never saw a skip across 200 seeds"


def test_pre_action_no_events_when_can_act():
    stats = _mon(
        status=StatusState(non_volatile=NonVolatileStatus.PARALYSIS),
    )
    saw_act = False
    for seed in range(200):
        result = _handlers().pre_action(stats, "player", rng=random.Random(seed))
        if result.can_act:
            saw_act = True
            assert result.new_stats is stats
            assert result.events == []
            break
    assert saw_act, "never saw a non-skip across 200 seeds"


# --- Engine integration --------------------------------------------------


def test_turn_order_paralyzed_slow_loses_to_unparalyzed_fast():
    """Paralyzed mon's effective speed is base/4. A non-paralyzed mon
    slightly slower in raw speed should still go first."""
    slow_paralyzed = _mon(
        name="SlowPlz",
        speed=400,  # → 100 after paralysis
        status=StatusState(non_volatile=NonVolatileStatus.PARALYSIS),
    )
    fast_unparalyzed = _mon(name="Fast", speed=150)
    order = turn_order(slow_paralyzed, fast_unparalyzed, rng=random.Random(0))
    assert order == ("opp", "player")


def test_turn_order_unparalyzed_outpaces_paralyzed_at_same_base_speed():
    a = _mon(
        name="A",
        speed=200,
        status=StatusState(non_volatile=NonVolatileStatus.PARALYSIS),
    )
    b = _mon(name="B", speed=200)
    for seed in range(10):
        order = turn_order(a, b, rng=random.Random(seed))
        assert order == ("opp", "player")


def test_engine_paralyzed_mon_sometimes_skips_with_prevented_event():
    """Across many seeds, a paralyzed actor occasionally fails its
    pre-action roll, emitting StatusPreventedEvent and producing no
    AttackEvent for that side that turn."""
    plzd = _mon(
        name="Plz",
        speed=200,
        status=StatusState(non_volatile=NonVolatileStatus.PARALYSIS),
    )
    other = _mon(name="Other", speed=10)
    saw_prevent = False
    for seed in range(200):
        events = plan_turn(
            plzd, other, player_move=TACKLE, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        prevented = [
            e for e in events
            if isinstance(e, StatusPreventedEvent) and e.side == "player"
        ]
        if prevented:
            saw_prevent = True
            assert prevented[0].status == "paralysis"
            player_attacks = [
                e for e in events
                if isinstance(e, AttackEvent) and e.actor == "player"
            ]
            assert player_attacks == []
            break
    assert saw_prevent, "paralyzed mon never skipped across 200 seeds"

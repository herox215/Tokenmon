"""Tests for the SLEEP non-volatile status handler."""
from __future__ import annotations

import random
from dataclasses import replace

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
    _ensure_handlers_loaded,
)
from tokenmon.battle.status_handlers import sleep as sleep_handler


_ensure_handlers_loaded()


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
SPORE = Move(
    key="spore", name="Spore", type="grass", category="status",
    power=None, accuracy=100, pp=15,
    ailment="sleep", ailment_chance=0,
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


# --- can_inflict --------------------------------------------------------


def test_already_statused_mon_cannot_be_slept():
    target = _mon(status=StatusState(non_volatile=NonVolatileStatus.POISON))
    assert sleep_handler.can_inflict(target) is False


def test_healthy_mon_can_be_slept():
    target = _mon()
    assert sleep_handler.can_inflict(target) is True


def test_already_sleeping_mon_cannot_be_re_slept():
    target = _mon(status=StatusState(non_volatile=NonVolatileStatus.SLEEP, nv_counter=3))
    assert sleep_handler.can_inflict(target) is False


# --- on_inflict ---------------------------------------------------------


def test_on_inflict_sets_sleep_status_and_counter_in_range():
    target = _mon(name="Sleeper")
    rng = random.Random(0)
    new_stats, events = sleep_handler.on_inflict(
        target,
        attacker=None, move=SPORE, actor="player", target_side="opp", rng=rng,
    )
    assert new_stats.status.non_volatile == NonVolatileStatus.SLEEP
    assert 2 <= new_stats.status.nv_counter <= 5
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, StatusInflictedEvent)
    assert ev.side == "opp"
    assert ev.status == "sleep"
    assert "Sleeper" in ev.message
    assert "asleep" in ev.message.lower()


def test_on_inflict_yields_each_duration_across_seeds():
    target = _mon()
    seen: set[int] = set()
    for seed in range(200):
        rng = random.Random(seed)
        new_stats, _ = sleep_handler.on_inflict(
            target,
            attacker=None, move=SPORE, actor="player", target_side="opp", rng=rng,
        )
        seen.add(new_stats.status.nv_counter)
    assert seen == {2, 3, 4, 5}


# --- pre_action ---------------------------------------------------------


def test_pre_action_decrements_counter_and_blocks_when_above_one():
    stats = _mon(
        name="Sleeper",
        status=StatusState(non_volatile=NonVolatileStatus.SLEEP, nv_counter=3),
    )
    result = sleep_handler.pre_action(stats, "player", rng=random.Random(0))
    assert result.can_act is False
    assert result.new_stats.status.non_volatile == NonVolatileStatus.SLEEP
    assert result.new_stats.status.nv_counter == 2
    assert len(result.events) == 1
    ev = result.events[0]
    assert isinstance(ev, StatusPreventedEvent)
    assert ev.side == "player"
    assert ev.status == "sleep"
    assert "fast asleep" in ev.message.lower()


def test_pre_action_with_counter_two_decrements_to_one_still_blocks():
    stats = _mon(
        status=StatusState(non_volatile=NonVolatileStatus.SLEEP, nv_counter=2),
    )
    result = sleep_handler.pre_action(stats, "player", rng=random.Random(0))
    assert result.can_act is False
    assert result.new_stats.status.nv_counter == 1


def test_pre_action_with_counter_one_wakes_and_allows_action():
    stats = _mon(
        name="Sleeper",
        status=StatusState(non_volatile=NonVolatileStatus.SLEEP, nv_counter=1),
    )
    result = sleep_handler.pre_action(stats, "opp", rng=random.Random(0))
    assert result.can_act is True
    assert result.new_stats.status.non_volatile == NonVolatileStatus.HEALTHY
    assert result.new_stats.status.nv_counter == 0
    assert len(result.events) == 1
    ev = result.events[0]
    assert isinstance(ev, StatusTickEvent)
    assert ev.side == "opp"
    assert ev.status == "sleep"
    assert ev.damage == 0
    assert ev.hp_before == ev.hp_after == stats.hp_current
    assert "woke up" in ev.message.lower()


# --- engine integration -------------------------------------------------


def test_sleeping_mon_does_not_attack_while_counter_above_one():
    asleep = _mon(
        name="Snoozer", speed=200,
        status=StatusState(non_volatile=NonVolatileStatus.SLEEP, nv_counter=3),
    )
    foe = _mon(name="Foe", speed=20)
    events = plan_turn(
        asleep, foe, player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(0),
    )
    player_attacks = [
        e for e in events if isinstance(e, AttackEvent) and e.actor == "player"
    ]
    assert player_attacks == []
    prevented = [e for e in events if isinstance(e, StatusPreventedEvent)]
    assert len(prevented) == 1
    assert prevented[0].side == "player"
    assert prevented[0].status == "sleep"


def test_waking_mon_attacks_same_turn():
    waker = _mon(
        name="Waker", speed=200,
        status=StatusState(non_volatile=NonVolatileStatus.SLEEP, nv_counter=1),
    )
    foe = _mon(name="Foe", speed=20)
    events = plan_turn(
        waker, foe, player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(0),
    )
    ticks = [e for e in events if isinstance(e, StatusTickEvent)]
    assert len(ticks) == 1
    assert ticks[0].side == "player"
    assert ticks[0].status == "sleep"
    player_attacks = [
        e for e in events if isinstance(e, AttackEvent) and e.actor == "player"
    ]
    assert len(player_attacks) == 1
    assert events.index(ticks[0]) < events.index(player_attacks[0])


def test_status_move_with_zero_chance_always_sleeps_healthy_target():
    p = _mon(name="Caster", speed=200, moves=(SPORE,))
    o = _mon(name="Target", speed=20, moves=(TACKLE,))
    for seed in range(20):
        events = plan_turn(
            p, o, player_move=SPORE, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        inflicted = [
            e for e in events
            if isinstance(e, StatusInflictedEvent) and e.status == "sleep"
        ]
        assert len(inflicted) == 1, f"seed {seed}: expected sleep inflict"
        assert inflicted[0].side == "opp"


def test_registry_has_sleep_handler():
    handlers = NON_VOLATILE_REGISTRY.get(NonVolatileStatus.SLEEP)
    assert handlers is not None
    assert handlers.can_inflict is not None
    assert handlers.on_inflict is not None
    assert handlers.pre_action is not None

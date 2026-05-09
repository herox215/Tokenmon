"""Tests for the Poison + Bad Poison (Toxic) status handlers."""
from __future__ import annotations

import random
from dataclasses import replace

from tokenmon.battle.engine import (
    StatusInflictedEvent,
    StatusTickEvent,
    plan_turn,
)
from tokenmon.battle.models import BattleStats, Move
from tokenmon.battle.status import (
    NON_VOLATILE_REGISTRY,
    NonVolatileStatus,
    StatusState,
)


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
POISON_POWDER = Move(
    key="poison-powder", name="Poison Powder", type="poison",
    category="status", power=None, accuracy=None, pp=35,
    ailment="poison", ailment_chance=0,
)
TOXIC = Move(
    key="toxic", name="Toxic", type="poison", category="status",
    power=None, accuracy=90, pp=10,
    ailment="poison", ailment_chance=0,
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


def test_poison_type_immune_to_poison():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.POISON]
    target = _mon(types=("poison",))
    assert handlers.can_inflict(target) is False


def test_steel_type_immune_to_poison():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.POISON]
    target = _mon(types=("steel",))
    assert handlers.can_inflict(target) is False


def test_already_burned_mon_cannot_be_poisoned():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.POISON]
    target = _mon(
        types=("normal",),
        status=StatusState(non_volatile=NonVolatileStatus.BURN),
    )
    assert handlers.can_inflict(target) is False


def test_healthy_normal_type_can_be_poisoned():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.POISON]
    target = _mon(types=("normal",))
    assert handlers.can_inflict(target) is True


# --- on_inflict ---------------------------------------------------------


def test_poison_powder_inflicts_regular_poison():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.POISON]
    target = _mon(name="Bulba", types=("grass",))
    new_target, events = handlers.on_inflict(
        target,
        attacker=_mon(name="Atk"),
        move=POISON_POWDER,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_target.status.non_volatile == NonVolatileStatus.POISON
    assert new_target.status.nv_counter == 0
    assert len(events) == 1
    assert isinstance(events[0], StatusInflictedEvent)
    assert events[0].message == "Bulba was poisoned!"
    assert events[0].side == "opp"


def test_toxic_inflicts_bad_poison_via_poison_handler():
    """The slug→enum map points "poison" at POISON, but Toxic should
    re-route through the POISON on_inflict to BAD_POISON."""
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.POISON]
    target = _mon(name="Bulba", types=("grass",))
    new_target, events = handlers.on_inflict(
        target,
        attacker=_mon(name="Atk"),
        move=TOXIC,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_target.status.non_volatile == NonVolatileStatus.BAD_POISON
    assert new_target.status.nv_counter == 1
    assert events[0].message == "Bulba was badly poisoned!"


def test_bad_poison_direct_inflict_resets_counter_to_one():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.BAD_POISON]
    target = _mon(name="Bulba", types=("grass",))
    new_target, events = handlers.on_inflict(
        target,
        attacker=_mon(name="Atk"),
        move=TOXIC,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_target.status.nv_counter == 1
    assert new_target.status.non_volatile == NonVolatileStatus.BAD_POISON


# --- end_of_turn: regular poison ---------------------------------------


def test_poison_tick_deals_one_eighth_max_hp():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.POISON]
    target = _mon(
        hp=80, status=StatusState(non_volatile=NonVolatileStatus.POISON),
    )
    new_stats, events = handlers.end_of_turn(
        target, "player", rng=random.Random(0),
    )
    assert new_stats.hp_current == 80 - 10  # 80 // 8 == 10
    assert isinstance(events[0], StatusTickEvent)
    assert events[0].damage == 10
    assert events[0].message == "Mon is hurt by poison!"


def test_poison_tick_floor_at_one_for_tiny_pools():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.POISON]
    target = _mon(
        hp=5, status=StatusState(non_volatile=NonVolatileStatus.POISON),
    )
    new_stats, events = handlers.end_of_turn(
        target, "player", rng=random.Random(0),
    )
    # 5 // 8 == 0; floored to 1.
    assert new_stats.hp_current == 4
    assert events[0].damage == 1


# --- end_of_turn: bad poison -------------------------------------------


def test_bad_poison_ramps_each_turn():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.BAD_POISON]
    target = _mon(
        hp=160,
        status=StatusState(
            non_volatile=NonVolatileStatus.BAD_POISON, nv_counter=1,
        ),
    )

    s1, _ = handlers.end_of_turn(target, "player", rng=random.Random(0))
    assert s1.hp_current == 160 - 10  # 1 * 160 // 16 == 10
    assert s1.status.nv_counter == 2

    s2, _ = handlers.end_of_turn(s1, "player", rng=random.Random(0))
    assert s2.hp_current == 150 - 20  # 2 * 160 // 16 == 20
    assert s2.status.nv_counter == 3

    s3, _ = handlers.end_of_turn(s2, "player", rng=random.Random(0))
    assert s3.hp_current == 130 - 30  # 3 * 160 // 16 == 30
    assert s3.status.nv_counter == 4


def test_bad_poison_clamps_hp_at_zero():
    handlers = NON_VOLATILE_REGISTRY[NonVolatileStatus.BAD_POISON]
    target = _mon(
        hp=160,
        status=StatusState(
            non_volatile=NonVolatileStatus.BAD_POISON, nv_counter=8,
        ),
    )
    target = replace(target, hp_current=4)
    new_stats, events = handlers.end_of_turn(
        target, "player", rng=random.Random(0),
    )
    assert new_stats.hp_current == 0
    assert events[0].hp_after == 0
    assert events[0].damage == 4  # only the remaining HP, not the full 80


# --- engine integration ------------------------------------------------


def test_plan_turn_emits_poison_tick_at_end_of_turn():
    """A poisoned mon that survives an exchange takes a poison tick at
    the end of the turn."""
    poisoned = _mon(
        name="Poisoned",
        speed=20, hp=200,
        status=StatusState(non_volatile=NonVolatileStatus.POISON),
    )
    other = _mon(name="Other", speed=200, attack=10)
    events = plan_turn(
        poisoned, other,
        player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(0),
    )
    ticks = [e for e in events if isinstance(e, StatusTickEvent)]
    assert any(
        t.side == "player" and t.status == NonVolatileStatus.POISON.value
        for t in ticks
    )


def test_status_move_with_zero_chance_always_poisons():
    """PokeAPI uses ``ailment_chance=0`` to mean "guaranteed for status
    moves". A Poison Powder hit should land the status every time."""
    user = _mon(
        name="Atk", speed=200, types=("grass",),
        moves=(POISON_POWDER,),
    )
    target = _mon(
        name="Tgt", speed=20, types=("normal",),
        moves=(TACKLE,),
    )
    for seed in range(10):
        events = plan_turn(
            user, target,
            player_move=POISON_POWDER, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        inflicts = [
            e for e in events
            if isinstance(e, StatusInflictedEvent)
            and e.status == NonVolatileStatus.POISON.value
        ]
        assert inflicts, f"seed {seed} did not poison"

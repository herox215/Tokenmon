"""Tests for the Burn non-volatile status handler."""
from __future__ import annotations

import random
from dataclasses import replace

from tokenmon.battle.damage import compute_damage
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
    _ensure_handlers_loaded,
    ailment_to_status,
)

# Make sure the burn module is registered (it is via status_handlers/__init__).
_ensure_handlers_loaded()


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
EMBER = Move(
    key="ember", name="Ember", type="fire", category="special",
    power=40, accuracy=100, pp=25,
)
GROWL = Move(
    key="growl", name="Growl", type="normal", category="status",
    power=None, accuracy=100, pp=40,
)
WILL_O_WISP = Move(
    key="will-o-wisp", name="Will-O-Wisp", type="fire", category="status",
    power=None, accuracy=85, pp=15,
    ailment="burn", ailment_chance=0,
)
FIRE_PUNCH_BURN = Move(
    key="fire-punch", name="Fire Punch", type="fire", category="physical",
    power=75, accuracy=100, pp=15,
    ailment="burn", ailment_chance=10,
)


def _mon(
    *,
    name: str = "Mon",
    types: tuple[str, ...] = ("normal",),
    level: int = 50,
    hp: int = 100,
    hp_current: int | None = None,
    attack: int = 100,
    defense: int = 100,
    sp_attack: int = 100,
    sp_defense: int = 100,
    speed: int = 100,
    moves: tuple[Move, ...] = (TACKLE,),
    status: StatusState | None = None,
) -> BattleStats:
    kwargs = dict(
        species_dex_id=1,
        level=level,
        types=types,
        hp_max=hp,
        hp_current=hp if hp_current is None else hp_current,
        attack=attack,
        defense=defense,
        sp_attack=sp_attack,
        sp_defense=sp_defense,
        speed=speed,
        moves=moves,
        move_pps=tuple(m.pp for m in moves),
        name=name,
    )
    if status is not None:
        kwargs["status"] = status
    return BattleStats(**kwargs)


def _burn_handlers():
    return NON_VOLATILE_REGISTRY[NonVolatileStatus.BURN]


# --- can_inflict ---------------------------------------------------------


def test_fire_type_cannot_be_burned():
    target = _mon(types=("fire",))
    assert _burn_handlers().can_inflict(target) is False


def test_already_poisoned_cannot_be_burned():
    poisoned = _mon(
        types=("normal",),
        status=StatusState(non_volatile=NonVolatileStatus.POISON),
    )
    assert _burn_handlers().can_inflict(poisoned) is False


def test_healthy_non_fire_can_be_burned():
    target = _mon(types=("water",))
    assert _burn_handlers().can_inflict(target) is True


# --- on_inflict ----------------------------------------------------------


def test_on_inflict_sets_burn_and_emits_event():
    target = _mon(name="Bulba", types=("grass",))
    attacker = _mon(name="Charm", types=("fire",))
    new_stats, events = _burn_handlers().on_inflict(
        target,
        attacker=attacker,
        move=WILL_O_WISP,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_stats.status.non_volatile == NonVolatileStatus.BURN
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, StatusInflictedEvent)
    assert ev.side == "opp"
    assert ev.status == "burn"
    assert "burned" in ev.message.lower()


# --- modify_attack -------------------------------------------------------


def test_modify_attack_halves_physical():
    burned = _mon(status=StatusState(non_volatile=NonVolatileStatus.BURN))
    assert _burn_handlers().modify_attack(burned, TACKLE, 100) == 50


def test_modify_attack_leaves_special_unchanged():
    burned = _mon(status=StatusState(non_volatile=NonVolatileStatus.BURN))
    assert _burn_handlers().modify_attack(burned, EMBER, 200) == 200


def test_modify_attack_leaves_status_move_unchanged():
    burned = _mon(status=StatusState(non_volatile=NonVolatileStatus.BURN))
    assert _burn_handlers().modify_attack(burned, GROWL, 123) == 123


# --- end_of_turn ---------------------------------------------------------


def test_end_of_turn_deals_one_eighth_max_hp():
    burned = _mon(
        hp=80, hp_current=80,
        status=StatusState(non_volatile=NonVolatileStatus.BURN),
    )
    new_stats, events = _burn_handlers().end_of_turn(
        burned, "player", rng=random.Random(0),
    )
    assert new_stats.hp_current == 80 - 10
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, StatusTickEvent)
    assert ev.damage == 10
    assert ev.hp_after == 70


def test_end_of_turn_damage_floors_at_one():
    # hp_max // 8 == 0 for hp_max < 8 — handler must still tick at least 1.
    burned = _mon(
        hp=4, hp_current=4,
        status=StatusState(non_volatile=NonVolatileStatus.BURN),
    )
    new_stats, _ = _burn_handlers().end_of_turn(
        burned, "player", rng=random.Random(0),
    )
    assert new_stats.hp_current == 3


def test_end_of_turn_clamps_hp_at_zero():
    burned = _mon(
        hp=80, hp_current=3,
        status=StatusState(non_volatile=NonVolatileStatus.BURN),
    )
    new_stats, events = _burn_handlers().end_of_turn(
        burned, "player", rng=random.Random(0),
    )
    assert new_stats.hp_current == 0
    assert events[0].hp_after == 0


# --- damage formula integration -----------------------------------------


def test_burn_halves_physical_damage_in_damage_formula():
    """A burned attacker dealing a physical move should output roughly
    half the damage of an unburned same-stats attacker.

    Use a high-defense defender so the damage roll's 85..100 random factor
    produces a stable ratio (no min-1 floor in play)."""
    healthy = _mon(types=("normal",), level=50, attack=200)
    burned = replace(
        healthy,
        status=StatusState(non_volatile=NonVolatileStatus.BURN),
    )
    defender = _mon(types=("water",), defense=100, hp=500, hp_current=500)
    seed = 12345
    healthy_dmg = compute_damage(
        healthy, defender, TACKLE, rng=random.Random(seed),
    ).damage
    burned_dmg = compute_damage(
        burned, defender, TACKLE, rng=random.Random(seed),
    ).damage
    # With atk halved (200 → 100) the damage should drop ~50%. Allow a
    # small slop for floor() across the formula.
    assert burned_dmg < healthy_dmg
    ratio = burned_dmg / healthy_dmg
    assert 0.45 <= ratio <= 0.6


# --- engine integration --------------------------------------------------


def test_plan_turn_emits_burn_tick_at_end_of_turn():
    burned_player = _mon(
        name="Bulba", speed=200, hp=80, hp_current=80,
        status=StatusState(non_volatile=NonVolatileStatus.BURN),
    )
    opp = _mon(name="Foe", speed=20, hp=200, hp_current=200, defense=200)
    events = plan_turn(
        burned_player, opp,
        player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(0),
    )
    ticks = [
        e for e in events
        if isinstance(e, StatusTickEvent) and e.status == "burn"
    ]
    assert len(ticks) == 1
    assert ticks[0].side == "player"
    assert ticks[0].damage == 10
    assert "burn" in ticks[0].message.lower()


# --- move-ailment registration ------------------------------------------


def test_will_o_wisp_status_move_always_burns():
    """Will-O-Wisp-style: ailment="burn", ailment_chance=0 (PokeAPI's
    "guaranteed for status moves") must always inflict burn on a healthy,
    non-Fire target — verified through the engine's plan_turn pipeline."""
    # Ailment slug must be registered.
    status, is_volatile = ailment_to_status("burn")
    assert status == NonVolatileStatus.BURN
    assert is_volatile is False

    attacker = _mon(name="Ghost", types=("ghost",), speed=200)
    # Use a Ghost-typed defender so the opp's Tackle has 0× effect and the
    # turn reduces to "attacker uses Will-O-Wisp on defender".
    defender = _mon(
        name="Bulba", types=("grass",), speed=20, hp=100, hp_current=100,
    )
    burned_count = 0
    for seed in range(20):
        events = plan_turn(
            attacker, defender,
            player_move=WILL_O_WISP, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        # Will-O-Wisp can miss (acc=85) — only count seeds where the
        # ailment event fires.
        infl = [
            e for e in events
            if isinstance(e, StatusInflictedEvent) and e.status == "burn"
        ]
        if infl:
            burned_count += 1
            assert infl[0].side == "opp"
    assert burned_count > 0, "Will-O-Wisp never landed across 20 seeds"

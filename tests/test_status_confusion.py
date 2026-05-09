"""Tests for the Confusion volatile status handler."""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

from tokenmon.battle.engine import (
    AttackEvent,
    ConfusionSelfHitEvent,
    FaintEvent,
    StatusInflictedEvent,
    StatusTickEvent,
    plan_turn,
)
from tokenmon.battle.models import BattleStats, Move
from tokenmon.battle.status import (
    NonVolatileStatus,
    PreActionResult,
    StatusState,
    VOLATILE_REGISTRY,
    VolatileStatus,
    ailment_to_status,
)
from tokenmon.battle.status_handlers import confusion as confusion_mod


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
CONFUSE_RAY = Move(
    key="confuse-ray", name="Confuse Ray", type="ghost", category="status",
    power=None, accuracy=100, pp=10,
    ailment="confusion", ailment_chance=0,
)


def _mon(
    *, name="Mon", speed=100, hp=100, types=("normal",),
    attack=100, defense=100, sp_attack=100, sp_defense=100,
    moves=(TACKLE,), level=20, status=None,
) -> BattleStats:
    return BattleStats(
        species_dex_id=1, level=level, types=types,
        hp_max=hp, hp_current=hp,
        attack=attack, defense=defense,
        sp_attack=sp_attack, sp_defense=sp_defense,
        speed=speed,
        moves=moves, move_pps=tuple(m.pp for m in moves),
        name=name,
        status=status if status is not None else StatusState(),
    )


# --- Registration --------------------------------------------------------


def test_confusion_registered():
    handlers = VOLATILE_REGISTRY[VolatileStatus.CONFUSION]
    assert handlers.can_inflict is not None
    assert handlers.on_inflict is not None
    assert handlers.pre_action is not None


def test_confusion_ailment_slug_maps():
    status, is_volatile = ailment_to_status("confusion")
    assert status == VolatileStatus.CONFUSION
    assert is_volatile is True


# --- on_inflict ----------------------------------------------------------


def test_on_inflict_sets_turns_in_range():
    target = _mon(name="T")
    seen = set()
    for seed in range(200):
        new_target, events = confusion_mod.on_inflict(
            target,
            attacker=_mon(name="A"),
            move=CONFUSE_RAY,
            actor="player",
            target_side="opp",
            rng=random.Random(seed),
        )
        seen.add(new_target.status.confusion_turns)
        assert isinstance(events[0], StatusInflictedEvent)
        assert events[0].status == "confusion"
        assert events[0].side == "opp"
        assert "T became confused" in events[0].message
    assert seen == {2, 3, 4, 5}


def test_can_inflict_blocks_already_confused():
    state = StatusState(confusion_turns=3)
    confused = _mon(status=state)
    assert confusion_mod.can_inflict(confused) is False


def test_can_inflict_allows_healthy():
    fine = _mon()
    assert confusion_mod.can_inflict(fine) is True


def test_can_inflict_allows_burned_mon():
    """Volatile + non-volatile coexist — burned mon CAN still be confused."""
    state = StatusState(non_volatile=NonVolatileStatus.BURN)
    burned = _mon(status=state)
    assert confusion_mod.can_inflict(burned) is True


def test_on_inflict_preserves_non_volatile():
    state = StatusState(non_volatile=NonVolatileStatus.BURN)
    burned = _mon(status=state)
    new_target, _ = confusion_mod.on_inflict(
        burned,
        attacker=_mon(),
        move=CONFUSE_RAY,
        actor="player",
        target_side="opp",
        rng=random.Random(0),
    )
    assert new_target.status.non_volatile == NonVolatileStatus.BURN
    assert new_target.status.confusion_turns >= 2


# --- pre_action: 50% self-hit rate --------------------------------------


class _FixedRNG:
    """Returns a fixed value for ``random()`` and a controlled int for
    randint, so we can drive pre_action down exactly one branch."""

    def __init__(self, rand_value: float, randint_value: int = 100):
        self._rand_value = rand_value
        self._randint_value = randint_value

    def random(self) -> float:
        return self._rand_value

    def randint(self, lo: int, hi: int) -> int:
        return self._randint_value


def test_pre_action_self_hit_rate_is_about_50_percent():
    state = StatusState(confusion_turns=5)
    mon = _mon(status=state, hp=10_000, attack=10, defense=999)
    self_hits = 0
    trials = 2000
    for seed in range(trials):
        result = confusion_mod.pre_action(
            mon, "player", rng=random.Random(seed),
        )
        if not result.can_act:
            self_hits += 1
    rate = self_hits / trials
    assert 0.45 < rate < 0.55, f"self-hit rate was {rate}"


# --- pre_action: self-hit branch ----------------------------------------


def test_pre_action_self_hit_emits_event_and_damages():
    state = StatusState(confusion_turns=4)
    mon = _mon(status=state, hp=200, attack=100, defense=100, level=20)
    rng = _FixedRNG(rand_value=0.0, randint_value=100)
    result = confusion_mod.pre_action(mon, "player", rng=rng)
    assert result.can_act is False
    assert result.new_stats.hp_current < mon.hp_current
    assert result.new_stats.status.confusion_turns == 3
    self_hits = [e for e in result.events if isinstance(e, ConfusionSelfHitEvent)]
    assert len(self_hits) == 1
    assert self_hits[0].side == "player"
    assert self_hits[0].damage > 0
    assert self_hits[0].hp_before == mon.hp_current
    assert self_hits[0].hp_after == result.new_stats.hp_current


def test_pre_action_self_hit_can_ko_user():
    state = StatusState(confusion_turns=4)
    mon = _mon(status=state, hp=1, attack=999, defense=1, level=50)
    rng = _FixedRNG(rand_value=0.0, randint_value=100)
    result = confusion_mod.pre_action(mon, "player", rng=rng)
    assert result.can_act is False
    assert result.new_stats.hp_current == 0
    self_hits = [e for e in result.events if isinstance(e, ConfusionSelfHitEvent)]
    assert self_hits[0].hp_after == 0


# --- pre_action: no-self-hit branch -------------------------------------


def test_pre_action_no_self_hit_lets_mon_act():
    state = StatusState(confusion_turns=4)
    mon = _mon(name="Bulba", status=state, hp=200)
    rng = _FixedRNG(rand_value=0.99, randint_value=100)
    result = confusion_mod.pre_action(mon, "player", rng=rng)
    assert result.can_act is True
    assert result.new_stats.hp_current == mon.hp_current
    assert result.new_stats.status.confusion_turns == 3
    ticks = [e for e in result.events if isinstance(e, StatusTickEvent)]
    assert len(ticks) == 1
    assert ticks[0].damage == 0
    assert "is confused" in ticks[0].message
    assert "Bulba" in ticks[0].message


# --- pre_action: snap-out branch ----------------------------------------


def test_pre_action_snap_out_clears_status_and_acts():
    state = StatusState(confusion_turns=1)
    mon = _mon(name="Foe", status=state)
    result = confusion_mod.pre_action(
        mon, "opp", rng=random.Random(0),
    )
    assert result.can_act is True
    assert result.new_stats.status.confusion_turns == 0
    ticks = [e for e in result.events if isinstance(e, StatusTickEvent)]
    assert len(ticks) == 1
    assert "snapped out of confusion" in ticks[0].message
    assert ticks[0].damage == 0
    assert ticks[0].side == "opp"


# --- Status move with ailment=confusion always confuses ----------------


def test_confuse_ray_always_confuses_target():
    """A status move with ailment='confusion' and ailment_chance=0
    guarantees confusion (PokeAPI convention)."""
    p = _mon(name="P", speed=200, moves=(CONFUSE_RAY,))
    o = _mon(name="O", speed=10)
    for seed in range(20):
        events = plan_turn(
            p, o, player_move=CONFUSE_RAY, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        infl = [
            e for e in events
            if isinstance(e, StatusInflictedEvent) and e.status == "confusion"
        ]
        assert len(infl) == 1, f"seed {seed}: no confusion inflicted"
        assert infl[0].side == "opp"


# --- Engine integration -------------------------------------------------


def test_engine_pre_action_runs_confusion_handler():
    """A confused mon's plan_turn run shows the expected event mix.

    We force the self-hit branch by giving the confused player so much
    HP that even a self-hit can't KO them, then check across many seeds
    that we see ConfusionSelfHitEvent and 'is confused' tick events."""
    state = StatusState(confusion_turns=4)
    p = _mon(name="P", speed=200, hp=10_000, status=state)
    o = _mon(name="O", speed=10, hp=10_000)

    saw_self_hit = False
    saw_confused_tick = False
    for seed in range(100):
        events = plan_turn(
            p, o, player_move=TACKLE, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        if any(isinstance(e, ConfusionSelfHitEvent) and e.side == "player" for e in events):
            saw_self_hit = True
        if any(
            isinstance(e, StatusTickEvent)
            and e.side == "player"
            and "is confused" in e.message
            for e in events
        ):
            saw_confused_tick = True
        if saw_self_hit and saw_confused_tick:
            break
    assert saw_self_hit, "never saw a confusion self-hit event"
    assert saw_confused_tick, "never saw a 'is confused' tick event"


def test_engine_self_hit_skips_player_attack():
    """When confusion routes to a self-hit, the player's AttackEvent
    on the opponent should not be emitted that turn."""
    state = StatusState(confusion_turns=4)
    p = _mon(name="P", speed=200, hp=10_000, attack=10, defense=999, status=state)
    o = _mon(name="O", speed=10, hp=10_000, attack=10, defense=999)

    found = False
    for seed in range(200):
        rng = random.Random(seed)
        events = plan_turn(
            p, o, player_move=TACKLE, opp_move=TACKLE, rng=rng,
        )
        self_hits = [e for e in events if isinstance(e, ConfusionSelfHitEvent)]
        if not self_hits:
            continue
        player_attacks = [
            e for e in events
            if isinstance(e, AttackEvent) and e.actor == "player"
        ]
        assert len(player_attacks) == 0
        found = True
        break
    assert found, "never reached the self-hit branch across 200 seeds"


def test_engine_self_hit_ko_emits_faint():
    """If the confusion self-hit drops the user to 0, the engine
    appends a FaintEvent for that side and skips its attack."""
    state = StatusState(confusion_turns=4)
    p = _mon(name="P", speed=200, hp=1, attack=999, defense=1, level=50, status=state)
    o = _mon(name="O", speed=10, hp=200)

    found = False
    for seed in range(200):
        events = plan_turn(
            p, o, player_move=TACKLE, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        self_hits = [e for e in events if isinstance(e, ConfusionSelfHitEvent)]
        if not self_hits or self_hits[0].hp_after != 0:
            continue
        faints = [
            e for e in events
            if isinstance(e, FaintEvent) and e.side == "player"
        ]
        assert len(faints) == 1
        player_attacks = [
            e for e in events
            if isinstance(e, AttackEvent) and e.actor == "player"
        ]
        assert len(player_attacks) == 0
        found = True
        break
    assert found, "never reached a self-KO across 200 seeds"

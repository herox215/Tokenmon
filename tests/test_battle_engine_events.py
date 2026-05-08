"""Tests for ``plan_turn``: the ordered event list the animated battle
pane consumes. ``resolve_turn`` is the fold of these events, so its
existing tests cover end-state correctness — these tests pin down
*sequencing* and *event shape*.
"""
from __future__ import annotations

import random

from tokenmon.battle.engine import (
    AttackEvent,
    FaintEvent,
    MissEvent,
    plan_turn,
)
from tokenmon.battle.models import BattleStats, Move


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
LICK = Move(
    key="lick", name="Lick", type="ghost", category="physical",
    power=30, accuracy=100, pp=30,
)
SHADOW_BALL = Move(
    key="shadow-ball", name="Shadow Ball", type="ghost", category="special",
    power=80, accuracy=100, pp=15,
)
LOW_ACC = Move(
    key="low", name="LowAcc", type="normal", category="physical",
    power=40, accuracy=10, pp=10,
)


def _mon(
    *, name="Mon", speed=100, hp=100, types=("normal",),
    attack=100, defense=100, sp_attack=100, sp_defense=100,
    moves=(TACKLE,),
) -> BattleStats:
    return BattleStats(
        species_dex_id=1, level=20, types=types,
        hp_max=hp, hp_current=hp,
        attack=attack, defense=defense,
        sp_attack=sp_attack, sp_defense=sp_defense,
        speed=speed,
        moves=moves, move_pps=tuple(m.pp for m in moves),
        name=name,
    )


def _attack_actors(events) -> list[str]:
    return [e.actor for e in events if isinstance(e, AttackEvent)]


# --- Order ---------------------------------------------------------------


def test_faster_side_attacks_first():
    fast = _mon(name="Fast", speed=200)
    slow = _mon(name="Slow", speed=20)
    events = plan_turn(
        fast, slow, player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(0),
    )
    assert _attack_actors(events)[0] == "player"


def test_slower_player_attacks_second():
    slow = _mon(name="Slow", speed=20)
    fast = _mon(name="Fast", speed=200)
    events = plan_turn(
        slow, fast, player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(0),
    )
    actors = _attack_actors(events)
    assert actors[0] == "opp"
    # If neither side fainted, the slower (player) acts second.
    assert "player" in actors


# --- Faster-KO short circuit --------------------------------------------


def test_faster_ko_emits_only_one_attack_event_and_faint():
    """Speed-200 attacker one-shots speed-20 defender: only the
    faster's AttackEvent is in the list, followed by a FaintEvent.
    The slower side never produces an AttackEvent."""
    p = _mon(name="P", speed=200, attack=999)
    o = _mon(name="O", speed=20, hp=5, defense=1)
    events = plan_turn(
        p, o, player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(3),
    )
    attacks = [e for e in events if isinstance(e, AttackEvent)]
    faints = [e for e in events if isinstance(e, FaintEvent)]
    assert len(attacks) == 1
    assert attacks[0].actor == "player"
    assert len(faints) == 1
    assert faints[0].side == "opp"
    # Faint must follow the killing attack — not precede it.
    assert events.index(faints[0]) > events.index(attacks[0])


def test_slower_can_still_attack_when_faster_doesnt_ko():
    p = _mon(name="P", speed=200, attack=10)
    o = _mon(name="O", speed=20, hp=200, defense=1, attack=20)
    events = plan_turn(
        p, o, player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(4),
    )
    actors = _attack_actors(events)
    assert actors == ["player", "opp"]


# --- Miss ----------------------------------------------------------------


def test_miss_emits_miss_event_no_damage():
    """A move that fails its accuracy roll yields a MissEvent and the
    defender's HP is unchanged in any AttackEvent that follows."""
    p = _mon(name="P", speed=200)
    o = _mon(name="O", speed=20)
    saw_miss = False
    for seed in range(200):
        events = plan_turn(
            p, o, player_move=LOW_ACC, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        miss_evs = [e for e in events if isinstance(e, MissEvent)]
        if not miss_evs:
            continue
        saw_miss = True
        # The miss is the player's; the opp's normal Tackle still hits
        # → exactly one AttackEvent (from opp), zero damage from player.
        assert miss_evs[0].actor == "player"
        attacks = [e for e in events if isinstance(e, AttackEvent)]
        assert all(a.actor == "opp" for a in attacks)
        break
    assert saw_miss, "low-accuracy move never missed across 200 seeds"


# --- Type immunity -------------------------------------------------------


def test_type_immunity_emits_attack_event_with_zero_effectiveness():
    """Normal vs Ghost is a 0× immunity. The engine still emits an
    AttackEvent (so the UI can show "X used Tackle!" → "no effect"),
    but damage is 0 and HP is unchanged."""
    p = _mon(name="P", speed=200, types=("normal",))
    ghost = _mon(name="G", speed=20, types=("ghost",))
    events = plan_turn(
        p, ghost, player_move=TACKLE, opp_move=TACKLE,
        rng=random.Random(7),
    )
    p_attacks = [
        e for e in events
        if isinstance(e, AttackEvent) and e.actor == "player"
    ]
    assert len(p_attacks) == 1
    assert p_attacks[0].damage == 0
    assert p_attacks[0].effectiveness == 0.0
    assert p_attacks[0].defender_hp_before == p_attacks[0].defender_hp_after


def test_super_effective_carries_label_and_full_damage():
    """Ghost-type Lick on a Ghost defender = super effective (2×).
    Verify the AttackEvent carries the label so the UI can flash it."""
    attacker = _mon(name="A", speed=200, types=("ghost",), attack=120)
    ghost = _mon(name="G", speed=20, types=("ghost",), defense=80)
    events = plan_turn(
        attacker, ghost, player_move=LICK, opp_move=TACKLE,
        rng=random.Random(11),
    )
    a_attacks = [
        e for e in events
        if isinstance(e, AttackEvent) and e.actor == "player"
    ]
    assert len(a_attacks) == 1
    assert a_attacks[0].effectiveness == 2.0
    assert a_attacks[0].damage > 0
    assert a_attacks[0].defender_hp_after < a_attacks[0].defender_hp_before
    assert a_attacks[0].effectiveness_label  # non-empty string


# --- Speed tie -----------------------------------------------------------


def test_speed_tie_resolves_via_rng():
    """Over many seeds, both orderings appear when speeds are equal."""
    a = _mon(name="A", speed=100)
    b = _mon(name="B", speed=100)
    seen_first: set[str] = set()
    for seed in range(50):
        events = plan_turn(
            a, b, player_move=TACKLE, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        actors = _attack_actors(events)
        if actors:
            seen_first.add(actors[0])
    assert seen_first == {"player", "opp"}

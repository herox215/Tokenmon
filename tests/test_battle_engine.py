"""Engine / turn-resolution tests.

The engine layers on top of damage; we test the orchestration logic
(turn order, faint short-circuit, never-miss accuracy, log format).
"""
from __future__ import annotations

import random

import pytest

from tokenmon.battle.engine import resolve_turn, turn_order
from tokenmon.battle.models import BattleStats, Move


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
HYPERFANG = Move(
    key="hyper-fang", name="Hyper Fang", type="normal", category="physical",
    power=80, accuracy=90, pp=15,
)
NEVER_MISS = Move(
    key="swift", name="Swift", type="normal", category="special",
    power=60, accuracy=None, pp=20,
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


# --- Turn order ----------------------------------------------------------


def test_higher_speed_goes_first():
    fast = _mon(name="Fast", speed=120)
    slow = _mon(name="Slow", speed=80)
    order = turn_order(fast, slow, rng=random.Random(0))
    assert order == ("player", "opp")
    order = turn_order(slow, fast, rng=random.Random(0))
    assert order == ("opp", "player")


def test_speed_tie_random():
    """Ties resolve via rng — over many seeds we should see both orders."""
    a = _mon(speed=100)
    b = _mon(speed=100)
    seen = set()
    for s in range(50):
        seen.add(turn_order(a, b, rng=random.Random(s))[0])
    assert seen == {"player", "opp"}


# --- Damage application --------------------------------------------------


def test_basic_attack_reduces_defender_hp():
    p = _mon(name="P", speed=200)
    o = _mon(name="O", speed=10)
    res = resolve_turn(p, o, player_move=TACKLE, opp_move=TACKLE,
                       rng=random.Random(1))
    assert res.opp_state.hp_current < o.hp_current
    assert res.player_state.hp_current < p.hp_current  # opp also hit P


def test_log_contains_move_names():
    p = _mon(name="Bulba", speed=200)
    o = _mon(name="Foe Pidgey", speed=10)
    res = resolve_turn(p, o, player_move=TACKLE, opp_move=TACKLE,
                       rng=random.Random(2))
    joined = "\n".join(res.log)
    assert "Bulba used Tackle!" in joined
    assert "Foe Pidgey used Tackle!" in joined


# --- Faint short-circuit -------------------------------------------------


def test_faster_ko_skips_slower_move():
    """If the faster's hit KOs the defender, the slower's action is
    skipped — Gen-3 canon, no ghost-damage."""
    # Ridiculously over-tuned attacker so one Tackle KOs.
    p = _mon(name="P", speed=200, attack=999)
    o = _mon(name="O", speed=10, hp=5, defense=1)
    res = resolve_turn(p, o, player_move=TACKLE, opp_move=TACKLE,
                       rng=random.Random(3))
    assert res.opp_fainted is True
    assert res.player_state.hp_current == p.hp_current  # P never got hit
    log = "\n".join(res.log)
    assert "O fainted!" in log
    # The opponent's move should NOT appear in the log:
    assert "Foe used Tackle!" not in log
    assert "O used Tackle!" not in log


def test_slower_can_still_attack_if_alive_at_low_hp():
    """If faster's hit doesn't KO, slower acts — even at 1 HP."""
    p = _mon(name="P", speed=200, attack=10)
    o = _mon(name="O", speed=10, hp=200, defense=1, attack=20)
    res = resolve_turn(p, o, player_move=TACKLE, opp_move=TACKLE,
                       rng=random.Random(4))
    assert not res.opp_fainted
    log = "\n".join(res.log)
    assert "O used Tackle!" in log


# --- Accuracy ------------------------------------------------------------


def test_never_miss_move_always_hits():
    """``accuracy=None`` means the move never misses regardless of rng."""
    p = _mon(name="P", speed=200)
    o = _mon(name="O", speed=10)
    # Across many seeds, the accuracy roll never causes a miss for Swift.
    misses = 0
    for s in range(50):
        res = resolve_turn(
            p, o, player_move=NEVER_MISS, opp_move=TACKLE,
            rng=random.Random(s),
        )
        if "missed" in "\n".join(res.log).lower() and "P" in res.log[0]:
            # Only count player misses
            misses += 1
    assert misses == 0


def test_low_accuracy_can_miss():
    """A 10% accuracy move misses often; sample many seeds and confirm
    at least one miss happens."""
    cheap = Move(
        key="cheap", name="Cheap", type="normal", category="physical",
        power=40, accuracy=10, pp=10,
    )
    p = _mon(name="P", speed=200)
    o = _mon(name="O", speed=10)
    saw_miss = False
    for s in range(200):
        res = resolve_turn(p, o, player_move=cheap, opp_move=TACKLE,
                           rng=random.Random(s))
        if "missed" in "\n".join(res.log).lower():
            saw_miss = True
            break
    assert saw_miss


# --- Faint flags ---------------------------------------------------------


def test_player_faint_flag_set_when_hp_zero():
    p = _mon(name="P", speed=10, hp=1)
    o = _mon(name="O", speed=200, attack=999)
    res = resolve_turn(p, o, player_move=TACKLE, opp_move=TACKLE,
                       rng=random.Random(5))
    assert res.player_fainted is True


def test_no_faint_flag_when_alive():
    p = _mon(name="P", speed=200, attack=5)
    o = _mon(name="O", speed=10, hp=500, defense=300, attack=5)
    res = resolve_turn(p, o, player_move=TACKLE, opp_move=TACKLE,
                       rng=random.Random(6))
    assert res.player_fainted is False
    assert res.opp_fainted is False

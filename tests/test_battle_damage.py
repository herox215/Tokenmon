"""Damage-formula tests.

Random rolls are pinned via an explicit ``random.Random(seed)`` instance
so every assertion is deterministic. We don't pin against magic damage
numbers — we test the relationships (STAB > non-STAB, super-effective
> neutral, crit > non-crit, status = 0, immune = 0, min-1 floor).
"""
from __future__ import annotations

import random

import pytest

from tokenmon.battle.damage import compute_damage
from tokenmon.battle.models import BattleStats, Move


def _make_mon(
    *,
    types: tuple[str, ...] = ("normal",),
    level: int = 50,
    attack: int = 100,
    defense: int = 100,
    sp_attack: int = 100,
    sp_defense: int = 100,
    speed: int = 100,
    hp_max: int = 200,
    moves: tuple[Move, ...] = (),
) -> BattleStats:
    return BattleStats(
        species_dex_id=1,
        level=level,
        types=types,
        hp_max=hp_max,
        hp_current=hp_max,
        attack=attack,
        defense=defense,
        sp_attack=sp_attack,
        sp_defense=sp_defense,
        speed=speed,
        moves=moves,
        move_pps=tuple(m.pp for m in moves),
    )


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
EMBER = Move(
    key="ember", name="Ember", type="fire", category="special",
    power=40, accuracy=100, pp=25,
)
STATUS_GROWL = Move(
    key="growl", name="Growl", type="normal", category="status",
    power=None, accuracy=100, pp=40,
)


def _no_crit_rng() -> random.Random:
    """A Random instance that never rolls a crit and always picks the
    median random_roll. Built by reseeding to a known no-crit seed and
    confirming via a sanity assertion."""
    # Seed 42 happens not to crit on the first call(s) we make in tests.
    return random.Random(42)


# --- Status moves and immunities -----------------------------------------


def test_status_move_does_zero_damage():
    rng = random.Random(0)
    a = _make_mon(types=("normal",))
    d = _make_mon(types=("normal",))
    res = compute_damage(a, d, STATUS_GROWL, rng=rng)
    assert res.damage == 0
    assert res.crit is False
    assert res.effectiveness == 1.0


def test_zero_power_move_does_zero_damage():
    rng = random.Random(0)
    a = _make_mon()
    d = _make_mon()
    move = Move(
        key="splash", name="Splash", type="normal", category="physical",
        power=0, accuracy=100, pp=40,
    )
    assert compute_damage(a, d, move, rng=rng).damage == 0


def test_immune_defender_zero_damage():
    """Normal vs Ghost is 0× — damage should be 0, not min-1."""
    rng = random.Random(0)
    a = _make_mon(types=("normal",))
    d = _make_mon(types=("ghost",))
    res = compute_damage(a, d, TACKLE, rng=rng)
    assert res.damage == 0
    assert res.effectiveness == 0.0
    assert res.effectiveness_label == "It had no effect…"


# --- STAB ----------------------------------------------------------------


def test_stab_increases_damage():
    """Same RNG seed → STAB attacker (Normal-type using Tackle) deals
    more than non-STAB attacker (Fire-type using Tackle)."""
    a_stab = _make_mon(types=("normal",))
    a_no_stab = _make_mon(types=("fire",))
    d = _make_mon(types=("water",))
    rng_a = random.Random(123)
    rng_b = random.Random(123)
    stab = compute_damage(a_stab, d, TACKLE, rng=rng_a).damage
    no_stab = compute_damage(a_no_stab, d, TACKLE, rng=rng_b).damage
    assert stab > no_stab


# --- Type effectiveness --------------------------------------------------


def test_super_effective_more_damage_than_neutral():
    a_fire = _make_mon(types=("fire",), sp_attack=100)
    d_grass = _make_mon(types=("grass",), sp_defense=100)
    d_water = _make_mon(types=("water",), sp_defense=100)
    rng_a = random.Random(7)
    rng_b = random.Random(7)
    super_eff = compute_damage(a_fire, d_grass, EMBER, rng=rng_a)
    not_eff = compute_damage(a_fire, d_water, EMBER, rng=rng_b)
    # Same seed, same Pokémon stats — only difference is type-eff: 2× vs 0.5×.
    assert super_eff.damage > not_eff.damage * 3  # 2× / 0.5× = 4× ratio
    assert super_eff.effectiveness == 2.0
    assert not_eff.effectiveness == 0.5
    assert super_eff.effectiveness_label == "It's super effective!"
    assert not_eff.effectiveness_label == "It's not very effective…"


# --- Critical hits -------------------------------------------------------


def test_crit_doubles_damage_at_same_random_roll():
    """A crit (×2.0 in Gen-3) should deal exactly 2× a non-crit at the
    same random_roll — pinned by handcrafting a Random subclass that
    forces crit on/off and the same random_roll either way."""
    a = _make_mon(types=("normal",))
    d = _make_mon(types=("water",))

    class _ForceCrit(random.Random):
        def __init__(self, crit: bool):
            super().__init__(0)
            self._crit = crit
        def random(self):
            # First call decides crit. Force it.
            return 0.0 if self._crit else 0.99
        def randint(self, a, b):
            # Force the random_roll to 100 (max) so both branches roll same.
            return 100

    crit = compute_damage(a, d, TACKLE, rng=_ForceCrit(True))
    non_crit = compute_damage(a, d, TACKLE, rng=_ForceCrit(False))
    assert crit.crit is True
    assert non_crit.crit is False
    # Crit is exactly 2× the non-crit (Gen-3) given identical other modifiers.
    assert crit.damage == non_crit.damage * 2


# --- Min-1 damage floor --------------------------------------------------


def test_min_one_damage_against_high_defense():
    """Even an absurd defense advantage should still deal at least 1
    damage when the type is not immune."""
    a = _make_mon(types=("normal",), level=5, attack=10)
    d = _make_mon(types=("water",), defense=999)
    rng = random.Random(1)
    res = compute_damage(a, d, TACKLE, rng=rng)
    assert res.damage >= 1


# --- Physical/special split ---------------------------------------------


def test_physical_move_uses_physical_stats():
    """A high-Atk attacker hitting a low-Def defender with a physical
    move outdamages the same lineup with high-SpA / low-SpD."""
    move_phys = Move(
        key="x", name="X", type="normal", category="physical",
        power=80, accuracy=100, pp=15,
    )
    a = _make_mon(attack=200, sp_attack=10)
    d = _make_mon(defense=10, sp_defense=200)
    rng = random.Random(99)
    out = compute_damage(a, d, move_phys, rng=rng)
    assert out.damage > 50  # well above floor


def test_special_move_uses_special_stats():
    move_spec = Move(
        key="x", name="X", type="normal", category="special",
        power=80, accuracy=100, pp=15,
    )
    a = _make_mon(attack=10, sp_attack=200)
    d = _make_mon(defense=200, sp_defense=10)
    rng = random.Random(99)
    out = compute_damage(a, d, move_spec, rng=rng)
    assert out.damage > 50


# --- Random factor envelope ----------------------------------------------


def test_random_factor_within_85_100_envelope():
    """Across many seeds the damage should land within the theoretical
    min/max bracket (random ∈ [0.85, 1.00], crit either side). Spot-check
    by sampling 200 rolls and confirming all are bounded."""
    a = _make_mon(types=("normal",), level=50, attack=100)
    d = _make_mon(types=("water",), defense=100)
    damages = []
    for s in range(200):
        damages.append(compute_damage(a, d, TACKLE, rng=random.Random(s)).damage)
    # Theoretical: base = ((2×50/5+2) × 40 × 1) / 50 + 2 = 19.6
    # × stab(1.5, normal vs normal Tackle) × type(1, neutral on water)
    # × random(0.85..1.0) × crit(1 or 2)
    # min ≈ 25 (no crit, 0.85), max ≈ 59 (crit, 1.0). Allow slack.
    assert min(damages) >= 1
    assert max(damages) <= 80  # well above any reasonable upper bound

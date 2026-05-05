"""Per-instance stat math: IV rolls, final-stat formula, characteristic
derivation. Pure functions over the inert tables in ``data.py``.
"""
from __future__ import annotations

import hashlib
import random
from typing import Sequence

from .data import CHARACTERISTICS, GEN1_BASE_STATS, NATURES

__all__ = [
    "STAT_NAMES",
    "STAT_ORDER",
    "STAT_LABELS",
    "IV_MAX",
    "BASE_STAT_MAX",
    "RADAR_SCALE_MAX",
    "base_stats_of",
    "roll_ivs",
    "ivs_from_id",
    "final_stat",
    "final_stats",
    "characteristic_for_ivs",
    "nature_multipliers",
]

# Canonical order: HP, Attack, Defense, Sp.Atk, Sp.Def, Speed.
STAT_ORDER: tuple[str, ...] = (
    "hp", "attack", "defense", "sp_attack", "sp_defense", "speed",
)
STAT_NAMES = STAT_ORDER  # alias for callers preferring "names"
STAT_LABELS: dict[str, str] = {
    "hp": "HP",
    "attack": "ATK",
    "defense": "DEF",
    "sp_attack": "SP.A",
    "sp_defense": "SP.D",
    "speed": "SPD",
}

IV_MAX = 31
# Highest single base stat in Gen-1 is Chansey's HP (250). Radar uses a
# slightly larger fixed ceiling so even Chansey doesn't completely fill
# the chart, and lesser HP outliers (Snorlax 160) leave room to read.
BASE_STAT_MAX = 255
RADAR_SCALE_MAX = 200


def base_stats_of(dex_id: int) -> tuple[int, int, int, int, int, int]:
    """Return base (HP, ATK, DEF, Sp.Atk, Sp.Def, Speed) for ``dex_id``.
    Falls back to a flat 50/50/50/50/50/50 for unknown ids."""
    return GEN1_BASE_STATS.get(int(dex_id), (50, 50, 50, 50, 50, 50))


def roll_ivs(rng: random.Random | None = None) -> tuple[int, int, int, int, int, int]:
    """Six independent IVs in [0, 31]. ``rng`` defaults to SystemRandom."""
    r = rng if rng is not None else random.SystemRandom()
    return tuple(r.randint(0, IV_MAX) for _ in range(6))  # type: ignore[return-value]


def ivs_from_id(pokemon_id: int) -> tuple[int, int, int, int, int, int]:
    """Deterministic IV roll seeded by a pokemon row id — used to backfill
    legacy rows that pre-date the IV columns. Stable across migrations so
    a given old Pokemon always picks up the same values."""
    h = hashlib.sha256(f"ivs:{int(pokemon_id)}".encode()).digest()
    return tuple(b % (IV_MAX + 1) for b in h[:6])  # type: ignore[return-value]


def nature_multipliers(nature_name: str) -> dict[str, float]:
    """{stat_name: 1.1 / 0.9 / 1.0} for each of the five non-HP stats.
    HP is never modified by nature in the main-series formula."""
    out = {s: 1.0 for s in STAT_ORDER}
    for entry in NATURES:
        if entry["name"] != nature_name:
            continue
        plus, minus = entry["plus_stat"], entry["minus_stat"]
        if plus and minus and plus != minus:
            out[plus] = 1.1
            out[minus] = 0.9
        break
    return out


def final_stat(
    base: int, iv: int, level: int, *, is_hp: bool, nature_mult: float = 1.0,
) -> int:
    """Standard Gen-3+ formula with EVs fixed at 0 — we don't track EVs.

    HP:    floor((2*base + iv) * level / 100) + level + 10
    other: floor(((2*base + iv) * level / 100) + 5) * nature_mult, floored
    """
    level = max(1, int(level))
    base = max(0, int(base))
    iv = max(0, min(IV_MAX, int(iv)))
    if is_hp:
        return (2 * base + iv) * level // 100 + level + 10
    raw = (2 * base + iv) * level // 100 + 5
    return int(raw * nature_mult)


def final_stats(
    dex_id: int,
    ivs: Sequence[int],
    level: int,
    nature_name: str,
) -> tuple[int, int, int, int, int, int]:
    """All six final stats for a specific instance."""
    base = base_stats_of(dex_id)
    mults = nature_multipliers(nature_name)
    out: list[int] = []
    for i, stat in enumerate(STAT_ORDER):
        out.append(final_stat(
            base[i], ivs[i], level,
            is_hp=(stat == "hp"),
            nature_mult=mults[stat],
        ))
    return tuple(out)  # type: ignore[return-value]


# In canonical Pokemon (Gen-3+) the characteristic line is determined by
# the highest IV (with ties broken by a fixed stat order starting from
# the IV index that matches `personality_value % 6`). We don't track a
# personality value, so we tiebreak by the canonical stat order.
# The 30-line CHARACTERISTICS list is grouped 5 lines per stat in the
# order HP, ATK, DEF, SPD, SP.A, SP.D — matching the canonical mapping.
_CHARACTERISTIC_STAT_ORDER: tuple[str, ...] = (
    "hp", "attack", "defense", "speed", "sp_attack", "sp_defense",
)


def characteristic_for_ivs(ivs: Sequence[int]) -> str:
    """Pick the canon characteristic line whose stat is the Pokemon's
    highest IV. Ties broken by the canonical group order (HP > ATK > DEF
    > SPD > Sp.Atk > Sp.Def). The line within the chosen group is keyed
    on iv % 5 so the wording also encodes the IV value mod 5, like canon."""
    if len(ivs) != 6:
        raise ValueError(f"expected 6 ivs, got {len(ivs)}")
    by_stat = dict(zip(STAT_ORDER, (int(v) for v in ivs)))
    top_iv = max(by_stat.values())
    chosen_stat = next(
        s for s in _CHARACTERISTIC_STAT_ORDER if by_stat[s] == top_iv
    )
    group_index = _CHARACTERISTIC_STAT_ORDER.index(chosen_stat)
    line_index = group_index * 5 + (top_iv % 5)
    return CHARACTERISTICS[line_index]

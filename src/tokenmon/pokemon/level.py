"""XP / level math + evolution-line resolution.

All pure functions over the inert tables in ``data.py``.
"""
from __future__ import annotations

from .data import (
    ALL_NAMES,
    EVOLUTIONS,
    GEN1_CATCH_RATES,
    GEN1_TYPES,
    GROWTH_RATES,
    _LINE_OF,
)

MAX_LEVEL = 100


def xp_for_level(level: int, rate: str) -> int:
    """Total XP needed to BE at ``level`` (XP at the start of the level).
    Level 1 = 0 XP. Formulas per Bulbapedia / Pokémon main-series games."""
    if level <= 1:
        return 0
    n = level
    if rate == "fast":
        return (4 * n ** 3) // 5
    if rate == "medium_fast":
        return n ** 3
    if rate == "medium_slow":
        return max(0, (6 * n ** 3) // 5 - 15 * n ** 2 + 100 * n - 140)
    if rate == "slow":
        return (5 * n ** 3) // 4
    if rate == "erratic":
        if n <= 50:
            return (n ** 3 * (100 - n)) // 50
        if n <= 68:
            return (n ** 3 * (150 - n)) // 100
        if n <= 98:
            return (n ** 3 * ((1911 - 10 * n) // 3)) // 500
        return (n ** 3 * (160 - n)) // 100
    if rate == "fluctuating":
        if n <= 15:
            return (n ** 3 * (((n + 1) // 3) + 24)) // 50
        if n <= 36:
            return (n ** 3 * (n + 14)) // 50
        return (n ** 3 * ((n // 2) + 32)) // 50
    raise ValueError(f"unknown growth rate: {rate}")


def level_from_xp(xp: int, rate: str) -> tuple[int, int, int]:
    """Returns (level, xp_into_level, xp_to_next_level).
    At max level, xp_to_next_level == 0 and xp_into_level == 0."""
    if xp <= 0:
        return 1, 0, xp_for_level(2, rate)
    for lvl in range(1, MAX_LEVEL):
        next_xp = xp_for_level(lvl + 1, rate)
        if xp < next_xp:
            cur_xp = xp_for_level(lvl, rate)
            return lvl, xp - cur_xp, next_xp - cur_xp
    return MAX_LEVEL, 0, 0


def name_of(dex_id: int) -> str:
    return ALL_NAMES.get(dex_id, f"#{dex_id}")


def line_of(dex_id: int) -> int:
    """Return the base-form dex_id for the evolution line containing dex_id."""
    return _LINE_OF.get(dex_id, dex_id)


def growth_rate_of(dex_id: int) -> str:
    """Growth rate for any dex_id in a line — resolves via base form."""
    base = line_of(dex_id)
    return GROWTH_RATES.get(base, "medium_fast")


def catch_rate_of(dex_id: int) -> int:
    """Canonical Gen-1 capture rate (0-255), default 100 for unknown ids."""
    return GEN1_CATCH_RATES.get(int(dex_id), 100)


def types_of(dex_id: int) -> tuple[str, ...]:
    """Tuple of 1 or 2 lowercase type names for ``dex_id``. Resolves via
    line_of so evolved forms inherit their pre-evolution's typing entry
    when not listed directly. Returns ('normal',) as a safe default for
    unknown dex_ids."""
    direct = GEN1_TYPES.get(int(dex_id))
    if direct:
        return direct
    return GEN1_TYPES.get(line_of(int(dex_id)), ("normal",))


def evolution_chain(base_dex_id: int) -> list[int]:
    """[base, stage2, ...] in order. Singleton list when no evolution exists."""
    chain = [base_dex_id]
    for _, evo in EVOLUTIONS.get(base_dex_id, []):
        chain.append(evo)
    return chain


def stage_thresholds(base_dex_id: int) -> list[int]:
    """Level thresholds for each non-base stage in the line."""
    return [lvl for lvl, _ in EVOLUTIONS.get(base_dex_id, [])]


def current_stage_of(base_dex_id: int, xp: int) -> int:
    """Which dex_id should be displayed for this line at the given XP."""
    rate = GROWTH_RATES.get(base_dex_id, "medium_fast")
    level, _, _ = level_from_xp(xp, rate)
    chain = evolution_chain(base_dex_id)
    thresholds = stage_thresholds(base_dex_id)
    current = chain[0]
    for threshold, evolved in zip(thresholds, chain[1:]):
        if level >= threshold:
            current = evolved
        else:
            break
    return current


def unlocked_stages_of(base_dex_id: int, xp: int) -> list[int]:
    """All evolution stages reached so far (always includes the base)."""
    rate = GROWTH_RATES.get(base_dex_id, "medium_fast")
    level, _, _ = level_from_xp(xp, rate)
    chain = evolution_chain(base_dex_id)
    thresholds = stage_thresholds(base_dex_id)
    out = [chain[0]]
    for threshold, evolved in zip(thresholds, chain[1:]):
        if level >= threshold:
            out.append(evolved)
        else:
            break
    return out

"""Gen-3 damage formula.

Pure function — given attacker, defender, move, and an explicit
``random.Random`` for testability, returns the damage rolled this hit.
The formula is:

    base = ((2 * level / 5 + 2) * power * (atk / def) / 50) + 2
    modifier = stab × type_eff × crit × random_roll
    damage = max(1, floor(base × modifier))

Modifiers:
- ``stab``       1.5 if the move's type matches one of the attacker's types.
- ``type_eff``   ``types.effectiveness(move.type, defender.types)``.
- ``crit``       2.0 with 1/16 probability, 1.0 otherwise. Pinned high
                 because Gen-3 uses 2× crits (Gen-6+ moved to 1.5).
- ``random_roll`` Uniform integer 85..100, divided by 100 → 0.85..1.00.

Status moves (``power is None`` or category=='status') deal 0 damage and
return early — used for accuracy-only effects in future expansions.

Move accuracy is checked separately by the engine (so it can decide
whether to skip damage entirely / log a miss).
"""
from __future__ import annotations

import math
import random
from typing import Final

from .models import BattleStats, DamageResult, Move
from .status import attack_after_status
from .types import effectiveness, label_for

CRIT_NUMERATOR: Final = 1
CRIT_DENOMINATOR: Final = 16  # Gen-3 base crit ratio
CRIT_MULTIPLIER: Final = 2.0
STAB_MULTIPLIER: Final = 1.5


def compute_damage(
    attacker: BattleStats,
    defender: BattleStats,
    move: Move,
    *,
    rng: random.Random,
) -> DamageResult:
    """Return the damage one hit deals. Pure — no I/O. Caller decides
    whether to apply the damage (e.g. after an accuracy check)."""
    if move.category == "status" or move.power is None or move.power <= 0:
        return DamageResult(
            damage=0, crit=False, effectiveness=1.0,
            effectiveness_label="",
        )

    type_mult = effectiveness(move.type, defender.types)
    if type_mult == 0.0:
        return DamageResult(
            damage=0, crit=False, effectiveness=0.0,
            effectiveness_label=label_for(0.0),
        )

    if move.category == "physical":
        atk = attacker.attack
        defn = defender.defense
    else:  # special
        atk = attacker.sp_attack
        defn = defender.sp_defense

    # Status hook: burn halves physical attack output (Gen-3 rule).
    # Future statuses (e.g. swagger-style boosts) plug in here too.
    atk = attack_after_status(attacker, move, atk)

    # Avoid div-by-zero on bizarre data; floor at 1.
    if defn <= 0:
        defn = 1

    base = ((2 * attacker.level / 5 + 2) * move.power * (atk / defn) / 50) + 2

    crit = rng.random() < (CRIT_NUMERATOR / CRIT_DENOMINATOR)
    crit_mult = CRIT_MULTIPLIER if crit else 1.0
    stab_mult = STAB_MULTIPLIER if move.type in attacker.types else 1.0
    random_roll = rng.randint(85, 100) / 100.0
    modifier = stab_mult * type_mult * crit_mult * random_roll

    raw = base * modifier
    damage = max(1, math.floor(raw))

    return DamageResult(
        damage=damage,
        crit=crit,
        effectiveness=type_mult,
        effectiveness_label=label_for(type_mult),
    )

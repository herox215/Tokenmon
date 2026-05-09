"""Paralysis non-volatile status handler.

Mechanics modeled after Gen-3 with one Gen-6 borrowing for type immunity:

  * Speed is reduced to 1/4 of base while paralyzed (Gen-3 canon — Gen-7
    relaxed it to 1/2; we keep the punishing Gen-3 value).
  * Each turn the actor has a 25% chance to be fully unable to move.
  * Electric-type Pokémon are immune to paralysis. This is technically a
    Gen-6 rule (the Gen-3 cartridges allowed Electric mons to be
    paralyzed by Body Slam etc.) but it makes the type identity feel
    consistent and avoids the "Pikachu paralyzed itself with Static
    immunity" footgun. The choice is documented here intentionally.
  * Ground-type Pokémon are immune to Electric-typed *status* moves
    (Thunder Wave) — the engine already zeroes damage on Electric→Ground
    damaging hits, but Thunder Wave has no damage roll, so the move-level
    immunity has to live in ``on_inflict``.
  * A mon already carrying a non-volatile status (anything except
    HEALTHY) can't be paralyzed on top.
"""
from __future__ import annotations

import random
from dataclasses import replace

from tokenmon.battle.engine import (
    StatusInflictedEvent,
    StatusPreventedEvent,
)
from tokenmon.battle.models import BattleStats, Move
from tokenmon.battle.status import (
    NonVolatileStatus,
    PreActionResult,
    StatusHandlers,
    StatusState,
    register_non_volatile,
)


def _can_inflict(target: BattleStats) -> bool:
    if target.status.non_volatile != NonVolatileStatus.HEALTHY:
        return False
    if "electric" in target.types:
        return False
    return True


def _on_inflict(
    target: BattleStats,
    *,
    attacker: BattleStats,
    move: Move,
    actor: str,
    target_side: str,
    rng: random.Random,
) -> tuple[BattleStats, list]:
    if move.type == "electric" and move.category == "status" and "ground" in target.types:
        return target, []

    new_status = replace(target.status, non_volatile=NonVolatileStatus.PARALYSIS)
    new_target = replace(target, status=new_status)
    name = target.name or ("Foe" if target_side == "opp" else "Your Pokémon")
    event = StatusInflictedEvent(
        side=target_side,
        status=NonVolatileStatus.PARALYSIS.value,
        message=f"{name} was paralyzed!",
    )
    return new_target, [event]


def _pre_action(
    stats: BattleStats,
    side: str,
    *,
    rng: random.Random,
) -> PreActionResult:
    if rng.randint(1, 4) == 1:
        name = stats.name or ("Foe" if side == "opp" else "Your Pokémon")
        event = StatusPreventedEvent(
            side=side,
            status=NonVolatileStatus.PARALYSIS.value,
            message=f"{name} is paralyzed and can't move!",
        )
        return PreActionResult(can_act=False, new_stats=stats, events=[event])
    return PreActionResult(can_act=True, new_stats=stats, events=[])


def _modify_speed(stats: BattleStats, speed: int) -> int:
    return speed // 4


register_non_volatile(
    NonVolatileStatus.PARALYSIS,
    StatusHandlers(
        can_inflict=_can_inflict,
        on_inflict=_on_inflict,
        pre_action=_pre_action,
        modify_speed=_modify_speed,
    ),
    pokeapi_ailments=("paralysis",),
)

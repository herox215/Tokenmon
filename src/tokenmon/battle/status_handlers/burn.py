"""Burn handler.

Gen-3 mechanics:
- Fire-types are immune.
- A mon already carrying a non-volatile status (poison, sleep, etc.) cannot
  be re-burned — the "stack rule" for non-volatile ailments.
- End of turn: the burned mon takes max(1, hp_max // 8) damage.
- Physical attacks deal half damage (atk // 2 in the damage formula); special
  and status moves are unaffected.
"""
from __future__ import annotations

from dataclasses import replace

from ..models import BattleStats, Move
from ..status import (
    NonVolatileStatus,
    StatusHandlers,
    register_non_volatile,
)


def _can_inflict(target: BattleStats) -> bool:
    if "fire" in target.types:
        return False
    return target.status.non_volatile == NonVolatileStatus.HEALTHY


def _on_inflict(
    target: BattleStats,
    *,
    attacker: BattleStats,
    move: Move,
    actor: str,
    target_side: str,
    rng,
) -> tuple[BattleStats, list]:
    # Local import: engine imports this module transitively through the
    # status registry, so the event types must be resolved lazily to avoid
    # circular import at package-load time.
    from ..engine import StatusInflictedEvent

    new_status = replace(
        target.status,
        non_volatile=NonVolatileStatus.BURN,
        nv_counter=0,
    )
    new_stats = replace(target, status=new_status)
    name = target.name or "Foe"
    events = [
        StatusInflictedEvent(
            side=target_side,
            status=NonVolatileStatus.BURN.value,
            message=f"{name} was burned!",
        )
    ]
    return new_stats, events


def _end_of_turn(
    stats: BattleStats,
    side: str,
    *,
    rng,
) -> tuple[BattleStats, list]:
    from ..engine import StatusTickEvent

    damage = max(1, stats.hp_max // 8)
    hp_before = stats.hp_current
    hp_after = max(0, hp_before - damage)
    new_stats = replace(stats, hp_current=hp_after)
    name = stats.name or "Foe"
    events = [
        StatusTickEvent(
            side=side,
            status=NonVolatileStatus.BURN.value,
            damage=hp_before - hp_after,
            hp_before=hp_before,
            hp_after=hp_after,
            message=f"{name} is hurt by its burn!",
        )
    ]
    return new_stats, events


def _modify_attack(stats: BattleStats, move: Move, atk: int) -> int:
    if move.category == "physical":
        return atk // 2
    return atk


register_non_volatile(
    NonVolatileStatus.BURN,
    StatusHandlers(
        can_inflict=_can_inflict,
        on_inflict=_on_inflict,
        end_of_turn=_end_of_turn,
        modify_attack=_modify_attack,
    ),
    pokeapi_ailments=("burn",),
)

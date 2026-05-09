"""Sleep status handler.

Mechanics (Gen-3 canon):
  * Inflict rolls a 2..5-turn duration; counter ticks down at the start of
    each pre-action.
  * While ``nv_counter > 1``: the sleeper skips its turn (StatusPreventedEvent).
  * When ``nv_counter <= 1``: the sleeper wakes up *and acts the same turn*.
  * No type immunity — every Pokémon can sleep. Only blocker is an existing
    non-volatile status.
"""
from __future__ import annotations

from dataclasses import replace

from tokenmon.battle.engine import (
    StatusInflictedEvent,
    StatusPreventedEvent,
    StatusTickEvent,
)
from tokenmon.battle.models import BattleStats
from tokenmon.battle.status import (
    NonVolatileStatus,
    PreActionResult,
    StatusHandlers,
    StatusState,
    register_non_volatile,
)


def can_inflict(target: BattleStats) -> bool:
    return target.status.non_volatile == NonVolatileStatus.HEALTHY


def on_inflict(
    target: BattleStats,
    *,
    attacker,
    move,
    actor,
    target_side,
    rng,
) -> tuple[BattleStats, list]:
    duration = rng.randint(2, 5)
    new_status = replace(
        target.status,
        non_volatile=NonVolatileStatus.SLEEP,
        nv_counter=duration,
    )
    new_stats = replace(target, status=new_status)
    label = target.name or "Pokémon"
    event = StatusInflictedEvent(
        side=target_side,
        status="sleep",
        message=f"{label} fell asleep!",
    )
    return new_stats, [event]


def pre_action(stats: BattleStats, side: str, *, rng) -> PreActionResult:
    label = stats.name or "Pokémon"
    counter = stats.status.nv_counter
    if counter <= 1:
        new_status = replace(
            stats.status,
            non_volatile=NonVolatileStatus.HEALTHY,
            nv_counter=0,
        )
        new_stats = replace(stats, status=new_status)
        wake_event = StatusTickEvent(
            side=side,
            status="sleep",
            damage=0,
            hp_before=stats.hp_current,
            hp_after=stats.hp_current,
            message=f"{label} woke up!",
        )
        return PreActionResult(can_act=True, new_stats=new_stats, events=[wake_event])

    new_status = replace(stats.status, nv_counter=counter - 1)
    new_stats = replace(stats, status=new_status)
    skip_event = StatusPreventedEvent(
        side=side,
        status="sleep",
        message=f"{label} is fast asleep!",
    )
    return PreActionResult(can_act=False, new_stats=new_stats, events=[skip_event])


register_non_volatile(
    NonVolatileStatus.SLEEP,
    StatusHandlers(
        can_inflict=can_inflict,
        on_inflict=on_inflict,
        pre_action=pre_action,
    ),
    pokeapi_ailments=("sleep",),
)

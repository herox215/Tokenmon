"""Confusion volatile status (Gen-3 canon).

On inflict, the target rolls a 2-5 turn duration. Every turn while the
counter is positive, the pre-action handler decrements it. If it
reaches 0 the mon snaps out and attacks normally. Otherwise a 50% roll
either lets the move proceed or hits the attacker with a 40-power
typeless physical hit using the attacker's own attack and defense.
"""
from __future__ import annotations

import math
from dataclasses import replace

from ..models import BattleStats
from ..status import (
    PreActionResult,
    StatusHandlers,
    VolatileStatus,
    register_volatile,
)


def can_inflict(target: BattleStats) -> bool:
    return target.status.confusion_turns == 0


def on_inflict(target, *, attacker, move, actor, target_side, rng):
    duration = rng.randint(2, 5)
    new_status = replace(target.status, confusion_turns=duration)
    new_target = replace(target, status=new_status)
    from ..engine import StatusInflictedEvent
    label = target.name or "It"
    events = [StatusInflictedEvent(
        side=target_side,
        status="confusion",
        message=f"{label} became confused!",
    )]
    return new_target, events


def _self_hit_damage(stats: BattleStats, *, rng) -> int:
    atk = stats.attack
    defn = stats.defense if stats.defense > 0 else 1
    base = ((2 * stats.level / 5 + 2) * 40 * (atk / defn) / 50) + 2
    random_roll = rng.randint(85, 100) / 100.0
    return max(1, math.floor(base * random_roll))


def pre_action(stats, side, *, rng):
    from ..engine import ConfusionSelfHitEvent, StatusTickEvent

    label = stats.name or ("Your Pokémon" if side == "player" else "Foe")
    remaining = stats.status.confusion_turns - 1

    if remaining <= 0:
        new_status = replace(stats.status, confusion_turns=0)
        new_stats = replace(stats, status=new_status)
        events = [StatusTickEvent(
            side=side,
            status="confusion",
            damage=0,
            hp_before=stats.hp_current,
            hp_after=stats.hp_current,
            message=f"{label} snapped out of confusion!",
        )]
        return PreActionResult(can_act=True, new_stats=new_stats, events=events)

    new_status = replace(stats.status, confusion_turns=remaining)
    stats_with_counter = replace(stats, status=new_status)

    if rng.random() < 0.5:
        damage = _self_hit_damage(stats_with_counter, rng=rng)
        new_hp = max(0, stats_with_counter.hp_current - damage)
        hurt_stats = replace(stats_with_counter, hp_current=new_hp)
        events = [ConfusionSelfHitEvent(
            side=side,
            damage=damage,
            hp_before=stats_with_counter.hp_current,
            hp_after=new_hp,
        )]
        return PreActionResult(can_act=False, new_stats=hurt_stats, events=events)

    events = [StatusTickEvent(
        side=side,
        status="confusion",
        damage=0,
        hp_before=stats_with_counter.hp_current,
        hp_after=stats_with_counter.hp_current,
        message=f"{label} is confused!",
    )]
    return PreActionResult(can_act=True, new_stats=stats_with_counter, events=events)


register_volatile(
    VolatileStatus.CONFUSION,
    StatusHandlers(
        can_inflict=can_inflict,
        on_inflict=on_inflict,
        pre_action=pre_action,
    ),
    pokeapi_ailments=("confusion",),
)

"""Poison + Bad Poison (Toxic) handlers.

Both share the same can_inflict rules (Poison/Steel immunity, no overwrite
of an existing non-volatile status). They diverge only on inflict (Toxic
seeds ``nv_counter=1``) and on end_of_turn (Toxic ramps damage by counter
each turn while regular Poison is a flat 1/8 max HP).

Move → status routing: PokeAPI tags Toxic with the same ``"poison"``
ailment slug as Poison Powder, so the slug-to-status table maps "poison"
to ``NonVolatileStatus.POISON`` and the on_inflict here detects Toxic via
``move.key == "toxic"`` and re-dispatches to BAD_POISON.
"""
from __future__ import annotations

from dataclasses import replace

from tokenmon.battle.engine import StatusInflictedEvent, StatusTickEvent
from tokenmon.battle.status import (
    NON_VOLATILE_REGISTRY,
    NonVolatileStatus,
    StatusHandlers,
    register_non_volatile,
)


_POISON_IMMUNE_TYPES = {"poison", "steel"}


def _can_inflict(target):
    if any(t.lower() in _POISON_IMMUNE_TYPES for t in target.types):
        return False
    return target.status.non_volatile == NonVolatileStatus.HEALTHY


def _on_inflict_poison(target, *, attacker, move, actor, target_side, rng):
    if move is not None and getattr(move, "key", None) == "toxic":
        bad_handlers = NON_VOLATILE_REGISTRY.get(NonVolatileStatus.BAD_POISON)
        if bad_handlers is not None and bad_handlers.on_inflict is not None:
            return bad_handlers.on_inflict(
                target,
                attacker=attacker,
                move=move,
                actor=actor,
                target_side=target_side,
                rng=rng,
            )

    new_status = replace(
        target.status,
        non_volatile=NonVolatileStatus.POISON,
        nv_counter=0,
    )
    new_target = replace(target, status=new_status)
    name = target.name or "Pokémon"
    event = StatusInflictedEvent(
        side=target_side,
        status=NonVolatileStatus.POISON.value,
        message=f"{name} was poisoned!",
    )
    return new_target, [event]


def _on_inflict_bad_poison(target, *, attacker, move, actor, target_side, rng):
    new_status = replace(
        target.status,
        non_volatile=NonVolatileStatus.BAD_POISON,
        nv_counter=1,
    )
    new_target = replace(target, status=new_status)
    name = target.name or "Pokémon"
    event = StatusInflictedEvent(
        side=target_side,
        status=NonVolatileStatus.BAD_POISON.value,
        message=f"{name} was badly poisoned!",
    )
    return new_target, [event]


def _end_of_turn_poison(stats, side, *, rng):
    damage = max(1, stats.hp_max // 8)
    hp_before = stats.hp_current
    hp_after = max(0, hp_before - damage)
    new_stats = replace(stats, hp_current=hp_after)
    name = stats.name or "Pokémon"
    event = StatusTickEvent(
        side=side,
        status=NonVolatileStatus.POISON.value,
        damage=hp_before - hp_after,
        hp_before=hp_before,
        hp_after=hp_after,
        message=f"{name} is hurt by poison!",
    )
    return new_stats, [event]


def _end_of_turn_bad_poison(stats, side, *, rng):
    counter = max(1, stats.status.nv_counter)
    damage = max(1, (counter * stats.hp_max) // 16)
    hp_before = stats.hp_current
    hp_after = max(0, hp_before - damage)
    new_status = replace(stats.status, nv_counter=counter + 1)
    new_stats = replace(stats, hp_current=hp_after, status=new_status)
    name = stats.name or "Pokémon"
    event = StatusTickEvent(
        side=side,
        status=NonVolatileStatus.BAD_POISON.value,
        damage=hp_before - hp_after,
        hp_before=hp_before,
        hp_after=hp_after,
        message=f"{name} is hurt by poison!",
    )
    return new_stats, [event]


register_non_volatile(
    NonVolatileStatus.POISON,
    StatusHandlers(
        can_inflict=_can_inflict,
        on_inflict=_on_inflict_poison,
        end_of_turn=_end_of_turn_poison,
    ),
    pokeapi_ailments=("poison",),
)

register_non_volatile(
    NonVolatileStatus.BAD_POISON,
    StatusHandlers(
        can_inflict=_can_inflict,
        on_inflict=_on_inflict_bad_poison,
        end_of_turn=_end_of_turn_bad_poison,
    ),
)

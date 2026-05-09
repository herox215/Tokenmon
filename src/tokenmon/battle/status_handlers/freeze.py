"""Freeze non-volatile status (Gen-3 canon).

Mechanics implemented here:

  * 20% chance per turn to thaw at the start of the actor's turn
    (``pre_action``). On thaw, the mon may attack the same turn.
  * Ice types are immune to freeze.
  * A mon already carrying a non-volatile status cannot be frozen on
    top.

KNOWN GAP — fire-move thaw on receive
-------------------------------------
Gen-3 also thaws a frozen defender when it is hit by a fire-typed
move. Implementing that correctly requires a "post-receive-attack"
hook on the engine that does not exist yet — the only existing
post-attack hook is ``on_inflict``, which fires when the *attacker's*
move tries to apply a status to the *defender*. Trying to inline the
fire-thaw into ``on_inflict`` here would conflate the inflict and
receive paths.

TODO: add a post-attack ``on_hit`` hook in ``engine._step_attack`` and
move the fire-thaw logic into it. Until then, the only way out of
freeze is the per-turn 20% thaw roll. The ``thaw_on_fire_hit`` helper
below is left as a stub so the future engine integration has a
ready-made target to call.
"""
from __future__ import annotations

import random
from dataclasses import replace
from typing import TYPE_CHECKING

from tokenmon.battle.status import (
    NonVolatileStatus,
    PreActionResult,
    StatusHandlers,
    StatusState,
    register_non_volatile,
)

if TYPE_CHECKING:
    from tokenmon.battle.models import BattleStats, Move


_THAW_CHANCE = 20


def _can_inflict(target: "BattleStats") -> bool:
    if "ice" in tuple(t.lower() for t in target.types):
        return False
    if target.status.non_volatile != NonVolatileStatus.HEALTHY:
        return False
    return True


def _on_inflict(
    target: "BattleStats",
    *,
    attacker: "BattleStats",
    move: "Move",
    actor: str,
    target_side: str,
    rng: random.Random,
) -> tuple["BattleStats", list]:
    from tokenmon.battle.engine import StatusInflictedEvent

    new_status = StatusState(
        non_volatile=NonVolatileStatus.FREEZE,
        nv_counter=0,
        confusion_turns=target.status.confusion_turns,
        flinch=target.status.flinch,
    )
    new_target = replace(target, status=new_status)
    name = target.name or ("Foe" if target_side == "opp" else "Your Pokémon")
    events = [StatusInflictedEvent(
        side=target_side,
        status=NonVolatileStatus.FREEZE.value,
        message=f"{name} was frozen solid!",
    )]
    return new_target, events


def _pre_action(
    stats: "BattleStats",
    side: str,
    *,
    rng: random.Random,
) -> PreActionResult:
    from tokenmon.battle.engine import StatusPreventedEvent, StatusTickEvent

    name = stats.name or ("Foe" if side == "opp" else "Your Pokémon")

    if rng.randint(1, 100) <= _THAW_CHANCE:
        new_status = StatusState(
            non_volatile=NonVolatileStatus.HEALTHY,
            nv_counter=0,
            confusion_turns=stats.status.confusion_turns,
            flinch=stats.status.flinch,
        )
        new_stats = replace(stats, status=new_status)
        events = [StatusTickEvent(
            side=side,
            status=NonVolatileStatus.FREEZE.value,
            damage=0,
            hp_before=stats.hp_current,
            hp_after=stats.hp_current,
            message=f"{name} thawed out!",
        )]
        return PreActionResult(can_act=True, new_stats=new_stats, events=events)

    events = [StatusPreventedEvent(
        side=side,
        status=NonVolatileStatus.FREEZE.value,
        message=f"{name} is frozen solid!",
    )]
    return PreActionResult(can_act=False, new_stats=stats, events=events)


def thaw_on_fire_hit(
    defender: "BattleStats",
    move: "Move",
) -> tuple["BattleStats", list]:
    from tokenmon.battle.engine import StatusTickEvent

    if defender.status.non_volatile != NonVolatileStatus.FREEZE:
        return defender, []
    if (move.type or "").lower() != "fire":
        return defender, []

    new_status = StatusState(
        non_volatile=NonVolatileStatus.HEALTHY,
        nv_counter=0,
        confusion_turns=defender.status.confusion_turns,
        flinch=defender.status.flinch,
    )
    new_defender = replace(defender, status=new_status)
    name = defender.name or "Foe"
    events = [StatusTickEvent(
        side="opp",
        status=NonVolatileStatus.FREEZE.value,
        damage=0,
        hp_before=defender.hp_current,
        hp_after=defender.hp_current,
        message=f"{name} thawed out!",
    )]
    return new_defender, events


register_non_volatile(
    NonVolatileStatus.FREEZE,
    StatusHandlers(
        can_inflict=_can_inflict,
        on_inflict=_on_inflict,
        pre_action=_pre_action,
    ),
    pokeapi_ailments=("freeze",),
)

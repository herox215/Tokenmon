"""Flinch — single-turn volatile status.

Flinch is set by an attacker's move via ``Move.flinch_chance`` (read directly
by the engine — flinch is not signaled through PokeAPI's ``meta.ailment``).
The engine's ``_try_inflict_from_move`` runs the dice and calls
``on_inflict`` here when the roll lands.

Timing rule (Gen-3 canon): flinch only blocks the defender if the attacker
moved first this turn. In practice ``_try_inflict_from_move`` runs only after
the attacker's hit has already resolved, so:

  * If the attacker is faster, the slower defender's pre-action hasn't yet
    fired this turn — the flag is set and the defender's pre-action this
    turn sees ``status.flinch=True`` and skips.
  * If the attacker is slower, the faster defender already had its pre-action
    earlier in the turn (with ``status.flinch=False``); the flag set now is
    cleared at the start of the next turn anyway, before the defender acts
    again. So the rule "only flinches when attacker moved first" is naturally
    enforced by flinch's single-turn lifecycle without an explicit speed check.

The pre-action handler always clears the flag as it fires — flinch is single-
shot per inflict.
"""
from __future__ import annotations

from dataclasses import replace

from ..models import BattleStats
from ..status import (
    PreActionResult,
    StatusHandlers,
    VolatileStatus,
    register_volatile,
)


def _can_inflict(target: BattleStats) -> bool:
    return not target.status.flinch


def _on_inflict(
    target: BattleStats,
    *,
    attacker,
    move,
    actor,
    target_side,
    rng,
) -> tuple[BattleStats, list]:
    if target.status.flinch:
        return target, []
    new_status = replace(target.status, flinch=True)
    return replace(target, status=new_status), []


def _pre_action(
    stats: BattleStats,
    side: str,
    *,
    rng,
) -> PreActionResult:
    from ..engine import StatusPreventedEvent

    if not stats.status.flinch:
        return PreActionResult(can_act=True, new_stats=stats, events=[])

    cleared_status = replace(stats.status, flinch=False)
    new_stats = replace(stats, status=cleared_status)
    name = stats.name or ("Your Pokémon" if side == "player" else "Foe")
    event = StatusPreventedEvent(
        side=side,
        status=VolatileStatus.FLINCH.value,
        message=f"{name} flinched and couldn't move!",
    )
    return PreActionResult(can_act=False, new_stats=new_stats, events=[event])


register_volatile(
    VolatileStatus.FLINCH,
    StatusHandlers(
        can_inflict=_can_inflict,
        on_inflict=_on_inflict,
        pre_action=_pre_action,
    ),
)

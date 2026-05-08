"""Turn resolution: glues damage + accuracy + faint-detection.

A turn is one (player_choice, opp_choice) pair. The engine:

  1. Resolves the order from speed (random tie-break, deterministic per
     RNG).
  2. Builds a list of TurnEvents (attack / miss / faint) describing
     every discrete thing that should happen this turn, in the order
     the UI should animate them. ``plan_turn`` returns this list.
  3. ``resolve_turn`` folds the same events into a TurnResult — the
     existing post-turn snapshot + log used by callers that don't
     need step-by-step pacing.

The Gen-3 "faster-KO cancels slower-attack" rule is honored by
``plan_turn``: it stops emitting events as soon as either side's HP
drops to zero. Run / forfeit is handled by the caller.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Literal, Union

from .damage import compute_damage
from .models import BattleStats, Move, TurnResult

# ----------------------------------------------------------------------
# Turn events — ordered, animation-friendly description of one turn.
# ----------------------------------------------------------------------

Side = Literal["player", "opp"]


@dataclass(frozen=True, slots=True)
class AttackEvent:
    """One side hits the other (or attempts to — damage may be 0 on a
    type-immune hit). Carries enough state for the UI to animate the
    HP drain without re-running the damage roll."""

    actor: Side
    move: Move
    damage: int
    crit: bool
    effectiveness: float
    effectiveness_label: str
    defender_hp_before: int
    defender_hp_after: int


@dataclass(frozen=True, slots=True)
class MissEvent:
    """Accuracy roll failed."""

    actor: Side
    move: Move


@dataclass(frozen=True, slots=True)
class FaintEvent:
    """The side ``side`` just dropped to 0 HP. Always emitted right
    after the AttackEvent that caused it so the UI can sequence
    "drain bar" → "fade sprite" cleanly."""

    side: Side
    name: str


TurnEvent = Union[AttackEvent, MissEvent, FaintEvent]


# ----------------------------------------------------------------------


def turn_order(
    player: BattleStats,
    opp: BattleStats,
    *,
    rng: random.Random,
) -> tuple[str, str]:
    """Return ("player", "opp") or ("opp", "player") based on speed.
    Speed ties are broken randomly per ``rng``."""
    if player.speed > opp.speed:
        return ("player", "opp")
    if opp.speed > player.speed:
        return ("opp", "player")
    return ("player", "opp") if rng.random() < 0.5 else ("opp", "player")


def _accuracy_hits(move: Move, *, rng: random.Random) -> bool:
    """Accuracy check. ``accuracy is None`` → never-miss (e.g. Swift)."""
    if move.accuracy is None:
        return True
    return rng.randint(1, 100) <= move.accuracy


def _step_attack(
    attacker: BattleStats,
    defender: BattleStats,
    move: Move,
    *,
    actor: Side,
    rng: random.Random,
) -> tuple[BattleStats, list[TurnEvent]]:
    """Compute one side's attack and return (new_defender, [events]).
    Pure — does not mutate either state."""
    if not _accuracy_hits(move, rng=rng):
        return defender, [MissEvent(actor=actor, move=move)]
    result = compute_damage(attacker, defender, move, rng=rng)
    new_hp = max(0, defender.hp_current - result.damage)
    new_defender = replace(defender, hp_current=new_hp)
    return new_defender, [AttackEvent(
        actor=actor,
        move=move,
        damage=result.damage,
        crit=result.crit,
        effectiveness=result.effectiveness,
        effectiveness_label=result.effectiveness_label,
        defender_hp_before=defender.hp_current,
        defender_hp_after=new_hp,
    )]


def plan_turn(
    player: BattleStats,
    opp: BattleStats,
    *,
    player_move: Move,
    opp_move: Move,
    rng: random.Random,
) -> list[TurnEvent]:
    """Produce the ordered event list for one turn without committing
    state. The first AttackEvent is from whichever side the speed roll
    favors; if that hit causes a faint, no second AttackEvent is
    emitted (Gen-3 canon). Faint events trail the killing attack."""
    order = turn_order(player, opp, rng=rng)
    state_p = player
    state_o = opp
    events: list[TurnEvent] = []

    for actor in order:
        if state_p.hp_current <= 0 or state_o.hp_current <= 0:
            break
        if actor == "player":
            state_o, ev = _step_attack(
                state_p, state_o, player_move, actor="player", rng=rng,
            )
            events.extend(ev)
            if state_o.hp_current <= 0:
                events.append(FaintEvent(
                    side="opp", name=state_o.name or "Foe",
                ))
        else:
            state_p, ev = _step_attack(
                state_o, state_p, opp_move, actor="opp", rng=rng,
            )
            events.extend(ev)
            if state_p.hp_current <= 0:
                events.append(FaintEvent(
                    side="player",
                    name=state_p.name or "Your Pokémon",
                ))
    return events


def _label(side: Side, p: BattleStats, o: BattleStats) -> str:
    if side == "player":
        return p.name or "Your Pokémon"
    return o.name or "Foe"


def fold_events(
    events: list[TurnEvent],
    player: BattleStats,
    opp: BattleStats,
) -> TurnResult:
    """Walk an event list and produce the same TurnResult the legacy
    ``resolve_turn`` returned. Callers that don't animate (engine
    tests, headless replays) keep using this. The animated battle
    pane consumes ``plan_turn`` directly and updates state per event.
    """
    state_p = player
    state_o = opp
    log: list[str] = []
    p_fainted = False
    o_fainted = False

    for ev in events:
        if isinstance(ev, AttackEvent):
            log.append(
                f"{_label(ev.actor, state_p, state_o)} used {ev.move.name}!"
            )
            if ev.effectiveness == 0.0:
                if ev.effectiveness_label:
                    log.append(ev.effectiveness_label)
                continue
            if ev.actor == "player":
                state_o = replace(state_o, hp_current=ev.defender_hp_after)
            else:
                state_p = replace(state_p, hp_current=ev.defender_hp_after)
            if ev.crit:
                log.append("A critical hit!")
            if ev.effectiveness_label:
                log.append(ev.effectiveness_label)
        elif isinstance(ev, MissEvent):
            label = _label(ev.actor, state_p, state_o)
            log.append(f"{label} used {ev.move.name}!")
            log.append(f"{label}'s attack missed!")
        elif isinstance(ev, FaintEvent):
            log.append(f"{ev.name} fainted!")
            if ev.side == "player":
                p_fainted = True
            else:
                o_fainted = True

    return TurnResult(
        log=log,
        player_state=state_p,
        opp_state=state_o,
        player_fainted=p_fainted,
        opp_fainted=o_fainted,
    )


def resolve_turn(
    player: BattleStats,
    opp: BattleStats,
    *,
    player_move: Move,
    opp_move: Move,
    rng: random.Random,
) -> TurnResult:
    """Resolve one full battle turn.

    Caller has already decremented PP for the chosen moves. Engine
    returns post-turn snapshots and a list of log lines suitable for
    the battle pane's text feed. Implemented as ``fold_events`` over
    ``plan_turn`` so the animated and non-animated paths share one
    source of truth.
    """
    events = plan_turn(
        player, opp, player_move=player_move, opp_move=opp_move, rng=rng,
    )
    return fold_events(events, player, opp)

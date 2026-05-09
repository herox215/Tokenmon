"""Turn resolution: glues damage + accuracy + status + faint-detection.

A turn is one (player_choice, opp_choice) pair. The engine:

  1. Resolves the order from speed (random tie-break, deterministic per
     RNG). Paralysis halves speed at this step via the status registry.
  2. For each actor in order:
       a. ``pre_action`` — sleep / freeze / paralysis / flinch / confusion
          may skip the move. Status modules emit StatusPreventedEvent /
          StatusTickEvent / ConfusionSelfHitEvent.
       b. Attack — accuracy check, damage roll, AttackEvent.
       c. ``try_inflict`` — chance to apply a status from the move that
          just landed (Toxic, Will-O-Wisp, secondary on Sludge Bomb, etc.).
       d. Faint check.
  3. End-of-turn — poison / burn / toxic ramp tick on each side that's
     still standing, in speed order.

The Gen-3 "faster-KO cancels slower-attack" rule is honored. Run /
forfeit is handled by the caller.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Literal, Union

from .damage import compute_damage
from .models import BattleStats, Move, TurnResult
from .status import (
    NON_VOLATILE_REGISTRY,
    NonVolatileStatus,
    PreActionResult,
    VOLATILE_REGISTRY,
    VolatileStatus,
    ailment_to_status,
    speed_after_status,
)

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


@dataclass(frozen=True, slots=True)
class StatusInflictedEvent:
    """A move (or move's secondary effect) just inflicted ``status`` on
    ``side``. ``status`` is the string value of the enum member (so the
    UI doesn't need to import the enum). ``message`` is the user-facing
    log line ("Foe Pidgey was poisoned!", "Bulba was burned!", …)."""

    side: Side
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class StatusTickEvent:
    """End-of-turn (or pre-action) damage / state change from a status —
    poison / burn / toxic ramp / sleep wake / freeze thaw. ``damage`` is
    0 for non-damaging ticks (wake-up, thaw); HP fields are set anyway
    so the UI can decide whether to animate the bar."""

    side: Side
    status: str
    damage: int
    hp_before: int
    hp_after: int
    message: str


@dataclass(frozen=True, slots=True)
class StatusPreventedEvent:
    """The status fully prevented the action this turn — sleep, freeze,
    full-paralysis, flinch. ``message`` is the canonical Gen-3 log line
    ("Foe is fast asleep!" / "Bulba is paralyzed and can't move!")."""

    side: Side
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class ConfusionSelfHitEvent:
    """Confusion's 50/50 roll hit the attacker themselves with a 40-power
    typeless physical hit. Distinct event so the UI can show the lunge
    + "It hurt itself in its confusion!" line without going through the
    AttackEvent path (which would imply a target side)."""

    side: Side
    damage: int
    hp_before: int
    hp_after: int


TurnEvent = Union[
    AttackEvent,
    MissEvent,
    FaintEvent,
    StatusInflictedEvent,
    StatusTickEvent,
    StatusPreventedEvent,
    ConfusionSelfHitEvent,
]


# ----------------------------------------------------------------------


def turn_order(
    player: BattleStats,
    opp: BattleStats,
    *,
    rng: random.Random,
) -> tuple[str, str]:
    """Return ("player", "opp") or ("opp", "player") based on effective
    speed (after paralysis / future speed modifiers). Speed ties are
    broken randomly per ``rng``."""
    p_spd = speed_after_status(player)
    o_spd = speed_after_status(opp)
    if p_spd > o_spd:
        return ("player", "opp")
    if o_spd > p_spd:
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
) -> tuple[BattleStats, BattleStats, list[TurnEvent]]:
    """Compute one side's attack and return (new_attacker, new_defender,
    [events]). Pure — does not mutate. Returns attacker too because
    fire-typed hits on a frozen defender thaw the *defender* (same side
    as the attacker's target), and future moves like Rest mutate the
    attacker's status — keeping a single (atk, def) return makes those
    extensions simple.
    """
    if not _accuracy_hits(move, rng=rng):
        return attacker, defender, [MissEvent(actor=actor, move=move)]
    result = compute_damage(attacker, defender, move, rng=rng)
    new_hp = max(0, defender.hp_current - result.damage)
    new_defender = replace(defender, hp_current=new_hp)
    return attacker, new_defender, [AttackEvent(
        actor=actor,
        move=move,
        damage=result.damage,
        crit=result.crit,
        effectiveness=result.effectiveness,
        effectiveness_label=result.effectiveness_label,
        defender_hp_before=defender.hp_current,
        defender_hp_after=new_hp,
    )]


def _run_pre_action(
    stats: BattleStats,
    side: Side,
    *,
    rng: random.Random,
) -> PreActionResult:
    """Walk every relevant pre-action handler and combine their results.

    Order:
      1. flinch (volatile, single-turn)
      2. confusion (volatile, may roll self-hit)
      3. non-volatile (sleep / freeze / paralysis)

    The first handler that returns ``can_act=False`` short-circuits — if
    a mon is asleep AND flinching the same turn (rare combo via Yawn-
    flow, not implementable in v1), flinch fires first by canon. Each
    handler is responsible for clearing / decrementing its own counter
    even on the short-circuit path.
    """
    events: list[TurnEvent] = []
    new_stats = stats

    # Flinch — handled even if a higher-priority status will block too,
    # because flinch must clear at the start of the next turn.
    flinch_h = VOLATILE_REGISTRY.get(VolatileStatus.FLINCH)
    if flinch_h is not None and flinch_h.pre_action is not None and new_stats.status.flinch:
        result: PreActionResult = flinch_h.pre_action(new_stats, side, rng=rng)
        new_stats = result.new_stats
        events.extend(result.events)
        if not result.can_act:
            return PreActionResult(False, new_stats, events)

    # Confusion.
    conf_h = VOLATILE_REGISTRY.get(VolatileStatus.CONFUSION)
    if conf_h is not None and conf_h.pre_action is not None and new_stats.status.confusion_turns > 0:
        result = conf_h.pre_action(new_stats, side, rng=rng)
        new_stats = result.new_stats
        events.extend(result.events)
        if not result.can_act:
            return PreActionResult(False, new_stats, events)

    # Non-volatile.
    nv = new_stats.status.non_volatile
    if nv != NonVolatileStatus.HEALTHY:
        nv_h = NON_VOLATILE_REGISTRY.get(nv)
        if nv_h is not None and nv_h.pre_action is not None:
            result = nv_h.pre_action(new_stats, side, rng=rng)
            new_stats = result.new_stats
            events.extend(result.events)
            if not result.can_act:
                return PreActionResult(False, new_stats, events)

    return PreActionResult(True, new_stats, events)


def _try_inflict_from_move(
    attacker: BattleStats,
    defender: BattleStats,
    move: Move,
    *,
    actor: Side,
    rng: random.Random,
) -> tuple[BattleStats, BattleStats, list[TurnEvent]]:
    """Roll the move's secondary status / flinch chances against the
    defender (or the attacker, for self-targeted statuses — not currently
    used by anything we ship). Each registered status checks its own
    type-immunity / already-statused rules.

    The defender side of the inflict is "the side opposite ``actor``";
    flinch always targets the defender. Status modules implement the
    actual on_inflict logic.
    """
    events: list[TurnEvent] = []
    new_attacker = attacker
    new_defender = defender

    target_side: Side = "opp" if actor == "player" else "player"

    # 1. Ailment from move.
    ailment_slug = (move.ailment or "none").lower()
    if ailment_slug != "none":
        chance = move.ailment_chance
        # PokeAPI convention: ailment_chance == 0 means "guaranteed for
        # status moves" (Toxic, Will-O-Wisp, Sleep Powder, etc.). For
        # damaging moves with ailment metadata but chance==0 we treat as
        # guaranteed too — though in practice every secondary on a
        # damaging move has a non-zero chance.
        roll = rng.randint(1, 100)
        applies = (chance == 0) or (roll <= chance)
        if applies:
            status, is_volatile = ailment_to_status(ailment_slug)
            if status is not None:
                handlers = (
                    VOLATILE_REGISTRY.get(status) if is_volatile
                    else NON_VOLATILE_REGISTRY.get(status)
                )
                if handlers is not None and handlers.on_inflict is not None:
                    can = (
                        handlers.can_inflict is None
                        or handlers.can_inflict(new_defender)
                    )
                    if can:
                        new_defender, ev = handlers.on_inflict(
                            new_defender,
                            attacker=new_attacker,
                            move=move,
                            actor=actor,
                            target_side=target_side,
                            rng=rng,
                        )
                        events.extend(ev)

    # 2. Flinch — independent of ailment_chance. Some moves carry both
    # (e.g. Headbutt has only flinch_chance; Bite has only flinch_chance
    # in Gen-3 too — Dark-flinch came in Gen-4). The flinch handler
    # itself decides whether to actually apply (the engine asks it
    # whether the actor moved first; flinch only sticks then).
    if move.flinch_chance > 0 and rng.randint(1, 100) <= move.flinch_chance:
        flinch_h = VOLATILE_REGISTRY.get(VolatileStatus.FLINCH)
        if flinch_h is not None and flinch_h.on_inflict is not None:
            can = (
                flinch_h.can_inflict is None
                or flinch_h.can_inflict(new_defender)
            )
            if can:
                new_defender, ev = flinch_h.on_inflict(
                    new_defender,
                    attacker=new_attacker,
                    move=move,
                    actor=actor,
                    target_side=target_side,
                    rng=rng,
                )
                events.extend(ev)

    return new_attacker, new_defender, events


def _run_end_of_turn(
    stats: BattleStats,
    side: Side,
    *,
    rng: random.Random,
) -> tuple[BattleStats, list[TurnEvent]]:
    """Run end-of-turn handlers for one side. Currently only non-volatile
    statuses participate (poison, burn, toxic ramp). Returns updated
    stats + events."""
    events: list[TurnEvent] = []
    nv = stats.status.non_volatile
    if nv == NonVolatileStatus.HEALTHY:
        return stats, events
    handlers = NON_VOLATILE_REGISTRY.get(nv)
    if handlers is None or handlers.end_of_turn is None:
        return stats, events
    new_stats, ev = handlers.end_of_turn(stats, side, rng=rng)
    events.extend(ev)
    return new_stats, events


def simulate_turn(
    player: BattleStats,
    opp: BattleStats,
    *,
    player_move: Move,
    opp_move: Move,
    rng: random.Random,
) -> tuple[list[TurnEvent], BattleStats, BattleStats]:
    """Resolve one turn; return ordered events plus the final
    BattleStats for both sides.

    Use this when you need the post-turn state — particularly the
    StatusState, which ``fold_events`` cannot reconstruct from the
    event list (events log status changes via messages, not by carrying
    StatusState snapshots). The animated battle pane uses these final
    states to drive the per-mon status badge + DB persistence; pure
    test code can keep using ``plan_turn`` for events alone.
    """
    order = turn_order(player, opp, rng=rng)
    state_p = player
    state_o = opp
    events: list[TurnEvent] = []

    for actor in order:
        if state_p.hp_current <= 0 or state_o.hp_current <= 0:
            break

        # Pick attacker / defender views of the current state.
        if actor == "player":
            attacker, defender, move = state_p, state_o, player_move
        else:
            attacker, defender, move = state_o, state_p, opp_move

        # 1) Pre-action.
        pre = _run_pre_action(attacker, actor, rng=rng)
        events.extend(pre.events)
        attacker = pre.new_stats
        if actor == "player":
            state_p = attacker
        else:
            state_o = attacker
        # Confusion self-hit can faint — bail in that case.
        if attacker.hp_current <= 0:
            events.append(FaintEvent(
                side=actor,
                name=attacker.name or ("Your Pokémon" if actor == "player" else "Foe"),
            ))
            continue
        if not pre.can_act:
            continue

        # 2) Attack.
        attacker, defender, atk_events = _step_attack(
            attacker, defender, move, actor=actor, rng=rng,
        )
        events.extend(atk_events)
        if actor == "player":
            state_p, state_o = attacker, defender
        else:
            state_o, state_p = attacker, defender

        # 3) Faint from the attack itself.
        if defender.hp_current <= 0:
            events.append(FaintEvent(
                side=("opp" if actor == "player" else "player"),
                name=defender.name or "Foe",
            ))
            continue

        # 4) Status inflict from the move (only on a real hit, not a miss
        # or a 0× type-immune hit).
        landed = any(
            isinstance(e, AttackEvent) and e.effectiveness > 0.0
            for e in atk_events
        )
        if landed:
            attacker, defender, inflict_events = _try_inflict_from_move(
                attacker, defender, move, actor=actor, rng=rng,
            )
            events.extend(inflict_events)
            if actor == "player":
                state_p, state_o = attacker, defender
            else:
                state_o, state_p = attacker, defender

    # End-of-turn ticks. Fire only if the side is still alive — a fainted
    # mon doesn't tick. Order: first-actor side first, then second.
    for actor in order:
        if actor == "player" and state_p.hp_current > 0:
            state_p, ev = _run_end_of_turn(state_p, "player", rng=rng)
            events.extend(ev)
            if state_p.hp_current <= 0:
                events.append(FaintEvent(
                    side="player",
                    name=state_p.name or "Your Pokémon",
                ))
        elif actor == "opp" and state_o.hp_current > 0:
            state_o, ev = _run_end_of_turn(state_o, "opp", rng=rng)
            events.extend(ev)
            if state_o.hp_current <= 0:
                events.append(FaintEvent(
                    side="opp",
                    name=state_o.name or "Foe",
                ))

    return events, state_p, state_o


def plan_turn(
    player: BattleStats,
    opp: BattleStats,
    *,
    player_move: Move,
    opp_move: Move,
    rng: random.Random,
) -> list[TurnEvent]:
    """Events-only wrapper around ``simulate_turn``.

    Existing tests + the legacy ``resolve_turn`` codepath consume just
    the event list, so this stays the events-only public surface. New
    state-aware callers should reach for ``simulate_turn`` directly.
    """
    events, _player, _opp = simulate_turn(
        player, opp,
        player_move=player_move, opp_move=opp_move, rng=rng,
    )
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

    def _apply_status_change(side: Side, new_status_state) -> None:
        nonlocal state_p, state_o
        if side == "player":
            state_p = replace(state_p, status=new_status_state)
        else:
            state_o = replace(state_o, status=new_status_state)

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
        elif isinstance(ev, StatusInflictedEvent):
            log.append(ev.message)
        elif isinstance(ev, StatusTickEvent):
            if ev.message:
                log.append(ev.message)
            # HP changes for ticks that hurt — sleep wake / freeze thaw
            # use damage=0 and only carry a message.
            if ev.side == "player":
                state_p = replace(state_p, hp_current=ev.hp_after)
            else:
                state_o = replace(state_o, hp_current=ev.hp_after)
        elif isinstance(ev, StatusPreventedEvent):
            if ev.message:
                log.append(ev.message)
        elif isinstance(ev, ConfusionSelfHitEvent):
            log.append("It hurt itself in its confusion!")
            if ev.side == "player":
                state_p = replace(state_p, hp_current=ev.hp_after)
            else:
                state_o = replace(state_o, hp_current=ev.hp_after)

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


# Trigger per-status handler registration AFTER all events + helpers are
# defined in this module. Handlers that ``from tokenmon.battle.engine
# import StatusInflictedEvent`` would otherwise fail with ImportError if
# they were loaded mid-engine-import.
from .status import _ensure_handlers_loaded  # noqa: E402

_ensure_handlers_loaded()

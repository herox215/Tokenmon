"""Turn resolution: glues damage + accuracy + faint-detection.

A turn is one (player_choice, opp_choice) pair. The engine:

  1. Resolves the order from speed (random tie-break, deterministic per
     RNG).
  2. Applies the faster move: accuracy check, damage roll, HP update,
     log entries. If the defender faints, the slower's queued action is
     skipped (no ghost-damage rule per Gen-3).
  3. If both still alive, applies the slower move.
  4. Returns a TurnResult containing log lines + post-turn stats +
     faint flags.

Run / forfeit is a separate action handled by the caller (the battle
controller short-circuits to lose-state). The engine sees only attacks.
"""
from __future__ import annotations

import random
from dataclasses import replace

from .damage import compute_damage
from .models import BattleStats, Move, TurnResult


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


def _apply_attack(
    attacker: BattleStats,
    defender: BattleStats,
    move: Move,
    *,
    attacker_label: str,
    defender_label: str,
    rng: random.Random,
) -> tuple[BattleStats, list[str]]:
    """Apply ``attacker``'s ``move`` against ``defender`` and return the
    updated defender + log lines. Pure — does not mutate input states."""
    log: list[str] = []
    log.append(f"{attacker_label} used {move.name}!")

    if not _accuracy_hits(move, rng=rng):
        log.append(f"{attacker_label}'s attack missed!")
        return defender, log

    result = compute_damage(attacker, defender, move, rng=rng)

    if result.effectiveness == 0.0:
        log.append(result.effectiveness_label)
        return defender, log

    new_hp = max(0, defender.hp_current - result.damage)
    new_defender = replace(defender, hp_current=new_hp)

    if result.crit:
        log.append("A critical hit!")
    if result.effectiveness_label:
        log.append(result.effectiveness_label)

    return new_defender, log


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
    the battle pane's text feed.
    """
    order = turn_order(player, opp, rng=rng)
    state_p = player
    state_o = opp
    log: list[str] = []

    for actor in order:
        if state_p.hp_current <= 0 or state_o.hp_current <= 0:
            break  # one side already fainted; skip remaining action
        if actor == "player":
            state_o, lines = _apply_attack(
                state_p, state_o, player_move,
                attacker_label=state_p.name or "Your Pokémon",
                defender_label=state_o.name or "Foe",
                rng=rng,
            )
        else:
            state_p, lines = _apply_attack(
                state_o, state_p, opp_move,
                attacker_label=state_o.name or "Foe",
                defender_label=state_p.name or "Your Pokémon",
                rng=rng,
            )
        log.extend(lines)

    player_fainted = state_p.hp_current <= 0
    opp_fainted = state_o.hp_current <= 0
    if opp_fainted:
        log.append(f"{state_o.name or 'Foe'} fainted!")
    if player_fainted:
        log.append(f"{state_p.name or 'Your Pokémon'} fainted!")

    return TurnResult(
        log=log,
        player_state=state_p,
        opp_state=state_o,
        player_fainted=player_fainted,
        opp_fainted=opp_fainted,
    )

"""Status-effect foundation.

This module defines the data shapes (``NonVolatileStatus`` / ``VolatileStatus``
enums, ``StatusState`` dataclass) and the registry that per-status modules
plug into. The engine consults the registry at four lifecycle points:

  1. ``turn_order``      → ``modify_speed``        (paralysis halves speed)
  2. ``_step_attack``    → ``modify_attack``       (burn halves physical atk)
  3. ``plan_turn`` pre   → ``pre_action``          (sleep / freeze / paralysis
                                                    / confusion-self-hit / flinch)
  4. ``plan_turn`` end   → ``end_of_turn``         (poison / burn / toxic ramp)
  5. post-attack         → ``try_inflict``         (Toxic, Poison Powder,
                                                    Sludge Bomb secondary, etc.)

Per-status module pattern (each agent fills one of these):

    # battle/status_handlers/poison.py
    from tokenmon.battle.status import register_non_volatile, NonVolatileStatus

    def _can_inflict(target): ...
    def _on_tick(stats, *, rng): ...

    register_non_volatile(
        NonVolatileStatus.POISON,
        StatusHandlers(can_inflict=_can_inflict, end_of_turn=_on_tick, ...),
    )

The foundation is intentionally minimal — it's the *shared* surface every
per-status agent reads from. No per-status logic lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import random
    from .models import BattleStats, Move


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------


class NonVolatileStatus(str, Enum):
    """Persistent status — one at a time per Pokémon, survives across turns
    and (per Pokémon-canon) across battles. Stored on the pokemon /
    encounters rows so a poisoned mon stays poisoned next fight.

    The string values double as PokeAPI ``meta.ailment.name`` slugs so a
    move payload can be parsed straight into one of these via the registry's
    ``ailment_to_status`` helper.
    """

    HEALTHY = "healthy"
    POISON = "poison"
    BAD_POISON = "bad-poison"   # PokeAPI uses "poison" + "TOXIC" flag; we
                                 # split for clarity. Toxic moves map here.
    BURN = "burn"
    PARALYSIS = "paralysis"
    SLEEP = "sleep"
    FREEZE = "freeze"


class VolatileStatus(str, Enum):
    """Transient status — only meaningful during a battle. Cleared when
    the Pokémon is switched out / when the battle ends. Multiple volatile
    statuses can stack with each other and with a non-volatile status.
    """

    CONFUSION = "confusion"
    FLINCH = "flinch"


# ----------------------------------------------------------------------
# StatusState — embedded on BattleStats
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatusState:
    """All status info for one Pokémon's current battle snapshot.

    ``non_volatile``: which persistent ailment, if any. Default HEALTHY.
    ``nv_counter``: meaning depends on the status —
        * SLEEP        → turns remaining (decremented at pre-action; 0 → wake)
        * BAD_POISON   → ramp counter (1, 2, 3, … = damage = ramp/16 × maxHP)
        * others       → unused (left as 0)
    ``confusion_turns``: 0 = not confused. Otherwise: turns remaining; the
        attacker's pre-action handler decrements after the self-hit roll.
    ``flinch``: True for one turn only. The pre-action handler clears it
        as it fires — flinch is single-shot per inflict.
    """

    non_volatile: NonVolatileStatus = NonVolatileStatus.HEALTHY
    nv_counter: int = 0
    confusion_turns: int = 0
    flinch: bool = False

    def is_healthy(self) -> bool:
        return (
            self.non_volatile == NonVolatileStatus.HEALTHY
            and self.confusion_turns == 0
            and not self.flinch
        )


# ----------------------------------------------------------------------
# Pre-action result shape
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreActionResult:
    """Returned by every ``pre_action`` handler.

    ``can_act``: False if the status fully blocks the move (sleep, freeze,
        full-paralysis, flinch, confusion-self-hit). True otherwise.
    ``new_stats``: the attacker's updated state — counter decremented,
        flinch cleared, HP reduced (confusion self-hit), etc.
    ``events``: ordered list of events to emit before the (skipped or not)
        attack. Typically a single ``StatusPreventedEvent`` /
        ``StatusTickEvent`` / ``ConfusionSelfHitEvent``.
    """

    can_act: bool
    new_stats: "BattleStats"
    events: list  # list[TurnEvent] — typed in engine; circular if imported


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


# Each handler is optional — a status that doesn't (e.g.) modify speed
# leaves the corresponding callable as None. The engine treats ``None`` as
# "no-op for this lifecycle hook".

NonVolatileHandlers = dict
VolatileHandlers = dict


@dataclass(slots=True)
class StatusHandlers:
    """Per-status lifecycle handlers. Optional; missing handlers default to
    no-ops. See module docstring for what each is called for.

    Signature contracts:
      can_inflict(target: BattleStats) -> bool
          Type immunity / already-statused check. Called before applying a
          status. Return False to silently no-op the inflict attempt.

      on_inflict(target: BattleStats, *, rng) -> tuple[BattleStats, list]
          Returns the new BattleStats with the status applied + the events
          to log (``StatusInflictedEvent`` typically). Sleep rolls its
          1-3 turn counter here, bad-poison resets the ramp to 1, etc.

      pre_action(stats: BattleStats, side: str, *, rng) -> PreActionResult
          Called at the top of the actor's turn slot. Decides whether the
          actor can move; updates counters. May emit events even when
          can_act is True (e.g. "X woke up!" then attack).

      end_of_turn(stats: BattleStats, side: str, *, rng) -> tuple[BattleStats, list]
          Called once per side at the end of the turn (only if the side
          didn't faint earlier). Poison / burn ticks live here.

      modify_attack(stats: BattleStats, move: Move, atk: int) -> int
          Hook for damage formula. Burn returns atk // 2 for physical
          moves. Default behavior (no handler) is the identity.

      modify_speed(stats: BattleStats, speed: int) -> int
          Hook for turn order. Paralysis returns speed // 4 (Gen-3).
          Default is identity.
    """

    can_inflict: Callable | None = None
    on_inflict: Callable | None = None
    pre_action: Callable | None = None
    end_of_turn: Callable | None = None
    modify_attack: Callable | None = None
    modify_speed: Callable | None = None


# Registries — populated by per-status modules at import time.
NON_VOLATILE_REGISTRY: dict[NonVolatileStatus, StatusHandlers] = {}
VOLATILE_REGISTRY: dict[VolatileStatus, StatusHandlers] = {}

# Maps PokeAPI ``meta.ailment.name`` slugs → our enum members. Per-status
# modules register their PokeAPI slug here so ``Move.ailment`` parsing in
# moves_remote can stay agnostic of which statuses exist.
_AILMENT_TO_NV: dict[str, NonVolatileStatus] = {}
_AILMENT_TO_V: dict[str, VolatileStatus] = {}


def register_non_volatile(
    status: NonVolatileStatus,
    handlers: StatusHandlers,
    *,
    pokeapi_ailments: tuple[str, ...] = (),
) -> None:
    """Register a non-volatile status. Idempotent — re-registering replaces.

    ``pokeapi_ailments`` is a tuple of PokeAPI ailment slugs that map to
    this status (e.g. ('poison',) for regular poison; ('poison',) with the
    move's badly_poison flag handled by the per-status on_inflict).
    """
    NON_VOLATILE_REGISTRY[status] = handlers
    for slug in pokeapi_ailments:
        _AILMENT_TO_NV[slug.lower()] = status


def register_volatile(
    status: VolatileStatus,
    handlers: StatusHandlers,
    *,
    pokeapi_ailments: tuple[str, ...] = (),
) -> None:
    NOTHING: tuple[str, ...] = ()
    VOLATILE_REGISTRY[status] = handlers
    for slug in pokeapi_ailments:
        _AILMENT_TO_V[slug.lower()] = status


def ailment_to_status(slug: str | None) -> tuple[NonVolatileStatus | VolatileStatus | None, bool]:
    """Look up a PokeAPI ailment slug. Returns (status, is_volatile) or
    (None, False) if unknown / "none". Per-status modules populate the
    map at import time."""
    if not slug or slug == "none":
        return (None, False)
    s = slug.lower()
    if s in _AILMENT_TO_NV:
        return (_AILMENT_TO_NV[s], False)
    if s in _AILMENT_TO_V:
        return (_AILMENT_TO_V[s], True)
    return (None, False)


# ----------------------------------------------------------------------
# Convenience accessors used by the engine
# ----------------------------------------------------------------------


def speed_after_status(stats: "BattleStats") -> int:
    """Apply every relevant ``modify_speed`` hook in order. Today only
    paralysis modifies speed but the loop is generic so future statuses
    plug in cleanly. Volatile statuses don't currently modify speed."""
    speed = stats.speed
    nv = NON_VOLATILE_REGISTRY.get(stats.status.non_volatile)
    if nv is not None and nv.modify_speed is not None:
        try:
            speed = int(nv.modify_speed(stats, speed))
        except Exception:
            pass
    return max(0, speed)


def attack_after_status(stats: "BattleStats", move: "Move", atk: int) -> int:
    """Apply ``modify_attack`` hooks. Burn lives here for physical moves."""
    nv = NON_VOLATILE_REGISTRY.get(stats.status.non_volatile)
    if nv is not None and nv.modify_attack is not None:
        try:
            atk = int(nv.modify_attack(stats, move, atk))
        except Exception:
            pass
    return max(1, atk)


def _ensure_handlers_loaded() -> None:
    """Import every per-status module so its register_* call runs.

    Per-status modules live in ``battle.status_handlers``. Importing the
    package via __init__ side-effect-imports each one. Tests that exercise
    only the foundation can call this to be sure handlers are wired.
    """
    # Lazy import — the package's __init__ does the wiring.
    from . import status_handlers  # noqa: F401  (side-effect import)

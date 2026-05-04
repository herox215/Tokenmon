"""Wild-encounter brain: spawn rolls, catch math, ball throws, hints.

This module is purely transactional logic — UI lives in popover.py and the
periodic tick is wired in by menubar.py / proxy.py callers. We import lazily
where useful but the top-level imports below are safe (none of these modules
import encounter).
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from tokenmon import box, pokemon
from tokenmon.storage import (
    DB_PATH,
    Encounter,
    get_pending_encounter,
    increment_ball_used,
    insert_encounter,
    mark_encounter_caught,
    mark_encounter_ran,
    query_ball_counts,
    update_encounter_hint,
)

log = logging.getLogger("tokenmon.encounter")

# --- Tunables --------------------------------------------------------------

SPAWN_PROBABILITY = 0.03                # 3% per output-bearing call
SPAWN_COOLDOWN_SECONDS = 30 * 60        # 30 min between spawn attempts
SPAWN_MIN_OUTPUT = 50                   # don't spawn from tiny calls

BALL_TYPES = ("pokeball", "greatball", "ultraball", "masterball")
BALL_MODIFIERS = {
    "pokeball": 1.0,
    "greatball": 1.5,
    "ultraball": 2.0,
    "masterball": 255.0,                # = guaranteed catch
}
CATCH_PROBABILITY_BASELINE = 0.7        # softener — 70% per ball at max catch_rate
LEVEL_MIN, LEVEL_MAX = 1, 1  # wild Pokemon are always Lv 1; XP comes from training

_RNG = random.SystemRandom()


# --- Spawning --------------------------------------------------------------


def _last_spawn_seconds_ago(path: Path = DB_PATH) -> float:
    """Seconds since the most recent encounters.spawned_utc, or +inf if none.

    Looks at *all* encounter rows, resolved or not — the cooldown is wall-
    clock based, not "since the last unresolved spawn".
    """
    import sqlite3
    try:
        conn = sqlite3.connect(path, timeout=5.0)
        try:
            row = conn.execute(
                "SELECT spawned_utc FROM encounters ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        # No table yet — same as no encounters.
        return float("inf")
    if row is None:
        return float("inf")
    ts = datetime.fromisoformat(row[0])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return delta.total_seconds()


def maybe_spawn(*, force: bool = False, path: Path = DB_PATH) -> Encounter | None:
    """Maybe spawn a new wild Pokemon. Returns the new ``Encounter`` or None.

    Rules:
      - never spawn while a pending encounter exists,
      - cooldown: at least SPAWN_COOLDOWN_SECONDS since the last spawn,
      - probability gate: ``random() < SPAWN_PROBABILITY`` (skipped when
        ``force=True``).
    """
    if get_pending_encounter(path=path) is not None:
        return None
    if not force:
        if _last_spawn_seconds_ago(path) < SPAWN_COOLDOWN_SECONDS:
            return None
        if _RNG.random() >= SPAWN_PROBABILITY:
            return None

    species_dex_id = pokemon.random_species()
    nature = pokemon.random_nature()
    characteristic = pokemon.random_characteristic()
    level = _RNG.randint(LEVEL_MIN, LEVEL_MAX)
    catch_rate = pokemon.catch_rate_of(species_dex_id)

    enc_id = insert_encounter(
        species_dex_id=species_dex_id,
        nature=nature["name"],
        characteristic=characteristic,
        level=level,
        catch_rate=catch_rate,
        path=path,
    )
    # Re-read so we return a fully-populated Encounter (with timestamps etc).
    pending = get_pending_encounter(path=path)
    if pending is None or pending.id != enc_id:  # pragma: no cover — defensive
        log.warning("maybe_spawn: race? inserted id=%s, pending=%s", enc_id, pending)
    return pending


# --- Catch math ------------------------------------------------------------


def catch_probability(catch_rate: int, ball_type: str) -> float:
    """Probability in [0, 1] for a single throw of `ball_type` at a wild
    Pokemon with the given Gen-1 ``catch_rate`` (0..255).

    Formula: clamp((catch_rate / 255) * BALL_MODIFIERS[ball] *
    CATCH_PROBABILITY_BASELINE, 0.0, 1.0). Masterball is hard-coded to 1.0
    (the modifier alone would drag every species over 1.0 anyway, but being
    explicit keeps the floor obvious for readers).
    """
    if ball_type not in BALL_MODIFIERS:
        raise ValueError(f"unknown ball_type: {ball_type!r}")
    if ball_type == "masterball":
        return 1.0
    raw = (catch_rate / 255.0) * BALL_MODIFIERS[ball_type] * CATCH_PROBABILITY_BASELINE
    if raw < 0.0:
        return 0.0
    if raw > 1.0:
        return 1.0
    return raw


# --- Hints -----------------------------------------------------------------


def _hint_for_species(dex_id: int) -> str:
    """Flavour string shown after a failed throw. Deterministic per dex_id.

    Heuristic (keeps it cute, not accurate to any particular stat):
      - very-rare legendaries (catch_rate <= 10) → 'Looks fierce!'
      - bug-tier trash (catch_rate >= 250)        → 'Looks tiny!'
      - mid-rare (catch_rate <= 75)               → 'Looks tough!'
      - everything else: dex_id parity decides 'Looks fast!' vs 'Hard to read…'
    """
    rate = pokemon.catch_rate_of(dex_id)
    if rate <= 10:
        return "Looks fierce!"
    if rate >= 250:
        return "Looks tiny!"
    if rate <= 75:
        return "Looks tough!"
    if dex_id % 2 == 0:
        return "Looks fast!"
    return "Hard to read…"


# --- Ball throws -----------------------------------------------------------


def _validate_pending(enc: Encounter) -> None:
    if enc.resolved is not None:
        raise ValueError(
            f"encounter {enc.id} is already resolved ({enc.resolved!r})"
        )


def throw_ball(
    encounter_id: int, ball_type: str, *, path: Path = DB_PATH
) -> dict:
    """Resolve a single ball throw. See module docstring for return shape.

    Side effects:
      - increments per-ball counter on the encounter,
      - on success: inserts a new pokemon row + marks encounter caught,
      - on failure: writes a new ``last_hint`` to the encounter.

    Raises ``ValueError`` if the encounter is already resolved, the ball type
    is unknown, or the user has zero of that ball available.
    """
    if ball_type not in BALL_MODIFIERS:
        raise ValueError(f"unknown ball_type: {ball_type!r}")

    pending = get_pending_encounter(path=path)
    if pending is None or pending.id != encounter_id:
        # Either resolved or doesn't exist — both are user errors here.
        raise ValueError(
            f"encounter {encounter_id} is not the current pending encounter"
        )
    _validate_pending(pending)

    counts = query_ball_counts(path=path)
    if counts.get(ball_type, 0) <= 0:
        raise ValueError(f"no {ball_type}s available")

    # Spend the ball regardless of outcome.
    increment_ball_used(pending.id, ball_type, path=path)

    p = catch_probability(pending.catch_rate, ball_type)
    if _RNG.random() < p:
        # Caught — insert a Pokemon row.
        try:
            new_pokemon_id = box.add_caught_pokemon(
                pending.species_dex_id,
                pending.nature,
                pending.characteristic,
                path=path,
            )
        except AttributeError:  # pragma: no cover — staging fallback
            from datetime import date as _date
            from tokenmon.storage import insert_pokemon
            new_pokemon_id = insert_pokemon(
                caught_date=_date.today(),
                species_dex_id=pending.species_dex_id,
                nature=pending.nature,
                characteristic=pending.characteristic,
                path=path,
            )
        mark_encounter_caught(pending.id, new_pokemon_id, path=path)
        return {"caught": True, "hint": None, "pokemon_id": new_pokemon_id}

    hint = _hint_for_species(pending.species_dex_id)
    update_encounter_hint(pending.id, hint, path=path)
    return {"caught": False, "hint": hint, "pokemon_id": None}


def run_away(encounter_id: int, *, path: Path = DB_PATH) -> None:
    """Run from the current encounter. Idempotent in spirit — but raises
    ``ValueError`` if the encounter has already been resolved (UI shouldn't
    show a Run button in that state)."""
    pending = get_pending_encounter(path=path)
    if pending is None or pending.id != encounter_id:
        raise ValueError(
            f"encounter {encounter_id} is not the current pending encounter"
        )
    _validate_pending(pending)
    mark_encounter_ran(pending.id, path=path)

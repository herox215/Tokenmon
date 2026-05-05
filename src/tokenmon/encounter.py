"""Wild-encounter brain: spawn rolls, catch math, item use, hints.

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
from tokenmon.items import (
    BALL_CATCH_MODIFIERS,
    is_throwable,
)
from tokenmon.items import get as get_item
from tokenmon.storage import (
    DB_PATH,
    Encounter,
    get_pending_encounter,
    increment_item_used,
    insert_encounter,
    mark_encounter_caught,
    mark_encounter_ran,
    query_item_counts,
    update_encounter_hint,
)

log = logging.getLogger("tokenmon.encounter")

# --- Tunables --------------------------------------------------------------

SPAWN_PROBABILITY = 0.03                # 3% per output-bearing call
SPAWN_COOLDOWN_SECONDS = 30 * 60        # 30 min between spawn attempts
SPAWN_MIN_OUTPUT = 50                   # don't spawn from tiny calls

CATCH_PROBABILITY_BASELINE = 0.7        # softener — 70% per ball at max catch_rate
LEVEL_MIN, LEVEL_MAX = 1, 1  # wild Pokemon are always Lv 1; XP comes from training

# Backwards-compat re-export — popover.py iterates this to render the per-ball
# rows in the encounter card. Phase 3 will swap that for an items-pane that
# pulls from ``tokenmon.items`` directly; until then we expose the same tuple
# under the old name so encounter.py stays the single source of truth.
BALL_TYPES: tuple[str, ...] = ("pokeball", "greatball", "ultraball", "masterball")

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
    gender = pokemon.roll_gender(species_dex_id)
    is_shiny = pokemon.roll_shiny()

    enc_id = insert_encounter(
        species_dex_id=species_dex_id,
        nature=nature["name"],
        characteristic=characteristic,
        level=level,
        catch_rate=catch_rate,
        gender=gender,
        is_shiny=is_shiny,
        path=path,
    )
    # Re-read so we return a fully-populated Encounter (with timestamps etc).
    pending = get_pending_encounter(path=path)
    if pending is None or pending.id != enc_id:  # pragma: no cover — defensive
        log.warning("maybe_spawn: race? inserted id=%s, pending=%s", enc_id, pending)
    return pending


# --- Catch math ------------------------------------------------------------


def catch_probability(catch_rate: int, item_key: str) -> float:
    """Returns 0..1 probability for a single throw of ``item_key`` against a
    Pokemon with the given ``catch_rate``.

    Master Ball is hard-coded to 1.0; non-throwable or unknown items return
    0.0. Otherwise: ``clamp((catch_rate / 255) * BALL_CATCH_MODIFIERS[key] *
    CATCH_PROBABILITY_BASELINE, 0.0, 1.0)``.
    """
    if not is_throwable(item_key):
        return 0.0
    if item_key == "masterball":
        return 1.0
    modifier = BALL_CATCH_MODIFIERS.get(item_key, 1.0)
    p = (catch_rate / 255.0) * modifier * CATCH_PROBABILITY_BASELINE
    if p < 0.0:
        return 0.0
    if p > 1.0:
        return 1.0
    return p


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


# --- Item use --------------------------------------------------------------


def _validate_pending(enc: Encounter) -> None:
    if enc.resolved is not None:
        raise ValueError(
            f"encounter {enc.id} is already resolved ({enc.resolved!r})"
        )


def use_item(
    encounter_id: int, item_key: str, *, path: Path = DB_PATH
) -> dict:
    """Generic item-use against an encounter. Dispatches based on the item's
    actions:
      - 'throw': consumes 1 of the item, runs catch math with the item's
        modifier, marks encounter caught/updates hint accordingly.
      - any other action / no actions: raises ``ValueError`` ("item not usable
        in encounter").

    Returns:
      ``{'caught': bool, 'hint': str|None, 'pokemon_id': int|None,
         'shakes': int}``

    ``shakes`` is the GBA-style wobble count (0..3) the UI should display
    before the outcome resolves. On a catch it's always 3 (followed by the
    "click"); on a failure it's the number of shakes survived before the ball
    broke open.

    Raises ``ValueError`` if ``item_key`` is unknown or the item has no
    encounter-applicable action.
    """
    item = get_item(item_key)
    if item is None:
        raise ValueError(f"unknown item: {item_key!r}")
    if "throw" in item.actions:
        return _resolve_throw(encounter_id, item_key, path=path)
    raise ValueError(f"item not usable in encounter: {item_key!r}")


def _resolve_throw(
    encounter_id: int, item_key: str, *, path: Path = DB_PATH
) -> dict:
    """Resolve a single throw of ``item_key`` against the pending encounter.

    Catch is sampled GBA-style: 4 independent Bernoulli checks each with
    survival probability ``s = p ** 0.25`` so the marginal catch chance stays
    exactly ``p``. The number of consecutive successes before the first
    failure becomes the wobble count the UI shows (0..3); all 4 passing is a
    catch and the UI plays 3 wobbles + "click".

    Side effects:
      - increments per-item usage counter on the encounter,
      - on success: inserts a new pokemon row + marks encounter caught,
      - on failure: writes a new ``last_hint`` to the encounter.

    Raises ``ValueError`` if the encounter is already resolved or the user has
    zero of the item available.
    """
    pending = get_pending_encounter(path=path)
    if pending is None or pending.id != encounter_id:
        # Either resolved or doesn't exist — both are user errors here.
        raise ValueError(
            f"encounter {encounter_id} is not the current pending encounter"
        )
    _validate_pending(pending)

    counts = query_item_counts([item_key], path=path)
    if counts.get(item_key, 0) <= 0:
        raise ValueError(f"out of {item_key}")

    # Spend the item regardless of outcome.
    increment_item_used(pending.id, item_key, path=path)

    p = catch_probability(pending.catch_rate, item_key)
    # Per-shake survival probability. p == 0 short-circuits to s == 0 (zero
    # shakes, immediate break-out); p == 1 short-circuits to s == 1 (always
    # caught) — both edge cases are exact under ``** 0.25``.
    s = p ** 0.25
    shakes_passed = 0
    for _ in range(4):
        if _RNG.random() < s:
            shakes_passed += 1
        else:
            break
    caught = shakes_passed == 4
    shakes = min(shakes_passed, 3)

    if caught:
        try:
            new_pokemon_id = box.add_caught_pokemon(
                pending.species_dex_id,
                pending.nature,
                pending.characteristic,
                is_shiny=pending.is_shiny,
                gender=pending.gender,
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
                is_shiny=pending.is_shiny,
                gender=pending.gender,
                path=path,
            )
        mark_encounter_caught(pending.id, new_pokemon_id, path=path)
        return {
            "caught": True,
            "hint": None,
            "pokemon_id": new_pokemon_id,
            "shakes": shakes,
        }

    hint = _hint_for_species(pending.species_dex_id)
    update_encounter_hint(pending.id, hint, path=path)
    return {
        "caught": False,
        "hint": hint,
        "pokemon_id": None,
        "shakes": shakes,
    }


def throw_ball(
    encounter_id: int, ball_type: str, *, path: Path = DB_PATH
) -> dict:
    """Deprecated thin shim — delegates to :func:`use_item`.

    Kept so callers (popover, menubar) that still speak in terms of "balls"
    keep working until Phase 3 migrates them. New code should call
    :func:`use_item` directly.
    """
    return use_item(encounter_id, ball_type, path=path)


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

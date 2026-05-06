"""Wild-encounter brain: spawn rolls, catch math, item use, hints.

This module is purely transactional logic — UI lives in popover.py and the
periodic tick is wired in by menubar.py / proxy.py callers. We import lazily
where useful but the top-level imports below are safe (none of these modules
import encounter).
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from tokenmon import box, config, pokemon, weather
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
    mark_caught as _pokedex_mark_caught,
    mark_encounter_caught,
    mark_encounter_ran,
    mark_seen as _pokedex_mark_seen,
    query_item_counts,
    update_encounter_hint,
)

log = logging.getLogger("tokenmon.encounter")

# --- Tunables --------------------------------------------------------------

# Spawn probability per request scales with output_tokens via
# ``1 - exp(-min(output_tokens, SPAWN_TOKEN_CAP) / SPAWN_TOKEN_SCALE)``.
# The cap keeps very large responses from saturating at 100% — e.g. with
# the values below, even a 50k-token response stays at ~63%, so big
# requests are *more* likely to spawn but never guaranteed.
SPAWN_TOKEN_SCALE = 2000                # curve scale: 2000 tokens → ~63%
SPAWN_TOKEN_CAP = 2000                  # output_tokens are clamped to this
SPAWN_COOLDOWN_SECONDS = 5 * 60         # 5 min between spawn attempts
SPAWN_MIN_OUTPUT = 50                   # don't even roll for tiny calls

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


def spawn_probability(output_tokens: int) -> float:
    """Per-request spawn probability as a function of output_tokens.

    Shaped so a small reply has a small chance and a long, substantive
    response has a meaningful one — without ever quite reaching 100% so
    encounters never feel mandatory. Below ``SPAWN_MIN_OUTPUT`` we don't
    roll at all (returns 0.0)."""
    if output_tokens < SPAWN_MIN_OUTPUT:
        return 0.0
    clamped = min(int(output_tokens), SPAWN_TOKEN_CAP)
    return 1.0 - math.exp(-clamped / SPAWN_TOKEN_SCALE)


def maybe_spawn(
    *, force: bool = False, output_tokens: int = 0, path: Path = DB_PATH,
) -> Encounter | None:
    """Maybe spawn a new wild Pokemon. Returns the new ``Encounter`` or None.

    Rules:
      - never spawn while a pending encounter exists,
      - cooldown: at least SPAWN_COOLDOWN_SECONDS since the last spawn,
      - probability gate: ``random() < spawn_probability(output_tokens)``
        (skipped when ``force=True``).
    """
    if get_pending_encounter(path=path) is not None:
        return None
    if not force:
        if _last_spawn_seconds_ago(path) < SPAWN_COOLDOWN_SECONDS:
            return None
        if _RNG.random() >= spawn_probability(output_tokens):
            return None

    type_weights: dict[str, float] | None = None
    if config.get("use_weather"):
        try:
            snap = weather.get_weather()
        except Exception:
            log.exception("weather.get_weather failed")
            snap = None
        if snap is not None:
            type_weights = weather.type_weights(snap)
    species_dex_id = pokemon.random_species(type_weights=type_weights)
    nature = pokemon.random_nature()
    ivs = pokemon.roll_ivs()
    characteristic = pokemon.characteristic_for_ivs(ivs)
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
        ivs=ivs,
        path=path,
    )
    # Persistent Pokedex entry: 'seen' on every spawn, even if the user
    # never throws a ball. mark_seen is a no-op when an entry already exists.
    try:
        _pokedex_mark_seen(species_dex_id, path=path)
    except Exception:
        log.exception("pokedex mark_seen failed")
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
                ivs=pending.ivs,
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
                ivs=pending.ivs,
                path=path,
            )
        mark_encounter_caught(pending.id, new_pokemon_id, path=path)
        # Promote Pokedex entry to 'caught' (the wild encounter is always at
        # the base form, so no chain expansion needed here).
        try:
            _pokedex_mark_caught(pending.species_dex_id, path=path)
        except Exception:
            log.exception("pokedex mark_caught failed")
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

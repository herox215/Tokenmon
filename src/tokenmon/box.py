"""High-level PC Box lifecycle.

Owns the "what Pokemon do we have, and for which days" question. Two
responsibilities:

1. ``ensure_today_pokemon()`` — make sure today (Europe/Berlin) has a row in
   the ``pokemon`` table, creating one with random species/nature/characteristic
   if it doesn't. Idempotent.

2. ``migrate_legacy_days()`` — backfill the ``pokemon`` table from the
   ``requests`` history. Each historical day with ``output_tokens > 0`` gets a
   deterministic entry seeded from the user salt, so the legacy "this was
   Magnemite day" attribution stays intact across reinstalls.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tokenmon import config, pokemon
from tokenmon.storage import (
    DB_PATH,
    Pokemon,
    _tokens_per_local_day,
    get_pokemon_by_id,
    get_pokemon_for_date,
    insert_pokemon,
)

TZ = ZoneInfo("Europe/Berlin")
TZ_NAME = "Europe/Berlin"


def _today_local() -> date:
    return datetime.now(TZ).date()


def ensure_today_pokemon(path: Path = DB_PATH) -> Pokemon:
    """Return today's Pokemon, creating it on first call of the day.

    Random (not seeded) — a fresh install on a new day rolls a fresh species,
    nature and characteristic. Subsequent calls on the same local day return
    the existing row.
    """
    today = _today_local()
    existing = get_pokemon_for_date(today, path=path)
    if existing is not None:
        return existing

    species = pokemon.random_species()
    nature = pokemon.random_nature()
    characteristic = pokemon.random_characteristic()

    new_id = insert_pokemon(
        caught_date=today,
        species_dex_id=species,
        nature=nature["name"],
        characteristic=characteristic,
        path=path,
    )
    row = get_pokemon_by_id(new_id, path=path)
    if row is None:  # pragma: no cover — insert just succeeded
        raise RuntimeError(f"failed to read back pokemon id={new_id}")
    return row


def get_today_pokemon_id(path: Path = DB_PATH) -> int | None:
    """Return today's box entry id (today in Europe/Berlin), or ``None`` if no
    row exists yet for today. Used by storage._resolve_trained_pokemon_id as
    the fallback when no active_pokemon_id is set in config.
    """
    today = _today_local()
    row = get_pokemon_for_date(today, path=path)
    return row.id if row is not None else None


def get_active_pokemon_id(path: Path = DB_PATH) -> int | None:
    """Return ``config['active_pokemon_id']`` if it points at an existing row,
    else fall back to today's box id (which may itself be ``None`` if the box
    is empty). The fallback ensures a sensible default before the user has
    ever touched the active selector.
    """
    pinned = config.get("active_pokemon_id")
    if isinstance(pinned, int):
        if get_pokemon_by_id(pinned, path=path) is not None:
            return pinned
        # Stale pin — fall through to today's id rather than returning a dead id.
    return get_today_pokemon_id(path=path)


def get_active_pokemon(path: Path = DB_PATH) -> Pokemon | None:
    """Convenience: get_pokemon_by_id(get_active_pokemon_id())."""
    pid = get_active_pokemon_id(path=path)
    if pid is None:
        return None
    return get_pokemon_by_id(pid, path=path)


def set_active_pokemon(pokemon_id: int, path: Path = DB_PATH) -> None:
    """Persist ``config['active_pokemon_id'] = pokemon_id``.

    Validates the id refers to an existing row — raises ``ValueError`` if not,
    so the caller can't accidentally pin a non-existent pokemon.
    """
    if get_pokemon_by_id(pokemon_id, path=path) is None:
        raise ValueError(f"no pokemon with id={pokemon_id}")
    config.set_("active_pokemon_id", pokemon_id)


def add_caught_pokemon(
    species_dex_id: int,
    nature: str,
    characteristic: str,
    *,
    caught_date: date | None = None,
    path: Path = DB_PATH,
) -> int:
    """Insert a Pokemon row for a wild encounter that the user just caught.

    The schema's ``UNIQUE(caught_date)`` index conflicts with the user's
    desire to allow duplicates (multiple Pokemon may share a calendar day if
    they catch one wild on top of the daily). As a v1 kludge we retreat the
    caught_date by one day at a time until we find an empty slot. This is
    visibly wrong (a Pokemon caught today might appear under yesterday's
    date) but works as an interim measure.

    TODO(schema): drop the UNIQUE constraint on pokemon.caught_date — or
    introduce a composite UNIQUE(caught_date, slot) — so duplicates can live
    on their actual catch day. Tracked in Phase 4.
    """
    target = caught_date or _today_local()
    # Direct insert path so we don't tangle with insert_pokemon's
    # ON CONFLICT DO NOTHING (which would silently return today's existing
    # id without letting us know we collided).
    while True:
        try:
            with sqlite3.connect(path, isolation_level=None, timeout=5.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cur = conn.execute(
                    """
                    INSERT INTO pokemon (
                        caught_date, species_dex_id, nature, characteristic,
                        nickname, is_shiny
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target.isoformat(),
                        species_dex_id,
                        nature,
                        characteristic,
                        None,
                        0,
                    ),
                )
                return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            # UNIQUE(caught_date) collision — back off one day and retry.
            target = target - timedelta(days=1)


def migrate_legacy_days(path: Path = DB_PATH) -> int:
    """Backfill `pokemon` rows for every historical day in `requests` that has
    output_tokens > 0 and no Pokemon row yet.

    Uses the seeded helpers so the chosen species/nature/characteristic for
    each day match what `pick_for_today` would have returned — i.e. the user's
    historical attribution is preserved across reinstalls.

    Returns the number of rows inserted. Idempotent: re-running after all days
    are migrated is a single SELECT and returns 0.
    """
    salt = config.get_user_salt()
    inserted = 0
    for day, _tokens in _tokens_per_local_day(TZ_NAME, path):
        if get_pokemon_for_date(day, path=path) is not None:
            continue
        date_iso = day.isoformat()
        species = pokemon.seeded_species(date_iso, salt)
        nature = pokemon.seeded_nature(date_iso, salt)
        characteristic = pokemon.seeded_characteristic(date_iso, salt)
        insert_pokemon(
            caught_date=day,
            species_dex_id=species,
            nature=nature["name"],
            characteristic=characteristic,
            path=path,
        )
        inserted += 1
    return inserted

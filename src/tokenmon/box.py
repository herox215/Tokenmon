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

from datetime import date, datetime
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

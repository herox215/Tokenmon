"""SQLite storage for token usage records.

This package was a single 957-line module before the Wave-B refactor. It's
now split into focused submodules; this ``__init__`` keeps the historical
``from tokenmon.storage import X`` surface stable so callers don't need
to know about the split.
"""
from __future__ import annotations

# Connection + paths
from ._db import (
    DB_DIR,
    DB_PATH,
    BALL_THRESHOLDS,
    BALL_CAP,
    AFFECTION_MAX,
    _connect,
)

# Schema + migrations
from .migrations import SCHEMA, init_db

# Usage / requests
from .usage import (
    Usage,
    Totals,
    insert_usage,
    query_today,
    query_today_by_model,
    query_xp_for_date,
    query_xp_for_pokemon,
    latest_request_ts,
    backfill_trained_pokemon_ids,
)

# Pokemon
from .pokemon import (
    Pokemon,
    insert_pokemon,
    get_pokemon_for_date,
    get_pokemon_by_id,
    list_pokemon,
    update_pokemon_species,
    bump_affection,
)

# Encounter
from .encounter import (
    Encounter,
    insert_encounter,
    get_pending_encounter,
    mark_encounter_caught,
    mark_encounter_ran,
    update_encounter_hint,
    increment_item_used,
    increment_ball_used,
    query_item_counts,
    list_distinct_encounter_species,
)

# Pokedex
from .pokedex import (
    PokedexEntry,
    query_pokedex,
    query_pokemon_xp,
    mark_seen,
    mark_caught,
    query_pokedex_seen,
    _tokens_per_local_day,  # used by box.migrate_legacy_days
)

__all__ = [
    # _db
    "DB_DIR", "DB_PATH", "BALL_THRESHOLDS", "BALL_CAP", "AFFECTION_MAX",
    "_connect",
    # migrations
    "SCHEMA", "init_db",
    # usage
    "Usage", "Totals", "insert_usage", "query_today", "query_today_by_model",
    "query_xp_for_date", "query_xp_for_pokemon", "latest_request_ts",
    "backfill_trained_pokemon_ids",
    # pokemon
    "Pokemon", "insert_pokemon", "get_pokemon_for_date", "get_pokemon_by_id",
    "list_pokemon", "update_pokemon_species", "bump_affection",
    # encounter
    "Encounter", "insert_encounter", "get_pending_encounter",
    "mark_encounter_caught", "mark_encounter_ran", "update_encounter_hint",
    "increment_item_used", "increment_ball_used", "query_item_counts",
    "list_distinct_encounter_species",
    # pokedex
    "PokedexEntry", "query_pokedex", "query_pokemon_xp",
    "mark_seen", "mark_caught", "query_pokedex_seen",
]

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
    query_today_token_buckets,
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
    update_pokemon_nickname,
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
    add_to_inventory,
    decrement_inventory,
    add_to_pending,
    query_pending_drops,
    claim_pending_drops,
)

# Player singleton (money, future per-account stats)
from .player import (
    add_money,
    get_money,
    set_money,
)

# Per-Pokémon move slots
from .moves import (
    PokemonMove,
    delete_pokemon_moves,
    decrement_pp,
    get_pokemon_moves,
    reset_pp_for_pokemon,
    set_pokemon_move,
)

# Move-learn queue
from .pending_moves import (
    PendingMoveLearn,
    claim_pending_move_learn,
    clear_pending_for_pokemon,
    query_pending_move_learns,
    queue_move_learn,
)

# Trainer battles
from .trainers import (
    Trainer,
    TrainerPokemonRow,
    get_pending_trainer,
    get_trainer,
    insert_trainer,
    latest_trainer_spawn_ts,
    list_trainer_pokemon,
    mark_trainer_pokemon_fainted,
    mark_trainer_resolved,
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
    "query_today_token_buckets",
    "query_xp_for_date", "query_xp_for_pokemon", "latest_request_ts",
    "backfill_trained_pokemon_ids",
    # pokemon
    "Pokemon", "insert_pokemon", "get_pokemon_for_date", "get_pokemon_by_id",
    "list_pokemon", "update_pokemon_species", "update_pokemon_nickname",
    "bump_affection",
    # encounter
    "Encounter", "insert_encounter", "get_pending_encounter",
    "mark_encounter_caught", "mark_encounter_ran", "update_encounter_hint",
    "increment_item_used", "increment_ball_used", "query_item_counts",
    "list_distinct_encounter_species",
    "add_to_inventory", "decrement_inventory",
    "add_to_pending", "query_pending_drops", "claim_pending_drops",
    # pokedex
    "PokedexEntry", "query_pokedex", "query_pokemon_xp",
    "mark_seen", "mark_caught", "query_pokedex_seen",
    # player singleton
    "get_money", "set_money", "add_money",
    # pokemon_moves
    "PokemonMove", "get_pokemon_moves", "set_pokemon_move",
    "decrement_pp", "reset_pp_for_pokemon", "delete_pokemon_moves",
    # pending move-learns
    "PendingMoveLearn", "queue_move_learn", "query_pending_move_learns",
    "claim_pending_move_learn", "clear_pending_for_pokemon",
    # trainers
    "Trainer", "TrainerPokemonRow", "insert_trainer",
    "get_pending_trainer", "get_trainer", "list_trainer_pokemon",
    "mark_trainer_pokemon_fainted", "mark_trainer_resolved",
    "latest_trainer_spawn_ts",
]

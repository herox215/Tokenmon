"""Pin the public-surface contract of ``tokenmon.storage`` so the Wave-B
package split can't silently drop a name. Failure means a previously-public
helper is no longer importable as ``tokenmon.storage.X``."""
from __future__ import annotations

import pytest

from tokenmon import storage

# These names are imported by other modules today (via grep -rn). Each must
# remain importable from the ``tokenmon.storage`` package after the split.
PUBLIC_NAMES = [
    # Module-level constants
    "DB_DIR",
    "DB_PATH",
    "BALL_THRESHOLDS",
    "BALL_CAP",
    "AFFECTION_MAX",
    # Dataclasses
    "Usage",
    "Totals",
    "Pokemon",
    "Encounter",
    "PokedexEntry",
    # Functions
    "init_db",
    "insert_usage",
    "query_today",
    "query_today_by_model",
    "query_pokedex",
    "query_pokemon_xp",
    "insert_pokemon",
    "get_pokemon_for_date",
    "get_pokemon_by_id",
    "list_pokemon",
    "list_distinct_encounter_species",
    "update_pokemon_species",
    "bump_affection",
    "latest_request_ts",
    "insert_encounter",
    "get_pending_encounter",
    "mark_encounter_caught",
    "mark_encounter_ran",
    "update_encounter_hint",
    "increment_item_used",
    "query_item_counts",
    "query_xp_for_date",
    "query_xp_for_pokemon",
    "backfill_trained_pokemon_ids",
]


@pytest.mark.parametrize("name", PUBLIC_NAMES)
def test_storage_exports(name):
    assert hasattr(storage, name), f"tokenmon.storage.{name} missing post-split"

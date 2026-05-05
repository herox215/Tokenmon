"""Persistent pokedex_seen table — both the storage helpers and the
backfill migration that runs on init_db."""
from __future__ import annotations

from datetime import date

import pytest

from tokenmon import storage
from tokenmon.storage import _connect


# --- mark_seen / mark_caught semantics -----------------------------------


def test_mark_seen_creates_row(db_path):
    storage.mark_seen(25, path=db_path)
    statuses = storage.query_pokedex_seen(path=db_path)
    assert statuses[25] == "seen"


def test_mark_seen_idempotent_preserves_first_timestamp(db_path):
    storage.mark_seen(25, path=db_path)
    with _connect(db_path) as conn:
        first = conn.execute(
            "SELECT first_seen_utc FROM pokedex_seen WHERE dex_id = 25"
        ).fetchone()[0]
    storage.mark_seen(25, path=db_path)
    with _connect(db_path) as conn:
        again = conn.execute(
            "SELECT first_seen_utc FROM pokedex_seen WHERE dex_id = 25"
        ).fetchone()[0]
    assert first == again, "second mark_seen must not overwrite the first timestamp"


def test_mark_caught_promotes_seen_to_caught(db_path):
    storage.mark_seen(25, path=db_path)
    storage.mark_caught(25, path=db_path)
    statuses = storage.query_pokedex_seen(path=db_path)
    assert statuses[25] == "caught"


def test_mark_caught_preserves_first_seen_utc_when_promoting(db_path):
    storage.mark_seen(25, path=db_path)
    with _connect(db_path) as conn:
        first_seen = conn.execute(
            "SELECT first_seen_utc FROM pokedex_seen WHERE dex_id = 25"
        ).fetchone()[0]
    storage.mark_caught(25, path=db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT first_seen_utc, first_caught_utc FROM pokedex_seen WHERE dex_id = 25"
        ).fetchone()
    assert row[0] == first_seen
    assert row[1] is not None


def test_mark_caught_creates_new_row_when_unseen(db_path):
    """Catching a species that was never marked seen should create a fresh
    'caught' row directly."""
    storage.mark_caught(150, path=db_path)
    statuses = storage.query_pokedex_seen(path=db_path)
    assert statuses[150] == "caught"


def test_mark_caught_preserves_first_caught_utc_on_re_catch(db_path):
    """A second mark_caught (e.g. catching another Magikarp) should not
    overwrite the original first_caught_utc."""
    storage.mark_caught(129, path=db_path)
    with _connect(db_path) as conn:
        first = conn.execute(
            "SELECT first_caught_utc FROM pokedex_seen WHERE dex_id = 129"
        ).fetchone()[0]
    storage.mark_caught(129, path=db_path)
    with _connect(db_path) as conn:
        again = conn.execute(
            "SELECT first_caught_utc FROM pokedex_seen WHERE dex_id = 129"
        ).fetchone()[0]
    assert first == again


def test_query_pokedex_seen_returns_status_map(db_path):
    storage.mark_seen(1, path=db_path)
    storage.mark_caught(2, path=db_path)
    statuses = storage.query_pokedex_seen(path=db_path)
    assert statuses == {1: "seen", 2: "caught"}


# --- Survival semantics: this is the whole point of the table ------------


def test_release_does_not_remove_pokedex_entry(db_path):
    """Simulate a future 'release' feature by deleting the pokemon row
    after marking it caught. The Pokedex entry must persist."""
    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=25, nature="Hardy",
        characteristic="X", path=db_path,
    )
    storage.mark_caught(25, path=db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM pokemon WHERE id = ?", (pid,))
    statuses = storage.query_pokedex_seen(path=db_path)
    assert statuses[25] == "caught"


# --- Migration backfill --------------------------------------------------


def test_backfill_marks_existing_encounters_as_seen(_isolate_db):
    """Build a pre-migration DB with an encounter, run init_db, confirm
    the species got a 'seen' entry."""
    db_path = _isolate_db
    # Hand-build the schema without pokedex_seen so the backfill has work
    # to do. We use the full init_db for everything else, then drop the
    # pokedex_seen table and re-run init_db to force the backfill again.
    storage.init_db(db_path)
    with _connect(db_path) as conn:
        # Seed an encounter that's resolved 'ran' (so no pokemon row).
        conn.execute(
            "INSERT INTO encounters "
            "(spawned_utc, species_dex_id, nature, characteristic, level, "
            "catch_rate, resolved, resolved_utc) "
            "VALUES ('2026-01-01T00:00:00+00:00', 92, 'Hardy', 'X', 1, 100, "
            "'ran', '2026-01-01T00:01:00+00:00')"
        )
        conn.execute("DELETE FROM pokedex_seen")
    storage.init_db(db_path)
    statuses = storage.query_pokedex_seen(path=db_path)
    assert statuses.get(92) == "seen"


def test_backfill_marks_evolved_pokemon_as_caught_for_full_chain(_isolate_db):
    """A Venusaur in the box should have Bulbasaur, Ivysaur, AND Venusaur
    in the Pokedex after backfill."""
    db_path = _isolate_db
    storage.init_db(db_path)
    storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=3, nature="Hardy",
        characteristic="X", path=db_path,
    )
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM pokedex_seen")
    storage.init_db(db_path)
    statuses = storage.query_pokedex_seen(path=db_path)
    assert statuses.get(1) == "caught"
    assert statuses.get(2) == "caught"
    assert statuses.get(3) == "caught"


def test_backfill_idempotent(_isolate_db):
    """Re-running init_db on a fully-migrated DB doesn't duplicate rows."""
    db_path = _isolate_db
    storage.init_db(db_path)
    storage.mark_caught(25, path=db_path)
    storage.init_db(db_path)
    storage.init_db(db_path)
    with _connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM pokedex_seen").fetchone()[0]
    assert count == 1

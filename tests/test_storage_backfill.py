"""Tests for storage.backfill_trained_pokemon_ids."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tokenmon import storage


def test_backfill_populates_null_trained_pokemon_id(db_path):
    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    # Insert a request with NULL trained_pokemon_id.
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO requests (ts_utc, model, output_tokens, trained_pokemon_id) "
        "VALUES (?, 'x', 100, NULL)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    conn.close()

    storage.backfill_trained_pokemon_ids(path=db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT trained_pokemon_id FROM requests"
    ).fetchall()
    conn.close()
    # All rows should now have a non-NULL trained_pokemon_id pointing at the
    # only existing pokemon.
    assert all(r[0] == pid for r in rows)


def test_backfill_idempotent(db_path):
    storage.backfill_trained_pokemon_ids(path=db_path)
    storage.backfill_trained_pokemon_ids(path=db_path)

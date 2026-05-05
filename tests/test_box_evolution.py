"""Evolution flow — XP threshold crossing mutates species_dex_id in place."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from tokenmon import box, pokemon, storage


def test_maybe_evolve_returns_none_when_under_threshold(db_path):
    pid = box.add_caught_pokemon(
        species_dex_id=1, nature="Hardy", characteristic="X", path=db_path,
    )
    # No XP attributed → should not evolve.
    result = box.maybe_evolve(pid, path=db_path)
    assert result is None


def test_maybe_evolve_advances_species_when_xp_clears_threshold(db_path):
    """Insert a Bulbasaur, attribute enough XP to it, then run maybe_evolve.
    The new species_dex_id should be Ivysaur (#2) per Gen-1 evolution table."""
    pid = box.add_caught_pokemon(
        species_dex_id=1, nature="Hardy", characteristic="X", path=db_path,
    )
    # Inject enough output_tokens to clear Lv 16 (Ivysaur threshold).
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO requests (ts_utc, model, output_tokens, trained_pokemon_id) "
        "VALUES (?, 'x', 5_000_000, ?)",
        (datetime.now(timezone.utc).isoformat(), pid),
    )
    conn.commit()
    conn.close()

    new_id = box.maybe_evolve(pid, path=db_path)
    # The actual evolution chain may go further than Ivysaur with that much XP,
    # but it must at least advance.
    assert new_id is not None
    assert new_id != 1
    row = storage.get_pokemon_by_id(pid, path=db_path)
    assert row.species_dex_id == new_id


def test_maybe_evolve_idempotent_at_max(db_path):
    """An already-final-form Pokemon (e.g. Venusaur #3) should never evolve
    again, regardless of XP."""
    pid = box.add_caught_pokemon(
        species_dex_id=3, nature="Hardy", characteristic="X", path=db_path,
    )
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO requests (ts_utc, model, output_tokens, trained_pokemon_id) "
        "VALUES (?, 'x', 50_000_000, ?)",
        (datetime.now(timezone.utc).isoformat(), pid),
    )
    conn.commit()
    conn.close()
    assert box.maybe_evolve(pid, path=db_path) is None

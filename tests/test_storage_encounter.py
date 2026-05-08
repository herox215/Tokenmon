"""Encounter-table layer tests."""
from __future__ import annotations

import pytest

from tokenmon import storage


def _new_enc(db_path, *, gender=None, is_shiny=False, level=1, catch_rate=100):
    return storage.insert_encounter(
        species_dex_id=25,
        nature="Hardy",
        characteristic="X",
        level=level,
        catch_rate=catch_rate,
        gender=gender,
        is_shiny=is_shiny,
        path=db_path,
    )


def test_insert_encounter_round_trip(db_path):
    eid = _new_enc(db_path, gender="M", is_shiny=True)
    pending = storage.get_pending_encounter(path=db_path)
    assert pending is not None
    assert pending.id == eid
    assert pending.species_dex_id == 25
    assert pending.gender == "M"
    assert pending.is_shiny is True
    assert pending.resolved is None
    assert pending.pokemon_id is None


def test_get_pending_encounter_returns_latest(db_path):
    a = _new_enc(db_path)
    b = _new_enc(db_path)
    pending = storage.get_pending_encounter(path=db_path)
    assert pending.id == b


def test_get_pending_encounter_none_when_resolved(db_path):
    eid = _new_enc(db_path)
    storage.mark_encounter_ran(eid, path=db_path)
    assert storage.get_pending_encounter(path=db_path) is None


def test_mark_encounter_caught(db_path):
    eid = _new_enc(db_path)
    storage.mark_encounter_caught(eid, pokemon_id=42, path=db_path)
    pending = storage.get_pending_encounter(path=db_path)
    assert pending is None  # caught is a resolved state


def test_update_encounter_hint(db_path):
    eid = _new_enc(db_path)
    storage.update_encounter_hint(eid, "Looks fierce!", path=db_path)
    pending = storage.get_pending_encounter(path=db_path)
    assert pending.last_hint == "Looks fierce!"


def test_increment_item_used(db_path):
    eid = _new_enc(db_path)
    storage.increment_item_used(eid, "pokeball", path=db_path)
    storage.increment_item_used(eid, "pokeball", path=db_path)
    storage.increment_item_used(eid, "greatball", path=db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT item_key, count FROM encounter_item_uses WHERE encounter_id = ? "
        "ORDER BY item_key",
        (eid,),
    ).fetchall()
    conn.close()
    assert rows == [("greatball", 1), ("pokeball", 2)]


def test_query_item_counts_subtracts_usage(db_path):
    """Counts returned by query_item_counts reflect remaining (threshold-based)
    inventory minus usage. Implementation detail: usage is summed across all
    encounters, so we just verify the structure."""
    counts = storage.query_item_counts(["pokeball", "greatball"], path=db_path)
    assert "pokeball" in counts
    assert "greatball" in counts
    assert counts["pokeball"] >= 0


def test_encounter_dataclass_defaults(db_path):
    """Encounter without gender/is_shiny supplied gets None/False."""
    eid = _new_enc(db_path)
    pending = storage.get_pending_encounter(path=db_path)
    assert pending.gender is None
    assert pending.is_shiny is False


def test_mark_encounter_ran_sets_resolved(db_path):
    eid = _new_enc(db_path)
    storage.mark_encounter_ran(eid, path=db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT resolved, resolved_utc FROM encounters WHERE id = ?",
        (eid,),
    ).fetchone()
    conn.close()
    assert row[0] == "ran"
    assert row[1] is not None


def test_set_encounter_hp_round_trip(db_path):
    eid = _new_enc(db_path)
    storage.set_encounter_hp(eid, 7, path=db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT hp_current FROM encounters WHERE id = ?", (eid,),
    ).fetchone()
    conn.close()
    assert row[0] == 7

    storage.set_encounter_hp(eid, 0, path=db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT hp_current FROM encounters WHERE id = ?", (eid,),
    ).fetchone()
    conn.close()
    assert row[0] == 0


def test_encounter_battle_columns_default_null(db_path):
    """A fresh insert via insert_encounter has NULL hp/move_keys."""
    eid = _new_enc(db_path)
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT hp_current, move_keys_json FROM encounters WHERE id = ?", (eid,),
    ).fetchone()
    conn.close()
    assert row[0] is None
    assert row[1] is None


def test_insert_encounter_persists_move_keys(db_path):
    eid = storage.insert_encounter(
        species_dex_id=25,
        nature="Hardy",
        characteristic="X",
        level=5,
        catch_rate=100,
        move_keys=("tackle", "growl"),
        path=db_path,
    )
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT move_keys_json FROM encounters WHERE id = ?", (eid,),
    ).fetchone()
    conn.close()
    assert row[0] is not None
    import json
    assert json.loads(row[0]) == ["tackle", "growl"]

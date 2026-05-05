"""Round-trip + semantics tests for the pokemon table layer."""
from __future__ import annotations

from datetime import date

import pytest

from tokenmon import storage


def test_insert_pokemon_round_trip(db_path):
    pid = storage.insert_pokemon(
        caught_date=date(2026, 1, 1),
        species_dex_id=25,
        nature="Hardy",
        characteristic="Loves to eat",
        is_shiny=True,
        gender="F",
        path=db_path,
    )
    row = storage.get_pokemon_by_id(pid, path=db_path)
    assert row is not None
    assert row.species_dex_id == 25
    assert row.nature == "Hardy"
    assert row.characteristic == "Loves to eat"
    assert row.is_shiny is True
    assert row.gender == "F"
    assert row.affection == 0


def test_insert_two_wild_same_day(db_path):
    """Multiple wild catches on the same day must coexist after the
    UNIQUE(caught_date) drop."""
    a = storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=1, nature="Hardy",
        characteristic="X", source="wild", path=db_path,
    )
    b = storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=2, nature="Hardy",
        characteristic="X", source="wild", path=db_path,
    )
    assert a != b
    assert storage.get_pokemon_by_id(a, path=db_path) is not None
    assert storage.get_pokemon_by_id(b, path=db_path) is not None


def test_get_pokemon_for_date_returns_only_daily(db_path):
    storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=1, nature="Hardy",
        characteristic="X", source="daily", path=db_path,
    )
    storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=2, nature="Hardy",
        characteristic="X", source="wild", path=db_path,
    )
    row = storage.get_pokemon_for_date(date(2026, 1, 1), path=db_path)
    assert row is not None
    assert row.species_dex_id == 1


def test_get_pokemon_for_date_none_when_no_daily(db_path):
    storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=2, nature="Hardy",
        characteristic="X", source="wild", path=db_path,
    )
    assert storage.get_pokemon_for_date(date(2026, 1, 1), path=db_path) is None


def test_list_pokemon_sorted_desc(db_path):
    storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    storage.insert_pokemon(
        caught_date=date(2026, 1, 3), species_dex_id=3, nature="Hardy",
        characteristic="X", path=db_path,
    )
    storage.insert_pokemon(
        caught_date=date(2026, 1, 2), species_dex_id=2, nature="Hardy",
        characteristic="X", path=db_path,
    )
    rows = storage.list_pokemon(path=db_path)
    assert [r.species_dex_id for r in rows] == [3, 2, 1]


def test_bump_affection_increments_and_caps(db_path):
    pid = storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    assert storage.bump_affection(pid, 1, path=db_path) == 1
    assert storage.bump_affection(pid, 50, path=db_path) == 51
    assert storage.bump_affection(pid, 999, path=db_path) == 255  # capped


def test_bump_affection_negative_noop(db_path):
    pid = storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    assert storage.bump_affection(pid, -5, path=db_path) == 0


def test_update_pokemon_species(db_path):
    pid = storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    storage.update_pokemon_species(pid, 2, path=db_path)
    assert storage.get_pokemon_by_id(pid, path=db_path).species_dex_id == 2


def test_pokemon_dataclass_defaults(db_path):
    """Newly inserted rows without affection/gender args still produce a
    Pokemon with sensible defaults."""
    pid = storage.insert_pokemon(
        caught_date=date(2026, 1, 1), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    row = storage.get_pokemon_by_id(pid, path=db_path)
    assert row.affection == 0
    assert row.gender is None
    assert row.is_shiny is False


def test_get_pokemon_by_id_unknown(db_path):
    assert storage.get_pokemon_by_id(99999, path=db_path) is None


def test_list_pokemon_empty(db_path):
    assert storage.list_pokemon(path=db_path) == []

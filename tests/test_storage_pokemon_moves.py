"""Per-Pokémon move-slot tests."""
from __future__ import annotations

from datetime import date

import pytest

from tokenmon import storage


def _make_pokemon(db_path) -> int:
    return storage.insert_pokemon(
        caught_date=date.today(),
        species_dex_id=1,
        nature="Hardy",
        characteristic="Loves to eat",
        path=db_path,
    )


def test_get_pokemon_moves_empty(db_path):
    pid = _make_pokemon(db_path)
    assert storage.get_pokemon_moves(pid, path=db_path) == []


def test_set_pokemon_move_inserts(db_path):
    pid = _make_pokemon(db_path)
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=35, path=db_path)
    moves = storage.get_pokemon_moves(pid, path=db_path)
    assert len(moves) == 1
    assert moves[0].slot == 0
    assert moves[0].move_key == "tackle"
    assert moves[0].current_pp == 35


def test_set_pokemon_move_replaces_slot(db_path):
    pid = _make_pokemon(db_path)
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=35, path=db_path)
    storage.set_pokemon_move(pid, 0, "scratch", max_pp=40, path=db_path)
    moves = storage.get_pokemon_moves(pid, path=db_path)
    assert len(moves) == 1
    assert moves[0].move_key == "scratch"


def test_set_pokemon_move_invalid_slot_raises(db_path):
    pid = _make_pokemon(db_path)
    with pytest.raises(ValueError):
        storage.set_pokemon_move(pid, 4, "x", max_pp=10, path=db_path)
    with pytest.raises(ValueError):
        storage.set_pokemon_move(pid, -1, "x", max_pp=10, path=db_path)


def test_decrement_pp_floors_at_zero(db_path):
    pid = _make_pokemon(db_path)
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=2, path=db_path)
    assert storage.decrement_pp(pid, 0, path=db_path) == 1
    assert storage.decrement_pp(pid, 0, path=db_path) == 0
    assert storage.decrement_pp(pid, 0, path=db_path) == 0


def test_decrement_pp_unknown_slot_returns_zero(db_path):
    pid = _make_pokemon(db_path)
    assert storage.decrement_pp(pid, 0, path=db_path) == 0


def test_reset_pp_uses_lookup(db_path):
    pid = _make_pokemon(db_path)
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=35, path=db_path)
    storage.decrement_pp(pid, 0, path=db_path)
    storage.decrement_pp(pid, 0, path=db_path)
    storage.reset_pp_for_pokemon(
        pid, pp_lookup=lambda key: 35 if key == "tackle" else None,
        path=db_path,
    )
    moves = storage.get_pokemon_moves(pid, path=db_path)
    assert moves[0].current_pp == 35


def test_reset_pp_skips_when_lookup_returns_none(db_path):
    pid = _make_pokemon(db_path)
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=35, path=db_path)
    storage.decrement_pp(pid, 0, path=db_path)
    storage.reset_pp_for_pokemon(
        pid, pp_lookup=lambda _: None, path=db_path,
    )
    # PP unchanged because lookup couldn't supply max.
    moves = storage.get_pokemon_moves(pid, path=db_path)
    assert moves[0].current_pp == 34


def test_delete_pokemon_moves(db_path):
    pid = _make_pokemon(db_path)
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=35, path=db_path)
    storage.set_pokemon_move(pid, 1, "growl", max_pp=40, path=db_path)
    storage.delete_pokemon_moves(pid, path=db_path)
    assert storage.get_pokemon_moves(pid, path=db_path) == []

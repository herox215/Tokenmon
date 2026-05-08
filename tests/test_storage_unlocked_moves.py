"""Tests for the per-Pokémon unlocked-moves pool."""
from __future__ import annotations

from datetime import date

from tokenmon import storage


def _pid(db_path) -> int:
    return storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1,
        nature="Hardy", characteristic="x", path=db_path,
    )


def test_unlock_inserts_row(db_path):
    pid = _pid(db_path)
    storage.unlock_move(pid, "ember", 8, path=db_path)
    rows = storage.get_unlocked_moves(pid, path=db_path)
    assert [r.move_key for r in rows] == ["ember"]
    assert rows[0].learned_at_level == 8


def test_unlock_is_idempotent(db_path):
    """Unlocking the same move twice keeps a single row with the
    original learned_at_level so an old unlock isn't bumped to a
    later level on a re-run."""
    pid = _pid(db_path)
    storage.unlock_move(pid, "ember", 8, path=db_path)
    storage.unlock_move(pid, "ember", 17, path=db_path)
    rows = storage.get_unlocked_moves(pid, path=db_path)
    assert len(rows) == 1
    assert rows[0].learned_at_level == 8


def test_get_unlocked_orders_by_level(db_path):
    pid = _pid(db_path)
    storage.unlock_move(pid, "scratch", 12, path=db_path)
    storage.unlock_move(pid, "ember", 8, path=db_path)
    storage.unlock_move(pid, "tackle", 1, path=db_path)
    rows = storage.get_unlocked_moves(pid, path=db_path)
    assert [r.move_key for r in rows] == ["tackle", "ember", "scratch"]


def test_unlock_isolates_per_pokemon(db_path):
    pid_a = _pid(db_path)
    pid_b = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=4,
        nature="Hardy", characteristic="y", path=db_path,
    )
    storage.unlock_move(pid_a, "ember", 8, path=db_path)
    storage.unlock_move(pid_b, "scratch", 1, path=db_path)
    a_rows = storage.get_unlocked_moves(pid_a, path=db_path)
    b_rows = storage.get_unlocked_moves(pid_b, path=db_path)
    assert [r.move_key for r in a_rows] == ["ember"]
    assert [r.move_key for r in b_rows] == ["scratch"]


def test_delete_clears_pool(db_path):
    pid = _pid(db_path)
    storage.unlock_move(pid, "ember", 8, path=db_path)
    storage.unlock_move(pid, "scratch", 12, path=db_path)
    storage.delete_unlocked_moves(pid, path=db_path)
    assert storage.get_unlocked_moves(pid, path=db_path) == []

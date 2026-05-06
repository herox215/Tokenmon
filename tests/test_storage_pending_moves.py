"""Move-learn queue tests."""
from __future__ import annotations

from datetime import date

from tokenmon import storage


def _pid(db_path) -> int:
    return storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1,
        nature="Hardy", characteristic="x", path=db_path,
    )


def test_queue_returns_id(db_path):
    pid = _pid(db_path)
    qid = storage.queue_move_learn(pid, "ember", 8, path=db_path)
    assert qid > 0


def test_query_pending_lists_in_order(db_path):
    pid = _pid(db_path)
    storage.queue_move_learn(pid, "ember", 8, path=db_path)
    storage.queue_move_learn(pid, "scratch", 12, path=db_path)
    rows = storage.query_pending_move_learns(pid, path=db_path)
    assert [r.move_key for r in rows] == ["ember", "scratch"]


def test_queue_idempotent_per_move(db_path):
    """Same pokémon + move queued twice → one row."""
    pid = _pid(db_path)
    qid_a = storage.queue_move_learn(pid, "ember", 8, path=db_path)
    qid_b = storage.queue_move_learn(pid, "ember", 8, path=db_path)
    assert qid_a == qid_b
    assert len(storage.query_pending_move_learns(pid, path=db_path)) == 1


def test_claim_drops_row(db_path):
    pid = _pid(db_path)
    qid = storage.queue_move_learn(pid, "ember", 8, path=db_path)
    storage.claim_pending_move_learn(qid, path=db_path)
    assert storage.query_pending_move_learns(pid, path=db_path) == []


def test_clear_pending_for_pokemon(db_path):
    pid = _pid(db_path)
    storage.queue_move_learn(pid, "ember", 8, path=db_path)
    storage.queue_move_learn(pid, "scratch", 12, path=db_path)
    storage.clear_pending_for_pokemon(pid, path=db_path)
    assert storage.query_pending_move_learns(pid, path=db_path) == []

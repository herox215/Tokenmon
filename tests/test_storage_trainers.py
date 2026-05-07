"""Trainer + trainer_pokemon storage tests."""
from __future__ import annotations

import pytest

from tokenmon import storage


def _team() -> list[dict]:
    return [
        {
            "species_dex_id": 16, "level": 8, "nature": "Hardy",
            "ivs": (10, 10, 10, 10, 10, 10),
            "move_keys": ("tackle", "growl"),
        },
        {
            "species_dex_id": 19, "level": 9, "nature": "Adamant",
            "ivs": (5, 20, 15, 0, 0, 25),
            "move_keys": ("tackle",),
        },
    ]


def test_insert_trainer_returns_id(db_path):
    tid = storage.insert_trainer(
        name="Tobi", title="Bug Catcher", difficulty="medium",
        seed=42, team=_team(), path=db_path,
    )
    assert tid > 0


def test_get_pending_trainer(db_path):
    tid = storage.insert_trainer(
        name="Tobi", title="Bug Catcher", difficulty="medium",
        seed=42, team=_team(), path=db_path,
    )
    pending = storage.get_pending_trainer(db_path)
    assert pending is not None
    assert pending.id == tid
    assert pending.name == "Tobi"
    assert pending.difficulty == "medium"
    assert pending.resolved is None


def test_get_pending_trainer_returns_none_when_resolved(db_path):
    tid = storage.insert_trainer(
        name="Tobi", title="Bug Catcher", difficulty="easy",
        seed=1, team=_team(), path=db_path,
    )
    storage.mark_trainer_resolved(
        tid, status="won", money_reward=100, xp_reward=50, path=db_path,
    )
    assert storage.get_pending_trainer(db_path) is None


def test_list_trainer_pokemon_returns_team(db_path):
    tid = storage.insert_trainer(
        name="Tobi", title="Bug Catcher", difficulty="medium",
        seed=42, team=_team(), path=db_path,
    )
    rows = storage.list_trainer_pokemon(tid, path=db_path)
    assert len(rows) == 2
    assert rows[0].species_dex_id == 16
    assert rows[0].move_keys == ("tackle", "growl")
    assert rows[1].species_dex_id == 19
    assert rows[1].fainted is False


def test_mark_trainer_pokemon_fainted(db_path):
    tid = storage.insert_trainer(
        name="A", title="B", difficulty="easy",
        seed=1, team=_team(), path=db_path,
    )
    rows = storage.list_trainer_pokemon(tid, path=db_path)
    storage.mark_trainer_pokemon_fainted(rows[0].id, path=db_path)
    rows2 = storage.list_trainer_pokemon(tid, path=db_path)
    assert rows2[0].fainted is True
    assert rows2[1].fainted is False


def test_mark_trainer_resolved_invalid_status_raises(db_path):
    tid = storage.insert_trainer(
        name="A", title="B", difficulty="easy",
        seed=1, team=_team(), path=db_path,
    )
    with pytest.raises(ValueError):
        storage.mark_trainer_resolved(
            tid, status="bogus", path=db_path,
        )


def test_mark_trainer_resolved_records_rewards(db_path):
    tid = storage.insert_trainer(
        name="A", title="B", difficulty="easy",
        seed=1, team=_team(), path=db_path,
    )
    storage.mark_trainer_resolved(
        tid, status="won", money_reward=200, xp_reward=80, path=db_path,
    )
    t = storage.get_trainer(tid, path=db_path)
    assert t is not None
    assert t.resolved == "won"
    assert t.money_reward == 200
    assert t.xp_reward == 80


def test_latest_trainer_spawn_ts(db_path):
    assert storage.latest_trainer_spawn_ts(db_path) is None
    storage.insert_trainer(
        name="A", title="B", difficulty="easy",
        seed=1, team=_team(), path=db_path,
    )
    ts = storage.latest_trainer_spawn_ts(db_path)
    assert ts is not None

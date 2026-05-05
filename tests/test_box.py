"""Box layer tests."""
from __future__ import annotations

from datetime import date

import pytest

from tokenmon import box, storage


def test_ensure_today_pokemon_idempotent(db_path):
    a = box.ensure_today_pokemon(path=db_path)
    b = box.ensure_today_pokemon(path=db_path)
    assert a.id == b.id


def test_add_caught_pokemon_uses_today_and_wild_source(db_path):
    pid = box.add_caught_pokemon(
        species_dex_id=25,
        nature="Hardy",
        characteristic="X",
        path=db_path,
    )
    row = storage.get_pokemon_by_id(pid, path=db_path)
    assert row.caught_date == date.today()
    # Read the source column directly since the dataclass doesn't expose it.
    import sqlite3
    conn = sqlite3.connect(db_path)
    src = conn.execute("SELECT source FROM pokemon WHERE id = ?", (pid,)).fetchone()[0]
    conn.close()
    assert src == "wild"


def test_add_caught_pokemon_does_not_backdate(db_path):
    """Even when a daily already exists for today, add_caught_pokemon must
    insert at today's date."""
    box.ensure_today_pokemon(path=db_path)
    pid = box.add_caught_pokemon(
        species_dex_id=99, nature="Hardy", characteristic="X", path=db_path,
    )
    row = storage.get_pokemon_by_id(pid, path=db_path)
    assert row.caught_date == date.today()


def test_get_active_pokemon_id_falls_back_to_today_daily(db_path, monkeypatch):
    monkeypatch.setattr(box.config, "get", lambda k, **_: None)
    daily = box.ensure_today_pokemon(path=db_path)
    assert box.get_active_pokemon_id(path=db_path) == daily.id


def test_set_active_pokemon_validates_id(db_path):
    with pytest.raises(ValueError):
        box.set_active_pokemon(99999, path=db_path)


def test_get_active_pokemon_returns_full_row(db_path, monkeypatch):
    monkeypatch.setattr(box.config, "get", lambda k, **_: None)
    box.ensure_today_pokemon(path=db_path)
    active = box.get_active_pokemon(path=db_path)
    assert active is not None
    assert active.species_dex_id is not None


def test_get_active_pokemon_none_when_empty(db_path, monkeypatch):
    monkeypatch.setattr(box.config, "get", lambda k, **_: None)
    assert box.get_active_pokemon(path=db_path) is None

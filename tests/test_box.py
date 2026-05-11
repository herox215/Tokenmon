"""Box layer tests."""
from __future__ import annotations

from datetime import date

import pytest

from tokenmon import box, storage


def test_ensure_today_pokemon_idempotent(db_path):
    a = box.ensure_today_pokemon(path=db_path)
    b = box.ensure_today_pokemon(path=db_path)
    assert a.id == b.id


def test_ensure_today_pokemon_starts_with_pikachu_on_empty_box(db_path):
    row = box.ensure_today_pokemon(path=db_path)
    assert row.species_dex_id == box.STARTER_SPECIES_DEX_ID == 25


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


# ---- use_ether: PP-restore item ----------------------------------------


def _seed_pokemon_with_moves_for_ether(db_path):
    """Insert a Pokémon + 3 moves with varying PP states. Returns the id
    plus the move list used so tests can assert against the right slugs."""
    from datetime import date

    pid = storage.insert_pokemon(
        caught_date=date.today(),
        species_dex_id=1, nature="Hardy",
        characteristic="Loves to eat",
        path=db_path,
    )
    # Slot 0: tackle, max 35, current 30 (deficit 5)
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=30, path=db_path)
    # Slot 1: vine-whip, max 25, current 8 (deficit 17 — biggest deficit)
    storage.set_pokemon_move(pid, 1, "vine-whip", max_pp=8, path=db_path)
    # Slot 2: growl, max 40, current 40 (no deficit)
    storage.set_pokemon_move(pid, 2, "growl", max_pp=40, path=db_path)
    return pid


def _stub_move_pp(monkeypatch, max_pps: dict[str, int]):
    """Patch moves_remote so use_ether doesn't hit the network for max-PP."""
    from tokenmon import moves_remote
    from tokenmon.battle.models import Move

    def fake(key):
        if key not in max_pps:
            return None
        return Move(
            key=key, name=key.title(), type="normal", category="physical",
            power=40, accuracy=100, pp=max_pps[key],
        )
    monkeypatch.setattr(moves_remote, "get_move_data", fake)


def test_use_ether_restores_pp_to_lowest_deficit_move(db_path, monkeypatch):
    """Ether must auto-pick the slot with the biggest absolute deficit so
    a single use makes the most impact — Vine Whip 8/25 (deficit 17)
    beats Tackle 30/35 (deficit 5)."""
    pid = _seed_pokemon_with_moves_for_ether(db_path)
    _stub_move_pp(monkeypatch, {"tackle": 35, "vine-whip": 25, "growl": 40})
    storage.add_to_inventory("ether", 3, path=db_path)

    result = box.use_ether(pid, "ether", path=db_path)
    assert result is not None
    move_key, slot, old_pp, new_pp = result
    assert move_key == "vine-whip"
    assert slot == 1
    assert old_pp == 8
    assert new_pp == 18  # 8 + 10 ether

    rows = {r.slot: r for r in storage.get_pokemon_moves(pid, path=db_path)}
    assert rows[1].current_pp == 18
    assert rows[0].current_pp == 30  # untouched
    assert rows[2].current_pp == 40

    counts = storage.query_item_counts(["ether"], path=db_path)
    assert counts["ether"] == 2  # 3 - 1 used


def test_use_ether_caps_at_move_max_pp(db_path, monkeypatch):
    """If +10 PP would overshoot the move's max, clamp to max so we don't
    over-fill an already-mostly-full slot."""
    from datetime import date

    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=30, path=db_path)  # 30/35
    _stub_move_pp(monkeypatch, {"tackle": 35})
    storage.add_to_inventory("ether", 1, path=db_path)

    result = box.use_ether(pid, "ether", path=db_path)
    assert result is not None
    _, _, old_pp, new_pp = result
    assert old_pp == 30
    assert new_pp == 35  # capped, not 40


def test_use_ether_returns_none_when_all_moves_full(db_path, monkeypatch):
    """No-op restore must return None and NOT spend the Ether — same
    guard pattern as ``use_potion`` for HP."""
    from datetime import date

    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=35, path=db_path)
    storage.set_pokemon_move(pid, 1, "growl", max_pp=40, path=db_path)
    _stub_move_pp(monkeypatch, {"tackle": 35, "growl": 40})
    storage.add_to_inventory("ether", 1, path=db_path)

    assert box.use_ether(pid, "ether", path=db_path) is None
    counts = storage.query_item_counts(["ether"], path=db_path)
    assert counts["ether"] == 1  # not spent


def test_use_ether_returns_none_when_no_moves(db_path, monkeypatch):
    """Fresh Pokémon with no moves yet — ether is a no-op (don't crash,
    don't consume)."""
    from datetime import date

    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    storage.add_to_inventory("ether", 1, path=db_path)

    assert box.use_ether(pid, "ether", path=db_path) is None


def test_use_ether_unknown_key_returns_none(db_path):
    """Defensive: unknown ether-style keys just no-op rather than throw."""
    from datetime import date

    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    assert box.use_ether(pid, "max-elixir", path=db_path) is None


def test_ether_is_in_items_registry_with_drop_chance():
    """Pin Ether's registry shape so a future regression that flips
    tok_chance to None or moves it out of the medicine pocket is
    caught immediately. The 1/50_000 chance mirrors Potion per design."""
    from tokenmon.items import ITEMS

    ether = ITEMS["ether"]
    assert ether.pocket == "medicine"
    assert ether.tok_chance == 1 / 50_000
    assert "use" in ether.actions

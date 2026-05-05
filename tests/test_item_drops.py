"""Item-drop lottery + persistent inventory."""
from __future__ import annotations

import statistics

import pytest

from tokenmon import items, storage


# --- roll_item_drops ------------------------------------------------------


def test_roll_drops_zero_tokens_returns_empty():
    assert items.roll_item_drops(0) == {}
    assert items.roll_item_drops(-5) == {}


def test_roll_drops_pokeball_at_threshold_almost_always_at_least_one():
    """At 1000 output tokens, pokéball EV = 1 → deterministic floor=1."""
    drops = items.roll_item_drops(1000)
    assert drops.get("pokeball", 0) >= 1


def test_roll_drops_high_tokens_yields_many_pokeballs():
    """50k tokens → EV 50 pokéballs deterministic minimum."""
    drops = items.roll_item_drops(50_000)
    assert drops.get("pokeball", 0) >= 50


def test_roll_drops_skips_items_without_tok_chance(monkeypatch):
    """Item with tok_chance=None must never appear in drops."""
    from tokenmon.items import Item, ITEMS

    fake = Item(
        key="rock", emoji="🪨", display_name="Rock",
        description="x", threshold=100, actions=(), tok_chance=None,
    )
    monkeypatch.setitem(ITEMS, "rock", fake)
    samples = [items.roll_item_drops(10_000) for _ in range(50)]
    assert all("rock" not in d for d in samples)


def test_roll_drops_expected_value_matches_tok_chance():
    """Mean pokéballs over 200 trials at 5000 tokens (EV=5) ≈ 5."""
    samples = [items.roll_item_drops(5000).get("pokeball", 0) for _ in range(200)]
    mean = statistics.mean(samples)
    # Deterministic floor=5 per trial; the only randomness is whether the
    # fractional 0.0 part adds 1 (it never does at exact integer EV).
    assert mean == pytest.approx(5.0, abs=0.5)


# --- add_to_inventory / decrement / query --------------------------------


def test_add_to_inventory_creates_row(db_path):
    new_count = storage.add_to_inventory("pokeball", 5, path=db_path)
    assert new_count == 5
    counts = storage.query_item_counts(["pokeball"], path=db_path)
    assert counts["pokeball"] == 5


def test_add_to_inventory_caps_at_item_cap(db_path):
    storage.add_to_inventory("pokeball", 200, path=db_path)
    counts = storage.query_item_counts(["pokeball"], path=db_path)
    assert counts["pokeball"] == 99  # default cap


def test_add_to_inventory_unknown_item_noop(db_path):
    assert storage.add_to_inventory("nonexistent", 5, path=db_path) == 0


def test_decrement_inventory_clamps_at_zero(db_path):
    storage.add_to_inventory("pokeball", 3, path=db_path)
    new_count = storage.decrement_inventory("pokeball", 10, path=db_path)
    assert new_count == 0


def test_increment_item_used_decrements_inventory(db_path):
    """The encounter-side ledger entry must also subtract from inventory
    so 'how many do I have?' stays consistent."""
    storage.add_to_inventory("pokeball", 5, path=db_path)
    eid = storage.insert_encounter(
        species_dex_id=1, nature="Hardy", characteristic="X",
        level=1, catch_rate=100, path=db_path,
    )
    storage.increment_item_used(eid, "pokeball", n=2, path=db_path)
    counts = storage.query_item_counts(["pokeball"], path=db_path)
    assert counts["pokeball"] == 3


# --- insert_usage triggers drops -----------------------------------------


def test_insert_usage_adds_pokeballs_to_inventory(db_path):
    """A 5000-token request should add ~5 pokéballs (deterministic floor)."""
    storage.insert_usage(
        storage.Usage(model="x", output_tokens=5000), path=db_path,
    )
    counts = storage.query_item_counts(["pokeball"], path=db_path)
    assert counts["pokeball"] >= 5


def test_insert_usage_zero_output_no_drops(db_path):
    storage.insert_usage(
        storage.Usage(model="x", output_tokens=0), path=db_path,
    )
    counts = storage.query_item_counts(["pokeball"], path=db_path)
    assert counts["pokeball"] == 0


# --- migration backfill snapshot -----------------------------------------


def test_inventory_backfill_seeds_from_legacy_formula(_isolate_db):
    """Build a DB with traffic but no inventory rows, force backfill, confirm
    the resulting inventory matches the legacy earned − used calculation."""
    from datetime import datetime, timezone

    db_path = _isolate_db
    storage.init_db(db_path)
    # Wipe inventory so we can re-trigger the backfill cleanly.
    import sqlite3
    conn = sqlite3.connect(db_path)
    # 12_000 output tokens → 12 pokéballs, 1 greatball, 0 ultraball, 0 master.
    conn.execute(
        "INSERT INTO requests (ts_utc, model, output_tokens) VALUES (?, 'x', 12000)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.execute("DELETE FROM inventory")
    conn.commit()
    conn.close()
    storage.init_db(db_path)
    counts = storage.query_item_counts(path=db_path)
    assert counts["pokeball"] == 12
    assert counts["greatball"] == 1
    assert counts["ultraball"] == 0
    assert counts["masterball"] == 0


def test_inventory_backfill_idempotent(db_path):
    """Re-running init_db doesn't reset inventory."""
    storage.add_to_inventory("ultraball", 7, path=db_path)
    storage.init_db(db_path)
    storage.init_db(db_path)
    counts = storage.query_item_counts(["ultraball"], path=db_path)
    assert counts["ultraball"] == 7

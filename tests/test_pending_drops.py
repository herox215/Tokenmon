"""Pending-drops table + claim flow."""
from __future__ import annotations

import pytest

from tokenmon import storage


def test_add_to_pending_creates_row(db_path):
    storage.add_to_pending("pokeball", 3, path=db_path)
    pending = storage.query_pending_drops(path=db_path)
    assert pending == {"pokeball": 3}


def test_add_to_pending_accumulates(db_path):
    storage.add_to_pending("pokeball", 2, path=db_path)
    storage.add_to_pending("pokeball", 5, path=db_path)
    pending = storage.query_pending_drops(path=db_path)
    assert pending == {"pokeball": 7}


def test_add_to_pending_unknown_item_noop(db_path):
    assert storage.add_to_pending("not-a-thing", 5, path=db_path) == 0
    assert storage.query_pending_drops(path=db_path) == {}


def test_add_to_pending_no_cap(db_path):
    """Pending has no cap — caps only kick in when claiming into inventory."""
    storage.add_to_pending("pokeball", 200, path=db_path)
    assert storage.query_pending_drops(path=db_path) == {"pokeball": 200}


def test_claim_moves_to_inventory(db_path):
    storage.add_to_pending("greatball", 4, path=db_path)
    transferred = storage.claim_pending_drops(path=db_path)
    assert transferred == {"greatball": 4}
    assert storage.query_pending_drops(path=db_path) == {}
    assert storage.query_item_counts(["greatball"], path=db_path)["greatball"] == 4


def test_claim_caps_inventory(db_path):
    """Existing 95 + pending 50 → inventory fills to cap=99 (4 granted),
    the remaining 46 stay in pending so they aren't silently destroyed
    when the bag is full."""
    storage.add_to_inventory("pokeball", 95, path=db_path)
    storage.add_to_pending("pokeball", 50, path=db_path)
    transferred = storage.claim_pending_drops(path=db_path)
    assert transferred == {"pokeball": 4}
    counts = storage.query_item_counts(["pokeball"], path=db_path)
    assert counts["pokeball"] == 99
    assert storage.query_pending_drops(path=db_path) == {"pokeball": 46}


def test_claim_full_bag_preserves_pending(db_path):
    """Bag already at cap → claim is a no-op and nothing is lost."""
    storage.add_to_inventory("pokeball", 99, path=db_path)
    storage.add_to_pending("pokeball", 7, path=db_path)
    transferred = storage.claim_pending_drops(path=db_path)
    assert transferred == {}
    assert storage.query_pending_drops(path=db_path) == {"pokeball": 7}
    assert storage.query_item_counts(["pokeball"], path=db_path)["pokeball"] == 99


def test_claim_noop_when_empty(db_path):
    assert storage.claim_pending_drops(path=db_path) == {}


def test_insert_usage_routes_drops_through_pending(db_path):
    """A 250_000-token request (pokéball EV=5) lands in pending_drops, not inventory."""
    storage.insert_usage(
        storage.Usage(model="x", output_tokens=250_000), path=db_path,
    )
    assert storage.query_item_counts(["pokeball"], path=db_path)["pokeball"] == 0
    pending = storage.query_pending_drops(path=db_path)
    assert pending.get("pokeball", 0) >= 5


def test_claim_handles_multiple_items(db_path):
    storage.add_to_pending("pokeball", 3, path=db_path)
    storage.add_to_pending("greatball", 2, path=db_path)
    storage.add_to_pending("ultraball", 1, path=db_path)
    transferred = storage.claim_pending_drops(path=db_path)
    assert transferred == {"pokeball": 3, "greatball": 2, "ultraball": 1}
    counts = storage.query_item_counts(path=db_path)
    assert counts["pokeball"] == 3
    assert counts["greatball"] == 2
    assert counts["ultraball"] == 1

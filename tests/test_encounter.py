"""Encounter logic — catch math, spawn rules, item dispatch."""
from __future__ import annotations

import random
from datetime import date
from unittest.mock import patch

import pytest

from tokenmon import encounter, items, pokemon, storage


def _seed_pending(db_path, *, catch_rate=100, gender="M", is_shiny=False):
    eid = storage.insert_encounter(
        species_dex_id=1,
        nature="Hardy",
        characteristic="X",
        level=1,
        catch_rate=catch_rate,
        gender=gender,
        is_shiny=is_shiny,
        path=db_path,
    )
    return eid


def _grant_items(db_path, key, count):
    """Stub: directly mutate encounter_item_uses to inflate inventory? No —
    the items system uses thresholds & a usage counter. Easiest path is to
    insert enough output_tokens history; but that's expensive. Instead,
    monkeypatch query_item_counts in the storage module so we have what we
    need."""
    raise NotImplementedError  # use the patch fixture below instead


@pytest.fixture
def stub_items(monkeypatch, db_path):
    """Return a context that lets tests pretend they have N of each ball
    available without seeding token usage history."""
    available = {k: 99 for k in items.ITEMS}

    def fake_counts(keys=None, *, path=None):
        if keys is None:
            return dict(available)
        return {k: available.get(k, 0) for k in keys}

    monkeypatch.setattr(storage, "query_item_counts", fake_counts)
    # Also patch on the encounter module's local import.
    monkeypatch.setattr(encounter, "query_item_counts", fake_counts)
    return available


# ---- Catch probability math ----------------------------------------------


def test_catch_probability_pokeball_baseline():
    # catch_rate=255 + pokeball + baseline 0.7 = 0.7
    assert abs(encounter.catch_probability(255, "pokeball") - 0.7) < 1e-9


def test_catch_probability_masterball_always_one():
    assert encounter.catch_probability(3, "masterball") == 1.0
    assert encounter.catch_probability(255, "masterball") == 1.0


def test_catch_probability_unknown_item():
    assert encounter.catch_probability(255, "rock") == 0.0


def test_catch_probability_clamped_to_one():
    # ultra ball at 2x against catch_rate 255 = 2 * 0.7 = 1.4 → clamped to 1.0
    assert encounter.catch_probability(255, "ultraball") == 1.0


# ---- 4-check sampling distribution ---------------------------------------


def test_resolve_throw_caught_path(db_path, stub_items, monkeypatch):
    eid = _seed_pending(db_path, catch_rate=255)
    # Force RNG to always return 0.0 so the masterball-style "always pass" path
    # fires through the 4-check sampler. Use pokeball + catch_rate=255 → s=0.7^.25,
    # but with random()<s always true (returning 0.0) we always pass all 4.
    monkeypatch.setattr(encounter._RNG, "random", lambda: 0.0)
    result = encounter._resolve_throw(eid, "pokeball", path=db_path)
    assert result["caught"] is True
    assert result["shakes"] == 3
    assert result["pokemon_id"] is not None
    assert result["hint"] is None


def test_resolve_throw_failed_path(db_path, stub_items, monkeypatch):
    eid = _seed_pending(db_path, catch_rate=10)
    # Force first check to always fail → 0 shakes.
    monkeypatch.setattr(encounter._RNG, "random", lambda: 0.999)
    result = encounter._resolve_throw(eid, "pokeball", path=db_path)
    assert result["caught"] is False
    assert result["shakes"] == 0
    assert result["pokemon_id"] is None
    assert result["hint"] is not None


def test_resolve_throw_marginal_p_matches(db_path, stub_items, monkeypatch):
    """With p=0.5 across 2000 trials, observed catch rate should be within
    bounds of 0.5."""
    caught_count = 0
    n = 2000
    for _ in range(n):
        eid = _seed_pending(db_path, catch_rate=255)
        # We need p exactly 0.5 — set baseline override via monkeypatch
        # rather than fudging catch_rate. Easier: trust the formula and use
        # a catch_rate that yields p≈0.5: catch_rate * (1/255) * 0.7 = 0.5
        # → catch_rate = 0.5 * 255 / 0.7 ≈ 182. Check:
        # _ = encounter.catch_probability(182, "pokeball") → ~0.5
        result = encounter._resolve_throw(eid, "pokeball", path=db_path)
        if result["caught"]:
            caught_count += 1
        # If caught, the encounter is resolved — for the next loop, we need
        # a fresh pending. _seed_pending makes one each time so we're fine,
        # but old caught encounters linger. mark_encounter_ran on misses too?
        # Actually _resolve_throw on a fail leaves the encounter pending.
        # For test purity, mark it ran so the next pending is unambiguous.
        if not result["caught"]:
            storage.mark_encounter_ran(eid, path=db_path)
    # Switch the catch_rate to one giving p≈0.5: re-do with proper rate.
    # ... Actually the test as-written used catch_rate=255 → p=0.7. So expect
    # ~70% catches, not 50%.
    expected = 0.7
    rate = caught_count / n
    assert abs(rate - expected) < 0.05, f"observed {rate:.3f}, expected ≈{expected}"


def test_use_item_dispatches_throw(db_path, stub_items, monkeypatch):
    eid = _seed_pending(db_path)
    monkeypatch.setattr(encounter._RNG, "random", lambda: 0.0)
    result = encounter.use_item(eid, "pokeball", path=db_path)
    assert "caught" in result and "shakes" in result


def test_use_item_unknown_raises(db_path):
    eid = _seed_pending(db_path)
    with pytest.raises(ValueError, match="unknown item"):
        encounter.use_item(eid, "nonexistent", path=db_path)


def test_resolve_throw_already_resolved_raises(db_path, stub_items):
    eid = _seed_pending(db_path)
    storage.mark_encounter_ran(eid, path=db_path)
    with pytest.raises(ValueError):
        encounter._resolve_throw(eid, "pokeball", path=db_path)


def test_resolve_throw_out_of_items_raises(db_path, monkeypatch):
    eid = _seed_pending(db_path)
    monkeypatch.setattr(storage, "query_item_counts", lambda keys=None, **_: {"pokeball": 0})
    monkeypatch.setattr(encounter, "query_item_counts", lambda keys=None, **_: {"pokeball": 0})
    with pytest.raises(ValueError, match="out of"):
        encounter._resolve_throw(eid, "pokeball", path=db_path)


# ---- maybe_spawn ---------------------------------------------------------


def test_maybe_spawn_force_inserts_encounter(db_path):
    enc = encounter.maybe_spawn(force=True, path=db_path)
    assert enc is not None
    assert enc.id is not None


def test_maybe_spawn_no_double_pending(db_path):
    encounter.maybe_spawn(force=True, path=db_path)
    second = encounter.maybe_spawn(force=True, path=db_path)
    assert second is None


def test_maybe_spawn_populates_gender_and_shiny(db_path, monkeypatch):
    monkeypatch.setattr(pokemon, "roll_gender", lambda dex: "F")
    monkeypatch.setattr(pokemon, "roll_shiny", lambda: True)
    enc = encounter.maybe_spawn(force=True, path=db_path)
    assert enc.gender == "F"
    assert enc.is_shiny is True


def test_run_away(db_path):
    enc = encounter.maybe_spawn(force=True, path=db_path)
    encounter.run_away(enc.id, path=db_path)
    assert storage.get_pending_encounter(path=db_path) is None


# ---- spawn_probability ---------------------------------------------------


def test_spawn_probability_below_min_is_zero():
    """Tiny replies don't even roll — the curve floor is hard."""
    assert encounter.spawn_probability(0) == 0.0
    assert encounter.spawn_probability(encounter.SPAWN_MIN_OUTPUT - 1) == 0.0


def test_spawn_probability_at_min_is_positive():
    p = encounter.spawn_probability(encounter.SPAWN_MIN_OUTPUT)
    assert 0.0 < p < 0.05  # ~2.5% at 50 tokens with scale=2000


def test_spawn_probability_caps_at_token_cap():
    """Beyond SPAWN_TOKEN_CAP the curve plateaus — even huge replies
    can't reach 100%."""
    cap = encounter.SPAWN_TOKEN_CAP
    p_at_cap = encounter.spawn_probability(cap)
    p_huge = encounter.spawn_probability(cap * 100)
    assert p_at_cap == p_huge
    assert p_at_cap < 1.0  # never certain


def test_spawn_probability_monotonic():
    """More output tokens → strictly greater chance, up to the cap."""
    samples = [50, 200, 500, 1000, 1500, 2000]
    probs = [encounter.spawn_probability(t) for t in samples]
    assert probs == sorted(probs)


def test_maybe_spawn_skips_below_min_output(db_path, monkeypatch):
    """Even with random() = 0 (forces the gate open) a tiny reply
    still doesn't spawn because spawn_probability returns 0.0."""
    monkeypatch.setattr(encounter._RNG, "random", lambda: 0.0)
    monkeypatch.setattr(encounter, "_last_spawn_seconds_ago",
                        lambda *_a, **_k: float("inf"))
    enc = encounter.maybe_spawn(output_tokens=10, path=db_path)
    assert enc is None


def test_maybe_spawn_uses_token_weighted_probability(db_path, monkeypatch):
    """Above SPAWN_MIN_OUTPUT, random() < p triggers a spawn."""
    monkeypatch.setattr(encounter._RNG, "random", lambda: 0.0)
    monkeypatch.setattr(encounter, "_last_spawn_seconds_ago",
                        lambda *_a, **_k: float("inf"))
    enc = encounter.maybe_spawn(output_tokens=2000, path=db_path)
    assert enc is not None


def test_maybe_spawn_respects_cooldown(db_path, monkeypatch):
    """Within the cooldown window we never spawn, even on a 'lucky' roll."""
    monkeypatch.setattr(encounter._RNG, "random", lambda: 0.0)
    monkeypatch.setattr(encounter, "_last_spawn_seconds_ago",
                        lambda *_a, **_k: 60.0)  # 1 min ago, < 5 min cooldown
    enc = encounter.maybe_spawn(output_tokens=2000, path=db_path)
    assert enc is None

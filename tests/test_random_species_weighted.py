"""Type-weighted species selection in pokemon.random_species."""
from __future__ import annotations

from collections import Counter

from tokenmon import pokemon
from tokenmon.pokemon import rng


def test_random_species_no_weights_matches_legacy_behavior(monkeypatch):
    """Without type_weights, random_species must behave identically to the
    legacy uniform pick — i.e. it goes through _RNG.choice on the pool."""
    captured: dict = {}

    def fake_choice(pool):
        captured["pool"] = pool
        return pool[0]

    monkeypatch.setattr(rng._RNG, "choice", fake_choice)
    out = pokemon.random_species()
    assert out == captured["pool"][0]
    # Sanity: pool is non-empty
    assert len(captured["pool"]) > 50


def test_random_species_with_water_weight_picks_mostly_water(monkeypatch):
    """A 100x boost on water should produce a water-dominated distribution
    over many draws. Test uses the real RNG — picks won't be 100% water
    because the pool has many non-water species, but should be ≥80%."""
    # Daytime so the night-only restrictions don't shrink the pool.
    monkeypatch.setattr(rng, "current_time_window", lambda *a, **k: "day")
    counts: Counter[str] = Counter()
    for _ in range(500):
        d = pokemon.random_species(type_weights={"water": 100.0})
        # Bucket by primary type
        counts[pokemon.types_of(d)[0]] += 1
    total = sum(counts.values())
    water_share = counts["water"] / total
    assert water_share >= 0.80, (
        f"expected ≥80% water with 100x boost, got {water_share:.0%} ({counts})"
    )


def test_random_species_dual_type_uses_max_weight(monkeypatch):
    """A species with types (water, ice) should pick up the higher of the
    two weights when the bias targets only one of them."""
    # Force a deterministic single-element pool so we can verify the
    # weight calculation directly via the RNG.choices spy.
    captured: dict = {}

    def fake_choices(pool, weights, k):
        captured["pool"] = pool
        captured["weights"] = weights
        return [pool[0]]

    monkeypatch.setattr(rng, "current_time_window", lambda *a, **k: "day")
    monkeypatch.setattr(rng._RNG, "choices", fake_choices)
    pokemon.random_species(type_weights={"ice": 5.0})
    pool = captured["pool"]
    weights = captured["weights"]
    # Find Articuno (#144, ice/flying) — weight should be 5.0 (max of {ice: 5, flying: 1}).
    assert 144 in pool
    idx = pool.index(144)
    assert weights[idx] == 5.0


def test_random_species_unboosted_species_keep_default_weight(monkeypatch):
    """A species with no boosted types gets weight 1.0."""
    captured: dict = {}

    def fake_choices(pool, weights, k):
        captured["pool"] = pool
        captured["weights"] = weights
        return [pool[0]]

    monkeypatch.setattr(rng, "current_time_window", lambda *a, **k: "day")
    monkeypatch.setattr(rng._RNG, "choices", fake_choices)
    pokemon.random_species(type_weights={"electric": 3.0})
    pool = captured["pool"]
    weights = captured["weights"]
    # Bulbasaur (#1) is grass/poison — neither electric — should be 1.0.
    assert 1 in pool
    idx = pool.index(1)
    assert weights[idx] == 1.0

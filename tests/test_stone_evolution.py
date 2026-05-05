"""Evolution stones — data model + box.use_stone wiring."""
from __future__ import annotations

from datetime import date

import pytest

from tokenmon import box, items, pokemon, storage


# --- Data model ----------------------------------------------------------


def test_pikachu_thunder_stone_evolves_to_raichu():
    assert pokemon.stone_evolution_for(25, "thunder-stone") == 26


def test_eevee_branches():
    assert pokemon.stone_evolution_for(133, "fire-stone") == 136
    assert pokemon.stone_evolution_for(133, "water-stone") == 134
    assert pokemon.stone_evolution_for(133, "thunder-stone") == 135


def test_unknown_stone_on_known_species_returns_none():
    assert pokemon.stone_evolution_for(25, "fire-stone") is None


def test_stone_on_non_evolvable_species_returns_none():
    """Bulbasaur doesn't evolve via stone (it's level-based)."""
    assert pokemon.stone_evolution_for(1, "fire-stone") is None
    assert pokemon.stone_evolution_for(1, "leaf-stone") is None


def test_evolution_chain_includes_stone_branches():
    chain = pokemon.evolution_chain(133)
    # Eevee + 3 stone branches.
    assert 133 in chain
    assert 134 in chain
    assert 135 in chain
    assert 136 in chain


def test_evolution_chain_preserves_level_then_stone():
    """Oddish line: level 21 → Gloom, then Leaf Stone → Vileplume."""
    chain = pokemon.evolution_chain(43)
    assert chain == [43, 44, 45]


def test_pikachu_is_a_base_form():
    """Pikachu has only stone evolutions; must still be in BASE_FORMS."""
    assert 25 in pokemon.GEN1_BASE_FORMS


def test_line_of_resolves_stone_evolved_forms():
    assert pokemon.line_of(26) == 25   # Raichu
    assert pokemon.line_of(45) == 43   # Vileplume
    assert pokemon.line_of(134) == 133  # Vaporeon
    assert pokemon.line_of(136) == 133  # Flareon


def test_species_seen_through_handles_stone_branch():
    """A Vaporeon walks back to Eevee, not to a sibling Eeveelution."""
    assert pokemon.species_seen_through(134) == (133, 134)


def test_species_seen_through_two_stage_then_stone():
    """Vileplume comes from Oddish via Gloom (level), Gloom→Vileplume (stone)."""
    assert pokemon.species_seen_through(45) == (43, 44, 45)


# --- Stone item registry --------------------------------------------------


@pytest.mark.parametrize("key", [
    "fire-stone", "water-stone", "thunder-stone", "leaf-stone", "moon-stone",
])
def test_stone_in_items_registry(key):
    item = items.get(key)
    assert item is not None
    assert "use" in item.actions
    assert item.tok_chance is not None
    assert item.sprite_name == key  # PokeAPI sprite name matches key


# --- box.use_stone --------------------------------------------------------


def _seed_pokemon(db_path, species_dex_id):
    return storage.insert_pokemon(
        caught_date=date(2026, 1, 1),
        species_dex_id=species_dex_id,
        nature="Hardy",
        characteristic="X",
        path=db_path,
    )


def test_use_stone_evolves_pikachu_to_raichu(db_path):
    storage.add_to_inventory("thunder-stone", 1, path=db_path)
    pid = _seed_pokemon(db_path, 25)
    new_id = box.use_stone(pid, "thunder-stone", path=db_path)
    assert new_id == 26
    row = storage.get_pokemon_by_id(pid, path=db_path)
    assert row.species_dex_id == 26


def test_use_stone_consumes_inventory(db_path):
    storage.add_to_inventory("thunder-stone", 3, path=db_path)
    pid = _seed_pokemon(db_path, 25)
    box.use_stone(pid, "thunder-stone", path=db_path)
    counts = storage.query_item_counts(["thunder-stone"], path=db_path)
    assert counts["thunder-stone"] == 2


def test_use_stone_no_effect_keeps_inventory(db_path):
    """Wrong stone for the species: don't evolve, don't consume."""
    storage.add_to_inventory("fire-stone", 1, path=db_path)
    pid = _seed_pokemon(db_path, 25)  # Pikachu
    new_id = box.use_stone(pid, "fire-stone", path=db_path)
    assert new_id is None
    row = storage.get_pokemon_by_id(pid, path=db_path)
    assert row.species_dex_id == 25  # unchanged
    counts = storage.query_item_counts(["fire-stone"], path=db_path)
    assert counts["fire-stone"] == 1  # not consumed


def test_use_stone_marks_pokedex(db_path):
    storage.add_to_inventory("water-stone", 1, path=db_path)
    pid = _seed_pokemon(db_path, 133)  # Eevee
    box.use_stone(pid, "water-stone", path=db_path)
    statuses = storage.query_pokedex_seen(path=db_path)
    assert statuses.get(134) == "caught"


def test_use_stone_eevee_branches(db_path):
    storage.add_to_inventory("fire-stone", 1, path=db_path)
    storage.add_to_inventory("thunder-stone", 1, path=db_path)
    a = _seed_pokemon(db_path, 133)
    b = _seed_pokemon(db_path, 133)
    assert box.use_stone(a, "fire-stone", path=db_path) == 136   # Flareon
    assert box.use_stone(b, "thunder-stone", path=db_path) == 135  # Jolteon


# --- maybe_evolve no longer triggers stone-based evolutions ---------------


def test_maybe_evolve_does_not_auto_trigger_stone(db_path):
    """Pikachu used to auto-evolve at level 30 (stand-in for the stone).
    After the refactor, Pikachu must require an explicit stone use."""
    from datetime import datetime, timezone
    pid = _seed_pokemon(db_path, 25)
    # Drop in enough XP to push Pikachu to level 99 — must not evolve.
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO requests (ts_utc, model, output_tokens, trained_pokemon_id) "
        "VALUES (?, 'x', 100_000_000, ?)",
        (datetime.now(timezone.utc).isoformat(), pid),
    )
    conn.commit()
    conn.close()
    assert box.maybe_evolve(pid, path=db_path) is None
    row = storage.get_pokemon_by_id(pid, path=db_path)
    assert row.species_dex_id == 25

"""Pure-logic tests for the pokemon module — RNG rolls, time windows, level
math, naming. Sprite fetching is mocked by conftest's autouse fixture."""
from __future__ import annotations

import collections
from datetime import datetime
from unittest.mock import patch

import pytest

from tokenmon import pokemon


# ---- Time windows --------------------------------------------------------


@pytest.mark.parametrize("hour, expected", [
    (0, "night"),
    (5, "night"),
    (6, "day"),    # boundary — DAY_HOUR_START inclusive
    (12, "day"),
    (19, "day"),
    (20, "night"),  # DAY_HOUR_END exclusive
    (23, "night"),
])
def test_current_time_window_boundaries(hour, expected):
    fake = datetime(2026, 1, 1, hour, 0)
    assert pokemon.current_time_window(fake) == expected


def test_can_spawn_now_default_species():
    assert pokemon.can_spawn_now(1, window="day") is True
    assert pokemon.can_spawn_now(1, window="night") is True


def test_can_spawn_now_night_only_blocked_in_day():
    assert pokemon.can_spawn_now(92, window="day") is False  # Gastly
    assert pokemon.can_spawn_now(92, window="night") is True


def test_can_spawn_now_day_only_blocked_at_night():
    assert pokemon.can_spawn_now(16, window="night") is False  # Pidgey
    assert pokemon.can_spawn_now(16, window="day") is True


def test_random_species_respects_window():
    """Patch current_time_window to force night, confirm no day-only species
    appear in 500 draws. Patches the rng submodule directly so the patch is
    visible to ``random_species`` regardless of how the package is laid out."""
    day_only = pokemon.GEN1_DAY_ONLY
    # Try the post-Wave-D submodule first; fall back to the package binding.
    try:
        from tokenmon.pokemon import rng as _rng_mod
        target = _rng_mod
    except ImportError:
        target = pokemon
    with patch.object(target, "current_time_window", return_value="night"):
        sample = {pokemon.random_species() for _ in range(500)}
    assert sample.isdisjoint(day_only), "Day-only species spawned at night"


def test_random_species_only_returns_base_forms():
    sample = {pokemon.random_species() for _ in range(200)}
    assert sample <= set(pokemon.GEN1_BASE_FORMS.keys())


# ---- Gender --------------------------------------------------------------


@pytest.mark.parametrize("dex", sorted(pokemon.GEN1_GENDERLESS))
def test_roll_gender_genderless(dex):
    assert pokemon.roll_gender(dex) is None


def test_roll_gender_50_50():
    counts = collections.Counter(pokemon.roll_gender(1) for _ in range(8000))
    # Statistical bound: each side at least 40% / at most 60%.
    assert 3200 <= counts["M"] <= 4800, counts
    assert 3200 <= counts["F"] <= 4800, counts
    assert counts[None] == 0


def test_is_genderless():
    assert pokemon.is_genderless(150) is True   # Mewtwo
    assert pokemon.is_genderless(132) is True   # Ditto
    assert pokemon.is_genderless(1) is False    # Bulbasaur


@pytest.mark.parametrize("g, expected", [
    ("M", "♂"),
    ("F", "♀"),
    (None, ""),
    ("X", ""),
])
def test_gender_symbol(g, expected):
    assert pokemon.gender_symbol(g) == expected


# ---- Shiny ---------------------------------------------------------------


def test_shiny_rate_constant():
    # Make sure the canonical "1 in 4096" rate isn't silently changed.
    assert abs(pokemon.SHINY_RATE - (1 / 4096)) < 1e-9


def test_roll_shiny_distribution():
    """Statistical sanity check — 40000 trials at rate 1/4096 should average
    around 9-10 shinies. Allow a generous bound."""
    shinies = sum(pokemon.roll_shiny() for _ in range(40000))
    assert 0 <= shinies <= 40, f"shinies={shinies} out of bound"


# ---- Sprite path computation ---------------------------------------------


def test_sprite_path_normal(_isolate_sprites):
    sprite_dir, _ = _isolate_sprites
    p = pokemon.sprite_path(25)
    assert p.parent == sprite_dir
    assert p.name == "25.gif"


def test_sprite_path_shiny(_isolate_sprites):
    _, shiny_dir = _isolate_sprites
    p = pokemon.sprite_path(25, shiny=True)
    assert p.parent == shiny_dir
    assert p.name == "25.gif"


def test_ensure_sprite_uses_stub(_isolate_sprites):
    """conftest replaces ensure_sprite with a no-network stub. This test
    confirms the stub is wired and produces a real file."""
    p = pokemon.ensure_sprite(150)
    assert p.exists()
    assert p.read_bytes() == b"GIF89a fake"


# ---- Level / catch / name lookups ----------------------------------------


def test_name_of_known_species():
    assert pokemon.name_of(1) == "Bulbasaur"
    assert pokemon.name_of(151) == "Mew"


def test_catch_rate_of_legendaries():
    # Articuno/Zapdos/Moltres/Mewtwo/Mew should all have low catch rates.
    for dex in (144, 145, 146, 150, 151):
        rate = pokemon.catch_rate_of(dex)
        assert 0 < rate <= 50, f"#{dex} catch rate {rate}"


def test_catch_rate_of_unknown_returns_default():
    assert pokemon.catch_rate_of(99999) == 100  # default fallback

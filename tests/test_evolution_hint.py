"""Tests for ``pokemon.next_evolution_hint`` — the helper that powers the
Bug 6 evolution-hint line in the Pokedex detail pane."""
from __future__ import annotations

from tokenmon import pokemon


# ---- Level evolutions ----------------------------------------------------


def test_bulbasaur_evolves_to_ivysaur_at_16():
    assert pokemon.next_evolution_hint(1) == "Evolves into Ivysaur at level 16"


def test_ivysaur_evolves_to_venusaur_at_32():
    assert pokemon.next_evolution_hint(2) == "Evolves into Venusaur at level 32"


def test_venusaur_no_evolution():
    assert pokemon.next_evolution_hint(3) is None


def test_charmander_to_charmeleon_at_16():
    assert pokemon.next_evolution_hint(4) == "Evolves into Charmeleon at level 16"


def test_magikarp_to_gyarados_at_20():
    assert pokemon.next_evolution_hint(129) == "Evolves into Gyarados at level 20"


# ---- Trade evolutions ----------------------------------------------------


def test_kadabra_via_trade():
    # Kadabra (64) → Alakazam (65) is the canonical Gen-1 trade evolution.
    assert pokemon.next_evolution_hint(64) == "Evolves into Alakazam via trade"


def test_machoke_via_trade():
    assert pokemon.next_evolution_hint(67) == "Evolves into Machamp via trade"


def test_graveler_via_trade():
    assert pokemon.next_evolution_hint(75) == "Evolves into Golem via trade"


def test_haunter_via_trade():
    assert pokemon.next_evolution_hint(93) == "Evolves into Gengar via trade"


# ---- Stone evolutions ----------------------------------------------------


def test_pikachu_thunder_stone_to_raichu():
    hint = pokemon.next_evolution_hint(25)
    assert hint == "Use Thunder Stone to evolve into Raichu"


def test_eevee_three_stones():
    hint = pokemon.next_evolution_hint(133)
    assert hint is not None
    # All three Eeveelutions named.
    assert "Vaporeon" in hint
    assert "Jolteon" in hint
    assert "Flareon" in hint
    # All three stone names are present.
    assert "Fire Stone" in hint
    assert "Water Stone" in hint
    assert "Thunder Stone" in hint
    # Joined as a list (Oxford-or for 3 branches).
    assert ", or " in hint


def test_clefairy_moon_stone_to_clefable():
    assert pokemon.next_evolution_hint(35) == (
        "Use Moon Stone to evolve into Clefable"
    )


def test_vulpix_fire_stone_to_ninetales():
    assert pokemon.next_evolution_hint(37) == (
        "Use Fire Stone to evolve into Ninetales"
    )


# ---- Final / non-evolving species ---------------------------------------


def test_mewtwo_no_evolution():
    assert pokemon.next_evolution_hint(150) is None


def test_mew_no_evolution():
    assert pokemon.next_evolution_hint(151) is None


def test_articuno_no_evolution():
    assert pokemon.next_evolution_hint(144) is None


def test_raichu_no_further_evolution():
    # Raichu is a stone-evolved final form — no further chain.
    assert pokemon.next_evolution_hint(26) is None


def test_alakazam_no_further_evolution():
    assert pokemon.next_evolution_hint(65) is None


def test_snorlax_no_evolution():
    # Single-stage species in EVOLUTIONS but with empty chain.
    assert pokemon.next_evolution_hint(143) is None

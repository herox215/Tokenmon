"""Trainer-team generation tests.

The generator takes a learnset_lookup callable so we stub it with a
fixed mapping. Determinism per seed is the central guarantee.
"""
from __future__ import annotations

import random

import pytest

from tokenmon.battle.team_gen import (
    DIFFICULTY_PROFILES,
    EASY_ONLY_LEVEL,
    generate_trainer_team,
    pick_difficulty,
)


def _stub_learnset(_dex_id: int) -> list[tuple[int, str]]:
    """Stub: every species knows tackle at L1 and quick-attack at L5."""
    return [(1, "tackle"), (5, "quick-attack"), (10, "bite")]


def test_easy_difficulty_one_pokemon():
    team = generate_trainer_team(
        seed=1, difficulty="easy", player_level=20,
        learnset_lookup=_stub_learnset,
    )
    assert len(team) == 1


def test_medium_difficulty_two_pokemon():
    team = generate_trainer_team(
        seed=1, difficulty="medium", player_level=20,
        learnset_lookup=_stub_learnset,
    )
    assert len(team) == 2


def test_hard_difficulty_three_pokemon():
    team = generate_trainer_team(
        seed=1, difficulty="hard", player_level=20,
        learnset_lookup=_stub_learnset,
    )
    assert len(team) == 3


def test_same_seed_yields_same_team():
    args = dict(
        difficulty="hard", player_level=25,
        learnset_lookup=_stub_learnset,
    )
    a = generate_trainer_team(seed=42, **args)
    b = generate_trainer_team(seed=42, **args)
    assert [m.species_dex_id for m in a] == [m.species_dex_id for m in b]
    assert [m.level for m in a] == [m.level for m in b]
    assert [m.ivs for m in a] == [m.ivs for m in b]


def test_level_within_difficulty_range():
    """Every member's level must lie inside ``player_level + delta_range``."""
    for diff in ("easy", "medium", "hard"):
        profile = DIFFICULTY_PROFILES[diff]
        for seed in range(20):
            team = generate_trainer_team(
                seed=seed, difficulty=diff, player_level=30,
                learnset_lookup=_stub_learnset,
            )
            for m in team:
                assert profile["delta_min"] + 30 <= m.level <= profile["delta_max"] + 30


def test_avoid_player_species():
    """If player has Bulbasaur active, trainer shouldn't field one."""
    for seed in range(20):
        team = generate_trainer_team(
            seed=seed, difficulty="hard", player_level=30,
            player_active_species=1,  # Bulbasaur
            learnset_lookup=_stub_learnset,
        )
        for m in team:
            assert m.species_dex_id != 1


def test_moves_drawn_from_learnset_at_level():
    """A L3 Pokémon should only know moves from learnset entries with
    level<=3 (so 'tackle' from our stub, but not 'quick-attack' which
    is L5)."""
    def learnset(_):
        return [(1, "tackle"), (5, "quick-attack"), (10, "bite")]

    # Force level to come out as 3 by using a low player_level + easy
    # (delta -7..-3 → with player_level=10 the range is 3..7).
    team = generate_trainer_team(
        seed=0, difficulty="easy", player_level=10,
        learnset_lookup=learnset,
    )
    for m in team:
        for key in m.move_keys:
            # Whichever moves were chosen, they must be eligible at this level.
            allowed = {k for lv, k in learnset(1) if lv <= m.level}
            # Fallback "tackle" allowed if learnset empty
            assert key in allowed or key == "tackle"


def test_empty_learnset_falls_back_to_tackle():
    team = generate_trainer_team(
        seed=0, difficulty="easy", player_level=20,
        learnset_lookup=lambda _: [],
    )
    for m in team:
        assert m.move_keys == ("tackle",)


def test_pick_difficulty_low_level_always_easy():
    rng = random.Random(0)
    for _ in range(50):
        assert pick_difficulty(EASY_ONLY_LEVEL - 1, rng) == "easy"


def test_pick_difficulty_distribution():
    """At higher levels we should see all three over many rolls."""
    seen = set()
    for s in range(200):
        seen.add(pick_difficulty(50, random.Random(s)))
    assert seen == {"easy", "medium", "hard"}

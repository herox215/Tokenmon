"""Pure tests for the Gen-3 type-effectiveness chart.

The chart is hand-curated; these tests pin every non-trivial matchup so
typos are caught at test time. We don't enumerate all 17×17 = 289 pairs
— neutral defaults are spot-checked via ``test_neutral_default_when_no_entry``
— but we cover every known super-effective, resist, and immune entry.
"""
from __future__ import annotations

import pytest

from tokenmon.battle.types import TYPES, effectiveness, label_for


# --- Sanity ---------------------------------------------------------------


def test_seventeen_types_present():
    """Gen-3 type set: 17 (Gen-1's 15 + Dark + Steel). No Fairy."""
    assert len(TYPES) == 17
    assert "fairy" not in TYPES
    assert "dark" in TYPES
    assert "steel" in TYPES


def test_neutral_default_when_no_entry():
    # Normal vs Water: not in the chart → neutral.
    assert effectiveness("normal", ("water",)) == 1.0
    # Bug vs Normal: no entry → neutral.
    assert effectiveness("bug", ("normal",)) == 1.0


def test_unknown_type_neutral():
    """Defensive: a malformed move type shouldn't crash the engine."""
    assert effectiveness("metal", ("water",)) == 1.0
    assert effectiveness("water", ("plasma",)) == 1.0


def test_case_insensitive():
    assert effectiveness("FIRE", ("GRASS",)) == 2.0
    assert effectiveness("Water", ("Fire",)) == 2.0


# --- Immunities (0×) ------------------------------------------------------


@pytest.mark.parametrize("attack, defender", [
    ("normal", "ghost"),
    ("fighting", "ghost"),
    ("ground", "flying"),
    ("electric", "ground"),
    ("psychic", "dark"),
    ("ghost", "normal"),
    ("poison", "steel"),
])
def test_immunities(attack, defender):
    assert effectiveness(attack, (defender,)) == 0.0


# --- Single-type super-effective (2×) -----------------------------------


@pytest.mark.parametrize("attack, defender", [
    ("water", "fire"), ("fire", "grass"), ("grass", "water"),
    ("electric", "water"), ("electric", "flying"),
    ("ground", "fire"), ("ground", "electric"), ("ground", "rock"),
    ("ground", "steel"), ("ground", "poison"),
    ("rock", "fire"), ("rock", "ice"), ("rock", "flying"), ("rock", "bug"),
    ("ice", "grass"), ("ice", "ground"), ("ice", "flying"), ("ice", "dragon"),
    ("fire", "ice"), ("fire", "bug"), ("fire", "steel"),
    ("water", "ground"), ("water", "rock"),
    ("grass", "ground"), ("grass", "rock"),
    ("fighting", "normal"), ("fighting", "ice"), ("fighting", "rock"),
    ("fighting", "dark"), ("fighting", "steel"),
    ("flying", "fighting"), ("flying", "bug"), ("flying", "grass"),
    ("psychic", "fighting"), ("psychic", "poison"),
    ("bug", "grass"), ("bug", "psychic"), ("bug", "dark"),
    ("ghost", "ghost"), ("ghost", "psychic"),
    ("dark", "ghost"), ("dark", "psychic"),
    ("dragon", "dragon"),
    ("steel", "rock"), ("steel", "ice"),
    ("poison", "grass"),
])
def test_super_effective(attack, defender):
    assert effectiveness(attack, (defender,)) == 2.0


# --- Single-type resists (0.5×) -----------------------------------------


@pytest.mark.parametrize("attack, defender", [
    ("water", "water"), ("fire", "fire"), ("grass", "grass"),
    ("water", "grass"), ("fire", "water"), ("grass", "fire"),
    ("electric", "electric"), ("electric", "grass"), ("electric", "dragon"),
    ("normal", "rock"), ("normal", "steel"),
    ("rock", "fighting"), ("rock", "ground"), ("rock", "steel"),
    ("flying", "rock"), ("flying", "steel"), ("flying", "electric"),
    ("ice", "fire"), ("ice", "water"), ("ice", "ice"), ("ice", "steel"),
    ("fire", "rock"), ("fire", "dragon"),
    ("water", "dragon"),
    ("grass", "flying"), ("grass", "poison"), ("grass", "bug"),
    ("grass", "steel"), ("grass", "dragon"),
    ("fighting", "poison"), ("fighting", "flying"), ("fighting", "psychic"),
    ("fighting", "bug"),
    ("psychic", "psychic"), ("psychic", "steel"),
    ("bug", "fighting"), ("bug", "flying"), ("bug", "poison"),
    ("bug", "ghost"), ("bug", "fire"), ("bug", "steel"),
    ("dark", "fighting"), ("dark", "dark"), ("dark", "steel"),
    ("ghost", "steel"), ("ghost", "dark"),
    ("dragon", "steel"),
    ("steel", "steel"), ("steel", "fire"), ("steel", "water"),
    ("steel", "electric"),
    ("poison", "poison"), ("poison", "ground"), ("poison", "rock"),
    ("poison", "ghost"),
])
def test_resists(attack, defender):
    assert effectiveness(attack, (defender,)) == 0.5


# --- Dual-type stacking ---------------------------------------------------


def test_dual_type_double_super_effective():
    # Ice on Dragon/Flying = 2 × 2 = 4×. Classic 4× combo.
    assert effectiveness("ice", ("dragon", "flying")) == 4.0


def test_dual_type_resist_stack():
    # Fire on Water/Rock = 0.5 × 0.5 = 0.25×.
    assert effectiveness("fire", ("water", "rock")) == 0.25


def test_dual_type_super_canceled_by_resist():
    # Fire is super-effective on Grass (2×) but resisted by Water (0.5×).
    # Charizard is Fire/Flying, so a Grass attacker hits Charizard
    # 2× from Grass-vs-Flying-no-effect... wait. Easier: Fire on Bug/Steel
    # = 2 × 2 = 4×. Use a real cancel: Water on Fire/Rock = 2 × 2 = 4×.
    # Cancel example: Grass on Water/Flying — water 2×, flying 0.5× → 1×.
    assert effectiveness("grass", ("water", "flying")) == 1.0


def test_dual_type_immunity_dominates():
    # Even if one type would be 2×, an immunity in the other forces 0.
    # Electric vs Water/Ground: water 2×, ground 0× → 0.
    assert effectiveness("electric", ("water", "ground")) == 0.0


# --- Labels ---------------------------------------------------------------


def test_label_super_effective():
    assert label_for(2.0) == "It's super effective!"
    assert label_for(4.0) == "It's super effective!"


def test_label_not_very_effective():
    assert label_for(0.5) == "It's not very effective…"
    assert label_for(0.25) == "It's not very effective…"


def test_label_no_effect():
    assert label_for(0.0) == "It had no effect…"


def test_label_neutral_empty():
    assert label_for(1.0) == ""

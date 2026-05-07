"""Gen-3 type-effectiveness chart.

17 types (Normal, Fighting, Flying, Poison, Ground, Rock, Bug, Ghost,
Steel, Fire, Water, Grass, Electric, Psychic, Ice, Dragon, Dark) — Gen-2
introduced Dark and Steel; Fairy (Gen-6) is intentionally excluded.

Hand-curated rather than scraped from PokeAPI because PokeAPI's
``damage_relations`` returns current-gen matchups; ``past_damage_relations``
exists but is fiddly to consume reliably for our purposes. The chart
below is the canonical Gen-3 matchup table per Bulbapedia, verified at
plan-review time.

Only non-1.0 entries are specified explicitly; ``effectiveness()`` falls
back to neutral (1.0) for any pair not listed.
"""
from __future__ import annotations

# Allowed types — Fairy is filtered out at move-load time.
TYPES: tuple[str, ...] = (
    "normal", "fighting", "flying", "poison", "ground", "rock",
    "bug", "ghost", "steel", "fire", "water", "grass", "electric",
    "psychic", "ice", "dragon", "dark",
)

# Effectiveness map: ``_CHART[attacker_type][defender_type]`` = multiplier.
# Missing pair → neutral 1.0. Use lowercase keys exclusively.
_CHART: dict[str, dict[str, float]] = {
    "normal": {"rock": 0.5, "ghost": 0.0, "steel": 0.5},
    "fighting": {
        "normal": 2.0, "ice": 2.0, "rock": 2.0, "dark": 2.0, "steel": 2.0,
        "poison": 0.5, "flying": 0.5, "psychic": 0.5, "bug": 0.5,
        "ghost": 0.0,
    },
    "flying": {
        "fighting": 2.0, "bug": 2.0, "grass": 2.0,
        "rock": 0.5, "steel": 0.5, "electric": 0.5,
    },
    "poison": {
        "grass": 2.0,
        "poison": 0.5, "ground": 0.5, "rock": 0.5, "ghost": 0.5,
        "steel": 0.0,
    },
    "ground": {
        "poison": 2.0, "rock": 2.0, "steel": 2.0, "fire": 2.0, "electric": 2.0,
        "bug": 0.5, "grass": 0.5,
        "flying": 0.0,
    },
    "rock": {
        "flying": 2.0, "bug": 2.0, "fire": 2.0, "ice": 2.0,
        "fighting": 0.5, "ground": 0.5, "steel": 0.5,
    },
    "bug": {
        "grass": 2.0, "psychic": 2.0, "dark": 2.0,
        "fighting": 0.5, "flying": 0.5, "poison": 0.5, "ghost": 0.5,
        "steel": 0.5, "fire": 0.5,
    },
    "ghost": {
        "ghost": 2.0, "psychic": 2.0,
        "normal": 0.0,
        "steel": 0.5, "dark": 0.5,
    },
    "steel": {
        "rock": 2.0, "ice": 2.0,
        "steel": 0.5, "fire": 0.5, "water": 0.5, "electric": 0.5,
    },
    "fire": {
        "bug": 2.0, "steel": 2.0, "grass": 2.0, "ice": 2.0,
        "rock": 0.5, "fire": 0.5, "water": 0.5, "dragon": 0.5,
    },
    "water": {
        "ground": 2.0, "rock": 2.0, "fire": 2.0,
        "water": 0.5, "grass": 0.5, "dragon": 0.5,
    },
    "grass": {
        "ground": 2.0, "rock": 2.0, "water": 2.0,
        "flying": 0.5, "poison": 0.5, "bug": 0.5, "steel": 0.5,
        "fire": 0.5, "grass": 0.5, "dragon": 0.5,
    },
    "electric": {
        "flying": 2.0, "water": 2.0,
        "grass": 0.5, "electric": 0.5, "dragon": 0.5,
        "ground": 0.0,
    },
    "psychic": {
        "fighting": 2.0, "poison": 2.0,
        "steel": 0.5, "psychic": 0.5,
        "dark": 0.0,
    },
    "ice": {
        "flying": 2.0, "ground": 2.0, "grass": 2.0, "dragon": 2.0,
        "steel": 0.5, "fire": 0.5, "water": 0.5, "ice": 0.5,
    },
    "dragon": {
        "dragon": 2.0,
        "steel": 0.5,
    },
    "dark": {
        "ghost": 2.0, "psychic": 2.0,
        "fighting": 0.5, "dark": 0.5, "steel": 0.5,
    },
}


def effectiveness(move_type: str, defender_types: tuple[str, ...]) -> float:
    """Multiplied effectiveness of a move type against a (mono- or dual-)
    typed defender. Unknown types fall back to neutral.

    Examples:
      >>> effectiveness("electric", ("water",))      # super-effective
      2.0
      >>> effectiveness("electric", ("water", "ground"))  # flying...nope, ground 0
      0.0
      >>> effectiveness("normal", ("ghost",))        # immune
      0.0
      >>> effectiveness("fire", ("water", "rock"))   # 0.5 × 0.5
      0.25
    """
    move_type = move_type.lower()
    row = _CHART.get(move_type)
    if row is None:
        return 1.0
    mult = 1.0
    for defender in defender_types:
        mult *= row.get(defender.lower(), 1.0)
    return mult


def label_for(mult: float) -> str:
    """User-facing string for a final multiplier."""
    if mult == 0.0:
        return "It had no effect…"
    if mult >= 2.0:
        return "It's super effective!"
    if 0.0 < mult < 1.0:
        return "It's not very effective…"
    return ""

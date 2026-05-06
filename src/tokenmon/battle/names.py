"""Trainer name pools — title + first name combinations.

Pools are intentionally small (~20 each = 400 unique combos) and
flavored after Gen-3-style trainer classes. The seed-based pick is
deterministic so the trainer's identity stays stable across renders.
"""
from __future__ import annotations

import random

# Trainer titles — Gen-3 trainer-class flavored. Some kept as one-word
# (Lass, Youngster) to vary length.
TITLES: tuple[str, ...] = (
    "Bug Catcher",
    "Lass",
    "Youngster",
    "Hiker",
    "Fisherman",
    "Picnicker",
    "Camper",
    "Bird Keeper",
    "Sailor",
    "Engineer",
    "Beauty",
    "Gentleman",
    "Schoolkid",
    "Black Belt",
    "PokéManiac",
    "Psychic",
    "Channeler",
    "Tamer",
    "Burglar",
    "Rocker",
)

# First-name pool. Mix of short common names and Gen-3-trainer-y names.
NAMES: tuple[str, ...] = (
    "Tobi", "Mira", "Henry", "Lena", "Otto", "Ines", "Nora", "Felix",
    "Anya", "Karl", "Greta", "Bruno", "Lina", "Theo", "Elsa", "Joran",
    "Pia", "Magnus", "Romy", "Ben", "Klara", "Ulrich", "Kira", "Wolf",
    "Hannes", "Ida", "Walter", "Ruth", "Egon", "Annika",
)


def random_trainer_id(rng: random.Random) -> tuple[str, str]:
    """Return ``(title, name)`` deterministically chosen from the pools."""
    return rng.choice(TITLES), rng.choice(NAMES)

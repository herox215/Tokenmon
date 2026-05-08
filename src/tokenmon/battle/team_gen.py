"""Deterministic trainer-team generation.

Given a seed, a difficulty, and the player's level, produce the trainer's
1–3 Pokémon: species, level, IVs, nature, moveset. Same seed always
produces the same team — important for "reload preview pane" UX so the
player sees the same trainer every render.

Move selection requires the species learnset (from PokeAPI). This module
only takes the resolved move keys; the caller is responsible for fetching
learnsets via ``pokedex_remote.get_learnset``. That separation keeps
team_gen pure (no I/O).
"""
from __future__ import annotations

import random
from typing import Callable

from tokenmon import pokemon as pkmn

from .models import Difficulty, TrainerMon

# Difficulty → (team_size, level_delta_range, reward_mult)
DIFFICULTY_PROFILES: dict[Difficulty, dict] = {
    "easy":   {"size": 1, "delta_min": -7, "delta_max": -3, "reward_mult": 1.0},
    "medium": {"size": 2, "delta_min": -5, "delta_max": -1, "reward_mult": 1.5},
    "hard":   {"size": 3, "delta_min": -3, "delta_max":  1, "reward_mult": 2.0},
}

# Difficulty roll weights for normal players.
DIFFICULTY_WEIGHTS: dict[Difficulty, int] = {"easy": 60, "medium": 30, "hard": 10}

# Players below this level only see Easy trainers.
EASY_ONLY_LEVEL = 8

# Min/max level a trainer's Pokémon can have. Floors keep level-1
# trainers from happening when the player is L4.
TRAINER_MIN_LEVEL = 2
TRAINER_MAX_LEVEL = 100


def pick_difficulty(player_level: int, rng: random.Random) -> Difficulty:
    """Roll a difficulty bucket. Below ``EASY_ONLY_LEVEL`` always Easy
    so newbies don't get curb-stomped."""
    if player_level < EASY_ONLY_LEVEL:
        return "easy"
    weights = DIFFICULTY_WEIGHTS
    total = sum(weights.values())
    n = rng.randint(1, total)
    acc = 0
    for diff, w in weights.items():
        acc += w
        if n <= acc:
            return diff
    return "easy"  # unreachable; appease type checker


def _pick_species(
    rng: random.Random,
    *,
    avoid: set[int] | None = None,
) -> int:
    """Pick a Gen-1 base-form species the trainer might own.

    Uses ``rng`` directly (not the module-level ``_RNG`` inside
    ``pkmn.random_species``) so team generation is deterministic per
    seed. ``avoid`` is typically ``{player_active_species}`` so the
    trainer doesn't mirror.
    """
    avoid = avoid or set()
    pool = [d for d in pkmn._BASE_IDS if d not in avoid]
    if not pool:
        pool = list(pkmn._BASE_IDS)
    return rng.choice(pool)


def _pick_level(
    rng: random.Random,
    player_level: int,
    profile: dict,
) -> int:
    delta = rng.randint(profile["delta_min"], profile["delta_max"])
    lv = player_level + delta
    return max(TRAINER_MIN_LEVEL, min(TRAINER_MAX_LEVEL, lv))


def _roll_ivs(rng: random.Random) -> tuple[int, int, int, int, int, int]:
    return tuple(rng.randint(0, 31) for _ in range(6))  # type: ignore[return-value]


def _pick_moves(
    rng: random.Random,
    learnset: list[tuple[int, str]],
    species_level: int,
    *,
    fallback: str = "tackle",
) -> tuple[str, ...]:
    """Pick up to 4 moves the species would know at ``species_level``.

    ``learnset`` is the species' level-up learnset (level, move_key).
    Picks the four highest-level moves at or below ``species_level``.
    Falls back to a single ``fallback`` move if the learnset is empty
    (PokeAPI failure).
    """
    eligible = [(lv, key) for lv, key in learnset if lv <= species_level]
    if not eligible:
        return (fallback,)
    # Sort highest-level first, take 4. Reverse so the latest learn comes first.
    eligible.sort(key=lambda x: x[0], reverse=True)
    chosen: list[str] = []
    seen: set[str] = set()
    for _, key in eligible:
        if key in seen:
            continue
        chosen.append(key)
        seen.add(key)
        if len(chosen) == 4:
            break
    return tuple(chosen)


def generate_wild_mon(
    *,
    encounter,
    learnset_lookup: Callable[[int], list[tuple[int, str]]],
) -> TrainerMon:
    """Build a one-mon "team" from a stored ``Encounter``. Used by the
    battle pane to translate a wild spawn into the same TrainerMon shape
    the engine consumes for trainer fights.

    Determinism: seeds the move-pick RNG off the encounter id so re-builds
    are stable. Species/level/IVs/nature are taken straight from the row.
    Move keys come from the row when baked at spawn; falls back to the
    learnset lookup when an older row has empty move_keys.
    """
    if encounter.move_keys:
        move_keys = tuple(encounter.move_keys)
    else:
        rng = random.Random(int(encounter.id))
        learnset = learnset_lookup(encounter.species_dex_id) or []
        move_keys = _pick_moves(rng, learnset, int(encounter.level))
    return TrainerMon(
        species_dex_id=int(encounter.species_dex_id),
        level=int(encounter.level),
        nature=encounter.nature,
        ivs=tuple(encounter.ivs),  # type: ignore[arg-type]
        move_keys=move_keys,
    )


def generate_trainer_team(
    *,
    seed: int,
    difficulty: Difficulty,
    player_level: int,
    player_active_species: int | None = None,
    learnset_lookup: Callable[[int], list[tuple[int, str]]],
) -> list[TrainerMon]:
    """Build the trainer's 1-3 Pokémon. Pure given the lookup callable.

    ``learnset_lookup`` is injected so this module stays pure. The
    caller passes ``pokedex_remote.get_learnset`` (or a stub in tests).
    """
    rng = random.Random(seed)
    profile = DIFFICULTY_PROFILES[difficulty]
    avoid = {player_active_species} if player_active_species is not None else None

    team: list[TrainerMon] = []
    for slot in range(profile["size"]):
        dex_id = _pick_species(rng, avoid=avoid)
        level = _pick_level(rng, player_level, profile)
        # ``pkmn.random_nature`` uses the module RNG; we need our own
        # seeded one for deterministic teams. NATURES entries are dicts
        # with a "name" key.
        nature = rng.choice(pkmn.NATURES)["name"]
        ivs = _roll_ivs(rng)
        learnset = learnset_lookup(dex_id) or []
        move_keys = _pick_moves(rng, learnset, level)
        team.append(TrainerMon(
            species_dex_id=dex_id,
            level=level,
            nature=nature,
            ivs=ivs,
            move_keys=move_keys,
        ))
    return team

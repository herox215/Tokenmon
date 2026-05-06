"""Trainer-battle spawning. Mirrors ``encounter.maybe_spawn`` but with
its own cooldown, lower probability (~⅓ of wild encounters), and team
generation via ``battle.team_gen``.

Side-effect surface: hits storage to gate on cooldown + pending state,
and inserts the trainer + team rows on spawn. Caller (menubar) handles
notifications + UI surfacing.
"""
from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from tokenmon import box, learnsets_remote, pokemon
from tokenmon.battle import names as battle_names
from tokenmon.battle.team_gen import (
    DIFFICULTY_PROFILES,
    generate_trainer_team,
    pick_difficulty,
)
from tokenmon.storage import (
    DB_PATH,
    Trainer,
    get_pending_encounter,
    get_pending_trainer,
    insert_trainer,
    latest_trainer_spawn_ts,
    query_xp_for_pokemon,
)

log = logging.getLogger("tokenmon.trainer")

# Tunables — see plan.
SPAWN_TOKEN_SCALE = 2000
SPAWN_TOKEN_CAP = 2000
SPAWN_COOLDOWN_SECONDS = 10 * 60     # twice the wild-encounter cooldown
SPAWN_MIN_OUTPUT = 50
SPAWN_PROBABILITY_DIVISOR = 3.0      # ~⅓ of the wild-encounter rate

_RNG = random.SystemRandom()


def _last_spawn_seconds_ago(path: Path = DB_PATH) -> float:
    ts = latest_trainer_spawn_ts(path)
    if ts is None:
        return float("inf")
    return (datetime.now(timezone.utc) - ts).total_seconds()


def spawn_probability(output_tokens: int) -> float:
    """Probability that a single request rolls a trainer spawn. ⅓ of
    the equivalent wild-encounter rate so trainers feel less frequent."""
    if output_tokens < SPAWN_MIN_OUTPUT:
        return 0.0
    clamped = min(output_tokens, SPAWN_TOKEN_CAP)
    base = 1.0 - math.exp(-clamped / SPAWN_TOKEN_SCALE)
    return base / SPAWN_PROBABILITY_DIVISOR


def _player_active_level(path: Path = DB_PATH) -> int:
    try:
        active = box.get_active_pokemon(path)
        if active is None:
            return 5
        xp = query_xp_for_pokemon(active.id, path=path)
        growth = pokemon.growth_rate_of(active.species_dex_id)
        level, _, _ = pokemon.level_from_xp(xp, growth)
        return level
    except Exception:
        log.exception("active level lookup failed")
        return 5


def _player_active_species(path: Path = DB_PATH) -> int | None:
    try:
        active = box.get_active_pokemon(path)
        return active.species_dex_id if active is not None else None
    except Exception:
        return None


def maybe_spawn(
    output_tokens: int = 0,
    *,
    force: bool = False,
    path: Path = DB_PATH,
) -> Trainer | None:
    """Roll a trainer spawn for one request. Returns the persisted
    Trainer or None if any guard rejected.

    Guards:
    - A trainer is already pending → never spawn another.
    - A wild encounter is pending → suppressed (player should resolve
      one before facing the other).
    - Cooldown active → suppressed.
    - Probability roll fails → no spawn.

    ``force=True`` bypasses the probability roll (still respects pending
    + cooldown). Used by debug helpers; production callers leave it off.
    """
    if get_pending_trainer(path) is not None:
        return None
    if get_pending_encounter(path) is not None:
        return None
    if _last_spawn_seconds_ago(path) < SPAWN_COOLDOWN_SECONDS:
        return None
    if not force:
        prob = spawn_probability(output_tokens)
        if prob <= 0.0 or _RNG.random() >= prob:
            return None

    seed = _RNG.randint(0, 2**31 - 1)
    rng = random.Random(seed)

    player_level = _player_active_level(path)
    difficulty = pick_difficulty(player_level, rng)
    title, name = battle_names.random_trainer_id(rng)

    avoid_species = _player_active_species(path)
    team = generate_trainer_team(
        seed=seed, difficulty=difficulty, player_level=player_level,
        player_active_species=avoid_species,
        learnset_lookup=learnsets_remote.get_learnset,
    )

    if not team:
        log.warning("trainer team generation returned empty; skipping spawn")
        return None

    trainer_id = insert_trainer(
        name=name,
        title=title,
        difficulty=difficulty,
        seed=seed,
        team=[
            {
                "species_dex_id": m.species_dex_id,
                "level": m.level,
                "nature": m.nature,
                "ivs": m.ivs,
                "move_keys": m.move_keys,
            }
            for m in team
        ],
        path=path,
    )
    return Trainer(
        id=trainer_id,
        spawned_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        name=name, title=title, difficulty=difficulty,
        seed=seed, resolved=None, resolved_utc=None,
        money_reward=None, xp_reward=None,
    )

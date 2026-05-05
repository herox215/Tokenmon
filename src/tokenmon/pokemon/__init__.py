"""Pokemon module — split into data / sprites / level / rng submodules.

This ``__init__`` re-exports the historical public surface so callers
(``from tokenmon.pokemon import X``) keep working unchanged.
"""
from __future__ import annotations

# Data tables (re-exported for callers that read them directly).
from .data import (
    ALL_NAMES,
    EVOLUTIONS,
    GEN1_BASE_FORMS,
    GEN1_CATCH_RATES,
    GEN1_DAY_ONLY,
    GEN1_GENDERLESS,
    GEN1_NIGHT_ONLY,
    GROWTH_RATES,
    NATURES,
    CHARACTERISTICS,
    _BASE_IDS,
    _LINE_OF,
)

# Sprite cache + fetcher.
from .sprites import (
    SHINY_SPRITE_DIR,
    SHINY_SPRITE_URL_TMPL,
    SPRITE_DIR,
    SPRITE_URL_TMPL,
    ensure_sprite,
    sprite_path,
)

# Level / XP math + evolution-line resolution.
from .level import (
    MAX_LEVEL,
    catch_rate_of,
    current_stage_of,
    evolution_chain,
    growth_rate_of,
    level_from_xp,
    line_of,
    name_of,
    stage_thresholds,
    unlocked_stages_of,
    xp_for_level,
)

# Random rolls + seeded picks + time windows.
from .rng import (
    DAY_HOUR_END,
    DAY_HOUR_START,
    SHINY_RATE,
    can_spawn_now,
    current_time_window,
    gender_symbol,
    is_genderless,
    pick_for_today,
    random_characteristic,
    random_nature,
    random_species,
    roll_gender,
    roll_shiny,
    seeded_characteristic,
    seeded_nature,
    seeded_species,
)

__all__ = [
    # data
    "ALL_NAMES", "EVOLUTIONS", "GEN1_BASE_FORMS", "GEN1_CATCH_RATES",
    "GEN1_DAY_ONLY", "GEN1_GENDERLESS", "GEN1_NIGHT_ONLY",
    "GROWTH_RATES", "NATURES", "CHARACTERISTICS",
    # sprites
    "SHINY_SPRITE_DIR", "SHINY_SPRITE_URL_TMPL",
    "SPRITE_DIR", "SPRITE_URL_TMPL",
    "ensure_sprite", "sprite_path",
    # level
    "MAX_LEVEL", "catch_rate_of", "current_stage_of",
    "evolution_chain", "growth_rate_of", "level_from_xp",
    "line_of", "name_of", "stage_thresholds",
    "unlocked_stages_of", "xp_for_level",
    # rng
    "DAY_HOUR_END", "DAY_HOUR_START", "SHINY_RATE",
    "can_spawn_now", "current_time_window",
    "gender_symbol", "is_genderless",
    "pick_for_today", "random_characteristic", "random_nature",
    "random_species", "roll_gender", "roll_shiny",
    "seeded_characteristic", "seeded_nature", "seeded_species",
]

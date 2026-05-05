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
    GEN1_BASE_STATS,
    GEN1_CATCH_RATES,
    GEN1_DAY_ONLY,
    GEN1_GENDERLESS,
    GEN1_NIGHT_ONLY,
    GEN1_TYPES,
    GROWTH_RATES,
    NATURES,
    CHARACTERISTICS,
    STONE_EVOLUTIONS,
    TYPE_COLORS,
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
    display_name,
    evolution_chain,
    growth_rate_of,
    level_from_xp,
    line_of,
    name_of,
    species_seen_through,
    stage_thresholds,
    stone_evolution_for,
    types_of,
    unlocked_stages_of,
    xp_for_level,
)

# Per-instance stats: IVs, final-stat formula, characteristic-from-IV.
from .stats import (
    BASE_STAT_MAX,
    IV_MAX,
    RADAR_SCALE_MAX,
    STAT_LABELS,
    STAT_ORDER,
    base_stats_of,
    characteristic_for_ivs,
    final_stat,
    final_stats,
    ivs_from_id,
    nature_multipliers,
    roll_ivs,
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
    seeded_ivs,
    seeded_nature,
    seeded_species,
)

__all__ = [
    # data
    "ALL_NAMES", "EVOLUTIONS", "GEN1_BASE_FORMS", "GEN1_BASE_STATS",
    "GEN1_CATCH_RATES", "GEN1_DAY_ONLY", "GEN1_GENDERLESS", "GEN1_NIGHT_ONLY",
    "GEN1_TYPES", "GROWTH_RATES", "NATURES", "CHARACTERISTICS",
    "STONE_EVOLUTIONS", "TYPE_COLORS",
    # sprites
    "SHINY_SPRITE_DIR", "SHINY_SPRITE_URL_TMPL",
    "SPRITE_DIR", "SPRITE_URL_TMPL",
    "ensure_sprite", "sprite_path",
    # level
    "MAX_LEVEL", "catch_rate_of", "current_stage_of", "display_name",
    "evolution_chain", "growth_rate_of", "level_from_xp",
    "line_of", "name_of", "species_seen_through", "stage_thresholds",
    "stone_evolution_for", "types_of", "unlocked_stages_of", "xp_for_level",
    # stats
    "BASE_STAT_MAX", "IV_MAX", "RADAR_SCALE_MAX", "STAT_LABELS", "STAT_ORDER",
    "base_stats_of", "characteristic_for_ivs", "final_stat", "final_stats",
    "ivs_from_id", "nature_multipliers", "roll_ivs",
    # rng
    "DAY_HOUR_END", "DAY_HOUR_START", "SHINY_RATE",
    "can_spawn_now", "current_time_window",
    "gender_symbol", "is_genderless",
    "pick_for_today", "random_characteristic", "random_nature",
    "random_species", "roll_gender", "roll_shiny",
    "seeded_characteristic", "seeded_ivs", "seeded_nature", "seeded_species",
]

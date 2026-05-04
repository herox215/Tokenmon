"""Daily Pokemon picker.

Picks one Gen-1 base-form Pokemon per calendar day (deterministic). Animated
sprites are downloaded from PokeAPI's sprite mirror and cached locally.

"Base form" = no pre-evolution exists in Gen 1. So Bulbasaur, Pikachu,
Eevee yes; Ivysaur, Raichu, Vaporeon no.
"""

from __future__ import annotations

import hashlib
import logging
import random
import urllib.request
from datetime import date
from pathlib import Path

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.pokemon")

SPRITE_DIR = DB_DIR / "sprites"
SPRITE_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/versions/generation-v/black-white/animated/{id}.gif"
)

# Gen-1 base forms: Pokemon with no pre-evolution that exists in Gen 1.
# (Cleffa, Igglybuff, Tyrogue etc. are Gen-2, so Clefairy/Jigglypuff/etc. are bases here.)
GEN1_BASE_FORMS: dict[int, str] = {
    1: "Bulbasaur",   4: "Charmander", 7: "Squirtle",  10: "Caterpie",
    13: "Weedle",    16: "Pidgey",    19: "Rattata",  21: "Spearow",
    23: "Ekans",     25: "Pikachu",   27: "Sandshrew",29: "Nidoran♀",
    32: "Nidoran♂",  35: "Clefairy",  37: "Vulpix",   39: "Jigglypuff",
    41: "Zubat",     43: "Oddish",    46: "Paras",    48: "Venonat",
    50: "Diglett",   52: "Meowth",    54: "Psyduck",  56: "Mankey",
    58: "Growlithe", 60: "Poliwag",   63: "Abra",     66: "Machop",
    69: "Bellsprout",72: "Tentacool", 74: "Geodude",  77: "Ponyta",
    79: "Slowpoke",  81: "Magnemite", 83: "Farfetch'd", 84: "Doduo",
    86: "Seel",      88: "Grimer",    90: "Shellder", 92: "Gastly",
    95: "Onix",      96: "Drowzee",   98: "Krabby",   100: "Voltorb",
    102: "Exeggcute",104: "Cubone",   106: "Hitmonlee",107: "Hitmonchan",
    108: "Lickitung",109: "Koffing",  111: "Rhyhorn", 113: "Chansey",
    114: "Tangela",  115: "Kangaskhan",116: "Horsea", 118: "Goldeen",
    120: "Staryu",   122: "Mr. Mime", 123: "Scyther", 124: "Jynx",
    125: "Electabuzz",126: "Magmar",  127: "Pinsir",  128: "Tauros",
    129: "Magikarp", 131: "Lapras",   132: "Ditto",   133: "Eevee",
    137: "Porygon",  138: "Omanyte",  140: "Kabuto",  142: "Aerodactyl",
    143: "Snorlax",  144: "Articuno", 145: "Zapdos",  146: "Moltres",
    147: "Dratini",  150: "Mewtwo",   151: "Mew",
}

_BASE_IDS: list[int] = sorted(GEN1_BASE_FORMS.keys())


# --- XP / level system -----------------------------------------------------

# Growth rate per Gen-1 base form (source: Bulbapedia).
# One of: "fast", "medium_fast", "medium_slow", "slow", "erratic", "fluctuating".
GROWTH_RATES: dict[int, str] = {
    1: "medium_slow",   4: "medium_slow",  7: "medium_slow",  10: "medium_fast",
    13: "medium_fast", 16: "medium_slow", 19: "medium_fast", 21: "medium_fast",
    23: "medium_fast", 25: "medium_fast", 27: "medium_fast", 29: "medium_slow",
    32: "medium_slow", 35: "fast",        37: "medium_fast", 39: "fast",
    41: "medium_fast", 43: "medium_slow", 46: "medium_fast", 48: "medium_fast",
    50: "medium_fast", 52: "medium_fast", 54: "medium_fast", 56: "medium_fast",
    58: "slow",        60: "medium_slow", 63: "medium_slow", 66: "medium_slow",
    69: "medium_slow", 72: "slow",        74: "medium_slow", 77: "medium_fast",
    79: "medium_fast", 81: "medium_fast", 83: "medium_fast", 84: "medium_fast",
    86: "medium_fast", 88: "medium_fast", 90: "slow",        92: "medium_slow",
    95: "medium_fast", 96: "medium_fast", 98: "medium_fast", 100: "medium_fast",
    102: "slow",       104: "medium_fast", 106: "medium_fast", 107: "medium_fast",
    108: "medium_fast", 109: "medium_fast", 111: "slow",      113: "fast",
    114: "medium_fast", 115: "medium_fast", 116: "medium_fast", 118: "medium_fast",
    120: "slow",       122: "medium_fast", 123: "medium_fast", 124: "medium_fast",
    125: "medium_fast", 126: "medium_fast", 127: "slow",      128: "slow",
    129: "slow",       131: "slow",       132: "medium_fast", 133: "medium_fast",
    137: "medium_fast", 138: "medium_fast", 140: "medium_fast", 142: "slow",
    143: "slow",       144: "slow",       145: "slow",       146: "slow",
    147: "slow",       150: "slow",       151: "medium_slow",
}

MAX_LEVEL = 100


def xp_for_level(level: int, rate: str) -> int:
    """Total XP needed to BE at `level` (i.e. XP at the start of this level).
    Level 1 = 0 XP. Formulas per Bulbapedia / Pokémon main-series games."""
    if level <= 1:
        return 0
    n = level
    if rate == "fast":
        return (4 * n ** 3) // 5
    if rate == "medium_fast":
        return n ** 3
    if rate == "medium_slow":
        return max(0, (6 * n ** 3) // 5 - 15 * n ** 2 + 100 * n - 140)
    if rate == "slow":
        return (5 * n ** 3) // 4
    if rate == "erratic":
        if n <= 50:
            return (n ** 3 * (100 - n)) // 50
        if n <= 68:
            return (n ** 3 * (150 - n)) // 100
        if n <= 98:
            return (n ** 3 * ((1911 - 10 * n) // 3)) // 500
        return (n ** 3 * (160 - n)) // 100
    if rate == "fluctuating":
        if n <= 15:
            return (n ** 3 * (((n + 1) // 3) + 24)) // 50
        if n <= 36:
            return (n ** 3 * (n + 14)) // 50
        return (n ** 3 * ((n // 2) + 32)) // 50
    raise ValueError(f"unknown growth rate: {rate}")


def level_from_xp(xp: int, rate: str) -> tuple[int, int, int]:
    """Returns (level, xp_into_level, xp_to_next_level).
    At max level, xp_to_next_level == 0 and xp_into_level == 0."""
    if xp <= 0:
        return 1, 0, xp_for_level(2, rate)
    # Linear scan is fine — only 100 levels.
    for lvl in range(1, MAX_LEVEL):
        next_xp = xp_for_level(lvl + 1, rate)
        if xp < next_xp:
            cur_xp = xp_for_level(lvl, rate)
            return lvl, xp - cur_xp, next_xp - cur_xp
    return MAX_LEVEL, 0, 0


def growth_rate_of(dex_id: int) -> str:
    return GROWTH_RATES.get(dex_id, "medium_fast")


def pick_for_today(today: date | None = None) -> int:
    """Deterministic pick for the given calendar date (defaults to today)."""
    today = today or date.today()
    h = int(hashlib.sha256(today.isoformat().encode()).hexdigest(), 16)
    return _BASE_IDS[h % len(_BASE_IDS)]


def pick_random() -> int:
    """Random pick (for the 'reroll' debug button)."""
    return random.choice(_BASE_IDS)


def name_of(dex_id: int) -> str:
    return GEN1_BASE_FORMS.get(dex_id, f"#{dex_id}")


def sprite_path(dex_id: int) -> Path:
    return SPRITE_DIR / f"{dex_id}.gif"


def ensure_sprite(dex_id: int, timeout: float = 5.0) -> Path | None:
    """Download the animated sprite if not already cached. Returns path or None."""
    p = sprite_path(dex_id)
    if p.exists() and p.stat().st_size > 0:
        return p
    SPRITE_DIR.mkdir(parents=True, exist_ok=True)
    url = SPRITE_URL_TMPL.format(id=dex_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tokenmon/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        p.write_bytes(data)
        return p
    except Exception as exc:
        log.warning("sprite download failed for #%d: %s", dex_id, exc)
        if p.exists():
            p.unlink(missing_ok=True)
        return None

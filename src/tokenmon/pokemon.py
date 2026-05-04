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

# All Gen-1 Pokemon names — bases AND evolved forms. We only need the entries
# that appear in our 79 evolution lines (which is most of Gen 1).
ALL_NAMES: dict[int, str] = {
    1: "Bulbasaur",    2: "Ivysaur",     3: "Venusaur",
    4: "Charmander",   5: "Charmeleon",  6: "Charizard",
    7: "Squirtle",     8: "Wartortle",   9: "Blastoise",
    10: "Caterpie",   11: "Metapod",    12: "Butterfree",
    13: "Weedle",     14: "Kakuna",     15: "Beedrill",
    16: "Pidgey",     17: "Pidgeotto",  18: "Pidgeot",
    19: "Rattata",    20: "Raticate",
    21: "Spearow",    22: "Fearow",
    23: "Ekans",      24: "Arbok",
    25: "Pikachu",    26: "Raichu",
    27: "Sandshrew",  28: "Sandslash",
    29: "Nidoran♀",   30: "Nidorina",   31: "Nidoqueen",
    32: "Nidoran♂",   33: "Nidorino",   34: "Nidoking",
    35: "Clefairy",   36: "Clefable",
    37: "Vulpix",     38: "Ninetales",
    39: "Jigglypuff", 40: "Wigglytuff",
    41: "Zubat",      42: "Golbat",
    43: "Oddish",     44: "Gloom",      45: "Vileplume",
    46: "Paras",      47: "Parasect",
    48: "Venonat",    49: "Venomoth",
    50: "Diglett",    51: "Dugtrio",
    52: "Meowth",     53: "Persian",
    54: "Psyduck",    55: "Golduck",
    56: "Mankey",     57: "Primeape",
    58: "Growlithe",  59: "Arcanine",
    60: "Poliwag",    61: "Poliwhirl",  62: "Poliwrath",
    63: "Abra",       64: "Kadabra",    65: "Alakazam",
    66: "Machop",     67: "Machoke",    68: "Machamp",
    69: "Bellsprout", 70: "Weepinbell", 71: "Victreebel",
    72: "Tentacool",  73: "Tentacruel",
    74: "Geodude",    75: "Graveler",   76: "Golem",
    77: "Ponyta",     78: "Rapidash",
    79: "Slowpoke",   80: "Slowbro",
    81: "Magnemite",  82: "Magneton",
    83: "Farfetch'd",
    84: "Doduo",      85: "Dodrio",
    86: "Seel",       87: "Dewgong",
    88: "Grimer",     89: "Muk",
    90: "Shellder",   91: "Cloyster",
    92: "Gastly",     93: "Haunter",    94: "Gengar",
    95: "Onix",
    96: "Drowzee",    97: "Hypno",
    98: "Krabby",     99: "Kingler",
    100: "Voltorb",   101: "Electrode",
    102: "Exeggcute", 103: "Exeggutor",
    104: "Cubone",    105: "Marowak",
    106: "Hitmonlee",
    107: "Hitmonchan",
    108: "Lickitung",
    109: "Koffing",   110: "Weezing",
    111: "Rhyhorn",   112: "Rhydon",
    113: "Chansey",
    114: "Tangela",
    115: "Kangaskhan",
    116: "Horsea",    117: "Seadra",
    118: "Goldeen",   119: "Seaking",
    120: "Staryu",    121: "Starmie",
    122: "Mr. Mime",
    123: "Scyther",
    124: "Jynx",
    125: "Electabuzz",
    126: "Magmar",
    127: "Pinsir",
    128: "Tauros",
    129: "Magikarp",  130: "Gyarados",
    131: "Lapras",
    132: "Ditto",
    133: "Eevee",     134: "Vaporeon",
    137: "Porygon",
    138: "Omanyte",   139: "Omastar",
    140: "Kabuto",    141: "Kabutops",
    142: "Aerodactyl",
    143: "Snorlax",
    144: "Articuno",
    145: "Zapdos",
    146: "Moltres",
    147: "Dratini",   148: "Dragonair", 149: "Dragonite",
    150: "Mewtwo",
    151: "Mew",
}

# Backwards-compat alias used by tests / older imports.
GEN1_BASE_FORMS: dict[int, str] = {}  # populated below


# Evolution chains: {base_dex_id: [(level_threshold, evolved_dex_id), ...]}.
# Level-up evolutions use the in-game level. Stone/trade/friendship evolutions
# are mapped to level 30 by convention (gen 1 stones are typically obtainable
# mid-game, so this lines up with when a player would actually evolve them).
# Branched evolutions (only Eevee in gen 1) default to one branch — Vaporeon —
# to keep the model simple.
_STONE_TRADE_LEVEL = 30

EVOLUTIONS: dict[int, list[tuple[int, int]]] = {
    1:   [(16, 2), (32, 3)],                  # Bulbasaur → Ivysaur → Venusaur
    4:   [(16, 5), (36, 6)],                  # Charmander line
    7:   [(16, 8), (36, 9)],                  # Squirtle line
    10:  [(7, 11), (10, 12)],                 # Caterpie line
    13:  [(7, 14), (10, 15)],                 # Weedle line
    16:  [(18, 17), (36, 18)],                # Pidgey line
    19:  [(20, 20)],                          # Rattata
    21:  [(20, 22)],                          # Spearow
    23:  [(22, 24)],                          # Ekans
    25:  [(_STONE_TRADE_LEVEL, 26)],          # Pikachu (Thunder Stone)
    27:  [(22, 28)],                          # Sandshrew
    29:  [(16, 30), (_STONE_TRADE_LEVEL, 31)],# Nidoran♀ → Nidorina → Nidoqueen (Moon Stone)
    32:  [(16, 33), (_STONE_TRADE_LEVEL, 34)],# Nidoran♂ → Nidorino → Nidoking (Moon Stone)
    35:  [(_STONE_TRADE_LEVEL, 36)],          # Clefairy (Moon Stone)
    37:  [(_STONE_TRADE_LEVEL, 38)],          # Vulpix (Fire Stone)
    39:  [(_STONE_TRADE_LEVEL, 40)],          # Jigglypuff (Moon Stone)
    41:  [(22, 42)],                          # Zubat
    43:  [(21, 44), (_STONE_TRADE_LEVEL, 45)],# Oddish → Gloom → Vileplume (Leaf Stone)
    46:  [(24, 47)],                          # Paras
    48:  [(31, 49)],                          # Venonat
    50:  [(26, 51)],                          # Diglett
    52:  [(28, 53)],                          # Meowth
    54:  [(33, 55)],                          # Psyduck
    56:  [(28, 57)],                          # Mankey
    58:  [(_STONE_TRADE_LEVEL, 59)],          # Growlithe (Fire Stone)
    60:  [(25, 61), (_STONE_TRADE_LEVEL, 62)],# Poliwag → Poliwhirl → Poliwrath (Water Stone)
    63:  [(16, 64), (_STONE_TRADE_LEVEL, 65)],# Abra → Kadabra → Alakazam (Trade)
    66:  [(28, 67), (_STONE_TRADE_LEVEL, 68)],# Machop → Machoke → Machamp (Trade)
    69:  [(21, 70), (_STONE_TRADE_LEVEL, 71)],# Bellsprout → Weepinbell → Victreebel (Leaf Stone)
    72:  [(30, 73)],                          # Tentacool
    74:  [(25, 75), (_STONE_TRADE_LEVEL, 76)],# Geodude → Graveler → Golem (Trade)
    77:  [(40, 78)],                          # Ponyta
    79:  [(37, 80)],                          # Slowpoke
    81:  [(30, 82)],                          # Magnemite
    83:  [],                                  # Farfetch'd — no Gen-1 evolution
    84:  [(31, 85)],                          # Doduo
    86:  [(34, 87)],                          # Seel
    88:  [(38, 89)],                          # Grimer
    90:  [(_STONE_TRADE_LEVEL, 91)],          # Shellder (Water Stone)
    92:  [(25, 93), (_STONE_TRADE_LEVEL, 94)],# Gastly → Haunter → Gengar (Trade)
    95:  [],                                  # Onix
    96:  [(26, 97)],                          # Drowzee
    98:  [(28, 99)],                          # Krabby
    100: [(30, 101)],                         # Voltorb
    102: [(_STONE_TRADE_LEVEL, 103)],         # Exeggcute (Leaf Stone)
    104: [(28, 105)],                         # Cubone
    106: [],                                  # Hitmonlee
    107: [],                                  # Hitmonchan
    108: [],                                  # Lickitung
    109: [(35, 110)],                         # Koffing
    111: [(42, 112)],                         # Rhyhorn
    113: [],                                  # Chansey
    114: [],                                  # Tangela
    115: [],                                  # Kangaskhan
    116: [(32, 117)],                         # Horsea
    118: [(33, 119)],                         # Goldeen
    120: [(_STONE_TRADE_LEVEL, 121)],         # Staryu (Water Stone)
    122: [],                                  # Mr. Mime
    123: [],                                  # Scyther
    124: [],                                  # Jynx
    125: [],                                  # Electabuzz
    126: [],                                  # Magmar
    127: [],                                  # Pinsir
    128: [],                                  # Tauros
    129: [(20, 130)],                         # Magikarp → Gyarados
    131: [],                                  # Lapras
    132: [],                                  # Ditto
    133: [(_STONE_TRADE_LEVEL, 134)],         # Eevee → Vaporeon (chose Water for branch)
    137: [],                                  # Porygon
    138: [(40, 139)],                         # Omanyte
    140: [(40, 141)],                         # Kabuto
    142: [],                                  # Aerodactyl
    143: [],                                  # Snorlax
    144: [],                                  # Articuno
    145: [],                                  # Zapdos
    146: [],                                  # Moltres
    147: [(30, 148), (55, 149)],              # Dratini → Dragonair → Dragonite
    150: [],                                  # Mewtwo
    151: [],                                  # Mew
}

# Map every dex_id (base + every evolved form) back to its base_dex_id.
_LINE_OF: dict[int, int] = {}
for _base, _chain in EVOLUTIONS.items():
    GEN1_BASE_FORMS[_base] = ALL_NAMES[_base]
    _LINE_OF[_base] = _base
    for _, _evo in _chain:
        _LINE_OF[_evo] = _base

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
    """Growth rate for any dex_id in a line (resolves to the base form's rate,
    since evolution lines share a growth rate)."""
    base = line_of(dex_id)
    return GROWTH_RATES.get(base, "medium_fast")


def pick_for_today(today: date | None = None, salt: str | None = None) -> int:
    """Deterministic pick for the given calendar date.

    Salts the hash with a per-install user_salt so two people running Tokenmon
    on the same day see different Pokemon. The salt is loaded from config
    (and generated on first call there) when not passed explicitly.
    """
    today = today or date.today()
    if salt is None:
        # Local import to avoid a circular import with tokenmon.config.
        from tokenmon import config
        salt = config.get_user_salt()
    seed = f"{today.isoformat()}:{salt}".encode()
    h = int(hashlib.sha256(seed).hexdigest(), 16)
    return _BASE_IDS[h % len(_BASE_IDS)]


def pick_random() -> int:
    """Random pick (for the 'reroll' debug button)."""
    return random.choice(_BASE_IDS)


def name_of(dex_id: int) -> str:
    return ALL_NAMES.get(dex_id, f"#{dex_id}")


def line_of(dex_id: int) -> int:
    """Return the base-form dex_id for the evolution line containing dex_id.
    For an unknown dex_id, returns dex_id itself."""
    return _LINE_OF.get(dex_id, dex_id)


def evolution_chain(base_dex_id: int) -> list[int]:
    """Return [base_dex_id, stage2_dex_id, ...] in order. For Pokemon with no
    evolutions, returns [base_dex_id]."""
    chain = [base_dex_id]
    for _, evo in EVOLUTIONS.get(base_dex_id, []):
        chain.append(evo)
    return chain


def stage_thresholds(base_dex_id: int) -> list[int]:
    """Return list of level thresholds for each non-base stage in the line.
    Length = len(evolution_chain) - 1."""
    return [lvl for lvl, _ in EVOLUTIONS.get(base_dex_id, [])]


def current_stage_of(base_dex_id: int, xp: int) -> int:
    """Which dex_id should be displayed for this line at the given XP."""
    rate = GROWTH_RATES.get(base_dex_id, "medium_fast")
    level, _, _ = level_from_xp(xp, rate)
    chain = evolution_chain(base_dex_id)
    thresholds = stage_thresholds(base_dex_id)
    current = chain[0]
    for threshold, evolved in zip(thresholds, chain[1:]):
        if level >= threshold:
            current = evolved
        else:
            break
    return current


def unlocked_stages_of(base_dex_id: int, xp: int) -> list[int]:
    """All evolution stages reached so far (always includes the base)."""
    rate = GROWTH_RATES.get(base_dex_id, "medium_fast")
    level, _, _ = level_from_xp(xp, rate)
    chain = evolution_chain(base_dex_id)
    thresholds = stage_thresholds(base_dex_id)
    out = [chain[0]]
    for threshold, evolved in zip(thresholds, chain[1:]):
        if level >= threshold:
            out.append(evolved)
        else:
            break
    return out


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

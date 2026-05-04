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
    135: "Jolteon",   136: "Flareon",
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


# --- Natures & characteristics --------------------------------------------

# Reference table (Bulbapedia):
#   Hardy: neutral
#   Lonely: +Atk -Def, spicy
#   Brave: +Atk -Speed, spicy
#   Adamant: +Atk -SpAtk, spicy
#   Naughty: +Atk -SpDef, spicy
#   Bold: +Def -Atk, sour
#   Docile: neutral
#   Relaxed: +Def -Speed, sour
#   Impish: +Def -SpAtk, sour
#   Lax: +Def -SpDef, sour
#   Timid: +Speed -Atk, sweet
#   Hasty: +Speed -Def, sweet
#   Serious: neutral
#   Jolly: +Speed -SpAtk, sweet
#   Naive: +Speed -SpDef, sweet
#   Modest: +SpAtk -Atk, dry
#   Mild: +SpAtk -Def, dry
#   Quiet: +SpAtk -Speed, dry
#   Bashful: neutral
#   Rash: +SpAtk -SpDef, dry
#   Calm: +SpDef -Atk, bitter
#   Gentle: +SpDef -Def, bitter
#   Sassy: +SpDef -Speed, bitter
#   Careful: +SpDef -SpAtk, bitter
#   Quirky: neutral

NATURES: list[dict] = [
    {"name": "Hardy",   "plus_stat": None,         "minus_stat": None,         "flavor": None},
    {"name": "Lonely",  "plus_stat": "attack",     "minus_stat": "defense",    "flavor": "spicy"},
    {"name": "Brave",   "plus_stat": "attack",     "minus_stat": "speed",      "flavor": "spicy"},
    {"name": "Adamant", "plus_stat": "attack",     "minus_stat": "sp_attack",  "flavor": "spicy"},
    {"name": "Naughty", "plus_stat": "attack",     "minus_stat": "sp_defense", "flavor": "spicy"},
    {"name": "Bold",    "plus_stat": "defense",    "minus_stat": "attack",     "flavor": "sour"},
    {"name": "Docile",  "plus_stat": None,         "minus_stat": None,         "flavor": None},
    {"name": "Relaxed", "plus_stat": "defense",    "minus_stat": "speed",      "flavor": "sour"},
    {"name": "Impish",  "plus_stat": "defense",    "minus_stat": "sp_attack",  "flavor": "sour"},
    {"name": "Lax",     "plus_stat": "defense",    "minus_stat": "sp_defense", "flavor": "sour"},
    {"name": "Timid",   "plus_stat": "speed",      "minus_stat": "attack",     "flavor": "sweet"},
    {"name": "Hasty",   "plus_stat": "speed",      "minus_stat": "defense",    "flavor": "sweet"},
    {"name": "Serious", "plus_stat": None,         "minus_stat": None,         "flavor": None},
    {"name": "Jolly",   "plus_stat": "speed",      "minus_stat": "sp_attack",  "flavor": "sweet"},
    {"name": "Naive",   "plus_stat": "speed",      "minus_stat": "sp_defense", "flavor": "sweet"},
    {"name": "Modest",  "plus_stat": "sp_attack",  "minus_stat": "attack",     "flavor": "dry"},
    {"name": "Mild",    "plus_stat": "sp_attack",  "minus_stat": "defense",    "flavor": "dry"},
    {"name": "Quiet",   "plus_stat": "sp_attack",  "minus_stat": "speed",      "flavor": "dry"},
    {"name": "Bashful", "plus_stat": None,         "minus_stat": None,         "flavor": None},
    {"name": "Rash",    "plus_stat": "sp_attack",  "minus_stat": "sp_defense", "flavor": "dry"},
    {"name": "Calm",    "plus_stat": "sp_defense", "minus_stat": "attack",     "flavor": "bitter"},
    {"name": "Gentle",  "plus_stat": "sp_defense", "minus_stat": "defense",    "flavor": "bitter"},
    {"name": "Sassy",   "plus_stat": "sp_defense", "minus_stat": "speed",      "flavor": "bitter"},
    {"name": "Careful", "plus_stat": "sp_defense", "minus_stat": "sp_attack",  "flavor": "bitter"},
    {"name": "Quirky",  "plus_stat": None,         "minus_stat": None,         "flavor": None},
]

# Canonical Gen-3+ characteristic phrases (Bulbapedia). One per IV-mod-5 slot
# across the six stats (HP, Atk, Def, Speed, SpAtk, SpDef = 30 phrases).
CHARACTERISTICS: list[str] = [
    "Loves to eat",
    "Often dozes off",
    "Often scatters things",
    "Scatters things often",
    "Likes to relax",
    "Proud of its power",
    "Likes to thrash about",
    "A little quick tempered",
    "Likes to fight",
    "Quick tempered",
    "Sturdy body",
    "Capable of taking hits",
    "Highly persistent",
    "Good endurance",
    "Good perseverance",
    "Highly curious",
    "Mischievous",
    "Thoroughly cunning",
    "Often lost in thought",
    "Very finicky",
    "Strong willed",
    "Somewhat vain",
    "Strongly defiant",
    "Hates to lose",
    "Somewhat stubborn",
    "Likes to run",
    "Alert to sounds",
    "Impetuous and silly",
    "Somewhat of a clown",
    "Quick to flee",
]


_RNG = random.SystemRandom()


def random_species() -> int:
    """Uniform random pick from the gen-1 base forms."""
    return _RNG.choice(_BASE_IDS)


def random_nature() -> dict:
    """Uniform random pick from NATURES."""
    return _RNG.choice(NATURES)


def random_characteristic() -> str:
    """Uniform random pick from CHARACTERISTICS."""
    return _RNG.choice(CHARACTERISTICS)


def _seed_index(date_iso: str, salt: str, kind: str, n: int) -> int:
    h = int(hashlib.sha256(f"{date_iso}:{salt}:{kind}".encode()).hexdigest(), 16)
    return h % n


def seeded_species(date_iso: str, salt: str) -> int:
    """Deterministic species pick — same algorithm as pick_for_today, so the
    legacy 'Magnemite day' attribution stays intact when migrating historical
    days."""
    seed = f"{date_iso}:{salt}".encode()
    h = int(hashlib.sha256(seed).hexdigest(), 16)
    return _BASE_IDS[h % len(_BASE_IDS)]


def seeded_nature(date_iso: str, salt: str) -> dict:
    """SHA256(date+salt+':nature') mod len(NATURES)."""
    return NATURES[_seed_index(date_iso, salt, "nature", len(NATURES))]


def seeded_characteristic(date_iso: str, salt: str) -> str:
    """SHA256(date+salt+':characteristic') mod len(CHARACTERISTICS)."""
    return CHARACTERISTICS[_seed_index(date_iso, salt, "characteristic", len(CHARACTERISTICS))]


# --- Catch rates -----------------------------------------------------------

# Canonical Gen-1 (Red/Blue/Yellow) capture rates per dex_id (1..151).
# Source: Bulbapedia "List of Pokémon by catch rate" (Generation I column).
# Range: 3 (legendary, hardest) .. 255 (super common, trivial).
GEN1_CATCH_RATES: dict[int, int] = {
    1: 45,    2: 45,    3: 45,        # Bulbasaur line
    4: 45,    5: 45,    6: 45,        # Charmander line
    7: 45,    8: 45,    9: 45,        # Squirtle line
    10: 255,  11: 120,  12: 45,       # Caterpie line
    13: 255,  14: 120,  15: 45,       # Weedle line
    16: 255,  17: 120,  18: 45,       # Pidgey line
    19: 255,  20: 127,                # Rattata / Raticate
    21: 255,  22: 90,                 # Spearow / Fearow
    23: 255,  24: 90,                 # Ekans / Arbok
    25: 190,  26: 75,                 # Pikachu / Raichu
    27: 255,  28: 90,                 # Sandshrew / Sandslash
    29: 235,  30: 120,  31: 45,       # Nidoran♀ line
    32: 235,  33: 120,  34: 45,       # Nidoran♂ line
    35: 150,  36: 25,                 # Clefairy / Clefable
    37: 190,  38: 75,                 # Vulpix / Ninetales
    39: 170,  40: 50,                 # Jigglypuff / Wigglytuff
    41: 255,  42: 90,                 # Zubat / Golbat
    43: 255,  44: 120,  45: 45,       # Oddish line
    46: 190,  47: 75,                 # Paras / Parasect
    48: 190,  49: 75,                 # Venonat / Venomoth
    50: 255,  51: 50,                 # Diglett / Dugtrio
    52: 255,  53: 90,                 # Meowth / Persian
    54: 190,  55: 75,                 # Psyduck / Golduck
    56: 190,  57: 75,                 # Mankey / Primeape
    58: 190,  59: 75,                 # Growlithe / Arcanine
    60: 255,  61: 120,  62: 45,       # Poliwag line
    63: 200,  64: 100,  65: 50,       # Abra / Kadabra / Alakazam
    66: 180,  67: 90,   68: 45,       # Machop / Machoke / Machamp
    69: 255,  70: 120,  71: 45,       # Bellsprout line
    72: 190,  73: 60,                 # Tentacool / Tentacruel
    74: 255,  75: 120,  76: 45,       # Geodude / Graveler / Golem
    77: 190,  78: 60,                 # Ponyta / Rapidash
    79: 190,  80: 75,                 # Slowpoke / Slowbro
    81: 190,  82: 60,                 # Magnemite / Magneton
    83: 45,                           # Farfetch'd
    84: 190,  85: 45,                 # Doduo / Dodrio
    86: 190,  87: 75,                 # Seel / Dewgong
    88: 190,  89: 75,                 # Grimer / Muk
    90: 190,  91: 60,                 # Shellder / Cloyster
    92: 190,  93: 90,   94: 45,       # Gastly / Haunter / Gengar
    95: 45,                           # Onix
    96: 190,  97: 75,                 # Drowzee / Hypno
    98: 225,  99: 60,                 # Krabby / Kingler
    100: 190, 101: 60,                # Voltorb / Electrode
    102: 90,  103: 45,                # Exeggcute / Exeggutor
    104: 190, 105: 75,                # Cubone / Marowak
    106: 45,                          # Hitmonlee
    107: 45,                          # Hitmonchan
    108: 45,                          # Lickitung
    109: 190, 110: 60,                # Koffing / Weezing
    111: 120, 112: 60,                # Rhyhorn / Rhydon
    113: 30,                          # Chansey
    114: 45,                          # Tangela
    115: 45,                          # Kangaskhan
    116: 225, 117: 75,                # Horsea / Seadra
    118: 225, 119: 60,                # Goldeen / Seaking
    120: 225, 121: 60,                # Staryu / Starmie
    122: 45,                          # Mr. Mime
    123: 45,                          # Scyther
    124: 45,                          # Jynx
    125: 45,                          # Electabuzz
    126: 45,                          # Magmar
    127: 45,                          # Pinsir
    128: 45,                          # Tauros
    129: 255, 130: 45,                # Magikarp / Gyarados
    131: 45,                          # Lapras
    132: 35,                          # Ditto
    133: 45,  134: 45,                # Eevee / Vaporeon
    135: 45,  136: 45,                # Jolteon / Flareon
    137: 45,                          # Porygon
    138: 45,  139: 45,                # Omanyte / Omastar
    140: 45,  141: 45,                # Kabuto / Kabutops
    142: 45,                          # Aerodactyl
    143: 25,                          # Snorlax
    144: 3,                           # Articuno
    145: 3,                           # Zapdos
    146: 3,                           # Moltres
    147: 45,  148: 27,  149: 9,       # Dratini / Dragonair / Dragonite
    150: 3,                           # Mewtwo
    151: 45,                          # Mew
}


def catch_rate_of(dex_id: int) -> int:
    """Returns the canonical Gen-1 capture rate (0-255). Defaults to 100 for
    unknown dex_ids (reasonable middle-ground for safety)."""
    return GEN1_CATCH_RATES.get(int(dex_id), 100)

"""Inert Gen-1 data tables — names, evolution chains, catch + growth rates,
gender restrictions, time-window restrictions, natures, characteristics.

Pure-Python module: no I/O, no AppKit, safe to import from anywhere.
"""
from __future__ import annotations

# --- Names ----------------------------------------------------------------

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


# --- Evolution chains -----------------------------------------------------
# {base_dex_id: [(level_threshold, evolved_dex_id), ...]} for purely
# level-driven evolutions. Trade evolutions still stand in for level 30
# (no trade mechanic in the app yet). Stone evolutions live in
# STONE_EVOLUTIONS below and are user-triggered, not XP-driven.

_TRADE_LEVEL = 30  # placeholder for the four Gen-1 trade evolutions

EVOLUTIONS: dict[int, list[tuple[int, int]]] = {
    1:   [(16, 2), (32, 3)],
    4:   [(16, 5), (36, 6)],
    7:   [(16, 8), (36, 9)],
    10:  [(7, 11), (10, 12)],
    13:  [(7, 14), (10, 15)],
    16:  [(18, 17), (36, 18)],
    19:  [(20, 20)],
    21:  [(20, 22)],
    23:  [(22, 24)],
    25:  [],                                   # Pikachu (Thunder Stone — STONE_EVOLUTIONS)
    27:  [(22, 28)],
    29:  [(16, 30)],                           # Nidoran♀ → Nidorina (Moon Stone for stage 3)
    32:  [(16, 33)],                           # Nidoran♂ → Nidorino (Moon Stone for stage 3)
    35:  [],                                   # Clefairy (Moon Stone)
    37:  [],                                   # Vulpix (Fire Stone)
    39:  [],                                   # Jigglypuff (Moon Stone)
    41:  [(22, 42)],
    43:  [(21, 44)],                           # Oddish → Gloom (Leaf Stone for stage 3)
    46:  [(24, 47)],
    48:  [(31, 49)],
    50:  [(26, 51)],
    52:  [(28, 53)],
    54:  [(33, 55)],
    56:  [(28, 57)],
    58:  [],                                   # Growlithe (Fire Stone)
    60:  [(25, 61)],                           # Poliwag → Poliwhirl (Water Stone for stage 3)
    63:  [(16, 64), (_TRADE_LEVEL, 65)],       # Abra → Kadabra → Alakazam (Trade)
    66:  [(28, 67), (_TRADE_LEVEL, 68)],       # Machop → Machoke → Machamp (Trade)
    69:  [(21, 70)],                           # Bellsprout → Weepinbell (Leaf Stone for stage 3)
    72:  [(30, 73)],
    74:  [(25, 75), (_TRADE_LEVEL, 76)],       # Geodude → Graveler → Golem (Trade)
    77:  [(40, 78)],
    79:  [(37, 80)],
    81:  [(30, 82)],
    83:  [],
    84:  [(31, 85)],
    86:  [(34, 87)],
    88:  [(38, 89)],
    90:  [],                                   # Shellder (Water Stone)
    92:  [(25, 93), (_TRADE_LEVEL, 94)],       # Gastly → Haunter → Gengar (Trade)
    95:  [],
    96:  [(26, 97)],
    98:  [(28, 99)],
    100: [(30, 101)],
    102: [],                                   # Exeggcute (Leaf Stone)
    104: [(28, 105)],
    106: [],
    107: [],
    108: [],
    109: [(35, 110)],
    111: [(42, 112)],
    113: [],
    114: [],
    115: [],
    116: [(32, 117)],
    118: [(33, 119)],
    120: [],                                   # Staryu (Water Stone)
    122: [],
    123: [],
    124: [],
    125: [],
    126: [],
    127: [],
    128: [],
    129: [(20, 130)],
    131: [],
    132: [],
    133: [],                                   # Eevee — three stone branches
    137: [],
    138: [(40, 139)],
    140: [(40, 141)],
    142: [],
    143: [],
    144: [],
    145: [],
    146: [],
    147: [(30, 148), (55, 149)],
    150: [],
    151: [],
}


# Stone-driven evolutions: keyed by the dex_id the stone applies TO. Values
# map stone-item-key → evolved dex_id. Multi-branch (Eevee) is supported by
# listing every option under the same source species.
STONE_EVOLUTIONS: dict[int, dict[str, int]] = {
    25:  {"thunder-stone": 26},      # Pikachu  → Raichu
    35:  {"moon-stone":    36},      # Clefairy → Clefable
    37:  {"fire-stone":    38},      # Vulpix   → Ninetales
    39:  {"moon-stone":    40},      # Jigglypuff → Wigglytuff
    58:  {"fire-stone":    59},      # Growlithe → Arcanine
    90:  {"water-stone":   91},      # Shellder → Cloyster
    102: {"leaf-stone":    103},     # Exeggcute → Exeggutor
    120: {"water-stone":   121},     # Staryu → Starmie
    30:  {"moon-stone":    31},      # Nidorina  → Nidoqueen
    33:  {"moon-stone":    34},      # Nidorino  → Nidoking
    44:  {"leaf-stone":    45},      # Gloom     → Vileplume
    61:  {"water-stone":   62},      # Poliwhirl → Poliwrath
    70:  {"leaf-stone":    71},      # Weepinbell → Victreebel
    133: {
        "fire-stone":    136,        # Eevee → Flareon
        "water-stone":   134,        # Eevee → Vaporeon
        "thunder-stone": 135,        # Eevee → Jolteon
    },
}


# Map every dex_id (base + every evolved form) back to its base_dex_id.
# Composes both EVOLUTIONS (level chains) and STONE_EVOLUTIONS so
# stone-only base forms (e.g. Pikachu) and stone-evolved targets (Raichu,
# the three Eeveelutions, …) all resolve to the right base.
GEN1_BASE_FORMS: dict[int, str] = {}
_LINE_OF: dict[int, int] = {}
for _base, _chain in EVOLUTIONS.items():
    GEN1_BASE_FORMS[_base] = ALL_NAMES[_base]
    _LINE_OF[_base] = _base
    for _, _evo in _chain:
        _LINE_OF[_evo] = _base
# Pull stone-evolved forms into the same line as their source. Source
# species that aren't already in _LINE_OF (because they have empty level
# chains) are treated as base forms here.
for _src, _stones in STONE_EVOLUTIONS.items():
    _src_base = _LINE_OF.get(_src, _src)
    if _src not in _LINE_OF:
        GEN1_BASE_FORMS[_src] = ALL_NAMES[_src]
        _LINE_OF[_src] = _src
        _src_base = _src
    for _evolved in _stones.values():
        _LINE_OF[_evolved] = _src_base

_BASE_IDS: list[int] = sorted(GEN1_BASE_FORMS.keys())


# --- Growth rates ---------------------------------------------------------

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


# --- Catch rates ----------------------------------------------------------
# Source: Bulbapedia "List of Pokémon by catch rate" (Gen-I column).

GEN1_CATCH_RATES: dict[int, int] = {
    1: 45,    2: 45,    3: 45,
    4: 45,    5: 45,    6: 45,
    7: 45,    8: 45,    9: 45,
    10: 255,  11: 120,  12: 45,
    13: 255,  14: 120,  15: 45,
    16: 255,  17: 120,  18: 45,
    19: 255,  20: 127,
    21: 255,  22: 90,
    23: 255,  24: 90,
    25: 190,  26: 75,
    27: 255,  28: 90,
    29: 235,  30: 120,  31: 45,
    32: 235,  33: 120,  34: 45,
    35: 150,  36: 25,
    37: 190,  38: 75,
    39: 170,  40: 50,
    41: 255,  42: 90,
    43: 255,  44: 120,  45: 45,
    46: 190,  47: 75,
    48: 190,  49: 75,
    50: 255,  51: 50,
    52: 255,  53: 90,
    54: 190,  55: 75,
    56: 190,  57: 75,
    58: 190,  59: 75,
    60: 255,  61: 120,  62: 45,
    63: 200,  64: 100,  65: 50,
    66: 180,  67: 90,   68: 45,
    69: 255,  70: 120,  71: 45,
    72: 190,  73: 60,
    74: 255,  75: 120,  76: 45,
    77: 190,  78: 60,
    79: 190,  80: 75,
    81: 190,  82: 60,
    83: 45,
    84: 190,  85: 45,
    86: 190,  87: 75,
    88: 190,  89: 75,
    90: 190,  91: 60,
    92: 190,  93: 90,   94: 45,
    95: 45,
    96: 190,  97: 75,
    98: 225,  99: 60,
    100: 190, 101: 60,
    102: 90,  103: 45,
    104: 190, 105: 75,
    106: 45,
    107: 45,
    108: 45,
    109: 190, 110: 60,
    111: 120, 112: 60,
    113: 30,
    114: 45,
    115: 45,
    116: 225, 117: 75,
    118: 225, 119: 60,
    120: 225, 121: 60,
    122: 45,
    123: 45,
    124: 45,
    125: 45,
    126: 45,
    127: 45,
    128: 45,
    129: 255, 130: 45,
    131: 45,
    132: 35,
    133: 45,  134: 45,
    135: 45,  136: 45,
    137: 45,
    138: 45,  139: 45,
    140: 45,  141: 45,
    142: 45,
    143: 25,
    144: 3,
    145: 3,
    146: 3,
    147: 45,  148: 27,  149: 9,
    150: 3,
    151: 45,
}


# --- Types ---------------------------------------------------------------
# Gen-1 typing (Bulbapedia "List of Pokémon by type" — Generation I column).
# Tuple is 1 or 2 strings. Stored lowercase to match the TYPE_COLORS keys.

GEN1_TYPES: dict[int, tuple[str, ...]] = {
    1: ("grass", "poison"),    2: ("grass", "poison"),    3: ("grass", "poison"),
    4: ("fire",),              5: ("fire",),              6: ("fire", "flying"),
    7: ("water",),             8: ("water",),             9: ("water",),
    10: ("bug",),              11: ("bug",),              12: ("bug", "flying"),
    13: ("bug", "poison"),     14: ("bug", "poison"),     15: ("bug", "poison"),
    16: ("normal", "flying"),  17: ("normal", "flying"),  18: ("normal", "flying"),
    19: ("normal",),           20: ("normal",),
    21: ("normal", "flying"),  22: ("normal", "flying"),
    23: ("poison",),           24: ("poison",),
    25: ("electric",),         26: ("electric",),
    27: ("ground",),           28: ("ground",),
    29: ("poison",),           30: ("poison",),           31: ("poison", "ground"),
    32: ("poison",),           33: ("poison",),           34: ("poison", "ground"),
    35: ("normal",),           36: ("normal",),
    37: ("fire",),             38: ("fire",),
    39: ("normal",),           40: ("normal",),
    41: ("poison", "flying"),  42: ("poison", "flying"),
    43: ("grass", "poison"),   44: ("grass", "poison"),   45: ("grass", "poison"),
    46: ("bug", "grass"),      47: ("bug", "grass"),
    48: ("bug", "poison"),     49: ("bug", "poison"),
    50: ("ground",),           51: ("ground",),
    52: ("normal",),           53: ("normal",),
    54: ("water",),            55: ("water",),
    56: ("fighting",),         57: ("fighting",),
    58: ("fire",),             59: ("fire",),
    60: ("water",),            61: ("water",),            62: ("water", "fighting"),
    63: ("psychic",),          64: ("psychic",),          65: ("psychic",),
    66: ("fighting",),         67: ("fighting",),         68: ("fighting",),
    69: ("grass", "poison"),   70: ("grass", "poison"),   71: ("grass", "poison"),
    72: ("water", "poison"),   73: ("water", "poison"),
    74: ("rock", "ground"),    75: ("rock", "ground"),    76: ("rock", "ground"),
    77: ("fire",),             78: ("fire",),
    79: ("water", "psychic"),  80: ("water", "psychic"),
    81: ("electric",),         82: ("electric",),
    83: ("normal", "flying"),
    84: ("normal", "flying"),  85: ("normal", "flying"),
    86: ("water",),            87: ("water", "ice"),
    88: ("poison",),           89: ("poison",),
    90: ("water",),            91: ("water", "ice"),
    92: ("ghost", "poison"),   93: ("ghost", "poison"),   94: ("ghost", "poison"),
    95: ("rock", "ground"),
    96: ("psychic",),          97: ("psychic",),
    98: ("water",),            99: ("water",),
    100: ("electric",),        101: ("electric",),
    102: ("grass", "psychic"), 103: ("grass", "psychic"),
    104: ("ground",),          105: ("ground",),
    106: ("fighting",),
    107: ("fighting",),
    108: ("normal",),
    109: ("poison",),          110: ("poison",),
    111: ("ground", "rock"),   112: ("ground", "rock"),
    113: ("normal",),
    114: ("grass",),
    115: ("normal",),
    116: ("water",),           117: ("water",),
    118: ("water",),           119: ("water",),
    120: ("water",),           121: ("water", "psychic"),
    122: ("psychic",),
    123: ("bug", "flying"),
    124: ("ice", "psychic"),
    125: ("electric",),
    126: ("fire",),
    127: ("bug",),
    128: ("normal",),
    129: ("water",),           130: ("water", "flying"),
    131: ("water", "ice"),
    132: ("normal",),
    133: ("normal",),          134: ("water",),
    135: ("electric",),        136: ("fire",),
    137: ("normal",),
    138: ("rock", "water"),    139: ("rock", "water"),
    140: ("rock", "water"),    141: ("rock", "water"),
    142: ("rock", "flying"),
    143: ("normal",),
    144: ("ice", "flying"),
    145: ("electric", "flying"),
    146: ("fire", "flying"),
    147: ("dragon",),          148: ("dragon",),          149: ("dragon", "flying"),
    150: ("psychic",),
    151: ("psychic",),
}


# --- Base stats ----------------------------------------------------------
# Canonical Gen-1 base stats per species. Tuple order is
# (HP, Attack, Defense, Sp.Atk, Sp.Def, Speed). Modern split (post-Gen-2)
# values — Sp.Atk and Sp.Def are listed separately so the radar chart has
# six distinct axes; in canonical RBY they share a single "Special" stat.
# Source: Bulbapedia "List of Pokémon by base stats" (Gen-VIII column,
# which preserves Gen-1 species totals while exposing the modern split).

GEN1_BASE_STATS: dict[int, tuple[int, int, int, int, int, int]] = {
    1:   (45, 49, 49, 65, 65, 45),
    2:   (60, 62, 63, 80, 80, 60),
    3:   (80, 82, 83, 100, 100, 80),
    4:   (39, 52, 43, 60, 50, 65),
    5:   (58, 64, 58, 80, 65, 80),
    6:   (78, 84, 78, 109, 85, 100),
    7:   (44, 48, 65, 50, 64, 43),
    8:   (59, 63, 80, 65, 80, 58),
    9:   (79, 83, 100, 85, 105, 78),
    10:  (45, 30, 35, 20, 20, 45),
    11:  (50, 20, 55, 25, 25, 30),
    12:  (60, 45, 50, 90, 80, 70),
    13:  (40, 35, 30, 20, 20, 50),
    14:  (45, 25, 50, 25, 25, 35),
    15:  (65, 90, 40, 45, 80, 75),
    16:  (40, 45, 40, 35, 35, 56),
    17:  (63, 60, 55, 50, 50, 71),
    18:  (83, 80, 75, 70, 70, 101),
    19:  (30, 56, 35, 25, 35, 72),
    20:  (55, 81, 60, 50, 70, 97),
    21:  (40, 60, 30, 31, 31, 70),
    22:  (65, 90, 65, 61, 61, 100),
    23:  (35, 60, 44, 40, 54, 55),
    24:  (60, 95, 69, 65, 79, 80),
    25:  (35, 55, 40, 50, 50, 90),
    26:  (60, 90, 55, 90, 80, 110),
    27:  (50, 75, 85, 20, 30, 40),
    28:  (75, 100, 110, 45, 55, 65),
    29:  (55, 47, 52, 40, 40, 41),
    30:  (70, 62, 67, 55, 55, 56),
    31:  (90, 92, 87, 75, 85, 76),
    32:  (46, 57, 40, 40, 40, 50),
    33:  (61, 72, 57, 55, 55, 65),
    34:  (81, 102, 77, 85, 75, 85),
    35:  (70, 45, 48, 60, 65, 35),
    36:  (95, 70, 73, 95, 90, 60),
    37:  (38, 41, 40, 50, 65, 65),
    38:  (73, 76, 75, 81, 100, 100),
    39:  (115, 45, 20, 45, 25, 20),
    40:  (140, 70, 45, 85, 50, 45),
    41:  (40, 45, 35, 30, 40, 55),
    42:  (75, 80, 70, 65, 75, 90),
    43:  (45, 50, 55, 75, 65, 30),
    44:  (60, 65, 70, 85, 75, 40),
    45:  (75, 80, 85, 110, 90, 50),
    46:  (35, 70, 55, 45, 55, 25),
    47:  (60, 95, 80, 60, 80, 30),
    48:  (60, 55, 50, 40, 55, 45),
    49:  (70, 65, 60, 90, 75, 90),
    50:  (10, 55, 25, 35, 45, 95),
    51:  (35, 100, 50, 50, 70, 120),
    52:  (40, 45, 35, 40, 40, 90),
    53:  (65, 70, 60, 65, 65, 115),
    54:  (50, 52, 48, 65, 50, 55),
    55:  (80, 82, 78, 95, 80, 85),
    56:  (40, 80, 35, 35, 45, 70),
    57:  (65, 105, 60, 60, 70, 95),
    58:  (55, 70, 45, 70, 50, 60),
    59:  (90, 110, 80, 100, 80, 95),
    60:  (40, 50, 40, 40, 40, 90),
    61:  (65, 65, 65, 50, 50, 90),
    62:  (90, 95, 95, 70, 90, 70),
    63:  (25, 20, 15, 105, 55, 90),
    64:  (40, 35, 30, 120, 70, 105),
    65:  (55, 50, 45, 135, 95, 120),
    66:  (70, 80, 50, 35, 35, 35),
    67:  (80, 100, 70, 50, 60, 45),
    68:  (90, 130, 80, 65, 85, 55),
    69:  (50, 75, 35, 70, 30, 40),
    70:  (65, 90, 50, 85, 45, 55),
    71:  (80, 105, 65, 100, 70, 70),
    72:  (40, 40, 35, 50, 100, 70),
    73:  (80, 70, 65, 80, 120, 100),
    74:  (40, 80, 100, 30, 30, 20),
    75:  (55, 95, 115, 45, 45, 35),
    76:  (80, 120, 130, 55, 65, 45),
    77:  (50, 85, 55, 65, 65, 90),
    78:  (65, 100, 70, 80, 80, 105),
    79:  (90, 65, 65, 40, 40, 15),
    80:  (95, 75, 110, 100, 80, 30),
    81:  (25, 35, 70, 95, 55, 45),
    82:  (50, 60, 95, 120, 70, 70),
    83:  (52, 90, 55, 58, 62, 60),
    84:  (35, 85, 45, 35, 35, 75),
    85:  (60, 110, 70, 60, 60, 110),
    86:  (65, 45, 55, 45, 70, 45),
    87:  (90, 70, 80, 70, 95, 70),
    88:  (80, 80, 50, 40, 50, 25),
    89:  (105, 105, 75, 65, 100, 50),
    90:  (30, 65, 100, 45, 25, 40),
    91:  (50, 95, 180, 85, 45, 70),
    92:  (30, 35, 30, 100, 35, 80),
    93:  (45, 50, 45, 115, 55, 95),
    94:  (60, 65, 60, 130, 75, 110),
    95:  (35, 45, 160, 30, 45, 70),
    96:  (60, 48, 45, 43, 90, 42),
    97:  (85, 73, 70, 73, 115, 67),
    98:  (30, 105, 90, 25, 25, 50),
    99:  (55, 130, 115, 50, 50, 75),
    100: (40, 30, 50, 55, 55, 100),
    101: (60, 50, 70, 80, 80, 150),
    102: (60, 40, 80, 60, 45, 40),
    103: (95, 95, 85, 125, 75, 55),
    104: (50, 50, 95, 40, 50, 35),
    105: (60, 80, 110, 50, 80, 45),
    106: (50, 120, 53, 35, 110, 87),
    107: (50, 105, 79, 35, 110, 76),
    108: (90, 55, 75, 60, 75, 30),
    109: (40, 65, 95, 60, 45, 35),
    110: (65, 90, 120, 85, 70, 60),
    111: (80, 85, 95, 30, 30, 25),
    112: (105, 130, 120, 45, 45, 40),
    113: (250, 5, 5, 35, 105, 50),
    114: (65, 55, 115, 100, 40, 60),
    115: (105, 95, 80, 40, 80, 90),
    116: (30, 40, 70, 70, 25, 60),
    117: (55, 65, 95, 95, 45, 85),
    118: (45, 67, 60, 35, 50, 63),
    119: (80, 92, 65, 65, 80, 68),
    120: (30, 45, 55, 70, 55, 85),
    121: (60, 75, 85, 100, 85, 115),
    122: (40, 45, 65, 100, 120, 90),
    123: (70, 110, 80, 55, 80, 105),
    124: (65, 50, 35, 115, 95, 95),
    125: (65, 83, 57, 95, 85, 105),
    126: (65, 95, 57, 100, 85, 93),
    127: (65, 125, 100, 55, 70, 85),
    128: (75, 100, 95, 40, 70, 110),
    129: (20, 10, 55, 15, 20, 80),
    130: (95, 125, 79, 60, 100, 81),
    131: (130, 85, 80, 85, 95, 60),
    132: (48, 48, 48, 48, 48, 48),
    133: (55, 55, 50, 45, 65, 55),
    134: (130, 65, 60, 110, 95, 65),
    135: (65, 65, 60, 110, 95, 130),
    136: (65, 130, 60, 95, 110, 65),
    137: (65, 60, 70, 85, 75, 40),
    138: (35, 40, 100, 90, 55, 35),
    139: (70, 60, 125, 115, 70, 55),
    140: (30, 80, 90, 55, 45, 55),
    141: (60, 115, 105, 65, 70, 80),
    142: (80, 105, 65, 60, 75, 130),
    143: (160, 110, 65, 65, 110, 30),
    144: (90, 85, 100, 95, 125, 85),
    145: (90, 90, 85, 125, 90, 100),
    146: (90, 100, 90, 125, 85, 90),
    147: (41, 64, 45, 50, 50, 50),
    148: (61, 84, 65, 70, 70, 70),
    149: (91, 134, 95, 100, 100, 80),
    150: (106, 110, 90, 154, 90, 130),
    151: (100, 100, 100, 100, 100, 100),
}


# Canonical Pokemon-game type colors (Bulbapedia palette). RGB in 0..1
# floats so they drop straight into NSColor.colorWithCalibratedRed_… .
TYPE_COLORS: dict[str, tuple[float, float, float]] = {
    "normal":   (0xA8/255, 0xA8/255, 0x78/255),
    "fire":     (0xF0/255, 0x80/255, 0x30/255),
    "water":    (0x68/255, 0x90/255, 0xF0/255),
    "electric": (0xF8/255, 0xD0/255, 0x30/255),
    "grass":    (0x78/255, 0xC8/255, 0x50/255),
    "ice":      (0x98/255, 0xD8/255, 0xD8/255),
    "fighting": (0xC0/255, 0x30/255, 0x28/255),
    "poison":   (0xA0/255, 0x40/255, 0xA0/255),
    "ground":   (0xE0/255, 0xC0/255, 0x68/255),
    "flying":   (0xA8/255, 0x90/255, 0xF0/255),
    "psychic":  (0xF8/255, 0x58/255, 0x88/255),
    "bug":      (0xA8/255, 0xB8/255, 0x20/255),
    "rock":     (0xB8/255, 0xA0/255, 0x38/255),
    "ghost":    (0x70/255, 0x58/255, 0x98/255),
    "dragon":   (0x70/255, 0x38/255, 0xF8/255),
    "dark":     (0x70/255, 0x58/255, 0x48/255),
    "steel":    (0xB8/255, 0xB8/255, 0xD0/255),
    "fairy":    (0xEE/255, 0x99/255, 0xAC/255),
}


# --- Gender + time-of-day restrictions ------------------------------------

# Genderless: legendaries + canonically genderless lines.
GEN1_GENDERLESS: frozenset[int] = frozenset({
    81, 82,           # Magnemite, Magneton
    100, 101,         # Voltorb, Electrode
    120, 121,         # Staryu, Starmie
    132,              # Ditto
    137,              # Porygon
    144, 145, 146,    # Articuno, Zapdos, Moltres
    150, 151,         # Mewtwo, Mew
})

GEN1_NIGHT_ONLY: frozenset[int] = frozenset({
    35, 36,           # Clefairy, Clefable
    41, 42,           # Zubat, Golbat
    92, 93, 94,       # Gastly, Haunter, Gengar
    96, 97,           # Drowzee, Hypno
    104, 105,         # Cubone, Marowak (Lavender Town)
})

GEN1_DAY_ONLY: frozenset[int] = frozenset({
    16, 17, 18,       # Pidgey, Pidgeotto, Pidgeot
    21, 22,           # Spearow, Fearow
    84, 85,           # Doduo, Dodrio
})


# --- Natures + characteristics --------------------------------------------

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

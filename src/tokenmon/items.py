"""Items registry — single source of truth for all in-game items.

Each Item has:
- key: stable identifier used in DB rows ('pokeball', 'greatball', ...)
- emoji: display glyph
- display_name: human label
- description: 1-line flavour
- threshold: legacy field — kept for the one-shot inventory backfill, no
  longer drives runtime drops
- tok_chance: probability per output_token of dropping this item on a
  request. ``None`` means the item is not droppable via the token lottery
  (e.g. quest rewards, evolution stones — to be added later).
- cap: max stack size (default 99)
- actions: tuple of action keys this item supports — currently only 'throw'
- pocket: bag-pocket grouping for the Items pane tabs ('balls', 'medicine',
  'evolution', 'misc'). Pure UI grouping — no DB schema impact.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

_RNG = random.SystemRandom()


@dataclass(frozen=True, slots=True)
class Item:
    key: str
    emoji: str
    display_name: str
    description: str
    threshold: int
    cap: int = 99
    actions: tuple[str, ...] = ()
    sprite_name: str | None = None
    tok_chance: float | None = None
    pocket: str = "misc"


ITEMS: dict[str, Item] = {
    "pokeball": Item(
        key="pokeball",
        emoji="🔴",
        display_name="Poké Ball",
        description="Standard ball — works best on common Pokémon.",
        threshold=1_000,
        actions=("throw",),
        sprite_name="poke-ball",
        tok_chance=1 / 5_000,
        pocket="balls",
    ),
    "greatball": Item(
        key="greatball",
        emoji="🔵",
        display_name="Great Ball",
        description="1.5× catch rate — better odds against tougher Pokémon.",
        threshold=10_000,
        actions=("throw",),
        sprite_name="great-ball",
        tok_chance=1 / 10_000,
        pocket="balls",
    ),
    "ultraball": Item(
        key="ultraball",
        emoji="🟡",
        display_name="Ultra Ball",
        description="2× catch rate — for the rare ones.",
        threshold=50_000,
        actions=("throw",),
        sprite_name="ultra-ball",
        tok_chance=1 / 50_000,
        pocket="balls",
    ),
    "masterball": Item(
        key="masterball",
        emoji="💜",
        display_name="Master Ball",
        description="Catches anything, no questions asked. Save it for Mewtwo.",
        threshold=500_000,
        actions=("throw",),
        sprite_name="master-ball",
        tok_chance=1 / 500_000,
        pocket="balls",
    ),
    # Evolution stones — used on the active Pokemon to trigger species-
    # specific stone evolutions. Rarer than Ultra Balls (~1 per 250k tokens).
    "fire-stone": Item(
        key="fire-stone",
        emoji="🔥",
        display_name="Fire Stone",
        description="Evolves Fire-loving Pokémon. Use on the active Pokémon.",
        threshold=100_000,
        actions=("use",),
        sprite_name="fire-stone",
        tok_chance=1 / 250_000,
        pocket="evolution",
    ),
    "water-stone": Item(
        key="water-stone",
        emoji="💧",
        display_name="Water Stone",
        description="Evolves aquatic Pokémon. Use on the active Pokémon.",
        threshold=100_000,
        actions=("use",),
        sprite_name="water-stone",
        tok_chance=1 / 250_000,
        pocket="evolution",
    ),
    "thunder-stone": Item(
        key="thunder-stone",
        emoji="⚡",
        display_name="Thunder Stone",
        description="Evolves Electric Pokémon. Use on the active Pokémon.",
        threshold=100_000,
        actions=("use",),
        sprite_name="thunder-stone",
        tok_chance=1 / 250_000,
        pocket="evolution",
    ),
    "leaf-stone": Item(
        key="leaf-stone",
        emoji="🌿",
        display_name="Leaf Stone",
        description="Evolves Grass-type Pokémon. Use on the active Pokémon.",
        threshold=100_000,
        actions=("use",),
        sprite_name="leaf-stone",
        tok_chance=1 / 250_000,
        pocket="evolution",
    ),
    "moon-stone": Item(
        key="moon-stone",
        emoji="🌙",
        display_name="Moon Stone",
        description="Evolves nocturnal & Fairy-adjacent Pokémon. Use on the active Pokémon.",
        threshold=100_000,
        actions=("use",),
        sprite_name="moon-stone",
        tok_chance=1 / 250_000,
        pocket="evolution",
    ),
    # Healing items — used on the active Pokémon to restore HP.
    "potion": Item(
        key="potion",
        emoji="🧪",
        display_name="Potion",
        description="Restores 20 HP to the active Pokémon.",
        threshold=2_000,
        actions=("use",),
        sprite_name="potion",
        tok_chance=1 / 5_000,
        pocket="medicine",
    ),
}

# HP restored per use, keyed by item slug.
POTION_HEAL_AMOUNTS: dict[str, int] = {
    "potion": 20,
}

# Catch-rate modifier per ball when used in 'throw' action. Items not in this
# map are 1.0 (no boost). Master Ball is 255 = effectively guaranteed.
BALL_CATCH_MODIFIERS: dict[str, float] = {
    "pokeball": 1.0,
    "greatball": 1.5,
    "ultraball": 2.0,
    "masterball": 255.0,
}


def hp_modifier(hp_current: int, hp_max: int) -> float:
    """Gen-1 style HP factor for catch probability:
    ``(3 * hp_max - 2 * hp_current) / (3 * hp_max)`` clamped to [1/3, 1].

    Lower HP = better catch chance, capped at the canonical 1/3 floor and
    1.0 ceiling. Bogus inputs (zero/negative max) return 1.0 — the caller
    falls back to "full HP" semantics."""
    if hp_max <= 0:
        return 1.0
    raw = (3 * hp_max - 2 * hp_current) / (3 * hp_max)
    if raw < 1.0 / 3.0:
        return 1.0 / 3.0
    if raw > 1.0:
        return 1.0
    return raw

# Bag pockets — order = tab order in the Items pane. Each entry is
# ``(pocket_key, label)``; the label is shown on the segmented control.
POCKETS: tuple[tuple[str, str], ...] = (
    ("balls", "Bälle"),
    ("medicine", "Heilung"),
    ("evolution", "Power-Items"),
    ("misc", "Sonstiges"),
)


def items_in_pocket(pocket_key: str) -> list[tuple[str, "Item"]]:
    """Return the ITEMS entries belonging to ``pocket_key``, preserving the
    declaration order of ``ITEMS`` (which is also the in-pane render order).
    Pure helper — does not consult inventory counts; the caller filters by
    ownership."""
    return [(k, it) for k, it in ITEMS.items() if it.pocket == pocket_key]


def all_keys() -> list[str]:
    return list(ITEMS.keys())


def get(key: str) -> Item | None:
    return ITEMS.get(key)


def is_throwable(key: str) -> bool:
    item = ITEMS.get(key)
    return item is not None and "throw" in item.actions


def roll_item_drops(output_tokens: int) -> dict[str, int]:
    """For each item with ``tok_chance`` set, roll how many drop on a
    request that produced ``output_tokens`` output tokens.

    Algorithm: ``ev = output_tokens × tok_chance``; deterministic floor
    plus a Bernoulli on the fractional remainder. Same expected value as
    a pure Bernoulli-per-token roll, but cheap (one ``random()`` call per
    item per request) and the integer floor delivers the "you've crossed
    a threshold" feel without ever forcing a 100% roll.

    Returns ``{item_key: count}``; only positive entries are included.
    """
    drops: dict[str, int] = {}
    if output_tokens <= 0:
        return drops
    for key, item in ITEMS.items():
        if item.tok_chance is None or item.tok_chance <= 0:
            continue
        ev = output_tokens * item.tok_chance
        base = int(ev)
        rem = ev - base
        if rem > 0 and _RNG.random() < rem:
            base += 1
        if base > 0:
            drops[key] = base
    return drops

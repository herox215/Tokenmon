"""Items registry — single source of truth for all in-game items.

Each Item has:
- key: stable identifier used in DB rows ('pokeball', 'greatball', ...)
- emoji: display glyph
- display_name: human label
- description: 1-line flavour
- threshold: lifetime output_tokens needed per +1 of this item
- cap: max stack size (default 99)
- actions: tuple of action keys this item supports — currently only 'throw'
"""

from __future__ import annotations

from dataclasses import dataclass


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


ITEMS: dict[str, Item] = {
    "pokeball": Item(
        key="pokeball",
        emoji="🔴",
        display_name="Poké Ball",
        description="Standard ball — works best on common Pokémon.",
        threshold=1_000,
        actions=("throw",),
        sprite_name="poke-ball",
    ),
    "greatball": Item(
        key="greatball",
        emoji="🔵",
        display_name="Great Ball",
        description="1.5× catch rate — better odds against tougher Pokémon.",
        threshold=10_000,
        actions=("throw",),
        sprite_name="great-ball",
    ),
    "ultraball": Item(
        key="ultraball",
        emoji="🟡",
        display_name="Ultra Ball",
        description="2× catch rate — for the rare ones.",
        threshold=50_000,
        actions=("throw",),
        sprite_name="ultra-ball",
    ),
    "masterball": Item(
        key="masterball",
        emoji="💜",
        display_name="Master Ball",
        description="Catches anything, no questions asked. Save it for Mewtwo.",
        threshold=500_000,
        actions=("throw",),
        sprite_name="master-ball",
    ),
}

# Catch-rate modifier per ball when used in 'throw' action. Items not in this
# map are 1.0 (no boost). Master Ball is 255 = effectively guaranteed.
BALL_CATCH_MODIFIERS: dict[str, float] = {
    "pokeball": 1.0,
    "greatball": 1.5,
    "ultraball": 2.0,
    "masterball": 255.0,
}

def all_keys() -> list[str]:
    return list(ITEMS.keys())


def get(key: str) -> Item | None:
    return ITEMS.get(key)


def is_throwable(key: str) -> bool:
    item = ITEMS.get(key)
    return item is not None and "throw" in item.actions

"""Trainer-class avatars.

PokeAPI's sprites repo doesn't ship trainer-class sprites (Nintendo
IP concerns), so we map each title in the trainer-name pool to a
themed emoji. Renders as a large glyph in the preview pane.

The function name + module name are kept for compatibility with the
earlier URL-based draft of this module — callers expecting a Path-
returning ``ensure_trainer_sprite`` now get ``None`` (signalling
"render the emoji fallback") and ``emoji_for(title)`` is the right
entry point for the preview pane.
"""
from __future__ import annotations

# Title → emoji. Keys mirror the pool in ``battle.names.TITLES`` plus a
# generic fallback. Pick visually-suggestive glyphs without requiring
# specific Pokemon-game flavor.
_TITLE_EMOJI: dict[str, str] = {
    "Bug Catcher": "🐛",
    "Lass": "👧",
    "Youngster": "👦",
    "Hiker": "🧗",
    "Fisherman": "🎣",
    "Picnicker": "🧺",
    "Camper": "⛺",
    "Bird Keeper": "🦜",
    "Sailor": "⚓",
    "Engineer": "👷",
    "Beauty": "💄",
    "Gentleman": "🎩",
    "Schoolkid": "🎒",
    "Black Belt": "🥋",
    "PokéManiac": "🤓",
    "Psychic": "🔮",
    "Channeler": "👻",
    "Tamer": "🦁",
    "Burglar": "🦝",
    "Rocker": "🎸",
}

DEFAULT_EMOJI = "👤"


def emoji_for(title: str) -> str:
    """Return the themed emoji for ``title`` or the generic 👤 fallback."""
    return _TITLE_EMOJI.get(title, DEFAULT_EMOJI)


def ensure_trainer_sprite(title: str) -> None:
    """Compatibility shim: previous draft fetched PNGs from PokeAPI's
    sprites repo, but that repo doesn't ship trainer-class sprites at
    all. The function is kept (returning None) so callers that previously
    branched on its return value silently fall back to ``emoji_for``."""
    return None

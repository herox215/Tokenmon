"""Trainer-class avatars.

PixelLab-generated chibi sprites bundled at ``data/trainer_sprites/``
back the trainer-preview avatar. Each title in ``battle.names.TITLES``
has a 1×1 PNG mapped via the slugged filename (see
``_TITLE_SLUGS``). When the sprite file is missing for any reason
(bundle not yet built, slug typo) we fall back to a themed emoji so
the pane never goes blank.

Public surface (kept stable for callers):

* ``ensure_trainer_sprite(title) -> Path | None`` — preferred; returns
  the local PNG path or None when no asset is available.
* ``emoji_for(title) -> str`` — themed emoji fallback.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("tokenmon.trainers_remote")


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


# Title → on-disk slug. ``data/trainer_sprites/<slug>.png`` is the
# bundled PixelLab sprite. Slugs are lowercase, ASCII, hyphenated.
_TITLE_SLUGS: dict[str, str] = {
    "Bug Catcher": "bug-catcher",
    "Lass": "lass",
    "Youngster": "youngster",
    "Hiker": "hiker",
    "Fisherman": "fisherman",
    "Picnicker": "picnicker",
    "Camper": "camper",
    "Bird Keeper": "bird-keeper",
    "Sailor": "sailor",
    "Engineer": "engineer",
    "Beauty": "beauty",
    "Gentleman": "gentleman",
    "Schoolkid": "schoolkid",
    "Black Belt": "black-belt",
    "PokéManiac": "pokemaniac",
    "Psychic": "psychic",
    "Channeler": "channeler",
    "Tamer": "tamer",
    "Burglar": "burglar",
    "Rocker": "rocker",
}


# Repository-relative sprite directory. Resolved once at module-load
# time; dev installs (``uv pip install -e .``) leave the package next
# to the data dir, prod installs would need the data shipped via
# ``importlib.resources``. Keep it simple for now — the path walks up
# from this file's location.
_SPRITE_DIR: Path | None = None


def _sprite_dir() -> Path | None:
    """Locate ``data/trainer_sprites/`` relative to the package root.
    Returns None if the directory can't be found (e.g. installed
    without the data bundle), which makes ``ensure_trainer_sprite``
    silently fall through to the emoji branch."""
    global _SPRITE_DIR
    if _SPRITE_DIR is not None:
        return _SPRITE_DIR
    # tokenmon/trainers_remote.py → tokenmon → src → repo
    here = Path(__file__).resolve()
    candidate = here.parent.parent.parent / "data" / "trainer_sprites"
    if candidate.is_dir():
        _SPRITE_DIR = candidate
        return candidate
    return None


def emoji_for(title: str) -> str:
    """Return the themed emoji for ``title`` or the generic 👤 fallback."""
    return _TITLE_EMOJI.get(title, DEFAULT_EMOJI)


def ensure_trainer_sprite(title: str) -> Path | None:
    """Return the local PNG path for ``title`` if a bundled sprite
    exists, else ``None`` (caller falls back to the emoji glyph)."""
    slug = _TITLE_SLUGS.get(title)
    if slug is None:
        return None
    base = _sprite_dir()
    if base is None:
        return None
    path = base / f"{slug}.png"
    return path if path.exists() else None

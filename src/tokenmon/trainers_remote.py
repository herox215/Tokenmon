"""Trainer-class sprites from PokeAPI's GitHub repo.

Mirrors the pattern in ``pokemon.sprites``: a slug derived from the
trainer's title is downloaded once and cached on disk. Returns ``None``
on lookup failure — the trainer-preview pane falls back to a 👤 emoji
so a missing sprite doesn't break the UI.

Some Gen-1 trainer classes ("Schoolkid", "PokéManiac") don't map 1:1
to PokeAPI slugs; the slug helper does best-effort normalization
(lowercase, hyphenated, accents stripped) plus an explicit override
table for known mismatches.
"""
from __future__ import annotations

import logging
import unicodedata
import urllib.request
from pathlib import Path

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.trainers_remote")

SPRITE_DIR = DB_DIR / "sprites_trainers"
SPRITE_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "trainers/{slug}.png"
)
FETCH_TIMEOUT_SEC = 5.0

# Manual slug overrides for titles that don't map cleanly via the
# default normalize() pass. Keys are the user-facing title; values are
# the PokeAPI sprite slug.
_TITLE_SLUG_OVERRIDES: dict[str, str] = {
    "PokéManiac": "pokemaniac",
    "Schoolkid": "school-kid",
    "Bird Keeper": "bird-keeper",
    "Bug Catcher": "bug-catcher",
    "Black Belt": "black-belt",
}


def _slug_for(title: str) -> str:
    """Normalize a trainer title to a PokeAPI-compatible slug.

    Examples:
      ``"Bug Catcher" -> "bug-catcher"``
      ``"PokéManiac"  -> "pokemaniac"``  (via override)
      ``"Lass"        -> "lass"``
    """
    if title in _TITLE_SLUG_OVERRIDES:
        return _TITLE_SLUG_OVERRIDES[title]
    # Strip accents (é → e, ñ → n, …) before lowercasing.
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )
    return ascii_only.lower().replace(" ", "-").replace("_", "-")


def sprite_path(title: str) -> Path:
    return SPRITE_DIR / f"{_slug_for(title)}.png"


def ensure_trainer_sprite(
    title: str, *, timeout: float = FETCH_TIMEOUT_SEC,
) -> Path | None:
    """Download the trainer-class sprite if not already cached.

    Returns the cached path on hit/success, ``None`` on miss + failure.
    Negative results are NOT cached — a transient network blip won't
    permanently disable the sprite.
    """
    p = sprite_path(title)
    if p.exists() and p.stat().st_size > 0:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    url = SPRITE_URL_TMPL.format(slug=_slug_for(title))
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "tokenmon/0.1"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        p.write_bytes(data)
        return p
    except Exception as exc:
        log.warning(
            "trainer sprite download failed for %s (%s): %s",
            title, _slug_for(title), exc,
        )
        if p.exists():
            p.unlink(missing_ok=True)
        return None

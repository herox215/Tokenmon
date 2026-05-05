"""Sprite-fetching: PokeAPI animated GIFs cached on disk.

Two cache dirs (regular + shiny) so the variants don't collide. The
ensure_sprite function handles network failure gracefully — callers get
None and can fall back to text.
"""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.pokemon.sprites")

SPRITE_DIR = DB_DIR / "sprites"
SHINY_SPRITE_DIR = DB_DIR / "sprites_shiny"
SPRITE_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/versions/generation-v/black-white/animated/{id}.gif"
)
SHINY_SPRITE_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/versions/generation-v/black-white/animated/shiny/{id}.gif"
)


def sprite_path(dex_id: int, *, shiny: bool = False) -> Path:
    base = SHINY_SPRITE_DIR if shiny else SPRITE_DIR
    return base / f"{dex_id}.gif"


def ensure_sprite(
    dex_id: int, timeout: float = 5.0, *, shiny: bool = False,
) -> Path | None:
    """Download the animated sprite if not already cached. Returns the cached
    path or None on failure. ``shiny=True`` uses the shiny variant URL."""
    p = sprite_path(dex_id, shiny=shiny)
    if p.exists() and p.stat().st_size > 0:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    url = (SHINY_SPRITE_URL_TMPL if shiny else SPRITE_URL_TMPL).format(id=dex_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tokenmon/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        p.write_bytes(data)
        return p
    except Exception as exc:
        log.warning(
            "sprite download failed for #%d (shiny=%s): %s",
            dex_id, shiny, exc,
        )
        if p.exists():
            p.unlink(missing_ok=True)
        return None

"""Sprite-fetching: PokeAPI animated GIFs cached on disk.

Four cache dirs (front/back × regular/shiny) so variants don't collide.
Front sprites are always animated GIFs (gen-V Black/White animated set).
Back sprites prefer animated, fall back to static PNG when the animated
back doesn't exist for that species (common for post-gen-V Pokémon).
``ensure_sprite`` returns the cached path or None on failure; callers
can fall back to text or to the front sprite when ``back=True`` returns
None.
"""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.pokemon.sprites")

SPRITE_DIR = DB_DIR / "sprites"
SHINY_SPRITE_DIR = DB_DIR / "sprites_shiny"
BACK_SPRITE_DIR = DB_DIR / "sprites_back"
SHINY_BACK_SPRITE_DIR = DB_DIR / "sprites_back_shiny"

SPRITE_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/versions/generation-v/black-white/animated/{id}.gif"
)
SHINY_SPRITE_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/versions/generation-v/black-white/animated/shiny/{id}.gif"
)
BACK_GIF_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/versions/generation-v/black-white/animated/back/{id}.gif"
)
SHINY_BACK_GIF_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/versions/generation-v/black-white/animated/back/shiny/{id}.gif"
)
BACK_PNG_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/back/{id}.png"
)
SHINY_BACK_PNG_URL_TMPL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/back/shiny/{id}.png"
)


def sprite_path(dex_id: int, *, shiny: bool = False) -> Path:
    base = SHINY_SPRITE_DIR if shiny else SPRITE_DIR
    return base / f"{dex_id}.gif"


def back_sprite_dir(*, shiny: bool = False) -> Path:
    return SHINY_BACK_SPRITE_DIR if shiny else BACK_SPRITE_DIR


def _try_download(url: str, dest: Path, timeout: float) -> bool:
    """Fetch ``url`` into ``dest``. Returns True on success. On failure,
    cleans up the partial file and returns False — caller can try the next
    fallback URL."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tokenmon/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return True
    except Exception as exc:
        log.warning("sprite download failed for %s: %s", url, exc)
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False


def ensure_sprite(
    dex_id: int, timeout: float = 5.0, *,
    shiny: bool = False, back: bool = False,
) -> Path | None:
    """Download the sprite if not already cached. Returns the cached path
    or None on failure.

    When ``back=True``, tries the gen-V animated back GIF first; falls
    back to the static back PNG when the animated variant doesn't exist
    on PokeAPI (gen-VI+ species generally only have static backs). If
    neither is available, returns None — the caller should then fall back
    to the front sprite.
    """
    if not back:
        p = sprite_path(dex_id, shiny=shiny)
        if p.exists() and p.stat().st_size > 0:
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        url = (SHINY_SPRITE_URL_TMPL if shiny else SPRITE_URL_TMPL).format(id=dex_id)
        return p if _try_download(url, p, timeout) else None

    base = back_sprite_dir(shiny=shiny)
    gif_path = base / f"{dex_id}.gif"
    png_path = base / f"{dex_id}.png"
    # Cache hit: gif preferred, then png.
    if gif_path.exists() and gif_path.stat().st_size > 0:
        return gif_path
    if png_path.exists() and png_path.stat().st_size > 0:
        return png_path
    base.mkdir(parents=True, exist_ok=True)
    # Animated GIF first.
    gif_url = (SHINY_BACK_GIF_URL_TMPL if shiny else BACK_GIF_URL_TMPL).format(id=dex_id)
    if _try_download(gif_url, gif_path, timeout):
        return gif_path
    # Static PNG fallback — common for post-gen-V species.
    png_url = (SHINY_BACK_PNG_URL_TMPL if shiny else BACK_PNG_URL_TMPL).format(id=dex_id)
    if _try_download(png_url, png_path, timeout):
        return png_path
    return None

"""Item sprite fetcher — pulls the canonical PokeAPI item icons.

Sprites come from the PokeAPI sprites GitHub mirror, which serves stable
PNGs at predictable URLs. We cache them on disk under
``~/.tokenmon/item_sprites/{sprite_name}.png`` and keep an in-memory
``NSImage`` cache so repeated bag opens don't re-decode.

Lazy-fetched on first access. Falls back to ``None`` on any failure so
callers can degrade to the item's emoji.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path

from AppKit import NSImage

from tokenmon.items import Item
from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.items.remote")

SPRITE_DIR = DB_DIR / "item_sprites"
SPRITE_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/{name}.png"
)
FETCH_TIMEOUT_SEC = 5.0

_image_cache: dict[str, NSImage] = {}
_negative: set[str] = set()


def _disk_path(sprite_name: str) -> Path:
    return SPRITE_DIR / f"{sprite_name}.png"


def _download(sprite_name: str) -> bytes | None:
    url = SPRITE_URL.format(name=sprite_name)
    req = urllib.request.Request(url, headers={"User-Agent": "tokenmon/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("item sprite fetch failed for %s: %s", sprite_name, exc)
        return None


def _ensure_on_disk(sprite_name: str) -> Path | None:
    path = _disk_path(sprite_name)
    if path.exists():
        return path
    if sprite_name in _negative:
        return None
    data = _download(sprite_name)
    if data is None:
        _negative.add(sprite_name)
        return None
    try:
        SPRITE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        log.exception("failed to write item sprite %s", sprite_name)
        _negative.add(sprite_name)
        return None
    return path


def get_item_image(item: Item) -> NSImage | None:
    """Return an NSImage for the given item's sprite, or None if unavailable.

    Tries memory cache → disk cache → network. Failures are non-fatal and
    cached negatively for the rest of the session."""
    if item.sprite_name is None:
        return None
    cached = _image_cache.get(item.sprite_name)
    if cached is not None:
        return cached
    path = _ensure_on_disk(item.sprite_name)
    if path is None:
        return None
    img = NSImage.alloc().initWithContentsOfFile_(str(path))
    if img is None:
        log.warning("NSImage failed to decode %s", path)
        _negative.add(item.sprite_name)
        return None
    _image_cache[item.sprite_name] = img
    return img


def prefetch_all(items: list[Item]) -> None:
    """Download every item's sprite to disk if not already present.

    Safe to call from a background thread — only touches disk + urllib,
    no AppKit. The NSImage decode happens lazily on first ``get_item_image``
    call from the main thread."""
    for item in items:
        if item.sprite_name is None:
            continue
        if _disk_path(item.sprite_name).exists():
            continue
        _ensure_on_disk(item.sprite_name)


def prefetch_in_background(items: list[Item]) -> None:
    threading.Thread(
        target=prefetch_all, args=(items,), daemon=True
    ).start()

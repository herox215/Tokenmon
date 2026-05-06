"""PokeAPI move-data fetcher with disk + in-memory cache.

Mirrors the pattern in ``items_remote.py`` / ``pokedex_remote.py``:
JSON cache in ``~/.tokenmon/moves_cache.json``, lazy-loaded on first
call, downloaded on cache miss. Returns a ``battle.models.Move``
dataclass — battle code consumes typed moves rather than raw API dicts.

Battle-related fields parsed:
- ``name``     → display name
- ``type``     → lowercase type slug
- ``category`` → "physical" | "special" | "status"
- ``power``    → int or None (status moves)
- ``accuracy`` → int or None (never-miss moves like Swift)
- ``pp``       → int (max PP)
- ``priority`` → int (we filter to 0 in v1 but keep for future use)

Returns None on download failure or malformed payload — callers fall
back to a default move (typically "tackle") so the battle never starts
with zero moves.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tokenmon.battle.models import Move
from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.moves_remote")

CACHE_PATH = DB_DIR / "moves_cache.json"
MOVES_URL = "https://pokeapi.co/api/v2/move/{key}/"
FETCH_TIMEOUT_SEC = 5.0

_loaded = False
_moves: dict[str, dict] = {}  # key → raw json payload (small subset)


def _load_from_disk() -> None:
    global _loaded, _moves
    if _loaded:
        return
    if not CACHE_PATH.exists():
        _moves = {}
        _loaded = True
        return
    try:
        data = json.loads(CACHE_PATH.read_text())
        _moves = dict(data.get("moves", {}))
    except (OSError, ValueError):
        log.exception("moves cache parse failed; starting empty")
        _moves = {}
    _loaded = True


def _save_to_disk() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "moves": _moves,
    }
    CACHE_PATH.write_text(json.dumps(payload, indent=2))


def _parse_move(payload: dict) -> Move | None:
    """Translate the relevant slice of a PokeAPI move payload into our
    typed ``Move``. Returns None if the payload is missing required
    fields."""
    try:
        key = payload["name"]
        type_name = payload["type"]["name"].lower()
        # damage_class "physical" / "special" / "status" — our enum-strings
        category = payload["damage_class"]["name"]
        power = payload.get("power")  # may be null
        accuracy = payload.get("accuracy")  # may be null
        pp = payload["pp"]
        priority = payload.get("priority", 0)
    except (KeyError, TypeError):
        return None
    if category not in ("physical", "special", "status"):
        return None
    return Move(
        key=str(key),
        name=str(key).replace("-", " ").title(),
        type=str(type_name),
        category=category,  # type: ignore[arg-type]
        power=int(power) if power is not None else None,
        accuracy=int(accuracy) if accuracy is not None else None,
        pp=int(pp),
        priority=int(priority or 0),
    )


def get_move_data(move_key: str, *, timeout: float = FETCH_TIMEOUT_SEC) -> Move | None:
    """Return the typed Move for ``move_key`` (PokeAPI slug).

    On cache hit: returns the cached entry. On miss: HTTP fetch, cache
    + return. On any failure: log + return None.
    """
    _load_from_disk()
    key = move_key.strip().lower()
    if key in _moves:
        return _parse_move(_moves[key])
    url = MOVES_URL.format(key=key)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "tokenmon/0.1"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        log.warning("move fetch failed for %s: %s", key, exc)
        return None
    move = _parse_move(payload)
    if move is None:
        log.warning("move payload malformed for %s", key)
        return None
    # Cache only the slice we use, not the whole 30 KB payload.
    _moves[key] = {
        "name": move.key,
        "type": {"name": move.type},
        "damage_class": {"name": move.category},
        "power": move.power,
        "accuracy": move.accuracy,
        "pp": move.pp,
        "priority": move.priority,
    }
    try:
        _save_to_disk()
    except Exception:
        log.exception("moves cache save failed")
    return move


def clear_cache() -> None:
    """Test helper — wipe in-memory state so the next ``get_move_data``
    re-reads from disk."""
    global _loaded, _moves
    _loaded = False
    _moves = {}

"""Per-species level-up learnsets fetched from PokeAPI.

Different endpoint than ``pokedex_remote`` (which uses
``/pokemon-species/``): this hits ``/pokemon/{id}/`` and parses the
``moves`` array, filtering down to level-up entries from a stable
version group (default: red-blue, our flavor of choice — the moves
are the same as Gen-3 for the species we ship).

Returns ``list[(level, move_key)]`` sorted ascending by level. Caches
to ``~/.tokenmon/learnsets.json``.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.learnsets_remote")

CACHE_PATH = DB_DIR / "learnsets.json"
POKEMON_URL = "https://pokeapi.co/api/v2/pokemon/{id}/"
FETCH_TIMEOUT_SEC = 5.0

# Version groups we accept move data from, in priority order. Sticking
# with red-blue (then yellow, then later gens as fallback) keeps the
# learnset close to what a Gen-3-feel game would have.
_VG_PRIORITY: tuple[str, ...] = (
    "red-blue", "yellow", "firered-leafgreen",
    "ruby-sapphire", "emerald", "gold-silver",
)

_loaded = False
_learnsets: dict[int, list[list]] = {}


def _load_from_disk() -> None:
    global _loaded, _learnsets
    if _loaded:
        return
    if not CACHE_PATH.exists():
        _learnsets = {}
        _loaded = True
        return
    try:
        data = json.loads(CACHE_PATH.read_text())
        raw = data.get("learnsets") or {}
        _learnsets = {int(k): v for k, v in raw.items()}
    except (OSError, ValueError):
        log.exception("learnsets cache parse failed; starting empty")
        _learnsets = {}
    _loaded = True


def _save_to_disk() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "learnsets": {str(k): v for k, v in _learnsets.items()},
    }
    CACHE_PATH.write_text(json.dumps(payload, indent=2))


def _parse_learnset(payload: dict) -> list[list]:
    """Walk the PokeAPI moves array and pick (level, move_key) pairs
    for ``move_learn_method == "level-up"``, choosing the highest-
    priority version group available per move."""
    out: list[tuple[int, str]] = []
    for entry in payload.get("moves") or []:
        move_name = (entry.get("move") or {}).get("name")
        if not move_name:
            continue
        # Walk version groups in priority order; first hit wins.
        best_level: int | None = None
        for vg_name in _VG_PRIORITY:
            for vgd in entry.get("version_group_details") or []:
                if (vgd.get("move_learn_method") or {}).get("name") != "level-up":
                    continue
                if (vgd.get("version_group") or {}).get("name") != vg_name:
                    continue
                lvl = vgd.get("level_learned_at")
                if lvl is not None:
                    best_level = int(lvl)
                    break
            if best_level is not None:
                break
        if best_level is None:
            continue
        out.append((best_level, str(move_name)))
    out.sort(key=lambda x: (x[0], x[1]))
    # Stored as list-of-lists for JSON serializability.
    return [[lv, name] for lv, name in out]


def get_learnset(
    dex_id: int, *, timeout: float = FETCH_TIMEOUT_SEC,
) -> list[tuple[int, str]]:
    """Return the species' level-up learnset as a list of (level, move).

    Empty list on cache miss + fetch failure — callers fall back to a
    default move.
    """
    _load_from_disk()
    if dex_id in _learnsets:
        return [(int(lv), str(name)) for lv, name in _learnsets[dex_id]]
    url = POKEMON_URL.format(id=int(dex_id))
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "tokenmon/0.1"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        log.warning("learnset fetch failed for #%d: %s", dex_id, exc)
        return []
    parsed = _parse_learnset(payload)
    _learnsets[dex_id] = parsed
    try:
        _save_to_disk()
    except Exception:
        log.exception("learnsets cache save failed")
    return [(int(lv), str(name)) for lv, name in parsed]


def clear_cache() -> None:
    global _loaded, _learnsets
    _loaded = False
    _learnsets = {}


def initial_moves(
    dex_id: int, level: int, count: int = 4,
) -> list[str]:
    """The latest ``count`` level-up moves the species would know at
    ``level``. Used when a Pokémon is caught to seed its 4 move slots.
    Falls back to ``["tackle"]`` if the learnset is empty."""
    learnset = get_learnset(dex_id)
    eligible = [(lv, name) for lv, name in learnset if lv <= level]
    if not eligible:
        return ["tackle"]
    eligible.sort(key=lambda x: x[0], reverse=True)
    chosen: list[str] = []
    seen: set[str] = set()
    for _, name in eligible:
        if name in seen:
            continue
        chosen.append(name)
        seen.add(name)
        if len(chosen) >= count:
            break
    return chosen


def moves_at_level(dex_id: int, level: int) -> list[str]:
    """Names of all level-up moves the species learns *at exactly*
    ``level``. Used by the level-up hook to queue move-learn
    notifications."""
    return [name for lv, name in get_learnset(dex_id) if lv == level]

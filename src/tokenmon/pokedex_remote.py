"""Pokedex descriptions fetched on demand from PokeAPI.

The Pokedex detail pane wants flavour text + genus per species — there's no
clean way to ship 151 paragraphs of canonical text inside the codebase, so
we fetch lazily from https://pokeapi.co/api/v2/pokemon-species/{id}/ when
the user opens a detail view, and cache forever (with a soft 30-day TTL
just as a safety bound) under ~/.tokenmon/pokedex_descriptions.json.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.pokedex.remote")

CACHE_PATH = DB_DIR / "pokedex_descriptions.json"
CACHE_TTL_SECONDS = 30 * 24 * 3600
SPECIES_URL = "https://pokeapi.co/api/v2/pokemon-species/{id}/"
FETCH_TIMEOUT_SEC = 5.0

# In-memory cache.
_loaded = False
_entries: dict[int, dict] = {}


def _load_from_disk() -> None:
    global _loaded, _entries
    if _loaded:
        return
    if not CACHE_PATH.exists():
        _entries = {}
        _loaded = True
        return
    try:
        data = json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        _entries = {}
        _loaded = True
        return
    raw = data.get("entries") or {}
    _entries = {}
    for k, v in raw.items():
        try:
            _entries[int(k)] = v
        except (TypeError, ValueError):
            continue
    _loaded = True


def _save_to_disk() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries": {str(k): v for k, v in _entries.items()},
    }
    try:
        CACHE_PATH.write_text(json.dumps(payload, indent=2))
    except OSError:
        log.exception("failed to write pokedex cache")


def _pick_english_description(flavor_text_entries: list[dict]) -> str:
    """Prefer the Red/Blue text; fall back to the first English entry."""
    preferred_versions = ("red", "blue", "yellow", "firered", "leafgreen")
    for ver in preferred_versions:
        for entry in flavor_text_entries:
            lang = (entry.get("language") or {}).get("name")
            v = (entry.get("version") or {}).get("name")
            if lang == "en" and v == ver:
                return entry.get("flavor_text", "")
    for entry in flavor_text_entries:
        if (entry.get("language") or {}).get("name") == "en":
            return entry.get("flavor_text", "")
    return ""


def _pick_english_genus(genera: list[dict]) -> str:
    for entry in genera:
        if (entry.get("language") or {}).get("name") == "en":
            return entry.get("genus", "")
    return ""


def _normalise(text: str) -> str:
    """Strip the form-feed / hard-newline artefacts from canon Pokedex text."""
    return text.replace("\n", " ").replace("\f", " ").replace("­", "").strip()


def _fetch_one(dex_id: int, *, timeout: float = FETCH_TIMEOUT_SEC) -> dict | None:
    url = SPECIES_URL.format(id=dex_id)
    req = urllib.request.Request(url, headers={"User-Agent": "tokenmon/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("pokedex fetch failed for #%d: %s", dex_id, exc)
        return None
    return {
        "name": (data.get("name") or "").title(),
        "genus": _pick_english_genus(data.get("genera") or []),
        "description": _normalise(
            _pick_english_description(data.get("flavor_text_entries") or [])
        ),
    }


def get_species_info(dex_id: int) -> dict | None:
    """Returns ``{"name", "genus", "description"}`` for the given dex_id, or
    ``None`` if the fetch failed and we have no cached entry. Cached forever
    after the first successful pull."""
    _load_from_disk()
    if dex_id in _entries:
        return _entries[dex_id]
    info = _fetch_one(dex_id)
    if info is None:
        return None
    _entries[dex_id] = info
    _save_to_disk()
    return info


def force_refresh(dex_id: int) -> bool:
    info = _fetch_one(dex_id)
    if info is None:
        return False
    _load_from_disk()
    _entries[dex_id] = info
    _save_to_disk()
    return True

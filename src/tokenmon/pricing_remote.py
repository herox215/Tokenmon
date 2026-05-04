"""Live pricing for OpenRouter models, fetched from their /models endpoint.

OpenRouter's catalog has 300+ models that change weekly — too many to hardcode.
We pull the public pricing once per day (no auth needed) and cache it under
~/.tokenmon/openrouter_pricing.json. cost_for() falls back to this cache when
a model isn't in the in-tree pricing table.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tokenmon.pricing import ModelPrice
from tokenmon.storage import DB_DIR

# Match a trailing date suffix on model IDs: -20251001, -2025-10-01, etc.
_DATE_SUFFIX = re.compile(r"-\d{4}-?\d{2}-?\d{2}$")

log = logging.getLogger("tokenmon.pricing.remote")

CACHE_PATH = DB_DIR / "openrouter_pricing.json"
CACHE_TTL_SECONDS = 24 * 3600
MODELS_URL = "https://openrouter.ai/api/v1/models"
FETCH_TIMEOUT_SEC = 5.0

# In-memory cache (populated lazily on first lookup).
_loaded = False
_models: dict[str, ModelPrice] = {}


def _per_million(value: object) -> float:
    """OpenRouter pricing is per-token as decimal strings; convert to per-1M."""
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return 0.0


def fetch_openrouter_pricing(timeout: float = FETCH_TIMEOUT_SEC) -> dict[str, ModelPrice]:
    req = urllib.request.Request(MODELS_URL, headers={"User-Agent": "tokenmon/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    out: dict[str, ModelPrice] = {}
    for entry in data.get("data") or []:
        mid = entry.get("id")
        if not mid:
            continue
        pricing = entry.get("pricing") or {}
        out[mid] = ModelPrice(
            input=_per_million(pricing.get("prompt")),
            output=_per_million(pricing.get("completion")),
            cache_read=_per_million(pricing.get("input_cache_read")),
            cache_write=_per_million(pricing.get("input_cache_write")),
        )
    return out


def _save_cache(models: dict[str, ModelPrice]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": {
            k: {
                "input": v.input, "output": v.output,
                "cache_read": v.cache_read, "cache_write": v.cache_write,
            }
            for k, v in models.items()
        },
    }
    CACHE_PATH.write_text(json.dumps(payload, indent=2))


def _load_cache_from_disk() -> tuple[dict[str, ModelPrice], float | None]:
    """Returns (models, age_seconds_or_None_if_unknown)."""
    if not CACHE_PATH.exists():
        return {}, None
    try:
        data = json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        return {}, None
    age_s: float | None = None
    fetched_at_str = data.get("fetched_at")
    if fetched_at_str:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            age_s = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        except ValueError:
            pass
    models: dict[str, ModelPrice] = {}
    for mid, p in (data.get("models") or {}).items():
        try:
            models[mid] = ModelPrice(
                input=float(p.get("input", 0) or 0),
                output=float(p.get("output", 0) or 0),
                cache_read=float(p.get("cache_read", 0) or 0),
                cache_write=float(p.get("cache_write", 0) or 0),
            )
        except (TypeError, ValueError):
            continue
    return models, age_s


def ensure_loaded() -> dict[str, ModelPrice]:
    """Return the in-memory price map, refreshing it from disk and possibly
    from the OpenRouter API if the cache is missing or stale."""
    global _loaded, _models
    if _loaded and _models:
        return _models

    cached, age_s = _load_cache_from_disk()
    fresh_enough = cached and (age_s is not None and age_s < CACHE_TTL_SECONDS)
    if fresh_enough:
        _models = cached
        _loaded = True
        return _models

    # Cache missing, never timestamped, or stale — try to refresh.
    try:
        fresh = fetch_openrouter_pricing()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("OpenRouter pricing fetch failed: %s", exc)
        _models = cached  # may be empty
        _loaded = True
        return _models

    if fresh:
        try:
            _save_cache(fresh)
        except OSError:
            log.exception("failed to write pricing cache")
        _models = fresh
    else:
        _models = cached
    _loaded = True
    return _models


def _normalised_variants(model: str) -> list[str]:
    """Yield alternate model IDs to try, in priority order."""
    variants: list[str] = [model]
    stripped = _DATE_SUFFIX.sub("", model)
    if stripped != model:
        variants.append(stripped)
    # OpenRouter's catalog uses one word order (e.g. "claude-haiku-4.5") while
    # response IDs sometimes invert it ("claude-4.5-haiku"). Detect a "X.Y"
    # version part and swap with the next token.
    m = re.match(r"^([^/]+)/(.+)$", stripped)
    if m:
        vendor, rest = m.group(1), m.group(2)
        parts = rest.split("-")
        for i, part in enumerate(parts):
            if re.match(r"^\d+(\.\d+)+$", part) and i + 1 < len(parts):
                swapped = parts[:i] + [parts[i + 1], part] + parts[i + 2:]
                variants.append(f"{vendor}/{'-'.join(swapped)}")
    return variants


def lookup(model: str) -> ModelPrice | None:
    models = ensure_loaded()
    for candidate in _normalised_variants(model):
        if candidate in models:
            return models[candidate]
    return None


def force_refresh() -> bool:
    """Force a fetch from the API regardless of cache age. Returns True on success."""
    global _loaded, _models
    try:
        fresh = fetch_openrouter_pricing()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("OpenRouter pricing refresh failed: %s", exc)
        return False
    if not fresh:
        return False
    try:
        _save_cache(fresh)
    except OSError:
        log.exception("failed to write pricing cache")
    _models = fresh
    _loaded = True
    return True

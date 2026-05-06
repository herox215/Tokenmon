"""Weather + IP-geolocation fetcher with on-disk cache.

Mirrors the lazy-load + JSON-cache pattern from ``pokedex_remote.py``.
Two cached blobs live in ``~/.tokenmon/weather_cache.json``:

- ``location``: city + lat/lon from ipapi.co (7-day TTL)
- ``weather``:  current WMO code + temperature from Open-Meteo (30-min TTL)

Both fetches swallow every error and return ``None`` so a network blip
never breaks the encounter spawn — the caller falls back to neutral
spawning behavior.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.weather.remote")

CACHE_PATH = DB_DIR / "weather_cache.json"
LOCATION_TTL_SECONDS = 7 * 24 * 3600
WEATHER_TTL_SECONDS = 30 * 60
FETCH_TIMEOUT_SEC = 5.0

GEO_URL = "https://ipapi.co/json/"
WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&current_weather=true"
)


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    wmo: int
    temp_c: float
    city: str
    fetched_at: datetime
    # Open-Meteo's current_weather provides both. Wind direction follows
    # the meteorological convention: degrees from which the wind is
    # blowing, 0 = North, 90 = East, 180 = South, 270 = West.
    wind_kmh: float = 0.0
    wind_dir_deg: float = 0.0


# In-memory cache mirroring the on-disk JSON. Loaded lazily on first call.
_loaded = False
_cache: dict[str, dict] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(s: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _load_from_disk() -> None:
    global _loaded, _cache
    if _loaded:
        return
    if not CACHE_PATH.exists():
        _cache = {}
        _loaded = True
        return
    try:
        data = json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        _cache = {}
        _loaded = True
        return
    _cache = data if isinstance(data, dict) else {}
    _loaded = True


def _save_to_disk() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        CACHE_PATH.write_text(json.dumps(_cache, indent=2))
    except OSError:
        log.exception("failed to write weather cache")


def _is_fresh(blob: dict | None, ttl_seconds: int) -> bool:
    if not blob:
        return False
    ts = _parse_iso(blob.get("fetched_at", ""))
    if ts is None:
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age < ttl_seconds


def _fetch_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": "tokenmon/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("weather fetch failed (%s): %s", url, exc)
        return None


def _fetch_location() -> dict | None:
    """Resolve approximate location via IP. Returns the cache-shape blob
    or ``None`` on any error."""
    data = _fetch_json(GEO_URL)
    if not data:
        return None
    try:
        lat = float(data["latitude"])
        lon = float(data["longitude"])
    except (KeyError, TypeError, ValueError):
        log.warning("ipapi response missing lat/lon: %s", data)
        return None
    city = str(data.get("city") or "").strip() or "?"
    return {"lat": lat, "lon": lon, "city": city, "fetched_at": _now_iso()}


def _fetch_weather(lat: float, lon: float) -> dict | None:
    data = _fetch_json(WEATHER_URL.format(lat=lat, lon=lon))
    if not data:
        return None
    cw = data.get("current_weather") or {}
    try:
        wmo = int(cw["weathercode"])
        temp_c = float(cw["temperature"])
    except (KeyError, TypeError, ValueError):
        log.warning("open-meteo response missing fields: %s", data)
        return None
    # Wind is optional — old caches and partial responses both come
    # through with 0/0, which the animator interprets as "no drift".
    try:
        wind_kmh = float(cw.get("windspeed") or 0.0)
    except (TypeError, ValueError):
        wind_kmh = 0.0
    try:
        wind_dir = float(cw.get("winddirection") or 0.0)
    except (TypeError, ValueError):
        wind_dir = 0.0
    return {
        "wmo": wmo, "temp_c": temp_c,
        "wind_kmh": wind_kmh, "wind_dir_deg": wind_dir,
        "fetched_at": _now_iso(),
    }


def get_weather() -> WeatherSnapshot | None:
    """Return the current weather snapshot or ``None`` on any failure.

    Resolves the user's approximate location once (cached for 7 days) and
    fetches the current Open-Meteo weather (cached for 30 min). Errors at
    any step return ``None`` so the caller can fall back gracefully.
    """
    _load_from_disk()
    location = _cache.get("location")
    if not _is_fresh(location, LOCATION_TTL_SECONDS):
        location = _fetch_location()
        if location is None:
            return None
        _cache["location"] = location
        _save_to_disk()

    weather = _cache.get("weather")
    if not _is_fresh(weather, WEATHER_TTL_SECONDS):
        weather = _fetch_weather(float(location["lat"]), float(location["lon"]))
        if weather is None:
            return None
        _cache["weather"] = weather
        _save_to_disk()

    fetched = _parse_iso(weather["fetched_at"]) or datetime.now(timezone.utc)
    return WeatherSnapshot(
        wmo=int(weather["wmo"]),
        temp_c=float(weather["temp_c"]),
        city=str(location.get("city") or "?"),
        fetched_at=fetched,
        wind_kmh=float(weather.get("wind_kmh") or 0.0),
        wind_dir_deg=float(weather.get("wind_dir_deg") or 0.0),
    )


def clear_cache() -> None:
    """Drop both location and weather caches — next ``get_weather`` call
    will re-fetch from scratch. Useful for a 'Standort neu ermitteln'
    menu action."""
    global _cache, _loaded
    _cache = {}
    _loaded = True
    try:
        if CACHE_PATH.exists():
            CACHE_PATH.unlink()
    except OSError:
        log.exception("failed to delete weather cache")

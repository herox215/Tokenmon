"""Weather + IP-geolocation fetcher with on-disk cache."""
from __future__ import annotations

import io
import json
from contextlib import contextmanager

import pytest

from tokenmon.weather import remote


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Each test gets its own cache file + a fresh in-memory state so
    one test's writes don't leak into the next."""
    monkeypatch.setattr(remote, "CACHE_PATH", tmp_path / "weather_cache.json")
    monkeypatch.setattr(remote, "_loaded", False)
    monkeypatch.setattr(remote, "_cache", {})


@contextmanager
def _fake_response(payload: dict):
    """Mimic urllib.request.urlopen's context-manager + .read() shape."""
    class _Resp:
        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *exc):
            return False

        def read(self_inner):
            return json.dumps(payload).encode()

    yield _Resp()


def _patch_urls(monkeypatch, by_url: dict[str, dict | Exception]):
    """Route urlopen calls based on the URL prefix to canned responses
    (or raised exceptions, for failure-case tests)."""
    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for prefix, value in by_url.items():
            if url.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return _fake_response(value).__enter__()
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(remote.urllib.request, "urlopen", fake_urlopen)


def test_get_weather_happy_path(monkeypatch):
    _patch_urls(monkeypatch, {
        "https://ipapi.co/": {"latitude": 52.52, "longitude": 13.41, "city": "Berlin"},
        "https://api.open-meteo.com/": {
            "current_weather": {
                "weathercode": 61, "temperature": 8.4,
                "windspeed": 18.5, "winddirection": 270,
            },
        },
    })
    snap = remote.get_weather()
    assert snap is not None
    assert snap.wmo == 61
    assert snap.temp_c == 8.4
    assert snap.city == "Berlin"
    assert snap.wind_kmh == 18.5
    assert snap.wind_dir_deg == 270


def test_get_weather_handles_missing_wind_fields(monkeypatch):
    """Old caches and partial responses leave wind=0 instead of failing."""
    _patch_urls(monkeypatch, {
        "https://ipapi.co/": {"latitude": 1.0, "longitude": 2.0, "city": "X"},
        "https://api.open-meteo.com/": {
            "current_weather": {"weathercode": 0, "temperature": 20.0},
        },
    })
    snap = remote.get_weather()
    assert snap is not None
    assert snap.wind_kmh == 0.0
    assert snap.wind_dir_deg == 0.0


def test_get_weather_caches_within_ttl(monkeypatch):
    """Second call within the TTL must not hit the network."""
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        call_count["n"] += 1
        url = req.full_url
        if url.startswith("https://ipapi.co/"):
            return _fake_response({"latitude": 1.0, "longitude": 2.0, "city": "X"}).__enter__()
        return _fake_response({
            "current_weather": {"weathercode": 0, "temperature": 20.0},
        }).__enter__()

    monkeypatch.setattr(remote.urllib.request, "urlopen", fake_urlopen)
    first = remote.get_weather()
    second = remote.get_weather()
    assert first is not None and second is not None
    assert call_count["n"] == 2  # one geo + one weather, no second-call refetch


def test_get_weather_returns_none_on_geo_failure(monkeypatch):
    _patch_urls(monkeypatch, {
        "https://ipapi.co/": OSError("network down"),
    })
    assert remote.get_weather() is None


def test_get_weather_returns_none_on_weather_failure(monkeypatch):
    _patch_urls(monkeypatch, {
        "https://ipapi.co/": {"latitude": 1.0, "longitude": 2.0, "city": "X"},
        "https://api.open-meteo.com/": OSError("api down"),
    })
    assert remote.get_weather() is None


def test_get_weather_returns_none_when_geo_payload_invalid(monkeypatch):
    """Malformed ipapi response (missing lat/lon) → None, no crash."""
    _patch_urls(monkeypatch, {
        "https://ipapi.co/": {"city": "Nowhere"},  # no lat/lon
    })
    assert remote.get_weather() is None


def test_clear_cache_drops_disk_and_memory(monkeypatch, tmp_path):
    _patch_urls(monkeypatch, {
        "https://ipapi.co/": {"latitude": 1.0, "longitude": 2.0, "city": "X"},
        "https://api.open-meteo.com/": {
            "current_weather": {"weathercode": 0, "temperature": 20.0},
        },
    })
    snap = remote.get_weather()
    assert snap is not None
    assert remote.CACHE_PATH.exists()
    remote.clear_cache()
    assert not remote.CACHE_PATH.exists()
    assert remote._cache == {}

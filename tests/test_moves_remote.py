"""Tests for the PokeAPI move-data fetcher.

We never hit the network in tests — ``urllib.request.urlopen`` is
monkey-patched. The cache file lives under ``~/.tokenmon/moves_cache.json``
which the existing ``_isolate_db`` autouse fixture redirects per test.
"""
from __future__ import annotations

import io
import json

import pytest


@pytest.fixture(autouse=True)
def _reset_module_cache():
    """Clear the in-memory cache before each test so disk redirection
    via the autouse db fixture takes effect."""
    from tokenmon import moves_remote
    moves_remote.clear_cache()
    # Also reset the module-level CACHE_PATH because the test redirects
    # DB_DIR — re-import isn't enough since it was bound at import time.
    from tokenmon.storage import DB_DIR
    moves_remote.CACHE_PATH = DB_DIR / "moves_cache.json"
    yield
    moves_remote.clear_cache()


def _fake_payload(name="tackle", type_="normal", category="physical",
                  power=40, accuracy=100, pp=35, priority=0) -> dict:
    return {
        "name": name,
        "type": {"name": type_},
        "damage_class": {"name": category},
        "power": power,
        "accuracy": accuracy,
        "pp": pp,
        "priority": priority,
    }


class _FakeResponse:
    def __init__(self, payload: dict):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_get_move_returns_typed_move(monkeypatch, db_path):
    from tokenmon import moves_remote

    def fake_urlopen(req, timeout):
        return _FakeResponse(_fake_payload())

    monkeypatch.setattr(moves_remote.urllib.request, "urlopen", fake_urlopen)
    move = moves_remote.get_move_data("tackle")
    assert move is not None
    assert move.key == "tackle"
    assert move.type == "normal"
    assert move.category == "physical"
    assert move.power == 40
    assert move.accuracy == 100
    assert move.pp == 35


def test_get_move_caches_to_disk(monkeypatch, db_path):
    """First call hits the network; second call sees the cache."""
    from tokenmon import moves_remote

    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        return _FakeResponse(_fake_payload())

    monkeypatch.setattr(moves_remote.urllib.request, "urlopen", fake_urlopen)
    a = moves_remote.get_move_data("tackle")
    b = moves_remote.get_move_data("tackle")
    assert a == b
    assert calls["n"] == 1


def test_status_move_has_null_power(monkeypatch, db_path):
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(
            _fake_payload("growl", category="status", power=None, accuracy=100),
        ),
    )
    move = moves_remote.get_move_data("growl")
    assert move is not None
    assert move.category == "status"
    assert move.power is None


def test_never_miss_move_has_null_accuracy(monkeypatch, db_path):
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(
            _fake_payload("swift", power=60, accuracy=None),
        ),
    )
    move = moves_remote.get_move_data("swift")
    assert move is not None
    assert move.accuracy is None


def test_network_failure_returns_none(monkeypatch, db_path):
    from tokenmon import moves_remote

    def fake_urlopen(req, timeout):
        raise OSError("simulated timeout")

    monkeypatch.setattr(moves_remote.urllib.request, "urlopen", fake_urlopen)
    assert moves_remote.get_move_data("nonexistent") is None


def test_malformed_payload_returns_none(monkeypatch, db_path):
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse({"name": "x"}),  # missing fields
    )
    assert moves_remote.get_move_data("x") is None


def test_unknown_category_returns_none(monkeypatch, db_path):
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(
            _fake_payload(category="bogus"),
        ),
    )
    assert moves_remote.get_move_data("tackle") is None

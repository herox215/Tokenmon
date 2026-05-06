"""Learnset-remote tests: parse + cache + initial_moves + moves_at_level."""
from __future__ import annotations

import io
import json

import pytest


@pytest.fixture(autouse=True)
def _reset_cache():
    from tokenmon import learnsets_remote
    from tokenmon.storage import DB_DIR
    learnsets_remote.clear_cache()
    learnsets_remote.CACHE_PATH = DB_DIR / "learnsets.json"
    yield
    learnsets_remote.clear_cache()


def _payload(moves: list[tuple[str, int, str]]) -> dict:
    """``moves`` is list of (move_name, level, version_group)."""
    arr = []
    for name, lvl, vg in moves:
        arr.append({
            "move": {"name": name},
            "version_group_details": [{
                "level_learned_at": lvl,
                "move_learn_method": {"name": "level-up"},
                "version_group": {"name": vg},
            }],
        })
    return {"moves": arr}


class _FakeResp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())
    def read(self):
        return self._buf.read()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_parse_red_blue_moves(monkeypatch, db_path):
    from tokenmon import learnsets_remote
    monkeypatch.setattr(
        learnsets_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(_payload([
            ("tackle", 1, "red-blue"),
            ("growl", 1, "red-blue"),
            ("vine-whip", 7, "red-blue"),
        ])),
    )
    learnset = learnsets_remote.get_learnset(1)
    # Sorted ascending by level.
    assert (1, "growl") in learnset
    assert (1, "tackle") in learnset
    assert (7, "vine-whip") in learnset
    assert len(learnset) == 3


def test_caches_after_first_fetch(monkeypatch, db_path):
    from tokenmon import learnsets_remote
    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        return _FakeResp(_payload([("tackle", 1, "red-blue")]))

    monkeypatch.setattr(learnsets_remote.urllib.request, "urlopen", fake_urlopen)
    learnsets_remote.get_learnset(1)
    learnsets_remote.get_learnset(1)
    assert calls["n"] == 1


def test_skips_non_level_up_methods(monkeypatch, db_path):
    """Egg/TM moves shouldn't appear in the level-up learnset."""
    from tokenmon import learnsets_remote
    payload = {
        "moves": [{
            "move": {"name": "fire-blast"},
            "version_group_details": [{
                "level_learned_at": 0,
                "move_learn_method": {"name": "machine"},
                "version_group": {"name": "red-blue"},
            }],
        }, {
            "move": {"name": "tackle"},
            "version_group_details": [{
                "level_learned_at": 1,
                "move_learn_method": {"name": "level-up"},
                "version_group": {"name": "red-blue"},
            }],
        }],
    }
    monkeypatch.setattr(
        learnsets_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(payload),
    )
    learnset = learnsets_remote.get_learnset(1)
    assert (1, "tackle") in learnset
    assert all(name != "fire-blast" for _, name in learnset)


def test_network_failure_returns_empty(monkeypatch, db_path):
    from tokenmon import learnsets_remote
    monkeypatch.setattr(
        learnsets_remote.urllib.request, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("nope")),
    )
    assert learnsets_remote.get_learnset(1) == []


def test_initial_moves_picks_latest_four(monkeypatch, db_path):
    from tokenmon import learnsets_remote
    monkeypatch.setattr(
        learnsets_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(_payload([
            ("tackle", 1, "red-blue"),
            ("growl", 1, "red-blue"),
            ("leech-seed", 7, "red-blue"),
            ("vine-whip", 13, "red-blue"),
            ("poison-powder", 15, "red-blue"),
            ("razor-leaf", 19, "red-blue"),
            ("growth", 25, "red-blue"),
        ])),
    )
    moves = learnsets_remote.initial_moves(1, 20)
    # At level 20, should know moves at levels ≤ 20 — pick the four
    # latest: razor-leaf (19), poison-powder (15), vine-whip (13),
    # leech-seed (7).
    assert moves[0] == "razor-leaf"
    assert "growth" not in moves
    assert len(moves) == 4


def test_initial_moves_fallback_to_tackle(monkeypatch, db_path):
    from tokenmon import learnsets_remote
    monkeypatch.setattr(
        learnsets_remote.urllib.request, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("nope")),
    )
    assert learnsets_remote.initial_moves(99, 5) == ["tackle"]


def test_moves_at_level_returns_only_that_level(monkeypatch, db_path):
    from tokenmon import learnsets_remote
    monkeypatch.setattr(
        learnsets_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(_payload([
            ("tackle", 1, "red-blue"),
            ("growl", 1, "red-blue"),
            ("vine-whip", 13, "red-blue"),
            ("leech-seed", 7, "red-blue"),
        ])),
    )
    assert sorted(learnsets_remote.moves_at_level(1, 1)) == ["growl", "tackle"]
    assert learnsets_remote.moves_at_level(1, 7) == ["leech-seed"]
    assert learnsets_remote.moves_at_level(1, 12) == []

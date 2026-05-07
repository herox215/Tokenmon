"""Trainer-sprite slug + cache tests."""
from __future__ import annotations

import io

import pytest

from tokenmon import trainers_remote


def test_slug_lowercase_and_hyphenated():
    assert trainers_remote._slug_for("Bug Catcher") == "bug-catcher"
    assert trainers_remote._slug_for("Lass") == "lass"
    assert trainers_remote._slug_for("Black Belt") == "black-belt"


def test_slug_strips_accents():
    assert trainers_remote._slug_for("PokéManiac") == "pokemaniac"


def test_slug_override_table_wins():
    """Even if normalize would produce a different slug, an override
    forces the canonical PokeAPI value (e.g. ``School Kid`` →
    ``school-kid`` matches their hyphenated form)."""
    assert trainers_remote._slug_for("Schoolkid") == "school-kid"


def test_cache_hit_skips_network(tmp_path, monkeypatch, db_path):
    # Pre-populate the cache.
    monkeypatch.setattr(trainers_remote, "SPRITE_DIR", tmp_path)
    p = tmp_path / "lass.png"
    p.write_bytes(b"\x89PNG cached")
    monkeypatch.setattr(
        trainers_remote.urllib.request, "urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("network is down")),
    )
    result = trainers_remote.ensure_trainer_sprite("Lass")
    assert result == p


def test_cache_miss_downloads(tmp_path, monkeypatch, db_path):
    monkeypatch.setattr(trainers_remote, "SPRITE_DIR", tmp_path)

    class _FakeResp:
        def __init__(self):
            self._buf = io.BytesIO(b"\x89PNG fake")
        def read(self):
            return self._buf.read()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        trainers_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResp(),
    )
    result = trainers_remote.ensure_trainer_sprite("Bug Catcher")
    assert result is not None
    assert result.name == "bug-catcher.png"
    assert result.read_bytes() == b"\x89PNG fake"


def test_network_failure_returns_none(tmp_path, monkeypatch, db_path):
    monkeypatch.setattr(trainers_remote, "SPRITE_DIR", tmp_path)
    monkeypatch.setattr(
        trainers_remote.urllib.request, "urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("404")),
    )
    assert trainers_remote.ensure_trainer_sprite("Lass") is None
    # No partial file lingering on disk.
    assert not (tmp_path / "lass.png").exists()

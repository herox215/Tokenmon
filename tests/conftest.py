"""Shared pytest fixtures.

Two crucial autouse fixtures keep tests from clobbering the user's real DB
and from hitting the network:

  * ``_isolate_db`` redirects ``DB_PATH`` (and the cached ``DB_DIR``) to a
    per-test ``tmp_path``. A test that forgets to pass ``path=`` to a storage
    helper now fails loudly instead of silently writing to ``~/.tokenmon``.
  * ``_isolate_sprites`` redirects sprite cache directories to ``tmp_path``
    and replaces ``ensure_sprite`` with a stub that returns a fake on-disk
    path without ever calling ``urllib``. Tests that exercise the real
    network would be slow and flaky; tests that need a real sprite path can
    just create the file themselves under the redirected dir.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Redirect storage paths to a fresh tmp_path before any test runs."""
    db_dir = tmp_path / "tokenmon"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "usage.db"

    # Patch every place these paths are referenced. ``storage.DB_PATH`` is the
    # module-level default; we also replace it on submodules that re-export
    # it (post-Wave-B). The try/except keeps this robust against the
    # in-flight package split.
    import tokenmon.storage as storage
    from tokenmon.storage import _db as storage_db
    monkeypatch.setattr(storage, "DB_DIR", db_dir, raising=False)
    monkeypatch.setattr(storage, "DB_PATH", db_path, raising=False)
    monkeypatch.setattr(storage_db, "DB_DIR", db_dir, raising=False)
    monkeypatch.setattr(storage_db, "DB_PATH", db_path, raising=False)
    # Submodules import DB_PATH via ``from ._db import DB_PATH`` and
    # bind it at module-load time. Patching ``_db.DB_PATH`` alone
    # doesn't propagate to those bindings, so we patch each submodule
    # explicitly. ``raising=False`` makes this resilient to future
    # renames / additions.
    for sub in (
        "encounter", "pokedex", "pokemon", "usage",
        "player", "moves", "pending_moves", "trainers",
    ):
        try:
            mod = __import__(
                f"tokenmon.storage.{sub}", fromlist=["*"],
            )
            monkeypatch.setattr(mod, "DB_PATH", db_path, raising=False)
            monkeypatch.setattr(mod, "DB_DIR", db_dir, raising=False)
        except ImportError:
            continue
    yield db_path


@pytest.fixture(autouse=True)
def _isolate_sprites(tmp_path, monkeypatch):
    """Redirect sprite cache + stub the network fetcher."""
    sprite_dir = tmp_path / "sprites"
    shiny_dir = tmp_path / "sprites_shiny"
    back_dir = tmp_path / "sprites_back"
    shiny_back_dir = tmp_path / "sprites_back_shiny"
    for d in (sprite_dir, shiny_dir, back_dir, shiny_back_dir):
        d.mkdir(parents=True, exist_ok=True)

    import tokenmon.pokemon as pkmn

    # Patch BOTH the package namespace and the submodule (post-Wave-D split)
    # so ensure_sprite — defined in tokenmon.pokemon.sprites — sees the
    # redirected dirs when it does direct module-attribute lookups.
    monkeypatch.setattr(pkmn, "SPRITE_DIR", sprite_dir, raising=False)
    monkeypatch.setattr(pkmn, "SHINY_SPRITE_DIR", shiny_dir, raising=False)
    monkeypatch.setattr(pkmn, "BACK_SPRITE_DIR", back_dir, raising=False)
    monkeypatch.setattr(
        pkmn, "SHINY_BACK_SPRITE_DIR", shiny_back_dir, raising=False
    )
    try:
        from tokenmon.pokemon import sprites as _sprites_mod
        monkeypatch.setattr(_sprites_mod, "SPRITE_DIR", sprite_dir, raising=False)
        monkeypatch.setattr(_sprites_mod, "SHINY_SPRITE_DIR", shiny_dir, raising=False)
        monkeypatch.setattr(_sprites_mod, "BACK_SPRITE_DIR", back_dir, raising=False)
        monkeypatch.setattr(
            _sprites_mod, "SHINY_BACK_SPRITE_DIR", shiny_back_dir, raising=False
        )
    except ImportError:
        pass  # Pre-Wave-D layout — no sprites submodule yet.

    def _fake_ensure_sprite(dex_id, timeout=5.0, *, shiny=False, back=False):
        if back:
            base = shiny_back_dir if shiny else back_dir
        else:
            base = shiny_dir if shiny else sprite_dir
        target = base / f"{int(dex_id)}.gif"
        if not target.exists():
            target.write_bytes(b"GIF89a fake")
        return target

    monkeypatch.setattr(pkmn, "ensure_sprite", _fake_ensure_sprite, raising=False)
    yield sprite_dir, shiny_dir


@pytest.fixture
def db_path(_isolate_db):
    """Initialised, empty test DB."""
    from tokenmon.storage import init_db
    init_db(_isolate_db)
    return _isolate_db


def fresh_conn(path: Path) -> sqlite3.Connection:
    """Test helper for raw SQL queries."""
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

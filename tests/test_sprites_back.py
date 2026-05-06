"""Back-sprite cache tests for tokenmon.pokemon.sprites.

The autouse `_isolate_sprites` fixture in conftest.py redirects all four
cache dirs to tmp_path AND stubs the package-level ensure_sprite. To
exercise the real downloader logic (with the fallback chain), these
tests reach into the submodule and stub `_try_download` directly.
"""
from __future__ import annotations

import pytest


def test_back_sprite_returns_animated_when_available(tmp_path, monkeypatch):
    from tokenmon.pokemon import sprites as s
    calls: list[str] = []

    def fake_dl(url, dest, timeout):
        calls.append(url)
        # Pretend the animated GIF download succeeds.
        if url.endswith(".gif"):
            dest.write_bytes(b"GIF89a fake")
            return True
        return False

    monkeypatch.setattr(s, "_try_download", fake_dl)
    p = s.ensure_sprite(25, back=True)
    assert p is not None
    assert p.suffix == ".gif"
    assert p.exists()
    # Only the GIF URL was tried — PNG fallback shouldn't have fired.
    assert len(calls) == 1
    assert "back/" in calls[0] and calls[0].endswith(".gif")


def test_back_sprite_falls_back_to_static_png(tmp_path, monkeypatch):
    from tokenmon.pokemon import sprites as s
    calls: list[str] = []

    def fake_dl(url, dest, timeout):
        calls.append(url)
        if url.endswith(".gif"):
            return False  # animated 404
        if url.endswith(".png"):
            dest.write_bytes(b"\x89PNG fake")
            return True
        return False

    monkeypatch.setattr(s, "_try_download", fake_dl)
    p = s.ensure_sprite(906, back=True)  # Sprigatito — gen-IX, no animated back
    assert p is not None
    assert p.suffix == ".png"
    assert p.exists()
    assert len(calls) == 2  # gif tried first, png second


def test_back_sprite_returns_none_when_both_fail(monkeypatch):
    from tokenmon.pokemon import sprites as s
    monkeypatch.setattr(s, "_try_download", lambda *a, **kw: False)
    p = s.ensure_sprite(9999, back=True)
    assert p is None


def test_back_sprite_cache_hit_uses_gif_when_present(monkeypatch):
    from tokenmon.pokemon import sprites as s
    calls: list[str] = []
    monkeypatch.setattr(
        s, "_try_download",
        lambda *a, **kw: calls.append("download_attempted") or False,
    )
    # Pre-populate the cache with a GIF.
    cached = s.BACK_SPRITE_DIR / "1.gif"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"GIF89a cached")
    p = s.ensure_sprite(1, back=True)
    assert p == cached
    assert calls == [], "no download should be attempted on a cache hit"


def test_back_sprite_cache_hit_uses_png_when_no_gif(monkeypatch):
    from tokenmon.pokemon import sprites as s
    calls: list[str] = []
    monkeypatch.setattr(
        s, "_try_download",
        lambda *a, **kw: calls.append("download_attempted") or False,
    )
    cached = s.BACK_SPRITE_DIR / "906.png"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"\x89PNG cached")
    p = s.ensure_sprite(906, back=True)
    assert p == cached
    assert calls == []


def test_shiny_back_uses_separate_cache_dir(monkeypatch):
    from tokenmon.pokemon import sprites as s
    captured_urls: list[str] = []

    def fake_dl(url, dest, timeout):
        captured_urls.append(url)
        if "back/shiny" in url and url.endswith(".gif"):
            dest.write_bytes(b"GIF89a shiny back")
            return True
        return False

    monkeypatch.setattr(s, "_try_download", fake_dl)
    p = s.ensure_sprite(25, back=True, shiny=True)
    assert p is not None
    assert "sprites_back_shiny" in str(p) or p.parent == s.SHINY_BACK_SPRITE_DIR
    assert "back/shiny" in captured_urls[0]


def test_front_sprite_path_unchanged_by_back_param():
    """Calling with back=False should hit the existing front-sprite path."""
    from tokenmon.pokemon import sprites as s
    p = s.sprite_path(25)
    assert p == s.SPRITE_DIR / "25.gif"
    p_shiny = s.sprite_path(25, shiny=True)
    assert p_shiny == s.SHINY_SPRITE_DIR / "25.gif"

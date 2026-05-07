"""Trainer-avatar emoji-map tests."""
from __future__ import annotations

from tokenmon import trainers_remote


def test_known_titles_map_to_themed_emoji():
    assert trainers_remote.emoji_for("Bug Catcher") == "🐛"
    assert trainers_remote.emoji_for("Lass") == "👧"
    assert trainers_remote.emoji_for("Black Belt") == "🥋"
    assert trainers_remote.emoji_for("PokéManiac") == "🤓"


def test_unknown_title_returns_default():
    assert (
        trainers_remote.emoji_for("Bogus Class")
        == trainers_remote.DEFAULT_EMOJI
    )


def test_ensure_trainer_sprite_returns_none():
    """Compatibility shim — never returns a Path because PokeAPI doesn't
    ship trainer-class sprites. Callers must fall back to emoji_for."""
    assert trainers_remote.ensure_trainer_sprite("Bug Catcher") is None
    assert trainers_remote.ensure_trainer_sprite("Lass") is None


def test_every_pool_title_has_an_emoji():
    """Defensive: nothing in the live trainer-name pool should fall
    through to the generic default. New titles added to ``battle.names``
    should also land in the emoji map."""
    from tokenmon.battle.names import TITLES
    fallbacks = [
        t for t in TITLES if t not in trainers_remote._TITLE_EMOJI
    ]
    assert fallbacks == [], (
        f"these titles need a themed emoji: {fallbacks}"
    )

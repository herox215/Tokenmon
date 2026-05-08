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


def test_ensure_trainer_sprite_returns_path_for_known_titles():
    """Bundled PixelLab sprites live at ``data/trainer_sprites/<slug>.png``.
    Known titles should resolve to an existing file; unknown titles fall
    through to ``None`` so the caller renders the emoji glyph."""
    bug = trainers_remote.ensure_trainer_sprite("Bug Catcher")
    assert bug is not None
    assert bug.exists() and bug.suffix == ".png"
    assert trainers_remote.ensure_trainer_sprite("Bogus Class") is None


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

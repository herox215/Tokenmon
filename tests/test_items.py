"""Items registry shape + ball modifiers."""
from __future__ import annotations

import pytest

from tokenmon import items


@pytest.mark.parametrize("key", ["pokeball", "greatball", "ultraball", "masterball"])
def test_ball_in_registry(key):
    assert key in items.ITEMS
    assert items.ITEMS[key].sprite_name is not None


@pytest.mark.parametrize("key", ["pokeball", "greatball", "ultraball", "masterball"])
def test_ball_is_throwable(key):
    assert items.is_throwable(key) is True


def test_unknown_item_not_throwable():
    assert items.is_throwable("rock") is False


def test_ball_catch_modifiers_sane():
    assert items.BALL_CATCH_MODIFIERS["pokeball"] == 1.0
    assert items.BALL_CATCH_MODIFIERS["greatball"] == 1.5
    assert items.BALL_CATCH_MODIFIERS["ultraball"] == 2.0
    assert items.BALL_CATCH_MODIFIERS["masterball"] == 255.0


def test_get_unknown_returns_none():
    assert items.get("not-a-thing") is None


def test_all_keys_returns_list():
    keys = items.all_keys()
    assert "pokeball" in keys
    assert "masterball" in keys

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


# ---- Bug 5: bag pockets -------------------------------------------------

VALID_POCKETS = {"balls", "medicine", "evolution", "misc"}


def test_every_item_has_pocket():
    """Every registered item declares a pocket from the known set."""
    for key, item in items.ITEMS.items():
        assert item.pocket in VALID_POCKETS, (
            f"item {key!r} has unknown pocket {item.pocket!r}"
        )


def test_pockets_constant_shape():
    """``POCKETS`` lists the four bag tabs in display order."""
    keys = [pk for pk, _ in items.POCKETS]
    assert keys == ["balls", "medicine", "evolution", "misc"]
    for _, label in items.POCKETS:
        assert isinstance(label, str) and label


def test_items_in_pocket_balls():
    """The balls pocket contains exactly the four catch balls."""
    keys = {k for k, _ in items.items_in_pocket("balls")}
    assert keys == {"pokeball", "greatball", "ultraball", "masterball"}


def test_items_in_pocket_medicine():
    """The medicine pocket contains at least the basic potion."""
    keys = {k for k, _ in items.items_in_pocket("medicine")}
    assert "potion" in keys


def test_items_in_pocket_evolution():
    """The evolution pocket contains the five elemental stones."""
    keys = {k for k, _ in items.items_in_pocket("evolution")}
    assert keys == {
        "fire-stone", "water-stone", "thunder-stone",
        "leaf-stone", "moon-stone",
    }


def test_items_in_pocket_unknown_pocket_returns_empty():
    assert items.items_in_pocket("not-a-pocket") == []


# ---- HP modifier (Phase 3) ----------------------------------------------


def test_hp_modifier_full():
    """At full HP the canonical Gen-1/3+ HP factor is exactly 1/3 — the
    "worst case" multiplier. Lower HP shifts toward the 1.0 ceiling."""
    assert items.hp_modifier(40, 40) == pytest.approx(1.0 / 3.0)


def test_hp_modifier_at_one_hp():
    """Almost-zero HP saturates near 1.0. With hp_current=1, hp_max=40 the
    formula gives (3*40 - 2*1)/(3*40) ≈ 0.983."""
    val = items.hp_modifier(1, 40)
    assert 0.97 <= val <= 1.0


def test_hp_modifier_clamped():
    """Bogus inputs (zero/negative max) fall back to 1.0 (no effect).
    hp_current > hp_max clamps to the 1/3 floor."""
    assert items.hp_modifier(0, 0) == 1.0
    assert items.hp_modifier(-5, 40) == pytest.approx(1.0)
    # hp_current > hp_max → raw is below 1/3, clamped up to 1/3.
    assert items.hp_modifier(80, 40) == pytest.approx(1.0 / 3.0)

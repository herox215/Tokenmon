"""Tests for ShopController — view build + buy flow guards."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeApp:
    pass


class _FakePopover:
    def __init__(self):
        self._app = _FakeApp()
        self._show_pane_calls: list[int] = []

    def _show_pane(self, idx: int) -> None:
        self._show_pane_calls.append(idx)


def test_shop_controller_view_builds(db_path):
    from tokenmon.popover.panes.shop import ShopController
    pop = _FakePopover()
    ctrl = ShopController(pop)
    view = ctrl.build_view()
    assert view is not None


def test_buy_pokeball_succeeds(db_path):
    """$1000 → -$200, +1 pokeball, pane re-renders."""
    from tokenmon import storage
    from tokenmon.popover.panes.shop import ShopController
    from tokenmon.popover.widgets import PANE_SHOP

    storage.set_money(1000, path=db_path)
    pop = _FakePopover()
    ctrl = ShopController(pop)
    ctrl._handle_buy("pokeball")

    assert storage.get_money(path=db_path) == 800
    assert storage.query_item_counts(["pokeball"], path=db_path)["pokeball"] == 1
    assert pop._show_pane_calls == [PANE_SHOP]


def test_buy_blocked_when_broke(db_path):
    """Insufficient funds → no money change, no inventory change."""
    from tokenmon import storage
    from tokenmon.popover.panes.shop import ShopController

    storage.set_money(50, path=db_path)
    pop = _FakePopover()
    ctrl = ShopController(pop)
    ctrl._handle_buy("pokeball")  # costs 200

    assert storage.get_money(path=db_path) == 50
    assert storage.query_item_counts(["pokeball"], path=db_path)["pokeball"] == 0
    assert pop._show_pane_calls == []


def test_buy_blocked_at_cap(db_path):
    """At cap (99) → buy attempt is a no-op even with money."""
    from tokenmon import storage
    from tokenmon.popover.panes.shop import ShopController

    storage.set_money(10_000, path=db_path)
    storage.add_to_inventory("pokeball", 99, path=db_path)
    pop = _FakePopover()
    ctrl = ShopController(pop)
    ctrl._handle_buy("pokeball")

    assert storage.get_money(path=db_path) == 10_000
    assert storage.query_item_counts(["pokeball"], path=db_path)["pokeball"] == 99
    assert pop._show_pane_calls == []


def test_buy_unknown_item_noop(db_path):
    """Item without shop_price (e.g. masterball) → no-op."""
    from tokenmon import storage
    from tokenmon.popover.panes.shop import ShopController

    storage.set_money(1_000_000, path=db_path)
    pop = _FakePopover()
    ctrl = ShopController(pop)
    ctrl._handle_buy("masterball")

    assert storage.get_money(path=db_path) == 1_000_000
    assert storage.query_item_counts(["masterball"], path=db_path)["masterball"] == 0
    assert pop._show_pane_calls == []


def test_shop_items_helper_excludes_unsold(db_path):
    """``items.shop_items()`` returns the 5 priced items, in order, excluding
    masterball + stones."""
    from tokenmon import items

    keys = [k for k, _ in items.shop_items()]
    assert keys == ["pokeball", "greatball", "ultraball", "potion", "ether"]
    assert "masterball" not in keys
    assert "fire-stone" not in keys

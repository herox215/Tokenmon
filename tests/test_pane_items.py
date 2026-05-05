"""Tests for ItemsController + drop-claim animation (Phase 3d)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeApp:
    def _refresh_pokemon_state(self):
        pass


class _FakePopover:
    def __init__(self, claim_active: bool = False):
        self._app = _FakeApp()
        self._claim_active = claim_active
        self._claim_handler = None
        self._claim_payload: dict[str, int] = {}
        self._claim_views: list = []
        self._show_pane_calls: list[int] = []

    def _show_pane(self, idx: int) -> None:
        self._show_pane_calls.append(idx)

    def _refresh_sidebar_pokemon_icon(self) -> None:
        pass


def test_items_controller_list_view_builds(db_path):
    from tokenmon.popover.panes.items import ItemsController
    pop = _FakePopover(claim_active=False)
    ctrl = ItemsController(pop)
    view = ctrl.build_view()
    assert view is not None


def test_items_controller_claim_view_builds_with_payload(db_path):
    from tokenmon.popover.panes.items import ItemsController
    pop = _FakePopover(claim_active=True)
    pop._claim_payload = {"pokeball": 2, "fire-stone": 1}
    ctrl = ItemsController(pop)
    view = ctrl.build_view()
    assert view is not None
    # Each payload entry creates a sprite/label record.
    assert len(pop._claim_views) == 2


def test_begin_drop_claim_animation_skips_when_already_active(db_path):
    from tokenmon.popover.panes.items import ItemsController
    pop = _FakePopover(claim_active=True)
    ctrl = ItemsController(pop)
    ctrl.begin_drop_claim_animation({"pokeball": 1})
    # Already active → must NOT start another animation cycle.
    assert pop._show_pane_calls == []
    assert pop._claim_handler is None


def test_begin_drop_claim_animation_skips_when_pending_empty(db_path):
    from tokenmon.popover.panes.items import ItemsController
    pop = _FakePopover(claim_active=False)
    ctrl = ItemsController(pop)
    ctrl.begin_drop_claim_animation({})
    assert pop._show_pane_calls == []
    assert pop._claim_active is False


def test_claim_step_done_clears_active_flag(db_path, monkeypatch):
    from tokenmon.popover.panes.items import ItemsController
    import tokenmon.storage as storage
    monkeypatch.setattr(storage, "claim_pending_drops", lambda: {})
    pop = _FakePopover(claim_active=True)
    pop._claim_payload = {"pokeball": 1}
    pop._claim_views = [{}]  # placeholder
    ctrl = ItemsController(pop)
    ctrl.claim_step("done")
    assert pop._claim_active is False
    assert pop._claim_payload == {}
    assert pop._claim_views == []


def test_claim_step_unknown_action_is_no_op(db_path):
    from tokenmon.popover.panes.items import ItemsController
    pop = _FakePopover(claim_active=True)
    pop._claim_views = []
    ctrl = ItemsController(pop)
    # Should not raise, even with no views.
    ctrl.claim_step("totally_unknown")
    assert pop._claim_active is True  # unchanged

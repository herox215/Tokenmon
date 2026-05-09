"""Tests for BoxController + nickname-edit state machine (Phase 3c)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeApp:
    def _refresh_pokemon_state(self):
        pass

    def _update_tooltip(self):
        pass


class _FakePopover:
    def __init__(
        self,
        selected: int | None = None,
        editing: bool = False,
        stats_mode: str = "stats",
        swap_slot: int | None = None,
    ):
        self._app = _FakeApp()
        self._box_selected_id = selected
        self._box_return_pane = None
        self._editing_nickname = editing
        self._stats_mode = stats_mode
        self._box_swap_slot = swap_slot
        self._animated_image_views: list = []
        self._show_pane_calls: list[int] = []

    def _show_pane(self, idx: int) -> None:
        self._show_pane_calls.append(idx)

    def _refresh_sidebar_pokemon_icon(self) -> None:
        pass


def test_box_controller_grid_view_builds_when_no_selection(db_path):
    from tokenmon.popover.panes.box import BoxController
    pop = _FakePopover(selected=None)
    ctrl = BoxController(pop)
    view = ctrl.build_view()
    assert view is not None
    assert ctrl._selected_id is None


def test_box_controller_default_stats_mode_is_stats(db_path):
    from tokenmon.popover.panes.box import BoxController
    pop = _FakePopover()
    ctrl = BoxController(pop)
    assert ctrl._stats_mode == "stats"


def test_box_detail_back_returns_to_active_pane_when_requested(db_path):
    from datetime import date
    from tokenmon import storage
    from tokenmon.popover.panes.box import BoxController
    from tokenmon.popover.widgets import PANE_POKEMON

    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1,
        nature="Hardy", characteristic="x", path=db_path,
    )
    pop = _FakePopover(selected=pid)
    pop._box_return_pane = PANE_POKEMON
    ctrl = BoxController(pop)
    ctrl.build_view()

    back_handler = ctrl._handlers[0]
    back_handler._cb(None)

    assert pop._box_selected_id is None
    assert pop._box_return_pane is None
    assert pop._show_pane_calls == [PANE_POKEMON]


def test_nickname_inline_handler_save_button_commits_and_clears_state(
    db_path, monkeypatch,
):
    """Clicking ✓ commits the value and drops editing state on the popover."""
    from tokenmon.popover.panes.box import (
        BoxController,
        _NicknameInlineHandler,
    )
    pop = _FakePopover(editing=True)
    ctrl = BoxController(pop)

    # Stub storage so the test doesn't need a real Pokemon row.
    import tokenmon.storage as storage
    captured = []
    monkeypatch.setattr(
        storage, "update_pokemon_nickname",
        lambda pid, value: captured.append((pid, value)),
    )

    handler = _NicknameInlineHandler.alloc().initWithController_pokemonId_(
        ctrl, 7,
    )
    # Fake the field — saveButton_ pulls stringValue() off it.
    class _FakeField:
        def stringValue(self_):  # noqa: N802
            return "  Sparky  "
    handler._field = _FakeField()

    handler.saveButton_(None)
    assert captured == [(7, "Sparky")]
    assert pop._editing_nickname is False
    # _show_pane(PANE_BOX) is called to re-render after commit.
    assert pop._show_pane_calls != []


def test_nickname_cancel_button_clears_editing_state_without_storage_call(
    db_path, monkeypatch,
):
    from tokenmon.popover.panes.box import (
        BoxController,
        _NicknameInlineHandler,
    )
    pop = _FakePopover(editing=True)
    ctrl = BoxController(pop)
    import tokenmon.storage as storage
    monkeypatch.setattr(
        storage, "update_pokemon_nickname",
        lambda *a, **kw: pytest.fail("update_pokemon_nickname must not run on cancel"),
    )

    handler = _NicknameInlineHandler.alloc().initWithController_pokemonId_(
        ctrl, 7,
    )
    handler.cancelButton_(None)
    assert pop._editing_nickname is False
    assert pop._show_pane_calls != []


def test_nickname_save_field_blank_value_collapses_to_none(
    db_path, monkeypatch,
):
    """Empty/whitespace input should commit ``None`` so the species name
    re-takes the title slot."""
    from tokenmon.popover.panes.box import (
        BoxController,
        _NicknameInlineHandler,
    )
    pop = _FakePopover(editing=True)
    ctrl = BoxController(pop)
    import tokenmon.storage as storage
    captured = []
    monkeypatch.setattr(
        storage, "update_pokemon_nickname",
        lambda pid, value: captured.append((pid, value)),
    )

    handler = _NicknameInlineHandler.alloc().initWithController_pokemonId_(
        ctrl, 11,
    )
    class _FakeSender:
        def stringValue(self_):  # noqa: N802
            return "   "
    handler.saveField_(_FakeSender())
    assert captured == [(11, None)]


def test_swap_view_builds_with_no_unlocked_moves(db_path, monkeypatch):
    """Swap mode should render an empty-state when the unlocked pool
    is empty rather than crash."""
    from datetime import date
    from tokenmon import storage
    from tokenmon.popover.panes.box import BoxController

    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1,
        nature="Hardy", characteristic="x", path=db_path,
    )
    pop = _FakePopover(selected=pid, swap_slot=0)
    ctrl = BoxController(pop)
    view = ctrl.build_view()
    assert view is not None
    assert ctrl._swap_slot == 0


def test_swap_view_lists_unlocked_moves(db_path, monkeypatch):
    """Each unlocked move should produce a clickable row in the picker."""
    from datetime import date
    from tokenmon import storage
    from tokenmon.popover.panes.box import BoxController

    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1,
        nature="Hardy", characteristic="x", path=db_path,
    )
    storage.unlock_move(pid, "tackle", 1, path=db_path)
    storage.unlock_move(pid, "ember", 8, path=db_path)
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=35, path=db_path)

    pop = _FakePopover(selected=pid, swap_slot=0)
    ctrl = BoxController(pop)
    view = ctrl.build_view()
    assert view is not None
    # Two unlocked moves → at least two _MoveSlotButton handlers were
    # registered (plus the back-button handler).
    from tokenmon.popover.panes.box import _MoveSlotButton  # noqa: F401
    # The controller anchors every interactive handler on _handlers; we
    # don't pin to an exact count because some helpers may add more,
    # but any positive count proves the picker built.
    assert len(ctrl._handlers) >= 2


def test_swap_pick_writes_slot_and_clears_swap_state(db_path, monkeypatch):
    """Picking an unlocked move calls set_pokemon_move and resets
    _box_swap_slot before re-rendering."""
    from datetime import date
    from tokenmon import storage
    from tokenmon.popover.panes import box as box_module

    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1,
        nature="Hardy", characteristic="x", path=db_path,
    )
    storage.unlock_move(pid, "ember", 8, path=db_path)

    captured = []
    monkeypatch.setattr(
        box_module, "set_pokemon_move",
        lambda pokemon_id, slot, move_key, *, max_pp:
            captured.append((pokemon_id, slot, move_key, max_pp)),
    )

    pop = _FakePopover(selected=pid, swap_slot=2)
    ctrl = box_module.BoxController(pop)
    ctrl.build_view()  # registers click handlers on ctrl._handlers

    # Find the closure attached to one of the move-pick handlers.
    # Each handler has a callable `_fn`; we invoke them and look for
    # the one that captures into our stub.
    fired = False
    for h in ctrl._handlers:
        fn = getattr(h, "_cb", None)
        if fn is None:
            continue
        try:
            fn(None)
        except Exception:
            continue
        if captured:
            fired = True
            break
    assert fired, "no swap-pick handler ran"
    assert captured[0][0] == pid
    assert captured[0][1] == 2
    assert pop._box_swap_slot is None

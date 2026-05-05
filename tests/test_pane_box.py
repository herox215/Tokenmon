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
    ):
        self._app = _FakeApp()
        self._box_selected_id = selected
        self._editing_nickname = editing
        self._stats_mode = stats_mode
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

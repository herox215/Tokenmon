"""Smoke tests for TokendexController (Phase 3b)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakePopover:
    def __init__(self, selected_dex: int | None = None):
        self._pokedex_selected_dex = selected_dex
        self._animated_image_views: list = []
        self._show_pane_calls: list[int] = []

    def _show_pane(self, idx: int) -> None:
        self._show_pane_calls.append(idx)


def test_tokendex_controller_list_view_builds(db_path):
    from tokenmon.popover.panes.tokendex import TokendexController
    pop = _FakePopover(selected_dex=None)
    ctrl = TokendexController(pop)
    view = ctrl.build_view()
    assert view is not None
    assert ctrl._selected_dex is None


def test_tokendex_controller_detail_view_builds(db_path, monkeypatch):
    """Detail view must render even when remote info is unavailable."""
    from tokenmon.popover.panes.tokendex import TokendexController
    # Stub get_species_info so the test doesn't try to hit the network.
    import tokenmon.pokedex_remote as pkdex
    monkeypatch.setattr(pkdex, "get_species_info", lambda _i: {"genus": "Mouse", "description": "Lightning."})
    pop = _FakePopover(selected_dex=25)  # Pikachu
    ctrl = TokendexController(pop)
    view = ctrl.build_view()
    assert view is not None
    assert ctrl._selected_dex == 25
    # The back-button handler is anchored on the controller so it doesn't
    # GC away while the view is alive.
    assert len(ctrl._handlers) >= 1


def test_tokendex_teardown_clears_handlers(db_path):
    from tokenmon.popover.panes.tokendex import TokendexController
    ctrl = TokendexController(_FakePopover(selected_dex=25))
    ctrl.build_view()
    ctrl.teardown()
    assert ctrl._handlers == []

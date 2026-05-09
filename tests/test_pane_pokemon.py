"""Tests for PokemonController + pat-animation lifecycle (Phase 3f1+3f2)."""
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
        self._animated_image_views: list = []
        self._box_selected_id = None
        self._box_return_pane = None
        self._box_swap_slot = 2
        self._editing_nickname = True
        self._show_pane_calls: list[int] = []

    def _show_pane(self, idx: int) -> None:
        self._show_pane_calls.append(idx)


def test_pokemon_controller_no_active_renders_fallback(db_path, monkeypatch):
    from tokenmon.popover.panes.pokemon import PokemonController
    from tokenmon import box
    monkeypatch.setattr(box, "ensure_today_pokemon", lambda: None)
    monkeypatch.setattr(box, "get_active_pokemon", lambda: None)
    pop = _FakePopover()
    ctrl = PokemonController(pop)
    view = ctrl.build_view()
    assert view is not None
    # No active → no pat catcher should be configured.
    assert ctrl._pat_catcher is None
    assert ctrl._pat_sprite is None


def test_begin_pat_when_no_sprite_is_no_op(db_path):
    """Starting a pat on a controller that hasn't built a sprite must
    not crash and must not flip the active flag."""
    from tokenmon.popover.panes.pokemon import PokemonController
    pop = _FakePopover()
    ctrl = PokemonController(pop)
    ctrl._begin_pat()
    assert ctrl._pat_active is False
    assert ctrl._pat_handler is None


def test_pat_step_unknown_action_no_op(db_path):
    from tokenmon.popover.panes.pokemon import PokemonController
    pop = _FakePopover()
    ctrl = PokemonController(pop)
    ctrl._pat_sprite = None  # explicit
    ctrl.pat_step("totally_unknown")
    # No-op — no crash, no state change.
    assert ctrl._pat_active is False


def test_pat_step_done_calls_end_pat(db_path, monkeypatch):
    """The 'done' step terminates the pat lifecycle by clearing flags."""
    from tokenmon.popover.panes.pokemon import PokemonController
    pop = _FakePopover()
    ctrl = PokemonController(pop)
    # Stub a fake sprite that has frame() returning something setFrame_ accepts.
    class _FakeFrame:
        class _Origin:
            x = 0.0
        class _Size:
            width = 100.0
            height = 100.0
        origin = _Origin()
        size = _Size()
    class _FakeSprite:
        def frame(self):
            return _FakeFrame()
        def setFrame_(self, _f):  # noqa: N802
            pass
    ctrl._pat_sprite = _FakeSprite()
    ctrl._pat_active = True
    ctrl._pat_handler = object()  # placeholder
    ctrl._pat_hearts = []
    ctrl.pat_step("done")
    assert ctrl._pat_active is False
    assert ctrl._pat_handler is None


def test_open_box_detail_selects_active_pokemon_and_opens_box():
    from tokenmon.popover.panes.pokemon import PokemonController
    from tokenmon.popover.widgets import PANE_BOX, PANE_POKEMON

    pop = _FakePopover()
    ctrl = PokemonController(pop)

    ctrl._open_box_detail(42)

    assert pop._box_selected_id == 42
    assert pop._box_return_pane == PANE_POKEMON
    assert pop._box_swap_slot is None
    assert pop._editing_nickname is False
    assert pop._show_pane_calls == [PANE_BOX]

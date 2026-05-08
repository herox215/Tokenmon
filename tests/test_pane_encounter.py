"""Tests for EncounterController — reveal-only after Phase 4. The
preview now lives in EncounterPreviewController; the bag-open mode and
default action bar were removed (preview pre-fight, battle pane mid-fight)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakePopover:
    def __init__(self, reveal_payload: dict | None = None):
        self._encounter_bag_open = False
        self._pending_reveal_pokemon = reveal_payload
        self._reveal_timer = None
        self._reveal_timer_handler = None
        self._animated_image_views: list = []
        self._show_pane_calls: list[int] = []
        self._begin_catch_calls: list[dict] = []

    def _show_pane(self, idx: int) -> None:
        self._show_pane_calls.append(idx)

    def _begin_catch_animation(self, **kw) -> None:
        self._begin_catch_calls.append(kw)


def test_encounter_controller_renders_empty_when_no_payload(db_path):
    """Without a reveal payload the pane renders a placeholder, not crash."""
    from tokenmon.popover.panes.encounter import EncounterController
    pop = _FakePopover()
    ctrl = EncounterController(pop)
    view = ctrl.build_view()
    assert view is not None


def test_encounter_controller_reveal_view_uses_payload(db_path):
    from tokenmon.popover.panes.encounter import EncounterController
    pop = _FakePopover(reveal_payload={
        "species_dex_id": 25,
        "pokemon_id": 1,
        "gender": "M",
        "is_shiny": False,
    })
    ctrl = EncounterController(pop)
    view = ctrl.build_view()
    assert view is not None


def test_encounter_controller_shiny_reveal_uses_shiny_sprite(db_path):
    from tokenmon.popover.panes.encounter import EncounterController
    pop = _FakePopover(reveal_payload={
        "species_dex_id": 1,
        "pokemon_id": 7,
        "gender": None,
        "is_shiny": True,
    })
    ctrl = EncounterController(pop)
    assert ctrl.build_view() is not None

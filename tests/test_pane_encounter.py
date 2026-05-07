"""Tests for EncounterController + reveal logic (Phase 3e)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakePopover:
    def __init__(
        self,
        bag_open: bool = False,
        reveal_payload: dict | None = None,
    ):
        self._encounter_bag_open = bag_open
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


def test_encounter_controller_default_view_renders_with_no_pending(db_path):
    """When there is no pending encounter, the controller should render a
    fallback message rather than crashing."""
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
    """A shiny payload should not crash the reveal builder."""
    from tokenmon.popover.panes.encounter import EncounterController
    pop = _FakePopover(reveal_payload={
        "species_dex_id": 1,
        "pokemon_id": 7,
        "gender": None,
        "is_shiny": True,
    })
    ctrl = EncounterController(pop)
    assert ctrl.build_view() is not None


def test_item_row_handler_dispatch_skips_when_pending_id_mismatches(
    db_path, monkeypatch,
):
    """Throw must abort cleanly if the encounter ID changed under us
    (e.g. user opened the popover, encounter resolved elsewhere, then
    user clicked an item). Must not invoke begin_catch_animation."""
    from tokenmon.popover.panes import encounter as enc_pane
    from tokenmon.popover.panes.encounter import (
        EncounterController, _ItemRowHandler,
    )
    pop = _FakePopover()
    ctrl = EncounterController(pop)

    class _StalePending:
        id = 999  # different from handler's encounter_id
        species_dex_id = 1
    monkeypatch.setattr(
        enc_pane, "get_pending_encounter", lambda: _StalePending(),
    )
    handler = (
        _ItemRowHandler.alloc()
        .initWithController_encounterId_itemKey_(ctrl, 7, "pokeball")
    )
    handler._dispatch_action("throw")
    assert pop._begin_catch_calls == []
    assert pop._encounter_bag_open is False


def test_encounter_bag_only_shows_throwable_items(db_path, monkeypatch):
    """Bag-open inventory must filter the registry down to throw-capable
    items (Poke balls). Stones/Potions have other actions and should be
    omitted so the rows can't overflow into the bottom button bar.
    """
    from AppKit import NSButton

    from tokenmon import items
    from tokenmon.popover.panes import encounter as enc_pane
    from tokenmon.popover.panes.encounter import EncounterController

    class _Pending:
        id = 42
        species_dex_id = 25
        level = 5
        last_hint = None

    monkeypatch.setattr(enc_pane, "get_pending_encounter", lambda: _Pending())
    monkeypatch.setattr(
        enc_pane, "query_item_counts",
        lambda: {key: 5 for key in items.ITEMS},
    )

    pop = _FakePopover(bag_open=True)
    ctrl = EncounterController(pop)
    view = ctrl.build_view()
    assert view is not None

    titles: list[str] = []

    def _collect(v):
        for sub in v.subviews():
            if isinstance(sub, NSButton):
                t = str(sub.title())
                if t:
                    titles.append(t)
            _collect(sub)

    _collect(view)

    # No stones, no potions in the inventory rows.
    assert all("Stone" not in t for t in titles), titles
    assert all("Potion" not in t for t in titles), titles

    # Each ball display name appears as one inventory row (plus possible
    # button-bar rows like "← Back" / "Run away" — those don't conflict).
    ball_titles = [
        t for t in titles
        if any(
            name in t
            for name in ("Poké Ball", "Great Ball", "Ultra Ball", "Master Ball")
        )
    ]
    assert len(ball_titles) == 4, ball_titles
    for name in ("Poké Ball", "Great Ball", "Ultra Ball", "Master Ball"):
        assert any(name in t for t in ball_titles), (name, ball_titles)


def test_item_row_handler_dispatch_throw_triggers_catch_animation(
    db_path, monkeypatch,
):
    from tokenmon.popover.panes import encounter as enc_pane
    from tokenmon.popover.panes.encounter import (
        EncounterController, _ItemRowHandler,
    )
    pop = _FakePopover(bag_open=True)
    ctrl = EncounterController(pop)

    class _Pending:
        id = 7
        species_dex_id = 25
    monkeypatch.setattr(enc_pane, "get_pending_encounter", lambda: _Pending())
    # encounter.use_item is referenced via the module attribute, so we patch
    # the live module — both the panes namespace and the original module.
    import tokenmon.encounter as enc_mod
    monkeypatch.setattr(
        enc_mod, "use_item",
        lambda eid, key: {"caught": True, "shakes": 3, "hint": None},
    )
    handler = (
        _ItemRowHandler.alloc()
        .initWithController_encounterId_itemKey_(ctrl, 7, "pokeball")
    )
    handler._dispatch_action("throw")
    assert pop._encounter_bag_open is False
    assert len(pop._begin_catch_calls) == 1
    call = pop._begin_catch_calls[0]
    assert call["species_dex_id"] == 25
    assert call["caught"] is True
    assert call["shakes"] == 3

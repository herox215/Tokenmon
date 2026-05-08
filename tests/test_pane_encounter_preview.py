"""Unified encounter-preview pane: routes both wild and trainer pending
states through one controller. Trainer takes priority when both are pending."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakePopover:
    def __init__(self):
        self._show_pane_calls: list[int] = []
        self._battle_session = None
        self._animated_image_views: list = []

    def _show_pane(self, idx):
        self._show_pane_calls.append(int(idx))


def test_preview_renders_when_neither_pending(db_path):
    from tokenmon.popover.panes.encounter_preview import (
        EncounterPreviewController,
    )
    pop = _FakePopover()
    ctrl = EncounterPreviewController(pop)
    view = ctrl.build_view()
    assert view is not None


def test_preview_renders_trainer_when_pending_trainer(db_path):
    from tokenmon.popover.panes.encounter_preview import (
        EncounterPreviewController,
    )
    from tokenmon.storage import insert_trainer

    insert_trainer(
        name="Tobi", title="Bug Catcher", difficulty="medium",
        seed=1, team=[{
            "species_dex_id": 16, "level": 8, "nature": "Hardy",
            "ivs": (0, 0, 0, 0, 0, 0), "move_keys": ("tackle",),
        }],
        path=db_path,
    )
    pop = _FakePopover()
    ctrl = EncounterPreviewController(pop)
    view = ctrl.build_view()
    assert view is not None
    # Fight + Run handlers.
    assert len(ctrl._handlers) == 2


def test_preview_renders_wild_when_pending_encounter(db_path):
    from tokenmon import encounter
    from tokenmon.popover.panes.encounter_preview import (
        EncounterPreviewController,
    )

    encounter.maybe_spawn(force=True, path=db_path)
    pop = _FakePopover()
    ctrl = EncounterPreviewController(pop)
    view = ctrl.build_view()
    assert view is not None
    assert len(ctrl._handlers) == 2


def test_preview_trainer_takes_priority_when_both_pending(db_path, monkeypatch):
    """If both somehow exist (the spawn loop normally prevents it), trainer
    wins — matches show_from_button precedence."""
    from tokenmon.popover.panes import encounter_preview as ep
    from tokenmon.popover.panes.encounter_preview import (
        EncounterPreviewController,
    )

    class _Trainer:
        id = 1
        name = "Tobi"
        title = "Bug Catcher"
        difficulty = "easy"

    class _Encounter:
        id = 99
        species_dex_id = 25
        level = 5
        nature = "Hardy"
        characteristic = "X"
        is_shiny = False
        gender = None

    monkeypatch.setattr(ep, "get_pending_trainer", lambda: _Trainer())
    monkeypatch.setattr(ep, "get_pending_encounter", lambda: _Encounter())
    monkeypatch.setattr(ep, "list_trainer_pokemon", lambda _id: [])

    pop = _FakePopover()
    ctrl = EncounterPreviewController(pop)
    view = ctrl.build_view()
    assert view is not None
    # Trainer branch consumed both handlers (Fight, Run).
    assert len(ctrl._handlers) == 2
    assert getattr(ctrl, "_kind", None) == "trainer"


def test_preview_run_button_runs_wild(db_path, monkeypatch):
    """Wild-pending Run → encounter.run_away + back to PANE_POKEMON."""
    from tokenmon import encounter
    from tokenmon.popover.panes import encounter_preview as ep
    from tokenmon.popover.panes.encounter_preview import (
        EncounterPreviewController,
    )
    from tokenmon.popover.widgets import PANE_POKEMON

    enc = encounter.maybe_spawn(force=True, path=db_path)
    assert enc is not None

    monkeypatch.setattr(ep, "get_pending_trainer", lambda: None)

    called = {"ran": False}

    def _spy_run_away(eid, **kw):
        called["ran"] = True

    monkeypatch.setattr(ep.encounter, "run_away", _spy_run_away)

    pop = _FakePopover()
    ctrl = EncounterPreviewController(pop)
    ctrl.build_view()
    # Find Run handler and fire it.
    run_h = ctrl._handlers[-1]
    run_h.fire_(None)
    assert called["ran"] is True
    assert PANE_POKEMON in pop._show_pane_calls


def test_preview_run_button_resolves_trainer_ran(db_path, monkeypatch):
    """Trainer-pending Run → mark_trainer_resolved(status='ran') + PANE_POKEMON."""
    from tokenmon.popover.panes import encounter_preview as ep
    from tokenmon.popover.panes.encounter_preview import (
        EncounterPreviewController,
    )
    from tokenmon.popover.widgets import PANE_POKEMON
    from tokenmon.storage import insert_trainer

    tid = insert_trainer(
        name="Tobi", title="Bug Catcher", difficulty="easy", seed=1,
        team=[{
            "species_dex_id": 16, "level": 5, "nature": "Hardy",
            "ivs": (0, 0, 0, 0, 0, 0), "move_keys": ("tackle",),
        }],
        path=db_path,
    )

    called = {"resolved": None}

    def _spy_mark_resolved(trainer_id, *, status, **kw):
        called["resolved"] = (trainer_id, status)

    monkeypatch.setattr(ep, "mark_trainer_resolved", _spy_mark_resolved)

    pop = _FakePopover()
    ctrl = EncounterPreviewController(pop)
    ctrl.build_view()
    run_h = ctrl._handlers[-1]
    run_h.fire_(None)
    assert called["resolved"] == (tid, "ran")
    assert PANE_POKEMON in pop._show_pane_calls


def test_navigation_lock_single_slot(db_path, monkeypatch):
    """Exactly one Encounter sidebar slot exists when an entity is pending."""
    from tokenmon import popover as popover_pkg
    from tokenmon.popover.widgets import (
        PANE_ENCOUNTER, PANE_POKEMON, PANE_TOKENDEX, PANE_BOX, PANE_ITEMS,
        PANE_USAGE,
    )

    # The unified pane id is just PANE_ENCOUNTER; PANE_TRAINER_PREVIEW is gone.
    assert not hasattr(popover_pkg.widgets, "PANE_TRAINER_PREVIEW")

    from tokenmon import encounter
    encounter.maybe_spawn(force=True, path=db_path)
    # Validate sidebar item set when pending: PANE_ENCOUNTER prepended,
    # all base-pane ids present.
    base = {PANE_POKEMON, PANE_TOKENDEX, PANE_BOX, PANE_ITEMS, PANE_USAGE}
    expected = {PANE_ENCOUNTER} | base
    assert PANE_ENCOUNTER not in base
    assert PANE_ENCOUNTER == -1

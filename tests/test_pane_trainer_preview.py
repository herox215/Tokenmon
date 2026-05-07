"""Trainer-preview pane smoke tests."""
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

    def _show_pane(self, idx):
        self._show_pane_calls.append(int(idx))


def test_pane_renders_when_no_trainer(db_path):
    """No pending trainer → empty-state label, no crash."""
    from tokenmon.popover.panes.trainer_preview import TrainerPreviewController
    pop = _FakePopover()
    ctrl = TrainerPreviewController(pop)
    view = ctrl.build_view()
    assert view is not None


def test_pane_renders_with_pending_trainer(db_path):
    from tokenmon.popover.panes.trainer_preview import TrainerPreviewController
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
    ctrl = TrainerPreviewController(pop)
    view = ctrl.build_view()
    assert view is not None
    # Two action handlers: Fight + Run.
    assert len(ctrl._handlers) == 2

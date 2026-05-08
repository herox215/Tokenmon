"""Trainer-preview smoke tests — superseded by test_pane_encounter_preview.

Phase 4 unified the trainer + wild preview into EncounterPreviewController.
The legacy TrainerPreviewController is still importable until Phase 6 cleanup
but the registered slot now points at the unified controller; these tests
stay as a thin compatibility shim until the file goes away."""
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


def test_legacy_trainer_pane_still_imports(db_path):
    from tokenmon.popover.panes.trainer_preview import TrainerPreviewController
    pop = _FakePopover()
    ctrl = TrainerPreviewController(pop)
    view = ctrl.build_view()
    assert view is not None

"""Smoke tests for the Usage pane controller (Phase 3a pilot)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeApp:
    """Minimal stand-in for the rumps menubar app — just the attributes
    the UsageController reads."""
    _show_pokemon = True
    _show_overlay = False
    _use_weather = False


class _FakePopover:
    """Minimal popover stand-in for unit-testing UsageController."""
    def __init__(self):
        self._app = _FakeApp()
        self._show_pane_calls: list[int] = []

    def _show_pane(self, idx: int) -> None:
        self._show_pane_calls.append(idx)


def test_usage_controller_build_does_not_raise(db_path):
    from tokenmon.popover.panes.usage import UsageController
    pop = _FakePopover()
    ctrl = UsageController(pop)
    view = ctrl.build_view()
    assert view is not None
    # No animations on the usage pane → controller's handler list stays
    # tight: just the debug-spawn click handler.
    assert len(ctrl._handlers) == 1


def test_usage_controller_teardown_clears_handlers(db_path):
    from tokenmon.popover.panes.usage import UsageController
    pop = _FakePopover()
    ctrl = UsageController(pop)
    ctrl.build_view()
    ctrl.teardown()
    assert ctrl._handlers == []
    assert ctrl._already_pending_label is None
    assert ctrl._already_pending_timer is None


def test_usage_controller_teardown_idempotent_without_build(db_path):
    """Teardown must be safe to call even if build_view never ran."""
    from tokenmon.popover.panes.usage import UsageController
    ctrl = UsageController(_FakePopover())
    ctrl.teardown()  # no crash
    assert ctrl._handlers == []

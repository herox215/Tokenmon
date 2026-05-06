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
    _use_weather = False
    _companion_mode = False


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
    # Usage pane anchors at least the chart-refresh NSTimer target. Don't
    # assert exact equality so future tweaks don't churn this test.
    assert len(ctrl._handlers) >= 1


def test_usage_controller_teardown_clears_handlers(db_path):
    from tokenmon.popover.panes.usage import UsageController
    pop = _FakePopover()
    ctrl = UsageController(pop)
    ctrl.build_view()
    ctrl.teardown()
    assert ctrl._handlers == []
    assert ctrl._chart_view is None
    assert ctrl._chart_timer is None


def test_usage_controller_teardown_idempotent_without_build(db_path):
    """Teardown must be safe to call even if build_view never ran."""
    from tokenmon.popover.panes.usage import UsageController
    ctrl = UsageController(_FakePopover())
    ctrl.teardown()  # no crash
    assert ctrl._handlers == []


def test_usage_controller_chart_view_present_after_build(db_path):
    from tokenmon.popover.panes.usage import UsageController
    from tokenmon.popover.widgets import _TokenChartView

    ctrl = UsageController(_FakePopover())
    ctrl.build_view()
    assert isinstance(ctrl._chart_view, _TokenChartView)
    assert ctrl._chart_timer is not None
    ctrl.teardown()


def test_usage_controller_refresh_chart_handles_missing_view(db_path):
    """_refresh_chart must be safe to call after teardown drops the view."""
    from tokenmon.popover.panes.usage import UsageController
    ctrl = UsageController(_FakePopover())
    ctrl.build_view()
    ctrl.teardown()
    ctrl._refresh_chart()  # no crash, no-op

"""Tests for InputActivityMonitor.

The NSEvent global-monitor side can't be exercised from a non-graphical
test run, but the timestamp bookkeeping and callback wiring don't depend
on any real events firing — we drive them directly via mark_input_now.
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


def test_seconds_since_last_input_none_before_any_event():
    from tokenmon.companion.input_monitor import InputActivityMonitor
    m = InputActivityMonitor()
    assert m.seconds_since_last_input() is None


def test_mark_input_now_seeds_timestamp():
    from tokenmon.companion.input_monitor import InputActivityMonitor
    m = InputActivityMonitor()
    m.mark_input_now()
    elapsed = m.seconds_since_last_input()
    assert elapsed is not None
    assert 0 <= elapsed < 0.1  # just-now


def test_seconds_since_last_input_grows_with_time():
    from tokenmon.companion.input_monitor import InputActivityMonitor
    m = InputActivityMonitor()
    m.mark_input_now()
    time.sleep(0.05)
    e1 = m.seconds_since_last_input()
    assert e1 is not None and e1 >= 0.05
    time.sleep(0.05)
    e2 = m.seconds_since_last_input()
    assert e2 is not None and e2 > e1


def test_stop_is_safe_when_never_started():
    from tokenmon.companion.input_monitor import InputActivityMonitor
    m = InputActivityMonitor()
    m.stop()  # no crash, no-op


def test_double_start_is_idempotent():
    """Calling start twice shouldn't install two monitors. We can't
    actually verify the NSEvent side without firing events, but the
    second call must not raise."""
    from tokenmon.companion.input_monitor import InputActivityMonitor
    m = InputActivityMonitor()
    m.start()
    try:
        m.start()  # idempotent
    finally:
        m.stop()

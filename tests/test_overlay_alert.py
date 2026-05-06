"""Tests for PokemonOverlay.flash_alert (transient banner)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakeContent:
    def addSubview_(self, _v):
        pass


class _FakeWin:
    def __init__(self):
        self._content = _FakeContent()
    def setAlphaValue_(self, _a):
        pass
    def contentView(self):
        return self._content


def test_flash_alert_creates_label_and_timer():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._window = _FakeWin()  # type: ignore[assignment]
    o.flash_alert("⚡ wild!", duration_s=4.0)
    assert o._alert_label is not None
    assert o._alert_timer is not None
    o._end_alert()
    assert o._alert_label is None
    assert o._alert_timer is None


def test_flash_alert_replaces_in_flight_alert():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._window = _FakeWin()  # type: ignore[assignment]
    o.flash_alert("first", duration_s=10.0)
    first_label = o._alert_label
    o.flash_alert("second", duration_s=10.0)
    assert o._alert_label is not first_label
    o._end_alert()


def test_end_alert_idempotent_with_no_in_flight():
    from tokenmon.overlay import PokemonOverlay
    o = PokemonOverlay()
    o._end_alert()  # no crash
    assert o._alert_label is None
    assert o._alert_timer is None

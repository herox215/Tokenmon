"""Pure-helper tests for popover (no AppKit needed). The fact that these
imports work means the helpers are isolated enough to be testable; the
post-Wave-E plan moves them into ``popover/_pure.py`` so AppKit is no
longer dragged in transitively. Tests should not need to change."""
from __future__ import annotations

import pytest

# These helpers happen to be defined at the top of popover.py but currently
# importing the module itself loads AppKit. Until Wave E, we run these tests
# only when AppKit is available (i.e. on macOS with pyobjc installed).
pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)

from tokenmon import popover


# ---- _fmt_tokens ---------------------------------------------------------


@pytest.mark.parametrize("n, expected", [
    (0, "0"),
    (1, "1"),
    (999, "999"),
    (1000, "1.0K"),
    (1500, "1.5K"),
    (999_999, "1000.0K"),
    (1_000_000, "1.00M"),
    (2_500_000, "2.50M"),
    (1_000_000_000, "1.00B"),
])
def test_fmt_tokens(n, expected):
    assert popover._fmt_tokens(n) == expected


# ---- _fmt_usd ------------------------------------------------------------


@pytest.mark.parametrize("amount, expected", [
    (0.0001, "$0.0001"),
    (0.005, "$0.0050"),
    (0.5, "$0.500"),
    (1.0, "$1.00"),
    (12.345, "$12.35"),  # banker's rounding
])
def test_fmt_usd(amount, expected):
    assert popover._fmt_usd(amount) == expected


# ---- _fmt_affection ------------------------------------------------------


def test_fmt_affection_zero():
    assert popover._fmt_affection(0) == "♡♡♡♡♡  0 / 255"


def test_fmt_affection_one_lights_one_heart():
    assert popover._fmt_affection(1).startswith("♥♡♡♡♡")


def test_fmt_affection_full_lights_all():
    assert popover._fmt_affection(255).startswith("♥♥♥♥♥")


@pytest.mark.parametrize("v, filled", [
    (0, 0),
    (1, 1),
    (50, 1),
    (51, 1),
    (52, 2),
    (102, 2),
    (153, 3),
    (204, 4),
    (230, 5),
    (255, 5),
])
def test_fmt_affection_heart_count(v, filled):
    s = popover._fmt_affection(v)
    assert s.count("♥") == filled


# ---- _build_catch_steps --------------------------------------------------


def test_catch_steps_no_shake_failed():
    steps = popover._build_catch_steps(False, 0)
    actions = [a for _, a in steps]
    assert "burst" in actions
    assert "click" not in actions
    assert sum(1 for a in actions if a.startswith("shake_")) == 0


def test_catch_steps_three_shake_caught():
    steps = popover._build_catch_steps(True, 3)
    actions = [a for _, a in steps]
    assert "click" in actions
    # 3 shakes × 3 sub-actions (left/right/centre) = 9
    assert sum(1 for a in actions if a.startswith("shake_")) == 9


def test_catch_steps_shake_count_drives_length():
    short = popover._build_catch_steps(False, 0)
    long = popover._build_catch_steps(False, 3)
    assert len(long) > len(short)


# ---- _build_pat_steps ----------------------------------------------------


def test_pat_steps_no_hearts_excludes_heart_actions():
    steps = popover._build_pat_steps(False)
    actions = [a for _, a in steps]
    assert all(not a.startswith("heart_") for a in actions)


def test_pat_steps_with_hearts_has_five():
    steps = popover._build_pat_steps(True)
    hearts = [a for _, a in steps if a.startswith("heart_")]
    assert len(hearts) == 5


# ---- _nice_max -----------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    (0, 1),
    (1, 1),
    (2, 2),
    (3, 5),
    (5, 5),
    (6, 10),
    (17, 20),
    (42, 50),
    (123, 200),
    (4_500, 5_000),
    (17_342, 20_000),
    (60_000, 100_000),
])
def test_nice_max_rounds_to_one_two_five(value, expected):
    from tokenmon.popover.widgets import _nice_max
    assert _nice_max(value) == expected

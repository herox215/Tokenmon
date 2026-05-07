"""Unit tests for the shared move-tooltip formatter.

The helper is pure (no AppKit, no I/O) so tests run on every platform
without the AppKit-importorskip dance the UI tests need.
"""
from __future__ import annotations

from tokenmon.battle.models import Move
from tokenmon.popover.panes._move_tooltip import format_move_tooltip


def _move(**overrides) -> Move:
    base = dict(
        key="tackle",
        name="Tackle",
        type="normal",
        category="physical",
        power=40,
        accuracy=100,
        pp=35,
        priority=0,
        description="",
    )
    base.update(overrides)
    return Move(**base)


def test_tooltip_none_move_shows_placeholder():
    assert format_move_tooltip(None, 17) == "Move data not loaded yet."


def test_tooltip_with_description_includes_blank_separator():
    move = _move(description="Inflicts damage; 30% chance to paralyze.")
    text = format_move_tooltip(move, current_pp=12)
    lines = text.split("\n")
    assert lines[0] == "Tackle"
    assert "Type: Normal" in lines[1] and "Physical" in lines[1]
    assert "Power: 40" in lines[2] and "Accuracy: 100%" in lines[2]
    assert lines[3] == "PP: 12/35"
    assert lines[4] == ""  # blank separator before description
    assert lines[5] == "Inflicts damage; 30% chance to paralyze."


def test_tooltip_without_description_skips_separator():
    move = _move(description="")
    text = format_move_tooltip(move, current_pp=35)
    assert text.endswith("PP: 35/35")
    assert "\n\n" not in text  # no blank line dangling at end


def test_tooltip_current_pp_optional_falls_back_to_max():
    move = _move()
    text = format_move_tooltip(move)
    assert "PP: 35" in text
    assert "PP: /" not in text


def test_tooltip_status_move_renders_em_dash_for_power():
    move = _move(category="status", power=None, accuracy=None)
    text = format_move_tooltip(move, current_pp=5)
    assert "Power: —" in text
    assert "Always hits" in text


def test_tooltip_current_pp_varies_independently_of_max():
    move = _move(pp=20)
    low = format_move_tooltip(move, current_pp=3)
    full = format_move_tooltip(move, current_pp=20)
    assert "PP: 3/20" in low
    assert "PP: 20/20" in full


def test_tooltip_strips_whitespace_only_descriptions():
    move = _move(description="   \n  ")
    text = format_move_tooltip(move, current_pp=10)
    # Whitespace-only descriptions count as empty — no trailing blank line.
    assert text.endswith("PP: 10/35")

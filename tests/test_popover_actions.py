"""Pure-helper tests for popover actions/animation.

Phase 0 of the popover refactor: extract pure action helpers
(``title_for_action``, ``build_claim_steps``) out of TokenmonPopover
so they can be unit-tested without AppKit and reused from the
upcoming pane controllers.
"""
from __future__ import annotations

import pytest

# These imports drag AppKit transitively today (via popover._main).
# Skip on non-macOS until we fully decouple the popover package.
pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


# ---- title_for_action ----------------------------------------------------


def test_title_for_action_throw_uses_static_template():
    from tokenmon.popover._actions import title_for_action
    assert title_for_action("pokeball", "throw") == "Throw at wild Pokemon"


def test_title_for_action_use_formats_item_display_name():
    from tokenmon.popover._actions import title_for_action
    assert title_for_action("fire-stone", "use") == "Use Fire Stone"


def test_title_for_action_evolve_uses_static_template():
    from tokenmon.popover._actions import title_for_action
    assert title_for_action("fire-stone", "evolve") == "Use on a Pokemon"


def test_title_for_action_unknown_action_falls_through_to_action_key():
    from tokenmon.popover._actions import title_for_action
    assert title_for_action("pokeball", "shake") == "shake"


def test_title_for_action_unknown_item_uses_key_as_name():
    from tokenmon.popover._actions import title_for_action
    # Unknown item key — name placeholder falls back to the key itself
    assert title_for_action("not-a-real-item", "use") == "Use not-a-real-item"


def test_title_for_action_use_template_without_item_does_not_crash():
    from tokenmon.popover._actions import title_for_action
    # Empty key still produces a string; behaviour is "no item -> key shown".
    assert title_for_action("", "use") == "Use "


# ---- build_claim_steps ---------------------------------------------------


def test_build_claim_steps_three_items_yields_dense_indexes():
    from tokenmon.popover.animation import build_claim_steps
    ordered = [("pokeball", 1), ("greatball", 1), ("fire-stone", 1)]
    steps = build_claim_steps(ordered)
    actions = [a for _, a in steps]
    # 3 items × 3 sub-steps + 1 done step
    assert len(steps) == 10
    # Each item has indexes 0, 1, 2 in dense form
    for i in range(3):
        assert f"drop_{i}_1" in actions
        assert f"drop_{i}_2" in actions
        assert f"drop_{i}_3" in actions


def test_build_claim_steps_includes_terminal_done_step():
    from tokenmon.popover.animation import build_claim_steps
    steps = build_claim_steps([("pokeball", 1)])
    assert steps[-1][1] == "done"


def test_build_claim_steps_empty_yields_only_done():
    from tokenmon.popover.animation import build_claim_steps
    steps = build_claim_steps([])
    assert steps == [(0.80, "done")]


def test_build_claim_steps_delays_are_positive():
    from tokenmon.popover.animation import build_claim_steps
    steps = build_claim_steps([("pokeball", 1), ("greatball", 1)])
    for delay, _ in steps:
        assert delay > 0


# ---- _ActionHandler ------------------------------------------------------


def test_action_handler_invokes_callback():
    from tokenmon.popover._handlers import make_handler
    seen = []
    h = make_handler(lambda sender: seen.append(sender))
    h.fire_("a-sender")
    assert seen == ["a-sender"]


def test_action_handler_logs_and_swallows_exception(caplog):
    from tokenmon.popover._handlers import make_handler
    def boom(_sender):
        raise RuntimeError("boom")
    h = make_handler(boom)
    # Must not raise; failure goes into the log.
    with caplog.at_level("ERROR", logger="tokenmon.popover.handlers"):
        h.fire_(None)
    assert any("action handler failed" in rec.message for rec in caplog.records)


def test_action_handler_captures_id_via_default_arg():
    """Pattern used to replace ID-carrying handlers like _BoxItemHandler."""
    from tokenmon.popover._handlers import make_handler
    captured: list[int] = []
    handlers = []
    for pid in (10, 20, 30):
        handlers.append(make_handler(lambda _s, p=pid: captured.append(p)))
    for h in handlers:
        h.fire_(None)
    assert captured == [10, 20, 30]

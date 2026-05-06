"""Inline move-learn dialog used by both pokemon and box-detail panes.

Renders one queue row per call as a notice + Learn / Skip pair. Clicking
Learn sets the move into the first free slot (or replaces slot 0 if all
four are taken — a refined "pick which to forget" UI is a v2 follow-up).
Clicking Skip just claims the queue row without changing slots.

Returns the vertical pixels consumed so the caller can shift subsequent
rows down. Returns 0 if no learn is pending.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSButton,
    NSColor,
    NSFont,
    NSTextAlignmentCenter,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon.popover._handlers import make_handler
from tokenmon.popover.widgets import CONTENT_WIDTH, _label

log = logging.getLogger("tokenmon.popover.panes._move_learn_inline")

_DIALOG_HEIGHT = 56  # banner + button row + a little breathing room


def build_move_learn_inline(
    parent: NSView,
    controller,
    *,
    pokemon_id: int,
    top_y: float,
) -> int:
    """Render the topmost pending move-learn for ``pokemon_id`` (if any).

    Returns the vertical pixel height consumed (so the caller can flow
    its remaining widgets below). Zero when nothing is pending.
    """
    try:
        from tokenmon import moves_remote
        from tokenmon.storage import (
            claim_pending_move_learn,
            get_pokemon_moves,
            query_pending_move_learns,
            set_pokemon_move,
        )
    except Exception:
        log.exception("move-learn inline imports failed")
        return 0

    try:
        rows = query_pending_move_learns(pokemon_id)
    except Exception:
        log.exception("query_pending_move_learns failed")
        return 0
    if not rows:
        return 0

    pending = rows[0]
    move_label = pending.move_key.replace("-", " ").title()

    # Banner line
    banner_y = top_y - 18
    parent.addSubview_(_label(
        NSMakeRect(20, banner_y, CONTENT_WIDTH - 40, 16),
        f"📘  Can learn {move_label}!",
        font=NSFont.boldSystemFontOfSize_(11),
        color=NSColor.labelColor(),
        align=NSTextAlignmentCenter,
    ))

    btn_y = banner_y - 26
    btn_w = 80
    gap = 10
    total = btn_w * 2 + gap
    x_left = (CONTENT_WIDTH - total) // 2

    learn_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(x_left, btn_y, btn_w, 22)
    )
    learn_btn.setTitle_("Learn")
    learn_btn.setBezelStyle_(1)

    def _learn(_s):
        try:
            md = moves_remote.get_move_data(pending.move_key)
            max_pp = md.pp if md is not None else 35
            existing = get_pokemon_moves(pokemon_id)
            occupied_slots = {m.slot for m in existing}
            free_slot = next(
                (s for s in range(4) if s not in occupied_slots), 0,
            )
            set_pokemon_move(
                pokemon_id, free_slot, pending.move_key, max_pp=max_pp,
            )
            claim_pending_move_learn(pending.id)
            controller.popover._show_pane(controller.popover._current_pane)
        except Exception:
            log.exception("move learn failed")

    h_learn = make_handler(_learn)
    controller._handlers.append(h_learn)
    learn_btn.setTarget_(h_learn)
    learn_btn.setAction_(b"fire:")
    parent.addSubview_(learn_btn)

    skip_btn = NSButton.alloc().initWithFrame_(
        NSMakeRect(x_left + btn_w + gap, btn_y, btn_w, 22)
    )
    skip_btn.setTitle_("Skip")
    skip_btn.setBezelStyle_(1)

    def _skip(_s):
        try:
            claim_pending_move_learn(pending.id)
            controller.popover._show_pane(controller.popover._current_pane)
        except Exception:
            log.exception("move skip failed")

    h_skip = make_handler(_skip)
    controller._handlers.append(h_skip)
    skip_btn.setTarget_(h_skip)
    skip_btn.setAction_(b"fire:")
    parent.addSubview_(skip_btn)

    return _DIALOG_HEIGHT

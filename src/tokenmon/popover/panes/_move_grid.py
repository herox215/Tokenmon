"""Per-Pokémon move grid used by the active-Pokémon and box-detail panes.

Renders the four move slots as a 2×2 grid of small cards. Each card
shows the move name + type label and exposes a hover tooltip with the
full battle stats (power, accuracy, PP, category) — same data the
battle pane consumes via ``moves_remote.get_move_data``.

Slots without a learned move render a muted "(empty slot)" placeholder
so the grid stays balanced even for newly-caught Pokémon.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSBezierPath,
    NSColor,
    NSFont,
    NSTextAlignmentCenter,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon.popover.widgets import CONTENT_WIDTH, _label

log = logging.getLogger("tokenmon.popover.panes._move_grid")

GRID_HEIGHT = 90       # 2 rows × 40 + 8 gap + 2 px breathing
SLOT_HEIGHT = 40
SLOT_GAP = 8


class _MoveSlotView(NSView):
    """Tiny rounded card holding one move's name + type. Tooltip is
    set externally via setToolTip_; rendering is just the bezel."""

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.06).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 6, 6,
        ).fill()
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.18).set()
        border = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 6, 6,
        )
        border.setLineWidth_(0.5)
        border.stroke()


def _make_tooltip(move, current_pp: int) -> str:
    """Format the hover tooltip for one move. ``move`` is a Move
    dataclass or None (cache miss; we fall back to slug-only)."""
    if move is None:
        return "Move data not loaded yet."
    type_str = move.type.title()
    cat_str = move.category.title()
    power = move.power if move.power is not None else "—"
    acc = f"{move.accuracy}%" if move.accuracy is not None else "Always hits"
    pp_max = move.pp
    return (
        f"{move.name}\n"
        f"Type: {type_str} · {cat_str}\n"
        f"Power: {power} · Accuracy: {acc}\n"
        f"PP: {current_pp}/{pp_max}"
    )


def build_move_grid(
    parent: NSView,
    *,
    pokemon_id: int,
    top_y: float,
) -> int:
    """Render the move grid for ``pokemon_id`` ending at ``top_y`` (the
    grid grows downward from there). Returns vertical pixels consumed
    so callers can flow subsequent widgets below."""
    try:
        from tokenmon import moves_remote
        from tokenmon.storage import get_pokemon_moves
    except Exception:
        log.exception("move grid imports failed")
        return 0

    try:
        rows = get_pokemon_moves(pokemon_id)
    except Exception:
        log.exception("get_pokemon_moves failed")
        rows = []
    by_slot = {r.slot: r for r in rows}

    margin_x = 16
    grid_w = CONTENT_WIDTH - margin_x * 2
    slot_w = (grid_w - SLOT_GAP) // 2

    parent.addSubview_(_label(
        NSMakeRect(margin_x, top_y - 16, grid_w, 14),
        "Moves",
        font=NSFont.boldSystemFontOfSize_(11),
        color=NSColor.secondaryLabelColor(),
        align=NSTextAlignmentCenter,
    ))

    for slot in range(4):
        col = slot % 2
        row = slot // 2
        x = margin_x + col * (slot_w + SLOT_GAP)
        y = top_y - 30 - row * (SLOT_HEIGHT + SLOT_GAP) - SLOT_HEIGHT
        card = _MoveSlotView.alloc().initWithFrame_(
            NSMakeRect(x, y, slot_w, SLOT_HEIGHT)
        )

        learned = by_slot.get(slot)
        if learned is None:
            card.setToolTip_("Empty slot — Pokémon hasn't learned a move here.")
            parent.addSubview_(card)
            parent.addSubview_(_label(
                NSMakeRect(x, y + 11, slot_w, 18),
                "(empty slot)",
                font=NSFont.systemFontOfSize_(11),
                color=NSColor.tertiaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            continue

        try:
            md = moves_remote.get_move_data(learned.move_key)
        except Exception:
            log.exception("move data lookup failed for %s", learned.move_key)
            md = None

        display_name = (
            md.name if md is not None
            else learned.move_key.replace("-", " ").title()
        )
        type_or_pp = (
            f"{md.type.title()}  ·  PP {learned.current_pp}/{md.pp}"
            if md is not None
            else f"PP {learned.current_pp}"
        )

        card.setToolTip_(_make_tooltip(md, learned.current_pp))
        parent.addSubview_(card)

        parent.addSubview_(_label(
            NSMakeRect(x, y + 20, slot_w, 16),
            display_name,
            font=NSFont.boldSystemFontOfSize_(11),
            color=NSColor.labelColor(),
            align=NSTextAlignmentCenter,
        ))
        parent.addSubview_(_label(
            NSMakeRect(x, y + 4, slot_w, 14),
            type_or_pp,
            font=NSFont.systemFontOfSize_(9),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

    # Total height = label (16) + 2 rows × (SLOT_HEIGHT + GAP)
    return 16 + 2 * SLOT_HEIGHT + SLOT_GAP + 6

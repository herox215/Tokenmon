"""Modal move-learn pane.

Shown when the active Pokémon has 4 learned moves AND a queued
move-learn from a recent level-up. The sidebar is restricted to
this pane + the Usage tab while one of these is pending — the user
must pick which existing move to forget (or skip the learn) before
navigating elsewhere.

Layout: 4 cards for existing moves + 1 card for the new move + a
"Don't learn" button. Clicking an existing-move card forgets that
move and installs the new one in the freed slot. Clicking the new-
move card or "Don't learn" claims the queue row without changing
moves.

After resolution, ``query_pending_move_learns`` is checked again — if
more learns are still queued (e.g. the user gained two levels at once
each adding a move), the pane re-renders with the next one.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSTextAlignmentCenter,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import box, pokemon
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    _label,
)

log = logging.getLogger("tokenmon.popover.panes.move_learn")


class _MoveCard(NSView):
    """Selectable card for a move slot (existing move or the new one).
    Click registers via the click-catcher overlay set up by the
    controller; the card itself just paints the background."""

    def initWithFrame_highlighted_(self, frame, highlighted):  # noqa: N802
        import objc
        self = objc.super(_MoveCard, self).initWithFrame_(frame)
        if self is None:
            return None
        self._highlighted = bool(highlighted)
        return self

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        if self._highlighted:
            bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.36, 0.78, 0.20, 0.18,
            )
        else:
            bg = NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.06)
        bg.set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 8, 8,
        ).fill()
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.22).set()
        border = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 8, 8,
        )
        border.setLineWidth_(0.5)
        border.stroke()


class MoveLearnController(PaneController):
    """Forces the user to resolve a move-overflow before doing anything
    other than checking their token usage."""

    def build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        try:
            from tokenmon.storage import (
                claim_pending_move_learn,
                get_pokemon_moves,
                query_pending_move_learns,
                set_pokemon_move,
            )
            from tokenmon import moves_remote
        except Exception:
            log.exception("move-learn imports failed")
            return view

        active = None
        try:
            active = box.get_active_pokemon()
        except Exception:
            log.exception("active lookup failed")
        if active is None:
            view.addSubview_(_label(
                NSMakeRect(20, POPOVER_HEIGHT // 2, CONTENT_WIDTH - 40, 22),
                "No active Pokémon.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        try:
            pendings = query_pending_move_learns(active.id)
        except Exception:
            log.exception("query_pending_move_learns failed")
            pendings = []
        if not pendings:
            view.addSubview_(_label(
                NSMakeRect(20, POPOVER_HEIGHT // 2, CONTENT_WIDTH - 40, 22),
                "No moves to learn right now.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        pending = pendings[0]
        new_md = moves_remote.get_move_data(pending.move_key)
        new_name = (
            new_md.name if new_md is not None
            else pending.move_key.replace("-", " ").title()
        )

        try:
            existing = sorted(
                get_pokemon_moves(active.id), key=lambda m: m.slot,
            )
        except Exception:
            log.exception("get_pokemon_moves failed")
            existing = []

        active_name = pokemon.display_name(
            active.nickname, active.species_dex_id,
        )

        # Headline
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 50, CONTENT_WIDTH - 40, 24),
            f"📘  {active_name} möchte {new_name} lernen!",
            font=NSFont.boldSystemFontOfSize_(15),
            align=NSTextAlignmentCenter,
        ))
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 76, CONTENT_WIDTH - 40, 18),
            f"Aber {active_name} kennt schon vier Attacken — welche soll vergessen werden?",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        # 4 existing-move cards in a 2×2 grid
        margin_x = 16
        card_h = 60
        card_gap = 8
        grid_w = CONTENT_WIDTH - margin_x * 2
        card_w = (grid_w - card_gap) // 2
        grid_top = POPOVER_HEIGHT - 100

        def _forget_handler(slot_to_forget: int):
            def _click(_s):
                try:
                    md = moves_remote.get_move_data(pending.move_key)
                    pp = md.pp if md is not None else 35
                    set_pokemon_move(
                        active.id, slot_to_forget, pending.move_key,
                        max_pp=pp,
                    )
                    claim_pending_move_learn(pending.id)
                    self._after_resolved()
                except Exception:
                    log.exception("forget+learn failed")
            return make_handler(_click)

        for i in range(4):
            col = i % 2
            row = i // 2
            x = margin_x + col * (card_w + card_gap)
            y = grid_top - (row + 1) * (card_h + card_gap)
            slot_move = next(
                (m for m in existing if m.slot == i), None,
            )
            self._render_existing_card(
                view, x, y, card_w, card_h, slot=i,
                slot_move=slot_move,
                make_handler_for_slot=_forget_handler,
            )

        # New-move "preview" card + Don't-learn button
        preview_y = grid_top - 2 * (card_h + card_gap) - card_gap - card_h
        self._render_new_move_card(
            view, margin_x, preview_y, grid_w, card_h,
            move_key=pending.move_key, move_md=new_md,
        )

        def _skip(_s):
            try:
                claim_pending_move_learn(pending.id)
                self._after_resolved()
            except Exception:
                log.exception("skip failed")

        skip_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, 16, grid_w, 28)
        )
        skip_btn.setTitle_(f"Nicht lernen (skip {new_name})")
        skip_btn.setBezelStyle_(1)
        h_skip = make_handler(_skip)
        self._handlers.append(h_skip)
        skip_btn.setTarget_(h_skip)
        skip_btn.setAction_(b"fire:")
        view.addSubview_(skip_btn)

        return view

    def _render_existing_card(
        self, parent, x, y, w, h, *,
        slot: int,
        slot_move,
        make_handler_for_slot,
    ) -> None:
        card = _MoveCard.alloc().initWithFrame_highlighted_(
            NSMakeRect(x, y, w, h), False,
        )
        parent.addSubview_(card)

        if slot_move is None:
            parent.addSubview_(_label(
                NSMakeRect(x, y + h / 2 - 8, w, 16),
                "(empty)",
                color=NSColor.tertiaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return

        from tokenmon import moves_remote
        md = moves_remote.get_move_data(slot_move.move_key)
        name = md.name if md is not None else slot_move.move_key.replace("-", " ").title()
        type_str = md.type.title() if md is not None else "?"
        pp_str = (
            f"PP {slot_move.current_pp}/{md.pp}" if md is not None
            else f"PP {slot_move.current_pp}"
        )

        parent.addSubview_(_label(
            NSMakeRect(x, y + h - 22, w, 16),
            name,
            font=NSFont.boldSystemFontOfSize_(12),
            align=NSTextAlignmentCenter,
        ))
        parent.addSubview_(_label(
            NSMakeRect(x, y + h - 38, w, 14),
            f"{type_str}  ·  {pp_str}",
            font=NSFont.systemFontOfSize_(10),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))
        forget_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(x + 6, y + 4, w - 12, 20)
        )
        forget_btn.setTitle_(f"Vergessen → {name}")
        forget_btn.setBezelStyle_(1)
        h = make_handler_for_slot(slot)
        self._handlers.append(h)
        forget_btn.setTarget_(h)
        forget_btn.setAction_(b"fire:")
        parent.addSubview_(forget_btn)

    def _render_new_move_card(
        self, parent, x, y, w, h, *, move_key: str, move_md,
    ) -> None:
        card = _MoveCard.alloc().initWithFrame_highlighted_(
            NSMakeRect(x, y, w, h), True,
        )
        parent.addSubview_(card)
        name = (
            move_md.name if move_md is not None
            else move_key.replace("-", " ").title()
        )
        if move_md is not None:
            type_str = move_md.type.title()
            cat_str = move_md.category.title()
            power = move_md.power if move_md.power is not None else "—"
            acc = (
                f"{move_md.accuracy}%" if move_md.accuracy is not None
                else "Always hits"
            )
            details = f"{type_str} · {cat_str} · Power {power} · {acc} · PP {move_md.pp}"
        else:
            details = "Move data not loaded yet."

        parent.addSubview_(_label(
            NSMakeRect(x, y + h - 22, w, 16),
            f"Neuer Move:  {name}",
            font=NSFont.boldSystemFontOfSize_(12),
            color=NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.36, 0.78, 0.20, 1.0,
            ),
            align=NSTextAlignmentCenter,
        ))
        parent.addSubview_(_label(
            NSMakeRect(x, y + h - 40, w, 14),
            details,
            font=NSFont.systemFontOfSize_(10),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

    def _after_resolved(self) -> None:
        """One pending row claimed. If more are queued, re-render this
        pane to walk through them; otherwise hand off to the active-
        Pokémon pane."""
        try:
            from tokenmon.storage import query_pending_move_learns
            active = box.get_active_pokemon()
            if active is None:
                self.popover._show_pane(PANE_POKEMON)
                return
            still_pending = query_pending_move_learns(active.id)
            if still_pending:
                self.popover._show_pane(self.popover._current_pane)
            else:
                self.popover._show_pane(PANE_POKEMON)
        except Exception:
            log.exception("after-resolved transition failed")
            self.popover._show_pane(PANE_POKEMON)

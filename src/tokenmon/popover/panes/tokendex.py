"""Tokendex pane: pokedex list + per-species detail drilldown.

State: ``_selected_dex`` decides between list and detail view. The
controller owns the back/entry click handlers as closures via the
generic ``_ActionHandler``.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSBezelStyleRegularSquare,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSScrollView,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import pokemon
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_TOKENDEX,
    POPOVER_HEIGHT,
    _crisp_image_view,
    _label,
)

log = logging.getLogger("tokenmon.popover.panes.tokendex")


class TokendexController(PaneController):
    """Renders the Tokendex list or, when ``_selected_dex`` is set, the
    species-detail drilldown."""

    def __init__(self, popover) -> None:
        super().__init__(popover)
        self._selected_dex: int | None = popover._pokedex_selected_dex

    def build_view(self) -> NSView:
        if self._selected_dex is not None:
            return self._build_detail(self._selected_dex)
        return self._build_list()

    # ---- list view -----------------------------------------------------

    def _build_list(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        from tokenmon.storage import query_pokedex_seen
        try:
            statuses = query_pokedex_seen()
        except Exception:
            log.exception("query_pokedex_seen failed")
            statuses = {}
        caught_set: set[int] = {d for d, s in statuses.items() if s == "caught"}
        seen_set: set[int] = {d for d, s in statuses.items() if s == "seen"}

        all_ids = sorted(pokemon.ALL_NAMES.keys())

        caught_n = sum(1 for i in all_ids if i in caught_set)
        seen_n = sum(1 for i in all_ids if i in seen_set and i not in caught_set)
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, CONTENT_WIDTH - 32, 22),
            f"Pokedex   ·   {caught_n} caught   ·   {seen_n} seen",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        scroll_h = POPOVER_HEIGHT - 44
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, scroll_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)
        # Transparent so the popover's weather layer shows through.
        scroll.setDrawsBackground_(False)
        scroll.contentView().setDrawsBackground_(False)

        row_h = 44
        row_width = CONTENT_WIDTH - 16
        content_h = max(row_h * len(all_ids), scroll_h)
        content = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, row_width, content_h)
        )

        for i, dex_id in enumerate(all_ids):
            y = content_h - (i + 1) * row_h
            if dex_id in caught_set:
                state = "caught"
            elif dex_id in seen_set:
                state = "seen"
            else:
                state = "unknown"
            content.addSubview_(self._build_row(
                NSMakeRect(0, y, row_width, row_h - 4), dex_id, state,
            ))

        scroll.setDocumentView_(content)
        scroll.contentView().scrollToPoint_((0, max(0, content_h - scroll_h)))
        view.addSubview_(scroll)
        return view

    def _build_row(self, frame, dex_id: int, state: str) -> NSView:
        """One Pokedex entry: dex#, sprite (or silhouette), name (or ???).

        state ∈ {"caught", "seen", "unknown"}. Caught shows the full coloured
        animated sprite + the species name; clicking opens the species
        detail pane. Seen shows a white silhouette + "Seen". Unknown shows
        a faint "?" + "???".
        """
        height = frame.size.height
        width = frame.size.width
        sprite_size = 28

        if state == "caught":
            row = NSButton.alloc().initWithFrame_(frame)
            row.setTitle_("")
            row.setBordered_(False)
            row.setBezelStyle_(NSBezelStyleRegularSquare)
            def _open_dex(_s, did=dex_id):
                self.popover._pokedex_selected_dex = did
                self.popover._show_pane(PANE_TOKENDEX)
            handler = make_handler(_open_dex)
            self._handlers.append(handler)
            row.setTarget_(handler)
            row.setAction_(b"fire:")
        else:
            row = NSView.alloc().initWithFrame_(frame)

        sprite_x = 16
        sprite_y = (height - sprite_size) / 2

        if state == "caught":
            iv = _crisp_image_view(
                NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size)
            )
            sp = pokemon.ensure_sprite(dex_id)
            if sp is not None and sp.exists():
                img = NSImage.alloc().initWithContentsOfFile_(str(sp))
                if img is not None:
                    iv.setImage_(img)
            row.addSubview_(iv)
            self.popover._animated_image_views.append(iv)
        elif state == "seen":
            from tokenmon.overlay import _silhouette_image
            iv = _crisp_image_view(
                NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size)
            )
            sp = pokemon.ensure_sprite(dex_id)
            if sp is not None and sp.exists():
                img = NSImage.alloc().initWithContentsOfFile_(str(sp))
                if img is not None:
                    sil = _silhouette_image(
                        img, NSColor.colorWithCalibratedWhite_alpha_(0.55, 1.0)
                    )
                    iv.setImage_(sil)
                    iv.setAnimates_(False)
            row.addSubview_(iv)
        else:
            row.addSubview_(_label(
                NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size),
                "?",
                font=NSFont.boldSystemFontOfSize_(18),
                color=NSColor.tertiaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))

        text_x = sprite_x + sprite_size + 12
        text_w = width - text_x - 16

        if state == "caught":
            label_text = f"#{dex_id:03d}  {pokemon.name_of(dex_id)}"
            color = NSColor.labelColor()
        elif state == "seen":
            label_text = f"#{dex_id:03d}  Seen"
            color = NSColor.secondaryLabelColor()
        else:
            label_text = f"#{dex_id:03d}  ???"
            color = NSColor.tertiaryLabelColor()

        row.addSubview_(_label(
            NSMakeRect(text_x, (height - 18) / 2, text_w, 18),
            label_text,
            font=NSFont.systemFontOfSize_(13),
            color=color,
        ))
        return row

    # ---- detail view ---------------------------------------------------

    def _build_detail(self, dex_id: int) -> NSView:
        """Big animated sprite + name + genus + flavour text from PokeAPI."""
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        def _back(_s):
            self.popover._pokedex_selected_dex = None
            self.popover._show_pane(PANE_TOKENDEX)
        back_handler = make_handler(_back)
        self._handlers.append(back_handler)
        back = NSButton.alloc().initWithFrame_(
            NSMakeRect(8, POPOVER_HEIGHT - 32, 80, 24)
        )
        back.setTitle_("← Back")
        back.setBezelStyle_(1)
        back.setTarget_(back_handler)
        back.setAction_(b"fire:")
        view.addSubview_(back)

        sprite_size = 128
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = POPOVER_HEIGHT - 36 - sprite_size
        iv = _crisp_image_view(
            NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size)
        )
        sp = pokemon.ensure_sprite(dex_id)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self.popover._animated_image_views.append(iv)

        name = pokemon.name_of(dex_id)
        name_y = sprite_y - 30
        view.addSubview_(_label(
            NSMakeRect(0, name_y, CONTENT_WIDTH, 24),
            f"#{dex_id:03d}  {name}",
            font=NSFont.boldSystemFontOfSize_(16),
            align=NSTextAlignmentCenter,
        ))

        try:
            from tokenmon.pokedex_remote import get_species_info
            info = get_species_info(dex_id)
        except Exception:
            log.exception("get_species_info failed")
            info = None

        genus = (info or {}).get("genus") or ""
        description = (info or {}).get("description") or ""

        genus_y = name_y - 22
        view.addSubview_(_label(
            NSMakeRect(0, genus_y, CONTENT_WIDTH, 18),
            genus or "—",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        # Evolution hint line — always reserved (18 px) so the description
        # box doesn't shift between species with/without an evolution.
        evo_hint = pokemon.next_evolution_hint(dex_id) or ""
        evo_y = genus_y - 18
        view.addSubview_(_label(
            NSMakeRect(0, evo_y, CONTENT_WIDTH, 14),
            evo_hint,
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        desc_y_top = evo_y - 8
        desc_h = desc_y_top - 16
        desc_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, 16, CONTENT_WIDTH - 40, desc_h)
        )
        desc_field.setStringValue_(
            description or "(No description available — try again later.)"
        )
        desc_field.setBezeled_(False)
        desc_field.setDrawsBackground_(False)
        desc_field.setEditable_(False)
        desc_field.setSelectable_(True)
        desc_field.setFont_(NSFont.systemFontOfSize_(12))
        desc_field.setTextColor_(NSColor.labelColor())
        desc_field.setLineBreakMode_(0)
        try:
            desc_field.cell().setWraps_(True)
        except Exception:
            pass
        view.addSubview_(desc_field)
        return view

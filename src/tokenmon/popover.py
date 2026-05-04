"""Custom NSPopover that replaces the rumps dropdown menu.

Layout: a 60-pixel sidebar on the left with four icon buttons (Today /
Pokedex / Box / Usage) and a swappable content pane on the right. The
popover anchors to the menubar status item with NSRectEdgeMinY so it
"rolls down" from the icon, and uses NSPopoverBehaviorTransient so
clicking outside dismisses it automatically.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import objc
import rumps
from AppKit import (
    NSBezelStyleRegularSquare,
    NSBezierPath,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSEvent,
    NSEventMaskLeftMouseDown,
    NSEventMaskOtherMouseDown,
    NSEventMaskRightMouseDown,
    NSEventTypeRightMouseDown,
    NSEventTypeRightMouseUp,
    NSFont,
    NSGraphicsContext,
    NSImage,
    NSImageInterpolationNone,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMenu,
    NSMenuItem,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSRectEdgeMinY,
    NSScrollView,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
    NSViewController,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject

from tokenmon import config, pokemon
from tokenmon.pricing import cost_for
from tokenmon.storage import (
    list_pokemon,
    query_pokemon_xp,
    query_today,
    query_today_by_model,
    query_xp_for_date,
)
from tokenmon.tokendex import _XPBarView

log = logging.getLogger("tokenmon.popover")

POPOVER_WIDTH = 480
POPOVER_HEIGHT = 440
SIDEBAR_WIDTH = 60
CONTENT_WIDTH = POPOVER_WIDTH - SIDEBAR_WIDTH
TZ = "Europe/Berlin"

PANE_POKEMON = 0
PANE_TOKENDEX = 1
PANE_BOX = 2
PANE_USAGE = 3

ROW_HEIGHT = 100  # mirrors tokendex.ROW_HEIGHT


class _CrispImageView(NSImageView):
    """NSImageView subclass that disables interpolation when drawing the image.
    Without this, animated GIF sprites get bilinear-blurred when scaled up
    from their native ~96×96 to e.g. 144×144 in the popover."""

    def drawRect_(self, rect):  # noqa: N802
        ctx = NSGraphicsContext.currentContext()
        if ctx is not None:
            ctx.setImageInterpolation_(NSImageInterpolationNone)
        objc.super(_CrispImageView, self).drawRect_(rect)


def _crisp_image_view(frame) -> NSImageView:
    """Build a layer-backed NSImageView with nearest-neighbor magnification
    AND a draw-time interpolation override — belt-and-suspenders so pixel-art
    sprites stay sharp at any zoom level."""
    iv = _CrispImageView.alloc().initWithFrame_(frame)
    iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    iv.setAnimates_(True)
    iv.setWantsLayer_(True)
    layer = iv.layer()
    if layer is not None:
        layer.setMagnificationFilter_("nearest")
        layer.setMinificationFilter_("nearest")
    return iv


def _label(frame, text, *, font=None, color=None, align=None, multiline=False) -> NSTextField:
    f = NSTextField.alloc().initWithFrame_(frame)
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setFont_(font or NSFont.systemFontOfSize_(13))
    f.setTextColor_(color or NSColor.labelColor())
    if align is not None:
        f.setAlignment_(align)
    if multiline:
        f.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
    return f


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000:.1f}K"
    if n < 1_000_000_000:
        return f"{n/1_000_000:.2f}M"
    return f"{n/1_000_000_000:.2f}B"


def _fmt_usd(amount: float) -> str:
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 1:
        return f"${amount:.3f}"
    return f"${amount:.2f}"


class _ContentVC(NSViewController):
    """Trivial NSViewController subclass — NSPopover requires one."""

    def loadView(self):  # noqa: N802
        if hasattr(self, "_root_view") and self._root_view is not None:
            self.setView_(self._root_view)
        else:
            self.setView_(NSView.alloc().initWithFrame_(NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_HEIGHT)))


def _new_vc(root_view: NSView) -> NSViewController:
    vc = _ContentVC.alloc().init()
    vc._root_view = root_view
    vc.view()  # force loadView
    return vc


class _SidebarView(NSView):
    """Background-tinted sidebar that highlights the selected slot."""

    SLOT_HEIGHT = 60

    def initWithFrame_selectedIndex_(self, frame, idx):  # noqa: N802
        self = objc.super(_SidebarView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._selected = idx
        return self

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        # Subtle sidebar background tint.
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.03).set()
        NSBezierPath.fillRect_(bounds)
        # Right edge separator.
        NSColor.separatorColor().set()
        NSBezierPath.fillRect_(NSMakeRect(bounds.size.width - 1, 0, 1, bounds.size.height))
        # Selected-slot pill.
        slot_h = _SidebarView.SLOT_HEIGHT
        y = bounds.size.height - (self._selected + 1) * slot_h
        rect = NSMakeRect(4, y + 4, bounds.size.width - 8, slot_h - 8)
        NSColor.controlAccentColor().colorWithAlphaComponent_(0.18).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 6, 6).fill()

    def setSelected_(self, idx):  # noqa: N802
        self._selected = int(idx)
        self.setNeedsDisplay_(True)


class _RightClickHandler(NSObject):
    """Bridge for the right-click fallback menu's Quit item."""

    def quit_(self, _sender):  # noqa: N802
        rumps.quit_application(None)


# =============================================================================
# Box pane click handlers
# =============================================================================


class _BoxItemHandler(NSObject):
    """Per-item click target for grid Pokemon buttons. Stores the Pokemon id
    and on click pushes the popover into detail-view mode for that id."""

    def initWithPopover_pokemonId_(self, popover, pokemon_id):  # noqa: N802
        self = objc.super(_BoxItemHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._pokemon_id = int(pokemon_id)
        return self

    def itemClicked_(self, _sender):  # noqa: N802
        self._popover._box_selected_id = self._pokemon_id
        self._popover._show_pane(PANE_BOX)


class _BoxBackHandler(NSObject):
    """"← Back" button handler — clears the selected id and re-renders the grid."""

    def initWithPopover_(self, popover):  # noqa: N802
        self = objc.super(_BoxBackHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        return self

    def backClicked_(self, _sender):  # noqa: N802
        self._popover._box_selected_id = None
        self._popover._show_pane(PANE_BOX)


class TokenmonPopover(NSObject):
    """Holds the NSPopover, builds panes, owns sidebar selection state."""

    def initWithApp_(self, app):  # noqa: N802
        self = objc.super(TokenmonPopover, self).init()
        if self is None:
            return None
        self._app = app
        self._popover = NSPopover.alloc().init()
        self._popover.setBehavior_(NSPopoverBehaviorTransient)
        self._popover.setAnimates_(True)
        self._popover.setDelegate_(self)
        self._popover.setContentSize_(NSMakeSize(POPOVER_WIDTH, POPOVER_HEIGHT))

        self._root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_HEIGHT))

        self._sidebar = _SidebarView.alloc().initWithFrame_selectedIndex_(
            NSMakeRect(0, 0, SIDEBAR_WIDTH, POPOVER_HEIGHT), PANE_POKEMON,
        )
        self._sidebar_buttons: list[NSButton] = []
        self._build_sidebar_buttons()
        self._root.addSubview_(self._sidebar)

        self._content_container = NSView.alloc().initWithFrame_(
            NSMakeRect(SIDEBAR_WIDTH, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )
        self._root.addSubview_(self._content_container)

        self._current_pane: int = PANE_POKEMON
        self._current_pane_view: NSView | None = None
        self._animated_image_views: list[NSImageView] = []

        # Box pane state — None means show grid, an int id means show detail.
        self._box_selected_id: int | None = None
        # Strong references to handlers so they aren't garbage-collected
        # while the buttons that target them are alive.
        self._box_handlers: list[NSObject] = []
        self._box_back_handler: _BoxBackHandler | None = None

        self._vc = _new_vc(self._root)
        self._popover.setContentViewController_(self._vc)

        self._right_click_handler = _RightClickHandler.alloc().init()
        self._global_monitor = None  # NSEvent monitor; set while popover open

        return self

    # ---- sidebar ----

    def _build_sidebar_buttons(self) -> None:
        items = [
            (PANE_POKEMON, "🥚"),
            (PANE_TOKENDEX, "📖"),
            (PANE_BOX, "📦"),
            (PANE_USAGE, "$"),
        ]
        slot_h = _SidebarView.SLOT_HEIGHT
        for idx, (pane_id, fallback) in enumerate(items):
            y = POPOVER_HEIGHT - (idx + 1) * slot_h
            btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(8, y + 8, SIDEBAR_WIDTH - 16, slot_h - 16)
            )
            btn.setTitle_(fallback)
            btn.setBezelStyle_(NSBezelStyleRegularSquare)
            btn.setBordered_(False)
            btn.setFont_(NSFont.systemFontOfSize_(20))
            btn.setTag_(pane_id)
            btn.setTarget_(self)
            btn.setAction_(b"sidebarClicked:")
            self._sidebar.addSubview_(btn)
            self._sidebar_buttons.append(btn)

    def _refresh_sidebar_pokemon_icon(self) -> None:
        sprite = self._app._pokemon_sprite
        if sprite is not None and sprite.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sprite))
            if img is not None:
                img.setSize_(NSMakeSize(36, 36))
                btn = self._sidebar_buttons[PANE_POKEMON]
                btn.setImage_(img)
                btn.setTitle_("")
                # Nearest-neighbor scaling for the small sidebar sprite too.
                btn.setWantsLayer_(True)
                if btn.layer() is not None:
                    btn.layer().setMagnificationFilter_("nearest")
                    btn.layer().setMinificationFilter_("nearest")

    def sidebarClicked_(self, sender):  # noqa: N802
        idx = int(sender.tag())
        if idx == self._current_pane and self._current_pane_view is not None:
            return
        self._current_pane = idx
        self._sidebar.setSelected_(idx)
        self._show_pane(idx)

    # ---- panes ----

    def _show_pane(self, idx: int) -> None:
        # Reset animation tracking + box click handlers — new pane gets fresh lists.
        self._animated_image_views = []
        self._box_handlers = []
        self._box_back_handler = None
        try:
            if idx == PANE_POKEMON:
                view = self._build_pane_pokemon()
            elif idx == PANE_TOKENDEX:
                view = self._build_pane_tokendex()
            elif idx == PANE_BOX:
                view = self._build_pane_box()
            elif idx == PANE_USAGE:
                view = self._build_pane_usage()
            else:
                view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        except Exception:
            log.exception("failed to build pane %s", idx)
            view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        if self._current_pane_view is not None:
            self._current_pane_view.removeFromSuperview()
        view.setFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        self._content_container.addSubview_(view)
        self._current_pane_view = view

    # =========================================================================
    # Pane: Today (today's caught Pokemon detail)
    # =========================================================================

    def _build_pane_pokemon(self) -> NSView:
        # Lazy import to avoid a circular import (box → storage → ...).
        from tokenmon import box

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        try:
            row = box.ensure_today_pokemon()
        except Exception:
            log.exception("ensure_today_pokemon failed")
            row = None

        if row is None:
            view.addSubview_(_label(
                NSMakeRect(16, POPOVER_HEIGHT // 2 - 10, CONTENT_WIDTH - 32, 20),
                "Konnte heute kein Pokemon laden.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        species = row.species_dex_id

        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = POPOVER_HEIGHT - sprite_size - 28

        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(species)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self._animated_image_views.append(iv)

        name = pokemon.name_of(species)
        name_y = sprite_y - 32
        view.addSubview_(_label(
            NSMakeRect(0, name_y, CONTENT_WIDTH, 26),
            f"#{species:03d}  {name}",
            font=NSFont.boldSystemFontOfSize_(18),
            align=NSTextAlignmentCenter,
        ))

        # XP for today only (output tokens earned on today's local date).
        try:
            xp = query_xp_for_date(row.caught_date, TZ)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(species)
        level, into, needed = pokemon.level_from_xp(xp, rate)

        lvl_y = name_y - 28
        lvl_text = "Lv MAX" if level >= pokemon.MAX_LEVEL else f"Lv {level}"
        view.addSubview_(_label(
            NSMakeRect(0, lvl_y, CONTENT_WIDTH, 22),
            lvl_text,
            font=NSFont.boldSystemFontOfSize_(14),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        bar_w = 260
        bar_x = (CONTENT_WIDTH - bar_w) // 2
        bar_y = lvl_y - 14
        progress = into / needed if needed > 0 else (1.0 if level >= pokemon.MAX_LEVEL else 0.0)
        bar = _XPBarView.alloc().initWithFrame_progress_(
            NSMakeRect(bar_x, bar_y, bar_w, 8), progress,
        )
        view.addSubview_(bar)

        xp_y = bar_y - 20
        xp_text = "MAX" if level >= pokemon.MAX_LEVEL else f"{into:,} / {needed:,} XP"
        view.addSubview_(_label(
            NSMakeRect(0, xp_y, CONTENT_WIDTH, 14),
            xp_text,
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        nature_y = xp_y - 24
        view.addSubview_(_label(
            NSMakeRect(0, nature_y, CONTENT_WIDTH, 16),
            f"{row.nature} nature",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.labelColor(),
            align=NSTextAlignmentCenter,
        ))

        char_y = nature_y - 18
        view.addSubview_(_label(
            NSMakeRect(0, char_y, CONTENT_WIDTH, 16),
            f"“{row.characteristic}.”",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        return view

    # =========================================================================
    # Pane: Pokedex (species-counts from box)
    # =========================================================================

    def _build_pane_tokendex(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # Header
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, CONTENT_WIDTH - 32, 22),
            "Pokedex",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        scroll_h = POPOVER_HEIGHT - 44
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, scroll_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)

        # Aggregate species-counts from the box.
        try:
            box_rows = list_pokemon()
        except Exception:
            log.exception("list_pokemon failed")
            box_rows = []

        # Group by species_dex_id; remember each instance for level computation.
        by_species: dict[int, list] = {}
        for p in box_rows:
            by_species.setdefault(p.species_dex_id, []).append(p)

        # Per species: count + (highest_level, instance_for_sprite).
        species_summaries: list[dict] = []
        for species_id, instances in by_species.items():
            best_level = 0
            best_instance = instances[0]
            for inst in instances:
                try:
                    inst_xp = query_xp_for_date(inst.caught_date, TZ)
                except Exception:
                    inst_xp = 0
                rate = pokemon.growth_rate_of(species_id)
                lvl, _, _ = pokemon.level_from_xp(inst_xp, rate)
                if lvl > best_level:
                    best_level = lvl
                    best_instance = inst
            species_summaries.append({
                "species_id": species_id,
                "count": len(instances),
                "best_level": best_level,
                "best_instance": best_instance,
            })

        # Sort: highest level first, then by count, then by dex id.
        species_summaries.sort(
            key=lambda s: (-s["best_level"], -s["count"], s["species_id"])
        )

        row_h = 60
        row_width = CONTENT_WIDTH - 16  # leave room for scrollbar
        content_h = max(row_h * len(species_summaries), scroll_h)
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, row_width, content_h))

        if not species_summaries:
            content.addSubview_(_label(
                NSMakeRect(16, content_h / 2 - 10, row_width - 32, 20),
                "No Pokemon caught yet.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
        else:
            for i, s in enumerate(species_summaries):
                y = content_h - (i + 1) * row_h
                row_view = self._build_pokedex_row(
                    NSMakeRect(0, y, row_width, row_h - 4), s,
                )
                content.addSubview_(row_view)

        scroll.setDocumentView_(content)
        scroll.contentView().scrollToPoint_((0, max(0, content_h - scroll_h)))
        view.addSubview_(scroll)

        return view

    def _build_pokedex_row(self, frame, summary: dict) -> NSView:
        """One species row in the new Pokedex pane: sprite + name + count + level."""
        row = NSView.alloc().initWithFrame_(frame)
        height = frame.size.height
        width = frame.size.width

        species_id = summary["species_id"]
        count = summary["count"]
        best_level = summary["best_level"]
        best_instance = summary["best_instance"]

        # Sprite (32×32) of the highest-level instance's species. We use the
        # species sprite — instances within the same species share a sprite.
        sprite_size = 32
        iv = _crisp_image_view(
            NSMakeRect(12, (height - sprite_size) / 2, sprite_size, sprite_size)
        )
        sp = pokemon.ensure_sprite(species_id)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        row.addSubview_(iv)
        self._animated_image_views.append(iv)

        text_x = 12 + sprite_size + 12
        text_w = width - text_x - 16

        name_y = height - 26
        row.addSubview_(_label(
            NSMakeRect(text_x, name_y, text_w, 18),
            f"#{species_id:03d}  {pokemon.name_of(species_id)}",
            font=NSFont.boldSystemFontOfSize_(13),
        ))

        sub_y = name_y - 18
        badge = f"× {count}" if count > 1 else "Owned"
        lvl_text = "Lv MAX" if best_level >= pokemon.MAX_LEVEL else f"Lv {best_level}"
        row.addSubview_(_label(
            NSMakeRect(text_x, sub_y, text_w, 16),
            f"{badge}    {lvl_text}",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
        ))

        return row

    # =========================================================================
    # Pane: Box (grid of caught Pokemon + per-id detail view)
    # =========================================================================

    def _build_pane_box(self) -> NSView:
        if self._box_selected_id is None:
            return self._build_pane_box_grid()
        return self._build_pane_box_detail(self._box_selected_id)

    def _build_pane_box_grid(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        try:
            rows = list_pokemon()
        except Exception:
            log.exception("list_pokemon failed")
            rows = []

        # Header.
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, CONTENT_WIDTH - 32, 22),
            f"Box ({len(rows)} caught)",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        scroll_h = POPOVER_HEIGHT - 44
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, scroll_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)

        cols = 8
        cell = 40
        gap = 6
        margin = 12
        # Grid metrics.
        n = len(rows)
        rows_count = max(1, (n + cols - 1) // cols)
        grid_w = cols * cell + (cols - 1) * gap
        # Doc view: tall enough for all items.
        content_h = max(rows_count * cell + (rows_count - 1) * gap + margin * 2, scroll_h)
        doc_w = CONTENT_WIDTH - 16  # account for scrollbar gutter
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, doc_w, content_h))

        if not rows:
            content.addSubview_(_label(
                NSMakeRect(16, content_h / 2 - 10, doc_w - 32, 20),
                "No Pokemon caught yet.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
        else:
            grid_x0 = max(margin, (doc_w - grid_w) // 2)
            for i, p in enumerate(rows):
                col = i % cols
                row_idx = i // cols
                x = grid_x0 + col * (cell + gap)
                # Top-aligned: row 0 is at the top of the doc view.
                y = content_h - margin - (row_idx + 1) * cell - row_idx * gap

                btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, cell, cell))
                btn.setTitle_("")
                btn.setBordered_(False)
                btn.setBezelStyle_(NSBezelStyleRegularSquare)
                btn.setWantsLayer_(True)
                if btn.layer() is not None:
                    btn.layer().setMagnificationFilter_("nearest")
                    btn.layer().setMinificationFilter_("nearest")
                # Sprite as the button image.
                sp = pokemon.ensure_sprite(p.species_dex_id)
                if sp is not None and sp.exists():
                    img = NSImage.alloc().initWithContentsOfFile_(str(sp))
                    if img is not None:
                        img.setSize_(NSMakeSize(36, 36))
                        btn.setImage_(img)
                # Wire click → set _box_selected_id, re-render.
                handler = _BoxItemHandler.alloc().initWithPopover_pokemonId_(self, p.id)
                self._box_handlers.append(handler)
                btn.setTarget_(handler)
                btn.setAction_(b"itemClicked:")
                content.addSubview_(btn)

        scroll.setDocumentView_(content)
        # Scroll to top — newest entries (sorted desc by caught_date) sit at top.
        scroll.contentView().scrollToPoint_((0, max(0, content_h - scroll_h)))
        view.addSubview_(scroll)

        return view

    def _build_pane_box_detail(self, pokemon_id: int) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # Look up the row.
        from tokenmon.storage import get_pokemon_by_id
        try:
            p = get_pokemon_by_id(pokemon_id)
        except Exception:
            log.exception("get_pokemon_by_id failed")
            p = None

        # ← Back button (top-left).
        self._box_back_handler = _BoxBackHandler.alloc().initWithPopover_(self)
        back = NSButton.alloc().initWithFrame_(
            NSMakeRect(8, POPOVER_HEIGHT - 32, 80, 24)
        )
        back.setTitle_("← Back")
        back.setBezelStyle_(1)  # NSBezelStyleRounded
        back.setTarget_(self._box_back_handler)
        back.setAction_(b"backClicked:")
        view.addSubview_(back)

        if p is None:
            view.addSubview_(_label(
                NSMakeRect(16, POPOVER_HEIGHT // 2 - 10, CONTENT_WIDTH - 32, 20),
                "Pokemon nicht gefunden.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        species = p.species_dex_id

        # 2-column layout: left = big sprite, right = labels.
        sprite_size = 128
        sprite_x = 16
        sprite_y = POPOVER_HEIGHT - 56 - sprite_size
        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(species)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self._animated_image_views.append(iv)

        # Right column.
        col_x = sprite_x + sprite_size + 16
        col_w = CONTENT_WIDTH - col_x - 16
        y_cursor = POPOVER_HEIGHT - 60

        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 22, col_w, 22),
            f"#{species:03d}  {pokemon.name_of(species)}",
            font=NSFont.boldSystemFontOfSize_(15),
        ))
        y_cursor -= 26

        try:
            xp = query_xp_for_date(p.caught_date, TZ)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(species)
        level, into, needed = pokemon.level_from_xp(xp, rate)

        lvl_text = "Lv MAX" if level >= pokemon.MAX_LEVEL else f"Lv {level}"
        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 18, col_w, 18),
            lvl_text,
            font=NSFont.boldSystemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 22

        # XP bar
        progress = into / needed if needed > 0 else (1.0 if level >= pokemon.MAX_LEVEL else 0.0)
        bar = _XPBarView.alloc().initWithFrame_progress_(
            NSMakeRect(col_x, y_cursor - 8, col_w, 8), progress,
        )
        view.addSubview_(bar)
        y_cursor -= 14

        xp_text = "MAX" if level >= pokemon.MAX_LEVEL else f"{into:,} / {needed:,} XP"
        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 14, col_w, 14),
            xp_text,
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
        ))
        y_cursor -= 22

        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 16, col_w, 16),
            f"Nature: {p.nature}",
            font=NSFont.systemFontOfSize_(12),
        ))
        y_cursor -= 18

        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 16, col_w, 16),
            f"“{p.characteristic}.”",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 18

        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 16, col_w, 16),
            f"Caught: {p.caught_date.isoformat()}",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
        ))

        return view

    # =========================================================================
    # Pane: Usage
    # =========================================================================

    def _build_pane_usage(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        try:
            totals = query_today(TZ)
            by_model = query_today_by_model(TZ)
        except Exception:
            log.exception("usage query failed")
            from tokenmon.storage import Totals
            totals, by_model = Totals(), {}

        margin_x = 16
        y_cursor = POPOVER_HEIGHT - 30

        # Header
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 20),
            f"Heute: {_fmt_tokens(totals.output_tokens)} output tokens · {totals.request_count} requests",
            font=NSFont.boldSystemFontOfSize_(14),
        ))
        y_cursor -= 22
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 16),
            f"Output {_fmt_tokens(totals.output_tokens)}   ·   Input {_fmt_tokens(totals.input_tokens)}",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 26

        # Per-model
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 18),
            "Pro Modell",
            font=NSFont.boldSystemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 18

        total_cost = 0.0
        priced_tokens = 0
        all_tokens = 0
        max_rows = 6
        models_shown = 0
        for model, t in by_model.items():
            cost, has_price = cost_for(
                model,
                input_tokens=t.input_tokens,
                output_tokens=t.output_tokens,
                cache_read_tokens=t.cache_read_tokens,
                cache_creation_tokens=t.cache_creation_tokens,
            )
            total_cost += cost
            tokens = (
                t.input_tokens + t.output_tokens
                + t.cache_read_tokens + t.cache_creation_tokens
            )
            all_tokens += tokens
            if has_price:
                priced_tokens += tokens
            if models_shown < max_rows:
                cost_str = _fmt_usd(cost) if has_price else "?"
                model_short = model if len(model) <= 36 else model[:33] + "…"
                view.addSubview_(_label(
                    NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 16),
                    f"  {model_short}    {_fmt_tokens(t.output_tokens)} out  {cost_str}",
                    font=NSFont.systemFontOfSize_(11),
                    color=NSColor.labelColor(),
                ))
                y_cursor -= 16
                models_shown += 1
        if len(by_model) > max_rows:
            view.addSubview_(_label(
                NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 14),
                f"  … +{len(by_model) - max_rows} weitere",
                font=NSFont.systemFontOfSize_(10),
                color=NSColor.tertiaryLabelColor(),
            ))
            y_cursor -= 14
        y_cursor -= 6

        # Cost summary
        coverage_suffix = ""
        if all_tokens > 0 and priced_tokens < all_tokens:
            coverage = priced_tokens / all_tokens
            coverage_suffix = f"   ({coverage:.0%} Preisabdeckung)"
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 16),
            f"Geschätzte Kosten: {_fmt_usd(total_cost)}{coverage_suffix}",
            font=NSFont.boldSystemFontOfSize_(12),
        ))
        y_cursor -= 24

        # Footer toolbar — toggles + buttons.
        # Toggle: show pokemon in menubar
        sw_pokemon = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, y_cursor - 16, CONTENT_WIDTH - 32, 18)
        )
        sw_pokemon.setButtonType_(NSButtonTypeSwitch)
        sw_pokemon.setTitle_("Pokemon im Menubar anzeigen")
        sw_pokemon.setState_(1 if self._app._show_pokemon else 0)
        sw_pokemon.setTarget_(self)
        sw_pokemon.setAction_(b"toggleMenubarPokemon:")
        view.addSubview_(sw_pokemon)
        y_cursor -= 22

        sw_overlay = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, y_cursor - 16, CONTENT_WIDTH - 32, 18)
        )
        sw_overlay.setButtonType_(NSButtonTypeSwitch)
        sw_overlay.setTitle_("Pokemon als Desktop-Overlay anzeigen")
        sw_overlay.setState_(1 if self._app._show_overlay else 0)
        sw_overlay.setTarget_(self)
        sw_overlay.setAction_(b"toggleOverlay:")
        view.addSubview_(sw_overlay)
        y_cursor -= 26

        # Buttons row: Restart Proxy + Quit, anchored to bottom.
        btn_y = 12
        restart = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, btn_y, 160, 24)
        )
        restart.setTitle_("Proxy neustarten")
        restart.setBezelStyle_(1)  # NSBezelStyleRounded
        restart.setTarget_(self)
        restart.setAction_(b"restartProxy:")
        view.addSubview_(restart)

        quit_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(CONTENT_WIDTH - margin_x - 100, btn_y, 100, 24)
        )
        quit_btn.setTitle_("Beenden")
        quit_btn.setBezelStyle_(1)
        quit_btn.setTarget_(self)
        quit_btn.setAction_(b"quitApp:")
        view.addSubview_(quit_btn)

        return view

    # ---- toggle / button actions (Usage pane) ----

    def toggleMenubarPokemon_(self, _sender):  # noqa: N802
        self._app.toggle_menubar_pokemon(None)

    def toggleOverlay_(self, _sender):  # noqa: N802
        self._app.toggle_overlay(None)

    def restartProxy_(self, _sender):  # noqa: N802
        self._app.restart_proxy(None)

    def quitApp_(self, _sender):  # noqa: N802
        rumps.quit_application(None)

    # ---- click action ----

    @objc.IBAction
    def buttonClicked_(self, sender):  # noqa: N802
        try:
            from AppKit import NSApp
            event = NSApp.currentEvent()
        except Exception:
            event = None
        if event is not None and event.type() in (NSEventTypeRightMouseDown, NSEventTypeRightMouseUp):
            self.show_right_click_menu(sender)
        else:
            self.show_from_button(sender)

    # ---- show / hide ----

    def show_from_button(self, button) -> None:
        if self._popover.isShown():
            self._popover.close()
            return
        self._refresh_sidebar_pokemon_icon()
        self._show_pane(self._current_pane)
        # Activate so the popover gets keyboard focus and macOS-managed
        # transient dismiss has a chance.
        try:
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            log.exception("activateIgnoringOtherApps failed")
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, NSRectEdgeMinY,
        )
        self._install_global_monitor()

    def _install_global_monitor(self) -> None:
        """Install an NSEvent monitor for clicks anywhere outside our app.
        Belt-and-suspenders alongside NSPopoverBehaviorTransient — global
        monitors fire even when an LSUIElement-ish app hasn't yet "really"
        activated, which is exactly the case where transient dismiss fails."""
        if self._global_monitor is not None:
            return
        mask = (NSEventMaskLeftMouseDown
                | NSEventMaskRightMouseDown
                | NSEventMaskOtherMouseDown)
        try:
            self._global_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, self._on_global_click,
            )
        except Exception:
            log.exception("global monitor install failed")
            self._global_monitor = None

    def _uninstall_global_monitor(self) -> None:
        if self._global_monitor is None:
            return
        try:
            NSEvent.removeMonitor_(self._global_monitor)
        except Exception:
            log.exception("global monitor remove failed")
        self._global_monitor = None

    def _on_global_click(self, _event):
        # Any mouse-down anywhere outside our app while the popover is shown
        # → close the popover. The monitor doesn't fire for events inside our
        # own windows, so clicks inside the popover itself are safe.
        if self._popover is not None and self._popover.isShown():
            self._popover.close()

    def show_right_click_menu(self, button) -> None:
        """Fallback: small NSMenu with Quit, shown on right-click."""
        menu = NSMenu.alloc().init()
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Beenden", b"quit:", "",
        )
        item.setTarget_(self._right_click_handler)
        menu.addItem_(item)
        event = button.window().currentEvent() if button.window() is not None else None
        if event is not None:
            NSMenu.popUpContextMenuWithEvent_forView_(menu, event, button)

    # ---- NSPopoverDelegate ----

    def popoverWillShow_(self, _notification):  # noqa: N802
        for iv in self._animated_image_views:
            iv.setAnimates_(True)

    def popoverDidClose_(self, _notification):  # noqa: N802
        self._uninstall_global_monitor()
        for iv in self._animated_image_views:
            iv.setAnimates_(False)

"""Custom NSPopover that replaces the rumps dropdown menu.

Layout: a 60-pixel sidebar on the left with three icon buttons (Pokemon /
Tokendex / Usage) and a swappable content pane on the right. The popover
anchors to the menubar status item with NSRectEdgeMinY so it "rolls down"
from the icon, and uses NSPopoverBehaviorTransient so clicking outside
dismisses it automatically.
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
    query_pokemon_xp,
    query_today,
    query_today_by_model,
)
from tokenmon.tokendex import _RowView, _XPBarView, query_pokedex

log = logging.getLogger("tokenmon.popover")

POPOVER_WIDTH = 480
POPOVER_HEIGHT = 380
SIDEBAR_WIDTH = 60
CONTENT_WIDTH = POPOVER_WIDTH - SIDEBAR_WIDTH
TZ = "Europe/Berlin"

PANE_POKEMON = 0
PANE_TOKENDEX = 1
PANE_USAGE = 2

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
        # Reset animation tracking — new pane gets a fresh list.
        self._animated_image_views = []
        try:
            if idx == PANE_POKEMON:
                view = self._build_pane_pokemon()
            elif idx == PANE_TOKENDEX:
                view = self._build_pane_tokendex()
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

    # ---- pane: Pokemon ----

    def _build_pane_pokemon(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = POPOVER_HEIGHT - sprite_size - 28

        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(self._app._pokemon_dex_id)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self._animated_image_views.append(iv)

        name = pokemon.name_of(self._app._pokemon_dex_id)
        name_y = sprite_y - 32
        view.addSubview_(_label(
            NSMakeRect(0, name_y, CONTENT_WIDTH, 26),
            f"#{self._app._pokemon_dex_id:03d}  {name}",
            font=NSFont.boldSystemFontOfSize_(18),
            align=NSTextAlignmentCenter,
        ))

        try:
            xp = query_pokemon_xp(self._app._line_base_id, TZ)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(self._app._line_base_id)
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

        total_y = xp_y - 22
        view.addSubview_(_label(
            NSMakeRect(0, total_y, CONTENT_WIDTH, 14),
            f"Total XP: {xp:,}",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        return view

    # ---- pane: Tokendex ----

    def _build_pane_tokendex(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # Header
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, CONTENT_WIDTH - 32, 22),
            "Tokendex",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        scroll_h = POPOVER_HEIGHT - 44
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, scroll_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)

        try:
            pokedex = query_pokedex()
        except Exception:
            log.exception("query_pokedex failed")
            pokedex = {}
        rows = sorted(
            pokedex.values(),
            key=lambda e: (
                -pokemon.level_from_xp(e.xp, pokemon.growth_rate_of(e.dex_id))[0],
                -e.xp,
            ),
        )

        row_width = CONTENT_WIDTH - 16  # leave room for scrollbar
        content_h = max(ROW_HEIGHT * len(rows), scroll_h)
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, row_width, content_h))

        if not rows:
            content.addSubview_(_label(
                NSMakeRect(16, content_h / 2 - 10, row_width - 32, 20),
                "Noch kein Pokemon erlebt — sammle Tokens!",
                color=NSColor.secondaryLabelColor(),
            ))
        else:
            for i, entry in enumerate(rows):
                y = content_h - (i + 1) * ROW_HEIGHT + 4
                row = _RowView.alloc().initWithFrame_entry_(
                    NSMakeRect(0, y, row_width, ROW_HEIGHT - 8), entry
                )
                content.addSubview_(row)
                # Track image view inside the row for animation pause/resume.
                if getattr(row, "_image_view", None) is not None:
                    self._animated_image_views.append(row._image_view)

        scroll.setDocumentView_(content)
        scroll.contentView().scrollToPoint_((0, max(0, content_h - scroll_h)))
        view.addSubview_(scroll)

        return view

    # ---- pane: Usage ----

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

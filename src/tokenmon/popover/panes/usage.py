"""Usage pane: today totals + per-model breakdown + toolbar toggles.

Static layout (no animations) plus a debug spawn button that flashes
"(already pending)" when the spawn is rejected. Toggle/restart/quit
buttons stay wired to ``TokenmonPopover`` so the existing AppKit
selectors (``toggleMenubarPokemon:`` etc.) keep working.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSTextField,
    NSTimer,
    NSView,
)
from Foundation import NSMakeRect, NSObject

from tokenmon import encounter
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_ENCOUNTER,
    POPOVER_HEIGHT,
    _label,
)
from tokenmon.pricing import cost_for
from tokenmon.storage import query_today, query_today_by_model
from tokenmon.ui_helpers import (
    fmt_tokens as _fmt_tokens,
    fmt_usd as _fmt_usd,
)

log = logging.getLogger("tokenmon.popover.panes.usage")

TZ = "Europe/Berlin"


class UsageController(PaneController):
    """Renders the Usage pane and owns its 'already pending' flash state."""

    def __init__(self, popover) -> None:
        super().__init__(popover)
        self._already_pending_label: NSTextField | None = None
        self._already_pending_timer = None
        self._already_pending_label_frame = None
        self._already_pending_parent: NSView | None = None

    # ---- public --------------------------------------------------------

    def build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

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
        sw_pokemon = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, y_cursor - 16, CONTENT_WIDTH - 32, 18)
        )
        sw_pokemon.setButtonType_(NSButtonTypeSwitch)
        sw_pokemon.setTitle_("Pokemon im Menubar anzeigen")
        sw_pokemon.setState_(1 if self.popover._app._show_pokemon else 0)
        sw_pokemon.setTarget_(self.popover)
        sw_pokemon.setAction_(b"toggleMenubarPokemon:")
        view.addSubview_(sw_pokemon)
        y_cursor -= 22

        sw_overlay = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, y_cursor - 16, CONTENT_WIDTH - 32, 18)
        )
        sw_overlay.setButtonType_(NSButtonTypeSwitch)
        sw_overlay.setTitle_("Pokemon als Desktop-Overlay anzeigen")
        sw_overlay.setState_(1 if self.popover._app._show_overlay else 0)
        sw_overlay.setTarget_(self.popover)
        sw_overlay.setAction_(b"toggleOverlay:")
        view.addSubview_(sw_overlay)
        y_cursor -= 26

        # Debug spawn-encounter button (sits just above the Restart/Quit row).
        spawn_btn_y = 44
        spawn_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, spawn_btn_y, 220, 24)
        )
        spawn_btn.setTitle_("🐛 Spawn encounter (debug)")
        spawn_btn.setBezelStyle_(1)
        def _spawn_debug(_s):
            try:
                spawned = encounter.maybe_spawn(force=True)
            except Exception:
                log.exception("maybe_spawn(force=True) failed")
                return
            if spawned is None:
                self.flash_already_pending()
                return
            self.popover._show_pane(PANE_ENCOUNTER)
        spawn_handler = make_handler(_spawn_debug)
        self._handlers.append(spawn_handler)
        spawn_btn.setTarget_(spawn_handler)
        spawn_btn.setAction_(b"fire:")
        view.addSubview_(spawn_btn)

        # Inline label slot for "(already pending)" — created lazily by
        # flash_already_pending() and torn down by its NSTimer.
        self._already_pending_label_frame = NSMakeRect(
            margin_x + 226, spawn_btn_y + 4,
            CONTENT_WIDTH - margin_x - 226 - 16, 16,
        )
        self._already_pending_parent = view

        # Buttons row: Restart Proxy + Quit, anchored to bottom.
        btn_y = 12
        restart = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, btn_y, 160, 24)
        )
        restart.setTitle_("Proxy neustarten")
        restart.setBezelStyle_(1)
        restart.setTarget_(self.popover)
        restart.setAction_(b"restartProxy:")
        view.addSubview_(restart)

        quit_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(CONTENT_WIDTH - margin_x - 100, btn_y, 100, 24)
        )
        quit_btn.setTitle_("Beenden")
        quit_btn.setBezelStyle_(1)
        quit_btn.setTarget_(self.popover)
        quit_btn.setAction_(b"quitApp:")
        view.addSubview_(quit_btn)

        return view

    def flash_already_pending(self) -> None:
        """Show '(already pending)' next to the debug spawn button briefly."""
        parent = self._already_pending_parent
        frame = self._already_pending_label_frame
        if parent is None or frame is None:
            return
        # Tear down any in-flight one first.
        if self._already_pending_timer is not None:
            try:
                self._already_pending_timer.invalidate()
            except Exception:
                pass
            self._already_pending_timer = None
        if self._already_pending_label is not None:
            self._already_pending_label.removeFromSuperview()
            self._already_pending_label = None

        lbl = _label(
            frame,
            "(already pending)",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
        )
        parent.addSubview_(lbl)
        self._already_pending_label = lbl

        def _hide(_t):
            if self._already_pending_label is not None:
                self._already_pending_label.removeFromSuperview()
                self._already_pending_label = None
            self._already_pending_timer = None
        hider = make_handler(_hide)
        self._handlers.append(hider)
        self._already_pending_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.5, hider, b"fire:", None, False,
            )
        )

    def teardown(self) -> None:
        super().teardown()
        if self._already_pending_timer is not None:
            try:
                self._already_pending_timer.invalidate()
            except Exception:
                pass
            self._already_pending_timer = None
        if self._already_pending_label is not None:
            try:
                self._already_pending_label.removeFromSuperview()
            except Exception:
                pass
            self._already_pending_label = None

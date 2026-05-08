"""Usage pane: today totals + per-model breakdown + token-usage chart +
toolbar toggles. Toggle/restart/quit buttons stay wired to
``TokenmonPopover`` so the existing AppKit selectors
(``toggleMenubarPokemon:`` etc.) keep working.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from AppKit import (
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSTimer,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    POPOVER_HEIGHT,
    _TokenChartView,
    _label,
)
from tokenmon.pricing import cost_for
from tokenmon.storage import (
    query_today,
    query_today_by_model,
    query_today_token_buckets,
)
from tokenmon.ui_helpers import (
    fmt_tokens as _fmt_tokens,
    fmt_usd as _fmt_usd,
)

CHART_BUCKET_MINUTES = 15
CHART_REFRESH_S = 30.0

log = logging.getLogger("tokenmon.popover.panes.usage")

TZ = "Europe/Berlin"


class UsageController(PaneController):
    """Renders the Usage pane and owns its 'already pending' flash state."""

    def __init__(self, popover) -> None:
        super().__init__(popover)
        self._chart_view: _TokenChartView | None = None
        self._chart_timer = None

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
            f"Today: {_fmt_tokens(totals.output_tokens)} output tokens · {totals.request_count} requests",
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
                f"  … +{len(by_model) - max_rows} more",
                font=NSFont.systemFontOfSize_(10),
                color=NSColor.tertiaryLabelColor(),
            ))
            y_cursor -= 14
        y_cursor -= 6

        # Cost summary
        coverage_suffix = ""
        if all_tokens > 0 and priced_tokens < all_tokens:
            coverage = priced_tokens / all_tokens
            coverage_suffix = f"   ({coverage:.0%} price coverage)"
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 16),
            f"Estimated cost: {_fmt_usd(total_cost)}{coverage_suffix}",
            font=NSFont.boldSystemFontOfSize_(12),
        ))
        y_cursor -= 24

        # Token-usage chart — anchored at fixed y so per-model row count
        # can't push it into the toggles below. Sits between the cost
        # summary and the footer toolbar.
        chart_h = 100
        chart_y = 144  # 4 px above the debug-button row (top=138)
        chart_frame = NSMakeRect(
            margin_x, chart_y, CONTENT_WIDTH - 32, chart_h,
        )
        buckets = self._load_buckets()
        now_minute = self._now_minute_of_day()
        self._chart_view = (
            _TokenChartView.alloc()
            .initWithFrame_buckets_bucketMinutes_nowMinute_(
                chart_frame, buckets, CHART_BUCKET_MINUTES, now_minute,
            )
        )
        view.addSubview_(self._chart_view)
        # Live refresh while the pane stays visible.
        refresh_handler = make_handler(self._refresh_chart)
        self._handlers.append(refresh_handler)
        self._chart_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                CHART_REFRESH_S, refresh_handler, b"fire:", None, True,
            )
        )

        # Footer toolbar — toggles + buttons. All anchored bottom-up so
        # the chart above never overlaps. Restart/Quit at y=12, three
        # switches stacked above at y=50/72/94 (h=18, 22 px stride).
        sw_companion = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, 50, CONTENT_WIDTH - 32, 18)
        )
        sw_companion.setButtonType_(NSButtonTypeSwitch)
        sw_companion.setTitle_("Show Pokémon as desktop companion")
        sw_companion.setState_(
            1 if getattr(self.popover._app, "_companion_mode", False) else 0
        )
        sw_companion.setTarget_(self.popover)
        sw_companion.setAction_(b"toggleCompanion:")
        view.addSubview_(sw_companion)

        sw_weather = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, 72, CONTENT_WIDTH - 32, 18)
        )
        sw_weather.setButtonType_(NSButtonTypeSwitch)
        sw_weather.setTitle_("Use weather data for spawns")
        sw_weather.setState_(1 if self.popover._app._use_weather else 0)
        sw_weather.setTarget_(self.popover)
        sw_weather.setAction_(b"toggleWeather:")
        view.addSubview_(sw_weather)

        sw_pokemon = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, 94, CONTENT_WIDTH - 32, 18)
        )
        sw_pokemon.setButtonType_(NSButtonTypeSwitch)
        sw_pokemon.setTitle_("Show Pokémon in menubar")
        sw_pokemon.setState_(1 if self.popover._app._show_pokemon else 0)
        sw_pokemon.setTarget_(self.popover)
        sw_pokemon.setAction_(b"toggleMenubarPokemon:")
        view.addSubview_(sw_pokemon)

        # Buttons row: Restart Proxy + Quit, anchored to bottom.
        btn_y = 12
        restart = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, btn_y, 160, 24)
        )
        restart.setTitle_("Restart proxy")
        restart.setBezelStyle_(1)
        restart.setTarget_(self.popover)
        restart.setAction_(b"restartProxy:")
        view.addSubview_(restart)

        quit_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(CONTENT_WIDTH - margin_x - 100, btn_y, 100, 24)
        )
        quit_btn.setTitle_("Quit")
        quit_btn.setBezelStyle_(1)
        quit_btn.setTarget_(self.popover)
        quit_btn.setAction_(b"quitApp:")
        view.addSubview_(quit_btn)

        # Debug-button row — sits between the toggle switches and the
        # chart. Force-spawning bypasses probability + cooldown but
        # respects pending-guard, so a second click while one is queued
        # navigates to the existing pending entity instead of stacking.
        debug_y = 116
        half_w = (CONTENT_WIDTH - margin_x * 2 - 8) // 2

        spawn_trainer_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, debug_y, half_w, 22)
        )
        spawn_trainer_btn.setTitle_("🐛 Spawn trainer")
        spawn_trainer_btn.setBezelStyle_(1)

        def _spawn_trainer(_s):
            try:
                from tokenmon import trainer as trainer_mod
                from tokenmon.popover.widgets import PANE_ENCOUNTER
                trainer_mod.maybe_spawn(force=True)
                self.popover._show_pane(PANE_ENCOUNTER)
            except Exception:
                log.exception("trainer force-spawn failed")

        h_t = make_handler(_spawn_trainer)
        self._handlers.append(h_t)
        spawn_trainer_btn.setTarget_(h_t)
        spawn_trainer_btn.setAction_(b"fire:")
        view.addSubview_(spawn_trainer_btn)

        spawn_wild_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x + half_w + 8, debug_y, half_w, 22)
        )
        spawn_wild_btn.setTitle_("🐛 Spawn wild")
        spawn_wild_btn.setBezelStyle_(1)

        def _spawn_wild(_s):
            try:
                from tokenmon import encounter as enc_mod
                from tokenmon.popover.widgets import PANE_ENCOUNTER
                enc_mod.maybe_spawn(force=True)
                self.popover._show_pane(PANE_ENCOUNTER)
            except Exception:
                log.exception("wild encounter force-spawn failed")

        h_w = make_handler(_spawn_wild)
        self._handlers.append(h_w)
        spawn_wild_btn.setTarget_(h_w)
        spawn_wild_btn.setAction_(b"fire:")
        view.addSubview_(spawn_wild_btn)

        return view

    def _load_buckets(self) -> list[int]:
        try:
            return query_today_token_buckets(
                TZ, bucket_minutes=CHART_BUCKET_MINUTES,
            )
        except Exception:
            log.exception("query_today_token_buckets failed")
            return [0] * (1440 // CHART_BUCKET_MINUTES)

    def _now_minute_of_day(self) -> int:
        now = datetime.now(ZoneInfo(TZ))
        return now.hour * 60 + now.minute

    def _refresh_chart(self, _t=None) -> None:
        if self._chart_view is None:
            return
        try:
            buckets = self._load_buckets()
            self._chart_view.setBuckets_nowMinute_(
                buckets, self._now_minute_of_day(),
            )
        except Exception:
            log.exception("chart refresh failed")

    def teardown(self) -> None:
        super().teardown()
        if self._chart_timer is not None:
            try:
                self._chart_timer.invalidate()
            except Exception:
                pass
            self._chart_timer = None
        self._chart_view = None

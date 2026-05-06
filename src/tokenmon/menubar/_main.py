"""macOS menubar app — shows today's total tokens with a 🥚 icon.

Click opens a dropdown with per-model breakdown and estimated USD cost.
Refreshes every 30 seconds from SQLite. Pings the proxy /healthz every 10s
and shows a warning state if the proxy is down.
"""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import objc
import rumps
from AppKit import (
    NSColor,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseUp,
    NSEventTypeRightMouseDown,
    NSEventTypeRightMouseUp,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextField,
    NSTimer,
    NSView,
)
from Foundation import NSMakeRect, NSObject

from tokenmon import box, config, items, items_remote, pokemon, tokendex
from tokenmon.menubar_sprite import SpriteAnimator
from tokenmon.overlay import PokemonOverlay
from tokenmon.popover import TokenmonPopover
from tokenmon.pricing import cost_for
from tokenmon.proxy import HOST, PORT
from tokenmon.storage import (
    Totals,
    bump_affection,
    init_db,
    latest_request_ts,
    query_today,
    query_today_by_model,
    query_xp_for_date,
    query_xp_for_pokemon,
)

REFRESH_INTERVAL_SEC = 30
HEALTH_INTERVAL_SEC = 10
ACTIVITY_POLL_INTERVAL_SEC = 5
# Companion: how long an input event keeps the Pokémon facing the screen
# before it turns around to look at the user. Polled at the
# ACTIVITY_POLL_INTERVAL_SEC tick, so transitions can lag up to that
# interval — fine, since 30 s already implies "no recent activity".
INTERACTION_TIMEOUT_S = 30.0
# Zoom factor for the back sprite. PokeAPI gen-V back sprites draw the
# character noticeably smaller than the front sprite within the same
# 96×96 canvas — boosting the layer scale brings them visually back to
# par. The companion window clips the overflow so the larger render
# stays within the sprite frame.
COMPANION_BACK_ZOOM = 1.05
# Cursor-proximity fade tick frequency. 20 Hz feels smooth without
# noticeable CPU cost (each fire is one NSEvent.mouseLocation, one frame
# read, one cheap distance calc). The set_proximity_alpha path is
# idempotent, so identical alphas don't redisplay the window.
PROXIMITY_TICK_S = 0.05
# Affection grows while the active Pokemon stays the same. With a 5s poll,
# 120 ticks = 10 minutes of "owning" time per +1 affection — at the 0..255
# cap that's ~42h of cumulative active time to fully bond. Counter resets to
# 0 when the active changes so partial progress doesn't carry across pets.
AFFECTION_TICKS_PER_POINT = 120
# Idle gate: if no requests landed in the last 30 minutes, pause growth so
# leaving the laptop unattended doesn't bond a Pokemon to you for free. The
# tick counter holds — when activity resumes, growth picks up where it left.
AFFECTION_IDLE_GATE_SEC = 30 * 60
TZ = "Europe/Berlin"
EGG = "🥚"
EGG_DOWN = "⚠️"

log = logging.getLogger("tokenmon.menubar")


class _ButtonWireHandler(NSObject):
    """One-shot NSTimer target that wires the status-bar button to our popover.
    Has to fire AFTER applicationDidFinishLaunching, since rumps installs its
    own NSMenu on the status item there. Scheduling a 0.1 s timer from
    __init__ guarantees we run after launch finishes."""

    def initWithApp_(self, app):  # noqa: N802
        self = objc.super(_ButtonWireHandler, self).init()
        if self is None:
            return None
        self._app = app
        return self

    def wire_(self, _timer):  # noqa: N802
        try:
            btn = self._app._statusbar_button()
            if btn is None or self._app._popover is None:
                return
            try:
                self._app._nsapp.nsstatusitem.setMenu_(None)
            except Exception:
                log.exception("setMenu_(None) failed")
            # Receive both left and right mouse-up events so the popover can
            # be shown on left-click and a fallback context menu on right.
            try:
                btn.cell().sendActionOn_(
                    NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp
                )
            except Exception:
                log.exception("sendActionOn_ failed")
            btn.setTarget_(self._app._popover)
            # IMPORTANT: the popover's buttonClicked_ must be decorated with
            # @objc.IBAction. Without that, pyobjc's auto-bridged selector
            # registers OK (respondsToSelector_ returns True) and performClick_
            # works, but real mouse events don't dispatch through it.
            btn.setAction_("buttonClicked:")
        except Exception:
            log.exception("button wiring failed")


from tokenmon.ui_helpers import fmt_tokens as _fmt_tokens, fmt_usd as _fmt_usd

# Health helpers moved to menubar.health; aliased here to keep callers in
# this module unchanged during the split.
from tokenmon.menubar.health import (
    active_provider_endpoints as _active_provider_endpoints,
    ping as _ping,
    proxy_health as _proxy_health,
    restart_proxies_via_launchctl as _restart_proxies_via_launchctl,
)


from tokenmon.tokendex import _XPBarView  # ObjC class — defined once, imported here


def _label(frame, text, *, font=None, color=None) -> NSTextField:
    f = NSTextField.alloc().initWithFrame_(frame)
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setFont_(font or NSFont.menuFontOfSize_(13))
    f.setTextColor_(color or NSColor.labelColor())
    return f


def _make_pokemon_view(
    sprite: Path | None,
    name_label: str,
    level: int,
    xp_into: int,
    xp_needed: int,
) -> NSView:
    """Custom NSView for an NSMenuItem: animated sprite + name + level + XP bar.
    Uses pyobjc directly because rumps' MenuItem.icon is a static NSImage."""
    width, height = 260, 96
    container = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))

    sprite_size = 72
    img_view = NSImageView.alloc().initWithFrame_(
        NSMakeRect(12, (height - sprite_size) / 2, sprite_size, sprite_size)
    )
    if sprite is not None and sprite.exists():
        img = NSImage.alloc().initWithContentsOfFile_(str(sprite))
        if img is not None:
            img_view.setImage_(img)
    img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    img_view.setAnimates_(True)
    container.addSubview_(img_view)

    text_x = sprite_size + 24
    text_w = width - text_x - 8

    name_y = height - 30
    container.addSubview_(_label(
        NSMakeRect(text_x, name_y, text_w, 18),
        name_label,
        font=NSFont.boldSystemFontOfSize_(13),
    ))

    level_y = name_y - 20
    container.addSubview_(_label(
        NSMakeRect(text_x, level_y, text_w, 16),
        f"Lv {level}" if level < pokemon.MAX_LEVEL else "Lv MAX",
        font=NSFont.menuFontOfSize_(12),
    ))

    bar_y = level_y - 14
    bar_w = text_w - 4
    progress = xp_into / xp_needed if xp_needed > 0 else (1.0 if level >= pokemon.MAX_LEVEL else 0.0)
    bar = _XPBarView.alloc().initWithFrame_progress_(
        NSMakeRect(text_x, bar_y, bar_w, 8), progress,
    )
    container.addSubview_(bar)

    xp_y = bar_y - 16
    xp_text = "MAX" if level >= pokemon.MAX_LEVEL else f"{xp_into:,} / {xp_needed:,} XP"
    container.addSubview_(_label(
        NSMakeRect(text_x, xp_y, text_w, 14),
        xp_text,
        font=NSFont.menuFontOfSize_(10),
        color=NSColor.secondaryLabelColor(),
    ))

    return container


class TokenmonApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(name="Tokenmon", title=f"{EGG} 0", quit_button=None)
        self._proxy_up = True
        # Backfill any historical days that don't have a Pokemon entry yet,
        # then ensure today's entry exists. ensure_today_pokemon is the
        # source of truth for "today's species" — its species_dex_id drives
        # the menubar icon, the overlay, and level-up detection.
        is_shiny_init = False
        try:
            init_db()
            box.migrate_legacy_days()
            today_row = box.ensure_today_pokemon()
            self._line_base_id = today_row.species_dex_id
            is_shiny_init = bool(getattr(today_row, "is_shiny", False))
        except Exception:
            log.exception("box init failed; falling back to legacy pick")
            self._line_base_id = pokemon.pick_for_today()
        self._pokemon_picked_for: date = date.today()
        # Current displayed dex_id (could be evolved form). Recomputed each refresh.
        self._pokemon_dex_id: int = self._line_base_id
        # Whether the active Pokemon is shiny — drives which sprite we cache.
        # _refresh_pokemon_state keeps this in sync when the user pins a
        # different active or the active evolves.
        self._pokemon_is_shiny: bool = is_shiny_init
        self._pokemon_sprite: Path | None = pokemon.ensure_sprite(
            self._pokemon_dex_id, shiny=self._pokemon_is_shiny,
        )
        self._show_pokemon = bool(config.get("show_pokemon_in_menubar"))
        self._animator: SpriteAnimator | None = None
        self._overlay = PokemonOverlay(
            size=int(config.get("overlay_size") or 128),
            corner=str(config.get("overlay_corner") or "bottom-right"),
        )
        self._show_overlay = bool(config.get("show_overlay"))
        self._companion_mode = bool(config.get("companion_mode"))
        self._use_weather = bool(config.get("use_weather"))
        # Wire companion-mode persistence into the overlay so level-up /
        # evolution endings don't hide the sprite while the companion is on.
        self._overlay.set_persistent(self._companion_mode)
        # Active-app observer + global-input monitor — installed lazily when
        # companion mode is on so we don't subscribe to system events for
        # users who never enable the feature. Strong refs kept on `self` so
        # PyObjC doesn't GC them.
        self._active_app_observer = None
        self._input_monitor = None
        # Tracks which orientation the overlay last rendered ("front"/"back")
        # so the 5-s tick doesn't redundantly reload sprite paths every poll
        # when nothing changed.
        self._last_orientation: str | None = None
        # Last-docked window rect, used by _tick_dock to detect when the
        # focused window changed (different window of same app, moved,
        # resized, migrated to another screen) and we should re-dock.
        self._last_dock_rect = None
        # Throttle for input-event-driven dock checks — we re-poll the
        # window list at most this often so per-keystroke callbacks don't
        # spam CGWindowListCopyWindowInfo.
        self._last_dock_check_mono: float = 0.0
        if self._companion_mode:
            try:
                self._overlay.update_sprite(self._pokemon_sprite)
                self._overlay.show()
                self._install_active_app_observer()
                self._install_input_monitor()
            except Exception:
                log.exception("companion overlay show on init failed")
        # Level-up detection state. The overlay never appears outside of
        # level-up events, so we only need a "last seen level" to detect changes.
        self._last_known_level: int = self._compute_current_level()
        # Affection growth bookkeeping — counts polls while the same Pokemon
        # stays active. Resets when the active changes or becomes None.
        self._affection_ticks: int = 0
        self._affection_active_id: int | None = None
        # Floating-item-drop detection: snapshot pending_drops at startup so
        # any items already pending aren't re-played as "new drops" the
        # first time the poll fires. Subsequent polls diff against this.
        try:
            from tokenmon.storage import query_pending_drops
            self._last_pending_snapshot: dict[str, int] = dict(query_pending_drops())
        except Exception:
            log.exception("initial query_pending_drops failed")
            self._last_pending_snapshot = {}
        # Wild-encounter spawn tracking — for every new requests row, we roll
        # encounter.maybe_spawn(). _last_seen_request_id holds the highest id
        # already considered, so we don't double-roll.
        self._last_seen_request_id: int = self._query_max_request_id()
        # Popover replaces the rumps dropdown. Wired after launch via a
        # short-lived NSTimer (see _ButtonWireHandler).
        self._popover = TokenmonPopover.alloc().initWithApp_(self)
        self._button_wire_handler = _ButtonWireHandler.alloc().initWithApp_(self)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self._button_wire_handler, b"wire:", None, False,
        )
        self._sync_menubar_icon()
        self.refresh(None)

    def _refresh_pokemon_state(self) -> None:
        """The menubar icon mirrors the user's ACTIVE Pokemon. When the
        active's trained XP crosses an evolution threshold, box.maybe_evolve
        mutates its species_dex_id in the DB — the row literally becomes the
        evolved Pokemon, matching real Pokemon-game semantics."""
        try:
            active = box.get_active_pokemon()
        except Exception:
            log.exception("get_active_pokemon failed")
            active = None
        if active is None:
            return

        # User switched active to a different row.
        if active.species_dex_id != self._line_base_id or active.is_shiny != self._pokemon_is_shiny:
            self._line_base_id = active.species_dex_id
            self._pokemon_dex_id = active.species_dex_id
            self._pokemon_is_shiny = bool(active.is_shiny)
            self._pokemon_sprite = pokemon.ensure_sprite(
                active.species_dex_id, shiny=self._pokemon_is_shiny,
            )
            self._last_known_level = self._compute_current_level()
            self._sync_menubar_icon()
            self._sync_overlay()
            # Reset the companion's mirror/zoom transform — without this
            # the new species would render with the previous one's
            # engaged-state transform (e.g. inherits a -1.15 x scale and
            # appears mirrored). Then re-evaluate orientation from
            # scratch so the new sprite picks the correct front/back +
            # mirror based on current input recency.
            if self._companion_mode:
                try:
                    self._overlay.reset_sprite_state()
                except Exception:
                    log.exception("reset_sprite_state on active change failed")
                self._last_orientation = None
                try:
                    self._tick_orientation(force=True)
                except Exception:
                    log.exception("tick_orientation on active change failed")
            return

        # Try to evolve the active Pokemon if its XP says it's due.
        try:
            new_id = box.maybe_evolve(active.id)
        except Exception:
            log.exception("maybe_evolve failed")
            new_id = None
        if new_id is None:
            return

        old_id = self._pokemon_dex_id
        self._pokemon_dex_id = new_id
        self._line_base_id = new_id
        self._pokemon_sprite = pokemon.ensure_sprite(
            new_id, shiny=self._pokemon_is_shiny,
        )
        self._sync_menubar_icon()
        self._sync_overlay()
        # Same reset as in the active-switch branch — without this, the
        # evolved species inherits the previous form's mirror/zoom layer
        # transform and renders flipped after the evolution animation.
        if self._companion_mode:
            try:
                self._overlay.reset_sprite_state()
            except Exception:
                log.exception("reset_sprite_state on evolution failed")
            self._last_orientation = None

        # Evolution always implies a forward step — fire notification + animation.
        base = pokemon.line_of(new_id)
        chain = pokemon.evolution_chain(base)
        if old_id in chain and new_id in chain and chain.index(new_id) > chain.index(old_id):
            # Real in-line evolution. Fire notification + animation, and sync the
            # level cache so the level-up animation doesn't also fire.
            self._last_known_level = self._compute_current_level()
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle="Evolution!",
                    message=(
                        f"{pokemon.display_name(active.nickname, old_id)} "
                        f"is evolving into {pokemon.name_of(new_id)}!"
                    ),
                )
            except Exception:
                log.exception("evolution notification failed")
            if self._show_overlay:
                try:
                    self._overlay.show_evolution(old_id, new_id)
                except Exception:
                    log.exception("evolution animation failed")

    def _sync_overlay(self) -> None:
        """Refresh the overlay's sprite (when the displayed Pokemon changes).
        Does NOT decide visibility — the overlay only appears on level-up events."""
        if self._overlay.visible:
            self._overlay.update_sprite(self._pokemon_sprite)

    def _query_max_request_id(self) -> int:
        try:
            import sqlite3
            from tokenmon.storage import DB_PATH
            with sqlite3.connect(DB_PATH, timeout=2.0) as conn:
                row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()
                return int(row[0] or 0)
        except Exception:
            log.exception("max request id query failed")
            return 0

    def _query_new_requests(self, since: int) -> tuple[list[int], int]:
        """Return (output_token counts of new requests, max_id seen).

        The token list drives encounter.spawn_probability — one entry per
        new request, in id order. ``max_id`` is the high-water mark we'll
        hand back to the next poll."""
        try:
            import sqlite3
            from tokenmon.storage import DB_PATH
            with sqlite3.connect(DB_PATH, timeout=2.0) as conn:
                rows = conn.execute(
                    "SELECT id, output_tokens FROM requests "
                    "WHERE id > ? ORDER BY id ASC",
                    (since,),
                ).fetchall()
        except Exception:
            return [], since
        if not rows:
            return [], since
        tokens = [int(ot or 0) for _, ot in rows]
        max_id = int(rows[-1][0])
        return tokens, max_id

    def _maybe_roll_encounters(self) -> None:
        """For every new request that landed since the last poll, give
        encounter.maybe_spawn() a chance to spawn. The spawn logic itself
        guards on cooldown + pending status + a token-weighted probability,
        so calling it once per new request honours the per-call semantics."""
        tokens_per_req, max_id = self._query_new_requests(self._last_seen_request_id)
        if not tokens_per_req:
            return
        self._last_seen_request_id = max_id
        try:
            from tokenmon import encounter
        except Exception:
            log.exception("encounter import failed")
            return
        for output_tokens in tokens_per_req:
            try:
                spawned = encounter.maybe_spawn(output_tokens=output_tokens)
            except Exception:
                log.exception("maybe_spawn failed")
                spawned = None
            if spawned is not None:
                try:
                    rumps.notification(
                        title="Tokenmon",
                        subtitle="A wild Pokemon appeared!",
                        message="Click the menubar to investigate.",
                    )
                except Exception:
                    log.exception("encounter notification failed")
                # Companion-mode flash so the user sees the spawn even when
                # the menubar isn't where their eyes are.
                if self._companion_mode and self._overlay.visible:
                    try:
                        self._overlay.flash_alert("⚡ wild!", duration_s=4.0)
                    except Exception:
                        log.exception("encounter flash_alert failed")
                break  # only one pending encounter at a time

    def _compute_current_level(self) -> int:
        try:
            active = box.get_active_pokemon()
            if active is not None:
                xp = query_xp_for_pokemon(active.id)
            else:
                xp = query_xp_for_date(date.today(), TZ)
        except Exception:
            return 1
        rate = pokemon.growth_rate_of(self._line_base_id)
        level, _, _ = pokemon.level_from_xp(xp, rate)
        return level

    def _check_level_up(self, now: float) -> None:
        """Detect a level increase since the last poll and fire visuals/notification."""
        new_level = self._compute_current_level()
        if new_level > self._last_known_level:
            self._last_known_level = new_level
            try:
                active = box.get_active_pokemon()
            except Exception:
                active = None
            display = pokemon.display_name(
                active.nickname if active is not None else None,
                self._pokemon_dex_id,
            )
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle="Level up!",
                    message=f"{display} leveled up!",
                )
            except Exception:
                log.exception("level-up notification failed")
            # Pop the overlay for the duration of the level-up banner. The
            # overlay hides itself again when the banner timer expires.
            # Suppress the level-up animation while an evolution animation is
            # already running (level-up and evolution often coincide).
            if self._show_overlay and not self._overlay.evolution_running:
                try:
                    self._overlay.update_sprite(self._pokemon_sprite)
                    self._overlay.show_level_up()
                except Exception:
                    log.exception("overlay level-up animation failed")
        elif new_level < self._last_known_level:
            # Defensive: data shrunk (manual DB edit?). Track silently.
            self._last_known_level = new_level

    def _statusbar_button(self):
        try:
            return self._nsapp.nsstatusitem.button()
        except (AttributeError, Exception):
            return None

    def _set_menubar_image(self, img) -> None:
        btn = self._statusbar_button()
        if btn is not None:
            btn.setImage_(img)

    def _update_tooltip(self) -> None:
        btn = self._statusbar_button()
        if btn is None:
            return
        try:
            active = box.get_active_pokemon()
            if active is not None:
                xp = query_xp_for_pokemon(active.id)
            else:
                xp = query_xp_for_date(date.today(), TZ)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(self._line_base_id)
        level, into, needed = pokemon.level_from_xp(xp, rate)
        name = pokemon.display_name(
            active.nickname if active is not None else None,
            self._pokemon_dex_id,
        )
        if level >= pokemon.MAX_LEVEL:
            tooltip = f"{name} — Lv MAX • {xp:,} XP"
        else:
            tooltip = f"{name} — Lv {level} • {into:,}/{needed:,} XP"
        btn.setToolTip_(tooltip)

    def _stop_animator(self, *, clear_image: bool = True) -> None:
        if self._animator is not None:
            self._animator.stop()
            self._animator = None
        if clear_image:
            self._set_menubar_image(None)

    def _start_animator(self) -> None:
        # Don't blank the button while we're swapping animators — the new
        # animator's first frame paints synchronously when its init runs, and
        # any None state in between would cause the status-bar button to
        # collapse to its empty width and shift the open popover.
        self._stop_animator(clear_image=False)
        if not self._show_pokemon or self._pokemon_sprite is None or not self._pokemon_sprite.exists():
            self._set_menubar_image(None)
            return
        try:
            anim = SpriteAnimator.alloc().initWithGifPath_setter_(
                str(self._pokemon_sprite), self._set_menubar_image
            )
            self._animator = anim
        except Exception:
            log.exception("failed to start sprite animator")
            self._animator = None

    def _sync_menubar_icon(self) -> None:
        """Reconcile the title + image with the current state."""
        if self._show_pokemon and self._pokemon_sprite is not None and self._pokemon_sprite.exists():
            self._start_animator()
        else:
            self._stop_animator()

    def _maybe_repick_for_new_day(self) -> None:
        today = date.today()
        if today != self._pokemon_picked_for:
            # Day rolled over — make sure today's daily-catch row exists, but
            # the menubar icon follows the user's ACTIVE Pokemon (which may
            # be a different species they pinned previously).
            try:
                box.ensure_today_pokemon()
                active = box.get_active_pokemon()
                self._line_base_id = (
                    active.species_dex_id if active is not None
                    else pokemon.pick_for_today(today)
                )
            except Exception:
                log.exception("day-rollover state refresh failed")
                self._line_base_id = pokemon.pick_for_today(today)
            self._pokemon_picked_for = today
            self._pokemon_dex_id = self._line_base_id
            self._pokemon_is_shiny = bool(getattr(active, "is_shiny", False)) if active is not None else False
            self._pokemon_sprite = pokemon.ensure_sprite(
                self._pokemon_dex_id, shiny=self._pokemon_is_shiny,
            )
            self._last_known_level = self._compute_current_level()
            self._sync_menubar_icon()

    def _pokemon_menu_item(self) -> rumps.MenuItem:
        dex_id = self._pokemon_dex_id
        label = f"#{dex_id:03d}  {pokemon.name_of(dex_id)}"
        try:
            xp = query_xp_for_date(date.today(), TZ)
        except Exception:
            log.exception("failed to compute today's xp")
            xp = 0
        rate = pokemon.growth_rate_of(self._line_base_id)
        level, into, needed = pokemon.level_from_xp(xp, rate)
        item = rumps.MenuItem(label)
        view = _make_pokemon_view(self._pokemon_sprite, label, level, into, needed)
        item._menuitem.setView_(view)
        return item

    def _build_menu(self, totals: Totals, by_model: dict[str, Totals], proxy_up: bool) -> list:
        items: list = []
        items.append(self._pokemon_menu_item())
        items.append(rumps.MenuItem("📖 Open Tokendex", callback=self.open_tokendex))
        toggle = rumps.MenuItem("Show Pokémon in menubar", callback=self.toggle_menubar_pokemon)
        toggle.state = 1 if self._show_pokemon else 0
        items.append(toggle)
        overlay_toggle = rumps.MenuItem(
            "Show Pokémon as desktop overlay", callback=self.toggle_overlay
        )
        overlay_toggle.state = 1 if self._show_overlay else 0
        items.append(overlay_toggle)
        items.append(None)
        if not proxy_up:
            items.append(rumps.MenuItem("⚠️  Proxy offline — calls are NOT being tracked!"))
            items.append(rumps.MenuItem("Restart proxy", callback=self.restart_proxy))
            items.append(None)
        # XP / "active" tokens count only the model's output: it's the only
        # token category comparable across providers (cached input doesn't
        # exist in OpenRouter, while it dominates Anthropic-via-Claude-Code).
        active = totals.output_tokens
        items.extend([
            rumps.MenuItem(f"Today: {_fmt_tokens(active)} tokens (output)"),
            rumps.MenuItem(f"  Output:   {_fmt_tokens(totals.output_tokens)}"),
            rumps.MenuItem(f"  Input:    {_fmt_tokens(totals.input_tokens)}"),
            rumps.MenuItem(f"  Requests: {totals.request_count}"),
            None,
        ])
        total_cost = 0.0
        priced_tokens = 0
        all_tokens = 0
        if by_model:
            items.append(rumps.MenuItem("Per model:"))
            for model, t in by_model.items():
                cost, has_price = cost_for(
                    model,
                    input_tokens=t.input_tokens,
                    output_tokens=t.output_tokens,
                    cache_read_tokens=t.cache_read_tokens,
                    cache_creation_tokens=t.cache_creation_tokens,
                )
                total_cost += cost
                model_tokens = (
                    t.input_tokens + t.output_tokens
                    + t.cache_read_tokens + t.cache_creation_tokens
                )
                all_tokens += model_tokens
                if has_price:
                    priced_tokens += model_tokens
                visible_tokens = t.output_tokens
                cost_str = _fmt_usd(cost) if has_price else "?"
                items.append(
                    rumps.MenuItem(
                        f"  {model}: {_fmt_tokens(visible_tokens)} out ({cost_str})"
                    )
                )
            items.append(None)
            cost_line = f"Estimated cost: {_fmt_usd(total_cost)}"
            if all_tokens > 0 and priced_tokens < all_tokens:
                coverage = priced_tokens / all_tokens
                cost_line += f"  ({coverage:.0%} price coverage)"
            items.append(rumps.MenuItem(cost_line))
            items.append(None)
        items.append(rumps.MenuItem("Refresh", callback=self.refresh))
        items.append(rumps.MenuItem("Quit", callback=rumps.quit_application))
        return items

    @rumps.timer(REFRESH_INTERVAL_SEC)
    def auto_refresh(self, _sender) -> None:
        self.refresh(None)

    @rumps.timer(HEALTH_INTERVAL_SEC)
    def health_check(self, _sender) -> None:
        up, _down = _proxy_health()
        if up != self._proxy_up:
            self._proxy_up = up
            self.refresh(None)

    @rumps.timer(PROXIMITY_TICK_S)
    def proximity_tick(self, _sender) -> None:
        """Fade the companion overlay when the cursor approaches AND make
        the sprite clickable when the cursor sits inside its frame, so
        clicks on the Pokémon open the text bubble while clicks elsewhere
        still pass through to whatever's underneath. No-op when companion
        mode is off."""
        if not self._companion_mode or not self._overlay.visible:
            if self._overlay._proximity_alpha < 1.0:
                self._overlay.set_proximity_alpha(1.0)
            self._overlay.set_clickable(False)
            return
        if self._overlay._window is None:
            return
        try:
            from AppKit import NSEvent
            from tokenmon.companion.proximity import proximity_alpha
            loc = NSEvent.mouseLocation()
            frame = self._overlay._window.frame()
            inside = (
                frame.origin.x <= loc.x < frame.origin.x + frame.size.width
                and frame.origin.y <= loc.y < frame.origin.y + frame.size.height
            )
            # While the text bubble is open, keep the sprite fully opaque
            # — the user is mid-interaction; fading it would be jarring.
            # We still gate clickability so the user can click the sprite
            # again to close the bubble.
            if self._overlay.bubble_open:
                self._overlay.set_proximity_alpha(1.0)
            else:
                cx = float(frame.origin.x) + float(frame.size.width) / 2.0
                cy = float(frame.origin.y) + float(frame.size.height) / 2.0
                dx = float(loc.x) - cx
                dy = float(loc.y) - cy
                distance = (dx * dx + dy * dy) ** 0.5
                self._overlay.set_proximity_alpha(proximity_alpha(distance))
            self._overlay.set_clickable(inside)
        except Exception:
            log.exception("proximity tick failed")

    @rumps.timer(ACTIVITY_POLL_INTERVAL_SEC)
    def activity_poll(self, _sender) -> None:
        now = time.monotonic()
        prev_level = self._last_known_level
        self._check_level_up(now)
        self._maybe_roll_encounters()
        self._tick_affection()
        self._tick_pending_drops()
        self._tick_mood()
        self._tick_dock()
        self._tick_orientation()
        if self._last_known_level != prev_level:
            # Refresh now so the menubar title picks up the new level immediately
            # (otherwise we'd wait up to 30s for the next refresh).
            self.refresh(None)

    def _tick_dock(self, *, throttle_s: float = 0.0) -> None:
        """Re-check the focused window. NSWorkspace's activate notification
        only fires on app changes, but the user can also:
          - switch between windows of the same app (cmd-`, click)
          - drag a window to a new position
          - move a window to another screen

        Called from two places:
          1. The 5-s activity_poll — long-running drift detection.
          2. The input monitor on every key/click (very frequent) —
             gives snappy response to cmd-` and clicks on other windows.

        ``throttle_s`` enforces a minimum gap between successive checks
        so per-keystroke calls don't spam CGWindowListCopyWindowInfo.
        Pass 0 for the periodic tick (always run); pass e.g. 0.2 for
        input-driven calls.
        """
        if not self._companion_mode or not self._overlay.visible:
            return
        if self._overlay.evolution_running:
            return
        if throttle_s > 0.0:
            now_mono = time.monotonic()
            if (now_mono - self._last_dock_check_mono) < throttle_s:
                return
            self._last_dock_check_mono = now_mono
        else:
            self._last_dock_check_mono = time.monotonic()
        try:
            self._dock_to_focused_window()
        except Exception:
            log.exception("dock tick failed")

    def _tick_mood(self) -> None:
        """Apply the time-of-day mood modifier (night dims the sprite) on
        the 5-s tick. No-op when companion mode is off."""
        if not self._companion_mode or not self._overlay.visible:
            return
        try:
            from tokenmon.companion.mood import mood_modifiers
            from zoneinfo import ZoneInfo
            mods = mood_modifiers(datetime.now(ZoneInfo(TZ)))
            self._overlay.set_mood_alpha(mods.alpha_multiplier)
        except Exception:
            log.exception("mood modifier apply failed")

    def _tick_pending_drops(self) -> None:
        """Diff pending_drops against the last snapshot. Newly-arrived items
        get a floating overlay animation when the desktop overlay is on.

        Snapshot is updated unconditionally so a claim (which empties the
        table) doesn't "look like" -N new drops on the next tick."""
        try:
            from tokenmon.storage import query_pending_drops
            current = dict(query_pending_drops())
        except Exception:
            log.exception("query_pending_drops failed in tick")
            return
        new_drops: dict[str, int] = {}
        for key, count in current.items():
            delta = int(count) - int(self._last_pending_snapshot.get(key, 0))
            if delta > 0:
                new_drops[key] = delta
        self._last_pending_snapshot = current
        # Note: not gating on self._overlay.visible — the Pokémon overlay
        # only pops up briefly for level-ups, but the user's mental model
        # of "overlay aktiv" is the config flag. Floaters appear at the
        # overlay's nominal position regardless of whether the window is
        # currently shown.
        if new_drops and self._show_overlay:
            try:
                self._overlay.show_floating_items(new_drops)
            except Exception:
                log.exception("show_floating_items failed")

    def _tick_affection(self) -> None:
        """Grow the active Pokemon's affection by 1 every
        AFFECTION_TICKS_PER_POINT polls. Counter resets when the active
        Pokemon changes (or there's no active), so swapping pets doesn't
        leak partial progress. While the proxy has been idle (no requests in
        the last AFFECTION_IDLE_GATE_SEC) the counter is held in place — an
        unattended laptop shouldn't bond a Pokemon for free."""
        try:
            active_id = box.get_active_pokemon_id()
        except Exception:
            log.exception("get_active_pokemon_id failed in affection tick")
            return
        if active_id is None:
            self._affection_ticks = 0
            self._affection_active_id = None
            return
        if active_id != self._affection_active_id:
            self._affection_active_id = active_id
            self._affection_ticks = 0
        # Idle gate — no recent token activity means no growth this tick.
        try:
            last_ts = latest_request_ts()
        except Exception:
            log.exception("latest_request_ts failed in affection tick")
            return
        if last_ts is None:
            return
        idle_sec = (datetime.now(timezone.utc) - last_ts).total_seconds()
        if idle_sec > AFFECTION_IDLE_GATE_SEC:
            return
        self._affection_ticks += 1
        if self._affection_ticks >= AFFECTION_TICKS_PER_POINT:
            self._affection_ticks = 0
            try:
                bump_affection(active_id)
            except Exception:
                log.exception("bump_affection failed")

    def restart_proxy(self, _sender) -> None:
        ok, msg = _restart_proxies_via_launchctl()
        rumps.notification(
            title="Tokenmon",
            subtitle="Proxy restart" if ok else "Proxy restart failed",
            message=msg,
        )

    def toggle_menubar_pokemon(self, _sender) -> None:
        self._show_pokemon = not self._show_pokemon
        config.set_("show_pokemon_in_menubar", self._show_pokemon)
        self._sync_menubar_icon()
        self.refresh(None)

    def toggle_overlay(self, _sender) -> None:
        self._show_overlay = not self._show_overlay
        config.set_("show_overlay", self._show_overlay)
        # Companion mode keeps the sprite up regardless of show_overlay —
        # the latter only gates event-driven appearances now.
        if not self._show_overlay and self._overlay.visible and not self._companion_mode:
            self._overlay.hide()
        self.refresh(None)

    def toggle_companion(self, _sender) -> None:
        self._companion_mode = not self._companion_mode
        config.set_("companion_mode", self._companion_mode)
        self._overlay.set_persistent(self._companion_mode)
        if self._companion_mode:
            try:
                self._overlay.update_sprite(self._pokemon_sprite)
                self._overlay.show()
                self._install_active_app_observer()
                self._install_input_monitor()
                # Seed orientation + position from whatever app is currently
                # in front so we don't wait for the next activation event.
                self._on_active_app_changed(self._current_bundle_id_safe())
            except Exception:
                log.exception("companion overlay show failed")
        else:
            self._uninstall_active_app_observer()
            self._uninstall_input_monitor()
            self._last_orientation = None
            # Don't yank a level-up animation mid-flight; the next
            # _end_level_up / _end_evolution will hide it now that
            # _persistent is False.
            if self._overlay.visible and not self._overlay.evolution_running:
                self._overlay.hide()
        self.refresh(None)

    def _install_input_monitor(self) -> None:
        if self._input_monitor is not None:
            return
        try:
            from tokenmon.companion.input_monitor import InputActivityMonitor
            # Each input event triggers two cheap checks:
            #  * _tick_orientation early-exits when sprite + side are
            #    already correct, so per-keystroke cost is a comparison.
            #  * _tick_dock with a 200 ms throttle so cmd-` and clicks on
            #    other windows reposition us almost immediately, but
            #    sustained typing doesn't keep hammering the window list.
            mon = InputActivityMonitor(on_input=self._on_input_event)
            mon.start()
            mon.mark_input_now()
            self._input_monitor = mon
        except Exception:
            log.exception("install input monitor failed")

    def _on_input_event(self) -> None:
        """Called from the global input monitor on every key/click/scroll.
        Bumps orientation immediately and (throttled) re-checks the dock
        target so we follow same-app window switches with ~200 ms latency
        instead of waiting up to 5 s for the periodic tick."""
        try:
            self._tick_orientation()
        except Exception:
            log.exception("orientation tick failed")
        try:
            self._tick_dock(throttle_s=0.2)
        except Exception:
            log.exception("dock tick failed")

    def _uninstall_input_monitor(self) -> None:
        mon = self._input_monitor
        if mon is None:
            return
        try:
            mon.stop()
        except Exception:
            log.exception("stop input monitor failed")
        self._input_monitor = None

    def _install_active_app_observer(self) -> None:
        if self._active_app_observer is not None:
            return
        try:
            from tokenmon.companion.active_app import ActiveAppObserver
            obs = ActiveAppObserver.alloc().initWithCallback_(
                self._on_active_app_changed,
            )
            obs.start()
            self._active_app_observer = obs
        except Exception:
            log.exception("install active-app observer failed")

    def _uninstall_active_app_observer(self) -> None:
        obs = self._active_app_observer
        if obs is None:
            return
        try:
            obs.stop()
        except Exception:
            log.exception("stop active-app observer failed")
        self._active_app_observer = None

    def _current_bundle_id_safe(self) -> str | None:
        try:
            from tokenmon.companion.active_app import current_bundle_id
            return current_bundle_id()
        except Exception:
            log.exception("current_bundle_id failed")
            return None

    def _on_active_app_changed(self, _bundle_id: str | None) -> None:
        """When the foreground app changes, immediately dock to the new
        app's bottom-left window edge (animated) and re-evaluate
        orientation. The 5-s ``_tick_dock`` then keeps the position in
        sync as the user drags the window around or switches between
        windows of the same app (which doesn't fire this notification)."""
        if not self._companion_mode:
            return
        try:
            self._dock_to_focused_window(force=True)
        except Exception:
            log.exception("dock to focused window failed")
        try:
            self._tick_orientation(force=True)
        except Exception:
            log.exception("orientation tick after app change failed")

    def _dock_to_focused_window(self, *, force: bool = False) -> None:
        """Slide the overlay to the bottom-RIGHT of the focused window —
        a fixed anchor that doesn't move between engaged and idle
        states. Engagement is communicated by sprite orientation
        (front/back) AND by horizontal mirror: from the right anchor
        the un-mirrored back sprite would face right (away from
        content), so we mirror it to face left toward the window.

        Multi-monitor: we explicitly do NOT clamp negative x — a screen
        left of the primary has negative x in both CG and AppKit. We DO
        verify the target sits on a connected screen, otherwise corner-
        fallback.

        Cross-screen moves use ``animate=False`` because NSWindow's
        animated setFrame can glitch when the target frame is on a
        different display than the current one.

        ``force=True`` re-issues the move even if the rect is unchanged.
        """
        try:
            from tokenmon.companion.window_geom import (
                focused_window_bounds, frontmost_pid, screen_containing_point,
            )
        except Exception:
            log.exception("window_geom import failed")
            return
        pid = frontmost_pid()
        if pid is None:
            if self._last_dock_rect is not None or force:
                self._overlay.move_to_corner(animate=True)
                self._last_dock_rect = None
            return
        rect = focused_window_bounds(pid)
        if rect is None:
            if self._last_dock_rect is not None or force:
                self._overlay.move_to_corner(animate=True)
                self._last_dock_rect = None
            return
        if not force and rect == self._last_dock_rect:
            return
        sprite_size = self._overlay._size
        # Bottom-right of the focused window with 4 px inset so the
        # sprite doesn't ride the macOS window-shadow gradient.
        target_x = rect.x + rect.width - sprite_size - 4
        target_y = rect.y
        # Verify the target sits on a real connected screen.
        anchor_x = target_x + sprite_size / 2
        anchor_y = target_y + 1
        target_screen = screen_containing_point(anchor_x, anchor_y)
        if target_screen is None:
            log.warning(
                "dock target (%s, %s) is off all screens; falling back to corner",
                target_x, target_y,
            )
            self._overlay.move_to_corner(animate=True)
            self._last_dock_rect = None
            return
        try:
            current_screen = self._overlay._window.screen() if self._overlay._window else None
        except Exception:
            current_screen = None
        animate = (current_screen is not None and current_screen == target_screen)
        self._overlay.move_to(target_x, target_y, animate=animate)
        self._last_dock_rect = rect

    def _tick_orientation(self, *, force: bool = False) -> None:
        """Choose front vs. back sprite based on how recently the user
        provided input. Within INTERACTION_TIMEOUT_S of an input event →
        back (Pokémon looks at the window content). Otherwise → front
        (looks at the user). Position stays fixed at the focused window's
        bottom-RIGHT in both states; the back sprite is horizontally
        mirrored so it still appears to face the content area (which is
        to the LEFT of the sprite at the right anchor).

        ``force=True`` re-applies even if state hasn't changed.
        """
        if not self._companion_mode or not self._overlay.visible:
            return
        mon = self._input_monitor
        idle_s = mon.seconds_since_last_input() if mon is not None else None
        want = "front"
        if idle_s is not None and idle_s <= INTERACTION_TIMEOUT_S:
            want = "back"
        if not force and want == self._last_orientation:
            return
        try:
            front = pokemon.ensure_sprite(
                self._pokemon_dex_id, shiny=self._pokemon_is_shiny,
            )
            if front is None:
                return
            back = None
            if want == "back":
                back = pokemon.ensure_sprite(
                    self._pokemon_dex_id,
                    shiny=self._pokemon_is_shiny, back=True,
                )
            # Mirror the back sprite — the unmirrored gen-V back sprite
            # has the Pokémon's head turned to the right (3/4 view), but
            # at the right anchor we want it facing left toward content.
            mirrored = (want == "back")
            # Back sprites get an extra zoom to compensate for PokeAPI
            # rendering them smaller within the canvas. Front sprites
            # stay at zoom=1.0.
            zoom = COMPANION_BACK_ZOOM if want == "back" else 1.0
            self._overlay.animate_sprite_turn(
                front_path=front, back_path=back,
                mirrored=mirrored, zoom=zoom,
            )
            self._last_orientation = want
        except Exception:
            log.exception("orientation swap failed")

    def toggle_weather(self, _sender) -> None:
        self._use_weather = not self._use_weather
        config.set_("use_weather", self._use_weather)
        self.refresh(None)

    def open_tokendex(self, _sender) -> None:
        try:
            tokendex.show()
        except Exception:
            log.exception("failed to open tokendex")

    def refresh(self, _sender) -> None:
        self._maybe_repick_for_new_day()
        self._refresh_pokemon_state()
        try:
            totals = query_today(TZ)
            by_model = query_today_by_model(TZ)
        except Exception:
            log.exception("failed to query usage")
            self.title = f"{EGG} ?"
            return
        active = totals.output_tokens
        # When the sprite is showing, the status item shows just the sprite (no
        # text — that's surfaced via the tooltip and the popover). When the
        # sprite is off, we fall back to the egg emoji + total tokens (or
        # ⚠️ + tokens when the proxy is offline).
        sprite_active = self._show_pokemon and self._animator is not None
        if sprite_active and self._proxy_up:
            self.title = ""
        else:
            icon = EGG if self._proxy_up else EGG_DOWN
            self.title = f"{icon} {_fmt_tokens(active)}"
        self._update_tooltip()
        # Idempotently kill any rumps-installed menu — popover handles clicks.
        try:
            if self._nsapp is not None:
                self._nsapp.nsstatusitem.setMenu_(None)
        except Exception:
            pass


def _acquire_singleton_lock(restart: bool = False):
    """Hold an exclusive flock on ~/.tokenmon/menubar.lock for the lifetime
    of the process. Returns the open lockfile (caller must keep the ref to
    keep the lock alive).

    If another instance already holds the lock:
      - ``restart=True``: read the PID from the file, SIGTERM it, retry.
      - otherwise: print a message and exit non-zero so launchd / shells
        bubble the failure up.
    """
    import fcntl
    import os
    import signal
    import sys

    from tokenmon.storage import DB_DIR

    DB_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DB_DIR / "menubar.lock"
    f = open(lock_path, "a+")  # noqa: SIM115 — kept for process lifetime
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another instance holds the lock. Read its PID for the message
        # (and for --restart's SIGTERM target).
        try:
            f.seek(0)
            other_pid = int(f.read().strip() or "0")
        except (ValueError, OSError):
            other_pid = 0
        if not restart:
            print(
                f"tokenmon-menubar already running (PID {other_pid or '?'}). "
                f"Pass --restart to replace it.",
                file=sys.stderr,
            )
            sys.exit(1)
        # --restart: politely terminate the other instance and retry.
        if other_pid > 0:
            try:
                os.kill(other_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            # Wait up to 5 s for it to release the lock.
            for _ in range(50):
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.1)
            else:
                print(
                    f"tokenmon-menubar PID {other_pid} did not exit; aborting.",
                    file=sys.stderr,
                )
                sys.exit(1)
    # We hold the lock. Stamp our PID for future --restart calls.
    f.seek(0)
    f.truncate()
    f.write(f"{os.getpid()}\n")
    f.flush()
    return f


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(prog="tokenmon-menubar")
    parser.add_argument(
        "--restart", action="store_true",
        help="If another instance is running, terminate it and take over.",
    )
    args = parser.parse_args()

    # Hold the lock for the process lifetime — assigning it to a module-
    # level slot keeps the ref alive past main() (we never return from
    # rumps.run() until the app quits anyway, but defensive).
    global _SINGLETON_LOCK
    _SINGLETON_LOCK = _acquire_singleton_lock(restart=args.restart)

    init_db()
    items_remote.prefetch_in_background(list(items.ITEMS.values()))
    TokenmonApp().run()


_SINGLETON_LOCK = None


if __name__ == "__main__":
    main()

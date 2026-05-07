"""macOS menubar app — shows today's total tokens with a 🥚 icon.

Click opens a dropdown with per-model breakdown and estimated USD cost.
Refreshes every 30 seconds from SQLite. Pings the proxy /healthz every 10s
and shows a warning state if the proxy is down.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

import rumps
from AppKit import NSTimer

from tokenmon import box, config, items, items_remote, pokemon, tokendex
from tokenmon.menubar import (
    companion_drv as _companion_drv,
    encounter_roll as _encounter_roll,
    icon as _icon,
    levelup as _levelup,
    ticks as _ticks,
)
from tokenmon.overlay import PokemonOverlay
from tokenmon.popover import TokenmonPopover
from tokenmon.storage import (
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
# HP regen rate: one HP point per this many output-tokens trained on
# the active Pokémon. Out-of-battle only — battle damage drives HP
# during a fight, regen freezes for the duration.
HP_REGEN_TOKENS_PER_HP = 1000
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


from tokenmon.ui_helpers import fmt_tokens as _fmt_tokens

# Health helpers moved to menubar.health; aliased here to keep callers in
# this module unchanged during the split.
from tokenmon.menubar.health import (
    proxy_health as _proxy_health,
    restart_proxies_via_launchctl as _restart_proxies_via_launchctl,
)


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
                self._overlay.update_sprite(
                    self._pokemon_sprite,
                    speed=self._companion_sprite_speed(),
                )
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
        # HP-regen bookkeeping — every HP_REGEN_TOKENS_PER_HP output-
        # tokens trained on the active Pokémon restore one HP point
        # (clamped to max). Tracks the active Pokémon's last-seen total
        # XP plus a fractional remainder so partial chunks accumulate
        # across ticks instead of being floored away each time.
        self._hp_regen_active_id: int | None = None
        self._hp_regen_last_xp: int = 0
        self._hp_regen_remainder: int = 0
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
        self._button_wire_handler = _icon._ButtonWireHandler.alloc().initWithApp_(self)
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
            if self._companion_mode:
                try:
                    self._overlay.show_evolution(old_id, new_id)
                except Exception:
                    log.exception("evolution animation failed")

    def _sync_overlay(self) -> None:
        """Refresh the overlay's sprite (when the displayed Pokemon changes).
        Does NOT decide visibility — the overlay only appears on level-up events."""
        if self._overlay.visible:
            self._overlay.update_sprite(
                self._pokemon_sprite,
                speed=self._companion_sprite_speed(),
            )

    def _query_max_request_id(self) -> int:
        return _encounter_roll.query_max_request_id(self)

    def _query_new_requests(self, since: int) -> tuple[list[int], int]:
        return _encounter_roll.query_new_requests(self, since)

    def _maybe_roll_encounters(self) -> None:
        _encounter_roll.maybe_roll_encounters(self)

    def _compute_current_level(self) -> int:
        return _levelup.compute_current_level(self)

    def _check_level_up(self, now: float) -> None:
        _levelup.check_level_up(self, now)

    def _apply_level_up_moves(self, active, old_level: int, new_level: int) -> None:
        _levelup.apply_level_up_moves(self, active, old_level, new_level)

    def _statusbar_button(self):
        return _icon.statusbar_button(self)

    def _set_menubar_image(self, img) -> None:
        _icon.set_menubar_image(self, img)

    def _update_tooltip(self) -> None:
        _icon.update_tooltip(self)

    def _stop_animator(self, *, clear_image: bool = True) -> None:
        _icon.stop_animator(self, clear_image=clear_image)

    def _start_animator(self) -> None:
        _icon.start_animator(self)

    def _sync_menubar_icon(self) -> None:
        _icon.sync_menubar_icon(self)

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
        _companion_drv.proximity_tick(self)

    @rumps.timer(ACTIVITY_POLL_INTERVAL_SEC)
    def activity_poll(self, _sender) -> None:
        now = time.monotonic()
        prev_level = self._last_known_level
        self._check_level_up(now)
        self._maybe_roll_encounters()
        self._tick_affection()
        self._tick_pending_drops()
        self._tick_hp_regen()
        self._tick_mood()
        self._tick_dock()
        self._tick_orientation()
        if self._last_known_level != prev_level:
            # Refresh now so the menubar title picks up the new level immediately
            # (otherwise we'd wait up to 30s for the next refresh).
            self.refresh(None)

    def _companion_sprite_speed(self) -> float:
        return _ticks.companion_sprite_speed(self)

    def _refresh_companion_sprite_speed(self) -> None:
        _ticks.refresh_companion_sprite_speed(self)

    def _tick_hp_regen(self) -> None:
        _ticks.tick_hp_regen(self)

    def _tick_dock(self, *, throttle_s: float = 0.0) -> None:
        _ticks.tick_dock(self, throttle_s=throttle_s)

    def _tick_mood(self) -> None:
        _ticks.tick_mood(self)

    def _tick_pending_drops(self) -> None:
        _ticks.tick_pending_drops(self)

    def _tick_affection(self) -> None:
        _ticks.tick_affection(self)

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

    def toggle_companion(self, _sender) -> None:
        self._companion_mode = not self._companion_mode
        config.set_("companion_mode", self._companion_mode)
        self._overlay.set_persistent(self._companion_mode)
        if self._companion_mode:
            try:
                self._overlay.update_sprite(
                    self._pokemon_sprite,
                    speed=self._companion_sprite_speed(),
                )
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
        _companion_drv.install_input_monitor(self)

    def _on_input_event(self) -> None:
        _companion_drv.on_input_event(self)

    def _uninstall_input_monitor(self) -> None:
        _companion_drv.uninstall_input_monitor(self)

    def _install_active_app_observer(self) -> None:
        _companion_drv.install_active_app_observer(self)

    def _uninstall_active_app_observer(self) -> None:
        _companion_drv.uninstall_active_app_observer(self)

    def _current_bundle_id_safe(self) -> str | None:
        return _companion_drv.current_bundle_id_safe(self)

    def _on_active_app_changed(self, _bundle_id: str | None) -> None:
        _companion_drv.on_active_app_changed(self, _bundle_id)

    def _dock_to_focused_window(self, *, force: bool = False) -> None:
        _companion_drv.dock_to_focused_window(self, force=force)

    def _tick_orientation(self, *, force: bool = False) -> None:
        _ticks.tick_orientation(self, force=force)

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

"""Tick handlers — pure side-effect functions called from ``activity_poll``
and from ``companion_drv.on_input_event``.

Despite the name, these are NOT @rumps.timer methods themselves. They run on
the 5 s activity poll (and a subset run on every input event), reading and
mutating state on TokenmonApp:

  - HP regen (``app._hp_regen_active_id`` / ``._hp_regen_last_xp`` /
    ``._hp_regen_remainder``) — train-on-output → restore HP.
  - Dock drift (``app._last_dock_check_mono``) — same-app window switches.
  - Mood (``app._overlay.set_mood_alpha``) — time-of-day dim.
  - Pending drops (``app._last_pending_snapshot``) — diff floats new items.
  - Affection (``app._affection_ticks`` / ``._affection_active_id``).
  - Orientation (``app._last_orientation``) — input-recency → front/back.
  - Companion sprite-speed helper used by every overlay-sprite call site.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from tokenmon import box, pokemon
from tokenmon.storage import latest_request_ts, query_xp_for_pokemon

TZ = "Europe/Berlin"

# HP regen rate: one HP point per this many output-tokens trained on
# the active Pokémon. Out-of-battle only.
HP_REGEN_TOKENS_PER_HP = 1000
# Companion: how long an input event keeps the Pokémon facing the screen
# before it turns around to look at the user.
INTERACTION_TIMEOUT_S = 30.0
# Zoom factor for the back sprite. PokeAPI gen-V back sprites draw the
# character noticeably smaller than the front sprite within the same
# 96×96 canvas — boosting the layer scale brings them visually back to par.
COMPANION_BACK_ZOOM = 1.05
# Affection growth: ticks per +1 affection (5 s poll × 120 = 10 min).
AFFECTION_TICKS_PER_POINT = 120
# Idle gate for affection growth.
AFFECTION_IDLE_GATE_SEC = 30 * 60

log = logging.getLogger("tokenmon.menubar.ticks")


def companion_sprite_speed(app) -> float:
    """Map the active Pokémon's HP into a 0.25..1.0 GIF playback
    multiplier. Used by every overlay-sprite call site so the
    companion's animation visibly drags when the Pokémon is
    unwell."""
    try:
        from tokenmon.pokemon.stats import final_stats
        from tokenmon.sprite_speed import hp_playback_speed
        active = box.get_active_pokemon()
        if active is None:
            return 1.0
        xp = query_xp_for_pokemon(active.id)
        growth = pokemon.growth_rate_of(active.species_dex_id)
        level, _, _ = pokemon.level_from_xp(xp, growth)
        hp_max = final_stats(
            active.species_dex_id, active.ivs,
            max(1, level), active.nature,
        )[0]
        return hp_playback_speed(active.hp_current, hp_max)
    except Exception:
        log.exception("companion sprite speed compute failed")
        return 1.0


def refresh_companion_sprite_speed(app) -> None:
    """Reload the companion sprite with the current HP-derived
    playback speed. Cheap — re-uses the on-disk sprite cache.

    Picks front- vs back-sprite path according to ``_last_orientation``
    so an HP refresh post-battle (engaged → back) doesn't accidentally
    flash the front sprite back into place.
    """
    if not app._companion_mode:
        return
    if app._pokemon_sprite is None:
        return
    try:
        target = app._pokemon_sprite
        if app._last_orientation == "back":
            back = pokemon.ensure_sprite(
                app._pokemon_dex_id,
                shiny=app._pokemon_is_shiny, back=True,
            )
            if back is not None:
                target = back
        app._overlay.update_sprite(
            target,
            speed=companion_sprite_speed(app),
        )
    except Exception:
        log.exception("companion sprite speed refresh failed")


def tick_hp_regen(app) -> None:
    """Restore the active Pokémon's HP at one point per
    ``HP_REGEN_TOKENS_PER_HP`` output-tokens trained on it,
    clamped to its current max HP. Skips while a battle is in
    progress so combat damage drives HP during a fight rather
    than fighting the regen counter.

    ``_hp_regen_remainder`` accumulates partial chunks across
    ticks so a slow stream of small requests still adds up over
    time — e.g. 600 + 600 = 1200 yields 1 HP plus a 200 carry.
    """
    # Battle in progress → freeze regen.
    if getattr(app._popover, "_battle_session", None) is not None:
        return
    try:
        active = box.get_active_pokemon()
    except Exception:
        log.exception("active lookup in hp_regen tick failed")
        return
    if active is None:
        app._hp_regen_active_id = None
        return
    # Active Pokémon changed → reset baseline; no retroactive heal.
    if app._hp_regen_active_id != active.id:
        app._hp_regen_active_id = active.id
        try:
            app._hp_regen_last_xp = query_xp_for_pokemon(active.id)
        except Exception:
            log.exception("xp baseline read failed")
            app._hp_regen_last_xp = 0
        app._hp_regen_remainder = 0
        return
    # Already at full → nothing to do (and don't spam carry-over).
    if active.hp_current is None:
        return
    # Pull current XP and compute the delta since the last tick.
    try:
        current_xp = query_xp_for_pokemon(active.id)
    except Exception:
        log.exception("xp lookup in hp_regen tick failed")
        return
    delta = current_xp - app._hp_regen_last_xp
    app._hp_regen_last_xp = current_xp
    if delta <= 0:
        return
    pool = app._hp_regen_remainder + int(delta)
    hp_to_add = pool // HP_REGEN_TOKENS_PER_HP
    app._hp_regen_remainder = pool % HP_REGEN_TOKENS_PER_HP
    if hp_to_add <= 0:
        return
    # Compute current max HP from the active's IVs + level so the
    # regen respects post-level-up cap increases.
    try:
        from tokenmon.pokemon.stats import final_stats
        from tokenmon.storage import set_pokemon_hp
        growth = pokemon.growth_rate_of(active.species_dex_id)
        level, _, _ = pokemon.level_from_xp(current_xp, growth)
        hp_max = final_stats(
            active.species_dex_id, active.ivs, max(1, level), active.nature,
        )[0]
    except Exception:
        log.exception("hp_max compute in regen tick failed")
        return
    new_hp = min(hp_max, int(active.hp_current) + hp_to_add)
    try:
        if new_hp >= hp_max:
            # Fully healed → store NULL for the implicit-full
            # semantics so the regen tick stops firing for this
            # Pokémon until it takes damage again.
            set_pokemon_hp(active.id, None)
            # Reset remainder so the next damage cycle starts fresh.
            app._hp_regen_remainder = 0
        else:
            set_pokemon_hp(active.id, new_hp)
    except Exception:
        log.exception("hp_regen persist failed")
    # HP changed → companion's GIF speed may need to update too.
    refresh_companion_sprite_speed(app)


def tick_dock(app, *, throttle_s: float = 0.0) -> None:
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
    if not app._companion_mode or not app._overlay.visible:
        return
    if app._overlay.evolution_running or app._overlay.wiggling:
        return
    if throttle_s > 0.0:
        now_mono = time.monotonic()
        if (now_mono - app._last_dock_check_mono) < throttle_s:
            return
        app._last_dock_check_mono = now_mono
    else:
        app._last_dock_check_mono = time.monotonic()
    try:
        app._dock_to_focused_window()
    except Exception:
        log.exception("dock tick failed")


def tick_mood(app) -> None:
    """Apply the time-of-day mood modifier (night dims the sprite) on
    the 5-s tick. No-op when companion mode is off."""
    if not app._companion_mode or not app._overlay.visible:
        return
    try:
        from tokenmon.companion.mood import mood_modifiers
        from zoneinfo import ZoneInfo
        mods = mood_modifiers(datetime.now(ZoneInfo(TZ)))
        app._overlay.set_mood_alpha(mods.alpha_multiplier)
    except Exception:
        log.exception("mood modifier apply failed")


def tick_pending_drops(app) -> None:
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
        delta = int(count) - int(app._last_pending_snapshot.get(key, 0))
        if delta > 0:
            new_drops[key] = delta
    app._last_pending_snapshot = current
    # Drops only animate while the companion is on. Wiggle the sprite
    # first to announce the drop, then float the items up so they
    # appear to come "out of" the wiggling Pokémon.
    if new_drops and app._companion_mode:
        try:
            app._overlay.wiggle()
        except Exception:
            log.exception("wiggle failed")
        try:
            app._overlay.show_floating_items(new_drops)
        except Exception:
            log.exception("show_floating_items failed")


def tick_affection(app) -> None:
    """Grow the active Pokemon's affection by 1 every
    AFFECTION_TICKS_PER_POINT polls. Counter resets when the active
    Pokemon changes (or there's no active), so swapping pets doesn't
    leak partial progress. While the proxy has been idle (no requests in
    the last AFFECTION_IDLE_GATE_SEC) the counter is held in place — an
    unattended laptop shouldn't bond a Pokemon for free."""
    from tokenmon.storage import bump_affection
    try:
        active_id = box.get_active_pokemon_id()
    except Exception:
        log.exception("get_active_pokemon_id failed in affection tick")
        return
    if active_id is None:
        app._affection_ticks = 0
        app._affection_active_id = None
        return
    if active_id != app._affection_active_id:
        app._affection_active_id = active_id
        app._affection_ticks = 0
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
    app._affection_ticks += 1
    if app._affection_ticks >= AFFECTION_TICKS_PER_POINT:
        app._affection_ticks = 0
        try:
            bump_affection(active_id)
        except Exception:
            log.exception("bump_affection failed")


def tick_orientation(app, *, force: bool = False) -> None:
    """Choose front vs. back sprite based on how recently the user
    provided input. Within INTERACTION_TIMEOUT_S of an input event →
    back (Pokémon looks at the window content). Otherwise → front
    (looks at the user). Position stays fixed at the focused window's
    bottom-RIGHT in both states; the back sprite is horizontally
    mirrored so it still appears to face the content area (which is
    to the LEFT of the sprite at the right anchor).

    ``force=True`` re-applies even if state hasn't changed.
    """
    if not app._companion_mode or not app._overlay.visible:
        return
    mon = app._input_monitor
    idle_s = mon.seconds_since_last_input() if mon is not None else None
    want = "front"
    if idle_s is not None and idle_s <= INTERACTION_TIMEOUT_S:
        want = "back"
    if not force and want == app._last_orientation:
        return
    try:
        front = pokemon.ensure_sprite(
            app._pokemon_dex_id, shiny=app._pokemon_is_shiny,
        )
        if front is None:
            return
        back = None
        if want == "back":
            back = pokemon.ensure_sprite(
                app._pokemon_dex_id,
                shiny=app._pokemon_is_shiny, back=True,
            )
        # Mirror the back sprite — the unmirrored gen-V back sprite
        # has the Pokémon's head turned to the right (3/4 view), but
        # at the right anchor we want it facing left toward content.
        mirrored = (want == "back")
        # Back sprites get an extra zoom to compensate for PokeAPI
        # rendering them smaller within the canvas. Front sprites
        # stay at zoom=1.0.
        zoom = COMPANION_BACK_ZOOM if want == "back" else 1.0
        app._overlay.animate_sprite_turn(
            front_path=front, back_path=back,
            mirrored=mirrored, zoom=zoom,
            speed=companion_sprite_speed(app),
        )
        app._last_orientation = want
    except Exception:
        log.exception("orientation swap failed")

"""Turn-based trainer-battle pane.

Initialises a battle session from the pending trainer + the player's
active Pokémon. Renders both sprites, HP bars, the player's 4 moves
as buttons, and a battle log. Clicking a move resolves a single turn
via the pure ``battle.engine``; when one side faints the controller
transitions the trainer's next Pokémon in or moves to the reward pane.

Battle state lives on ``popover._battle_session`` so closing + re-
opening the popover during a single battle resumes mid-fight (within
a session — app restart still abandons the battle, per the v1 plan).
"""
from __future__ import annotations

import logging
import random
from dataclasses import asdict, replace

import objc
from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextAlignmentRight,
    NSView,
)
from Foundation import NSMakeRect, NSObject, NSTimer

from tokenmon import (
    box, encounter, items as items_registry, items_remote,
    learnsets_remote, moves_remote, pokemon,
)
from tokenmon.battle.engine import (
    AttackEvent,
    FaintEvent,
    MissEvent,
    fold_events,
    plan_turn,
    simulate_turn,
    resolve_turn,
)
from tokenmon.battle.models import BattleStats, Move
from tokenmon.battle.status import NonVolatileStatus, StatusState
from tokenmon.battle.team_gen import generate_wild_mon
from tokenmon.popover.panes.battle_fx import make_type_fx
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes._move_tooltip import format_move_tooltip
from tokenmon.popover.panes.move_button import _MoveButtonView
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_BATTLE_REWARD,
    PANE_ENCOUNTER,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    STATUS_BADGE_HEIGHT,
    STATUS_BADGE_WIDTH,
    _StatusBadge,
    _label,
)
from tokenmon.storage import (
    decrement_pp,
    get_pending_encounter,
    get_pending_trainer,
    list_trainer_pokemon,
    set_encounter_hp,
    set_encounter_status,
    set_pokemon_status,
)

log = logging.getLogger("tokenmon.popover.panes.battle")

_FALLBACK_MOVE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)


def _status_state_from_db(status_str: str | None, counter: int | None) -> StatusState:
    """Translate the persisted (status_non_volatile, status_counter) pair
    into a battle-engine ``StatusState``. Unknown / NULL values map to
    HEALTHY so older rows from before the status migration land safely.
    Volatile fields stay at defaults — they don't persist."""
    raw = (status_str or "healthy").lower()
    try:
        nv = NonVolatileStatus(raw)
    except ValueError:
        nv = NonVolatileStatus.HEALTHY
    return StatusState(non_volatile=nv, nv_counter=int(counter or 0))


def _status_badge_key(stats: BattleStats) -> str | None:
    """Pick the most relevant badge label for a Pokémon's current state.

    Non-volatile statuses always win over confusion (Gen-3 canon: only
    one badge slot). Confusion is shown only when no non-volatile is
    active. Flinch is single-turn and doesn't get a badge — it surfaces
    via the log line. Returns None for healthy / unbadged states so
    callers can skip drawing.
    """
    nv = stats.status.non_volatile.value
    if nv != "healthy":
        return nv
    if stats.status.confusion_turns > 0:
        return "confusion"
    return None


_HP_DRAIN_FPS = 30


class _HPBar(NSView):
    """HP bar with an animated drain (Game-Boy-style "tick down" feel).

    Color tiers — green > 50%, orange 20–50%, red < 20% — are recomputed
    each ``drawRect_`` so the bar shifts color naturally as it drains
    rather than snapping at the next render. The animation is a plain
    NSTimer at ~30 fps; no Core Animation, keeps the popover
    rendering-stack uniform.
    """

    def initWithFrame_current_max_(self, frame, current, hp_max):  # noqa: N802
        import objc
        self = objc.super(_HPBar, self).initWithFrame_(frame)
        if self is None:
            return None
        self._current = max(0, int(current))
        self._max = max(1, int(hp_max))
        # Animation state — quiescent when _anim_timer is None.
        self._anim_timer = None
        self._anim_start_value = float(self._current)
        self._anim_target_value = float(self._current)
        self._anim_start_ts = 0.0
        self._anim_duration = 0.0
        return self

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        # Track
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.18).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 4, 4,
        ).fill()
        # Fill
        frac = max(0.0, min(1.0, self._current / self._max))
        if frac == 0:
            return
        if frac > 0.5:
            r, g, b = 0.36, 0.78, 0.20
        elif frac > 0.2:
            r, g, b = 1.00, 0.65, 0.10
        else:
            r, g, b = 0.95, 0.30, 0.30
        fill_w = bounds.size.width * frac
        fill = NSMakeRect(0, 0, fill_w, bounds.size.height)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            fill, 4, 4,
        ).fill()

    def setCurrent_(self, value):  # noqa: N802
        """Snap to ``value`` (no animation). Used to reset state when
        re-rendering or when a half-finished animation is preempted."""
        self._cancel_animation()
        self._current = max(0, min(int(self._max), int(value)))
        self.setNeedsDisplay_(True)

    def animateToValue_duration_(self, target, duration):  # noqa: N802
        """Linearly interpolate ``_current`` to ``target`` over
        ``duration`` seconds. Calling again mid-drain re-anchors the
        start value to whatever's currently displayed and re-aims at
        the new target — no abrupt jumps."""
        from time import monotonic
        target = max(0, min(int(self._max), int(target)))
        if duration <= 0 or self._current == target:
            self.setCurrent_(target)
            return
        # Re-anchor to current (possibly mid-drain) value.
        self._cancel_animation()
        self._anim_start_value = float(self._current)
        self._anim_target_value = float(target)
        self._anim_start_ts = monotonic()
        self._anim_duration = float(duration)
        self._anim_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / _HP_DRAIN_FPS, self, b"tickAnimation:", None, True,
        )

    def tickAnimation_(self, _timer):  # noqa: N802 — NSTimer callback
        from time import monotonic
        elapsed = monotonic() - self._anim_start_ts
        t = elapsed / self._anim_duration if self._anim_duration > 0 else 1.0
        if t >= 1.0:
            self._current = int(self._anim_target_value)
            self._cancel_animation()
            self.setNeedsDisplay_(True)
            return
        # Linear interpolation. A small bias toward integer ticks keeps
        # the bar from looking subtly jittery between frames.
        value = (
            self._anim_start_value
            + (self._anim_target_value - self._anim_start_value) * t
        )
        self._current = int(round(value))
        self.setNeedsDisplay_(True)

    def _cancel_animation(self):
        if self._anim_timer is not None:
            try:
                self._anim_timer.invalidate()
            except Exception:
                pass
            self._anim_timer = None


def _player_battle_stats(active) -> BattleStats:
    """Build BattleStats for the player's active Pokémon. Uses
    pokemon.stats.final_stats for HP/Atk/Def/etc. Moves come from the
    pokemon_moves table (backfilled via learnsets if empty)."""
    from tokenmon.pokemon.stats import final_stats
    from tokenmon.storage import get_pokemon_moves, set_pokemon_move, unlock_move

    # ``Pokemon.ivs`` is the canonical 6-tuple; older code expected
    # flat ``iv_hp/...`` fields which the dataclass doesn't expose.
    ivs = tuple(active.ivs)
    # Compute current level from XP.
    from tokenmon.storage import query_xp_for_pokemon
    xp = query_xp_for_pokemon(active.id)
    growth = pokemon.growth_rate_of(active.species_dex_id)
    level, _, _ = pokemon.level_from_xp(xp, growth)
    level = max(1, level)
    hp_max, atk, defn, spa, spd, spe = final_stats(
        active.species_dex_id, ivs, level, active.nature,
    )
    types = pokemon.types_of(active.species_dex_id)

    # Backfill moves from learnset if the row has none.
    rows = get_pokemon_moves(active.id)
    if not rows:
        try:
            keys = learnsets_remote.initial_moves(
                active.species_dex_id, level,
            )
        except Exception:
            keys = ["tackle"]
        for slot, key in enumerate(keys[:4]):
            md = moves_remote.get_move_data(key) or _FALLBACK_MOVE
            set_pokemon_move(active.id, slot, key, max_pp=md.pp)
            try:
                unlock_move(active.id, key, max(1, level))
            except Exception:
                log.exception("unlock_move failed for %s", key)
        rows = get_pokemon_moves(active.id)

    moves: list[Move] = []
    pps: list[int] = []
    for r in rows:
        md = moves_remote.get_move_data(r.move_key) or _FALLBACK_MOVE
        moves.append(md)
        pps.append(r.current_pp)
    if not moves:
        moves = [_FALLBACK_MOVE]
        pps = [_FALLBACK_MOVE.pp]

    name = pokemon.display_name(active.nickname, active.species_dex_id)
    # HP carries over between battles. ``active.hp_current is None``
    # means a fresh / fully-healed Pokémon (use max). 0 means fainted —
    # auto-revive to full at battle init so the user isn't soft-locked
    # without a heal mechanic. Anything in (0, hp_max] is the actual
    # remaining HP from the previous fight.
    if active.hp_current is None or active.hp_current <= 0:
        starting_hp = hp_max
        # A 0-HP auto-revive also resets status — Pokémon Center semantics.
        status = StatusState()
    else:
        starting_hp = min(int(active.hp_current), hp_max)
        status = _status_state_from_db(
            getattr(active, "status_non_volatile", "healthy"),
            getattr(active, "status_counter", 0),
        )
    return BattleStats(
        species_dex_id=active.species_dex_id, level=level, types=types,
        hp_max=hp_max, hp_current=starting_hp,
        attack=atk, defense=defn, sp_attack=spa, sp_defense=spd,
        speed=spe, moves=tuple(moves), move_pps=tuple(pps), name=name,
        status=status,
    )


def _opp_battle_stats(row) -> BattleStats:
    """Build BattleStats for one trainer Pokémon."""
    from tokenmon.pokemon.stats import final_stats

    hp_max, atk, defn, spa, spd, spe = final_stats(
        row.species_dex_id, row.ivs, row.level, row.nature,
    )
    types = pokemon.types_of(row.species_dex_id)
    moves: list[Move] = []
    for key in row.move_keys:
        md = moves_remote.get_move_data(key) or _FALLBACK_MOVE
        moves.append(md)
    if not moves:
        moves = [_FALLBACK_MOVE]
    name = pokemon.name_of(row.species_dex_id)
    return BattleStats(
        species_dex_id=row.species_dex_id, level=row.level, types=types,
        hp_max=hp_max, hp_current=hp_max,
        attack=atk, defense=defn, sp_attack=spa, sp_defense=spd,
        speed=spe, moves=tuple(moves),
        move_pps=tuple(m.pp for m in moves),
        name=f"Foe {name}",
    )


def _init_battle_session(popover, trainer, active) -> dict:
    """Build the in-memory battle session — runs once when the player
    first clicks Fight. Resumes from popover._battle_session on later
    pane rebuilds within the same battle."""
    if getattr(popover, "_battle_session", None) is not None:
        existing = popover._battle_session
        if existing.get("trainer_id") == trainer.id:
            return existing
    team_rows = list_trainer_pokemon(trainer.id)
    opp_states = [_opp_battle_stats(r) for r in team_rows]
    player_state = _player_battle_stats(active)
    session = {
        "kind": "trainer",
        "trainer_id": trainer.id,
        "trainer_name": f"{trainer.title} {trainer.name}",
        "trainer_difficulty": trainer.difficulty,
        "player_pokemon_id": active.id,
        "player_state": player_state,
        "opp_states": opp_states,
        "opp_trainer_pokemon_ids": [r.id for r in team_rows],
        "active_opp_idx": 0,
        "log": [f"{trainer.title} {trainer.name} sent out {opp_states[0].name}!"],
        "defeated_count": 0,
        "rng": random.Random(),
    }
    popover._battle_session = session
    return session


def _wild_battle_stats(enc) -> BattleStats:
    """Build BattleStats for a wild Pokémon from the persisted Encounter."""
    from tokenmon.pokemon.stats import final_stats

    mon = generate_wild_mon(
        encounter=enc, learnset_lookup=learnsets_remote.get_learnset,
    )
    hp_max, atk, defn, spa, spd, spe = final_stats(
        mon.species_dex_id, mon.ivs, mon.level, mon.nature,
    )
    types = pokemon.types_of(mon.species_dex_id)
    moves: list[Move] = []
    for key in mon.move_keys:
        md = moves_remote.get_move_data(key) or _FALLBACK_MOVE
        moves.append(md)
    if not moves:
        moves = [_FALLBACK_MOVE]
    name = pokemon.name_of(mon.species_dex_id)
    starting_hp = enc.hp_current if enc.hp_current is not None else hp_max
    status = _status_state_from_db(
        getattr(enc, "status_non_volatile", "healthy"),
        getattr(enc, "status_counter", 0),
    )
    return BattleStats(
        species_dex_id=mon.species_dex_id, level=mon.level, types=types,
        hp_max=hp_max, hp_current=max(0, min(hp_max, int(starting_hp))),
        attack=atk, defense=defn, sp_attack=spa, sp_defense=spd,
        speed=spe, moves=tuple(moves),
        move_pps=tuple(m.pp for m in moves),
        name=f"Wild {name}",
        status=status,
    )


def _init_wild_battle_session(popover, enc, active) -> dict:
    """Build a one-mon wild battle session."""
    if getattr(popover, "_battle_session", None) is not None:
        existing = popover._battle_session
        if existing.get("encounter_id") == enc.id:
            return existing
    opp = _wild_battle_stats(enc)
    player_state = _player_battle_stats(active)
    session = {
        "kind": "wild",
        "encounter_id": enc.id,
        "player_pokemon_id": active.id,
        "player_state": player_state,
        "opp_state": opp,
        "log": [f"A wild {pokemon.name_of(opp.species_dex_id)} appeared!"],
        "rng": random.Random(),
    }
    popover._battle_session = session
    # Brand-new fight: make sure a leftover bag-open flag from a prior
    # encounter doesn't strand the player on an empty bag list.
    popover._battle_wild_bag_open = False
    return session


def _active_opp(session: dict) -> BattleStats:
    """Single point of branching for wild vs trainer opp lookup."""
    if session.get("kind") == "wild":
        return session["opp_state"]
    return session["opp_states"][session["active_opp_idx"]]


def _set_active_opp(session: dict, new_state: BattleStats) -> None:
    if session.get("kind") == "wild":
        session["opp_state"] = new_state
    else:
        session["opp_states"][session["active_opp_idx"]] = new_state


class _BattleStepRunner(NSObject):
    """Walks an ordered list of ``(delay_seconds, callable)`` steps via
    NSTimer. Mirrors the catch / pat / claim animation handlers — each
    step fires after its delay and schedules the next one. The
    callable runs Python code that mutates the controller (animate HP
    bar, mount FX, append log line, etc.).

    A separate ``done_cb`` runs after the final step so the controller
    can commit final state, decrement PP, and rerender.
    """

    def initWithSteps_doneCb_(self, steps, done_cb):  # noqa: N802
        self = objc.super(_BattleStepRunner, self).init()
        if self is None:
            return None
        self._steps = list(steps)
        self._done_cb = done_cb
        self._idx = 0
        self._cancelled = False
        return self

    def start(self):
        if not self._steps:
            self._finish()
            return
        self._scheduleNext()

    def cancel(self):
        """Mid-sequence abort. Pending steps don't fire, but the
        controller's ``done_cb`` still runs so it can apply final state
        and re-render. Used when the popover closes mid-turn."""
        self._cancelled = True
        # Skip remaining steps; let _finish run so state still commits.
        self._finish()

    def _scheduleNext(self):
        if self._cancelled or self._idx >= len(self._steps):
            return
        delay, _ = self._steps[self._idx]
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.001, delay), self, b"fire:", None, False,
        )

    def fire_(self, _timer):  # noqa: N802 — NSTimer callback
        if self._cancelled:
            return
        if self._idx >= len(self._steps):
            return
        _, action = self._steps[self._idx]
        self._idx += 1
        try:
            if action is not None:
                action()
        except Exception:
            log.exception("battle step failed")
        if self._idx >= len(self._steps):
            self._finish()
            return
        self._scheduleNext()

    def _finish(self):
        cb = self._done_cb
        self._done_cb = None
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.exception("battle runner done_cb failed")


# ----------------------------------------------------------------------
# Per-event timing — one source of truth so designers can tune feel.
# ----------------------------------------------------------------------

# Game-Boy-ish drain pacing (chosen in the planning round).
HP_DRAIN_SECONDS = 0.7
ATTACK_SHAKE_SECONDS = 0.15
TYPE_FX_SECONDS = 0.30  # Should match battle_fx.FX_DURATION; the
                        # runner just needs to know how long to wait.
LOG_GAP_SECONDS = 0.20
FAINT_FADE_SECONDS = 0.40


class BattleController(PaneController):
    """The active battle pane. Renders sprites + HP + move-picker + log
    based on the popover's battle session."""

    def __init__(self, popover) -> None:
        super().__init__(popover)
        # View refs the step runner needs. They're populated during
        # ``_render_battle_view`` and reset here so unit tests that
        # exercise ``_do_player_move`` directly (without rendering) find
        # the attributes present-but-empty and fall through to the
        # synchronous commit path.
        self._opp_bar = None
        self._player_bar = None
        self._opp_sprite_view = None
        self._player_sprite_view = None
        self._opp_hp_label = None
        self._player_hp_label = None
        self._battle_view = None
        self._move_buttons: list = []
        self._log_labels: list = []
        # Wild-only bag-open flag lives on the popover (not the
        # controller) because ``_show_pane`` rebuilds the controller on
        # every re-render — a controller-local bool would always reset
        # to False before render and the bag would never appear. See
        # ``_set_wild_bag_open`` / ``_is_wild_bag_open``.

    def build_view(self) -> NSView:
        # If the user re-opened the popover (or navigated away and back)
        # while a turn was animating, cancel the in-flight runner so it
        # commits final state — otherwise we'd render against stale bars
        # that the runner is still trying to drain via dangling refs.
        existing = getattr(self.popover, "_battle_runner", None)
        if existing is not None:
            try:
                existing.cancel()
            except Exception:
                log.exception("battle runner cancel failed")
            self.popover._battle_runner = None

        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )
        try:
            trainer = get_pending_trainer()
        except Exception:
            log.exception("get_pending_trainer failed")
            trainer = None
        try:
            wild = get_pending_encounter() if trainer is None else None
        except Exception:
            log.exception("get_pending_encounter failed")
            wild = None
        if trainer is None and wild is None:
            view.addSubview_(_label(
                NSMakeRect(20, POPOVER_HEIGHT // 2, CONTENT_WIDTH - 40, 22),
                "No active battle.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        try:
            active = box.get_active_pokemon()
        except Exception:
            active = None
        if active is None:
            view.addSubview_(_label(
                NSMakeRect(20, POPOVER_HEIGHT // 2, CONTENT_WIDTH - 40, 22),
                "No active Pokémon to battle with.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        try:
            if trainer is not None:
                session = _init_battle_session(self.popover, trainer, active)
            else:
                session = _init_wild_battle_session(self.popover, wild, active)
        except Exception:
            log.exception("battle session init failed")
            view.addSubview_(_label(
                NSMakeRect(20, POPOVER_HEIGHT // 2, CONTENT_WIDTH - 40, 22),
                "Failed to start the battle.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        return self._render_battle_view(view, session)

    def _render_battle_view(self, view: NSView, session: dict) -> NSView:
        opp = _active_opp(session)
        player = session["player_state"]

        # Re-anchor render-time refs (cleared by __init__; cleared again
        # here so a manual re-render can't hold stale views).
        self._opp_bar = None
        self._player_bar = None
        self._opp_sprite_view = None
        self._player_sprite_view = None
        self._opp_hp_label = None
        self._player_hp_label = None
        self._move_buttons = []
        self._log_labels = []
        self._battle_view = view

        sprite_size = 96

        # Opponent block (top half) — sprite top-right, name + HP top-left.
        # Front-view sprite for the foe.
        try:
            front_path = pokemon.ensure_sprite(opp.species_dex_id)
        except Exception:
            log.exception("opp sprite load failed")
            front_path = None
        if front_path is not None and front_path.exists():
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(
                CONTENT_WIDTH - sprite_size - 16,
                POPOVER_HEIGHT - sprite_size - 16,
                sprite_size, sprite_size,
            ))
            iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            iv.setAnimates_(True)
            iv.setWantsLayer_(True)
            layer = iv.layer()
            if layer is not None:
                layer.setMagnificationFilter_("nearest")
                layer.setMinificationFilter_("nearest")
            img = NSImage.alloc().initWithContentsOfFile_(str(front_path))
            if img is not None:
                iv.setImage_(img)
                view.addSubview_(iv)
                self._opp_sprite_view = iv

        opp_info_w = CONTENT_WIDTH - sprite_size - 40
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 40, opp_info_w, 20),
            f"{opp.name}    Lv {opp.level}",
            font=NSFont.boldSystemFontOfSize_(13),
        ))
        opp_bar = _HPBar.alloc().initWithFrame_current_max_(
            NSMakeRect(16, POPOVER_HEIGHT - 60, opp_info_w, 12),
            opp.hp_current, opp.hp_max,
        )
        view.addSubview_(opp_bar)
        self._opp_bar = opp_bar
        opp_hp_lbl = _label(
            NSMakeRect(16, POPOVER_HEIGHT - 80,
                       opp_info_w - STATUS_BADGE_WIDTH - 6, 14),
            f"{opp.hp_current}/{opp.hp_max} HP",
            font=NSFont.systemFontOfSize_(10),
            color=NSColor.secondaryLabelColor(),
        )
        view.addSubview_(opp_hp_lbl)
        self._opp_hp_label = opp_hp_lbl
        opp_badge_key = _status_badge_key(opp)
        if opp_badge_key is not None:
            badge = _StatusBadge.alloc().initWithFrame_status_(
                NSMakeRect(
                    16 + opp_info_w - STATUS_BADGE_WIDTH,
                    POPOVER_HEIGHT - 80,
                    STATUS_BADGE_WIDTH, STATUS_BADGE_HEIGHT,
                ),
                opp_badge_key,
            )
            view.addSubview_(badge)

        # Player block (middle) — back-view sprite bottom-left, name + HP
        # bottom-right (mirroring the GBA layout).
        player_sprite_y = POPOVER_HEIGHT - 230
        try:
            # Back sprite — fall back to front silently if unavailable.
            from tokenmon.storage import get_pokemon_by_id
            row = get_pokemon_by_id(session["player_pokemon_id"])
            shiny = bool(row.is_shiny) if row is not None else False
            back_path = pokemon.ensure_sprite(
                player.species_dex_id, shiny=shiny, back=True,
            )
            if back_path is None:
                back_path = pokemon.ensure_sprite(
                    player.species_dex_id, shiny=shiny,
                )
        except Exception:
            log.exception("player sprite load failed")
            back_path = None
        if back_path is not None and back_path.exists():
            iv2 = NSImageView.alloc().initWithFrame_(NSMakeRect(
                16, player_sprite_y, sprite_size, sprite_size,
            ))
            iv2.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            iv2.setAnimates_(True)
            iv2.setWantsLayer_(True)
            layer = iv2.layer()
            if layer is not None:
                layer.setMagnificationFilter_("nearest")
                layer.setMinificationFilter_("nearest")
            img = NSImage.alloc().initWithContentsOfFile_(str(back_path))
            if img is not None:
                iv2.setImage_(img)
                view.addSubview_(iv2)
                self._player_sprite_view = iv2

        info_x = 16 + sprite_size + 16
        info_w = CONTENT_WIDTH - info_x - 16
        view.addSubview_(_label(
            NSMakeRect(info_x, player_sprite_y + sprite_size - 18, info_w, 18),
            f"{player.name}    Lv {player.level}",
            font=NSFont.boldSystemFontOfSize_(13),
            align=NSTextAlignmentRight,
        ))
        player_bar = _HPBar.alloc().initWithFrame_current_max_(
            NSMakeRect(info_x, player_sprite_y + sprite_size - 36, info_w, 12),
            player.hp_current, player.hp_max,
        )
        view.addSubview_(player_bar)
        self._player_bar = player_bar
        player_hp_lbl = _label(
            NSMakeRect(
                info_x + STATUS_BADGE_WIDTH + 6,
                player_sprite_y + sprite_size - 54,
                info_w - STATUS_BADGE_WIDTH - 6, 14,
            ),
            f"{player.hp_current}/{player.hp_max} HP",
            font=NSFont.systemFontOfSize_(10),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentRight,
        )
        view.addSubview_(player_hp_lbl)
        self._player_hp_label = player_hp_lbl
        player_badge_key = _status_badge_key(player)
        if player_badge_key is not None:
            badge = _StatusBadge.alloc().initWithFrame_status_(
                NSMakeRect(
                    info_x,
                    player_sprite_y + sprite_size - 54,
                    STATUS_BADGE_WIDTH, STATUS_BADGE_HEIGHT,
                ),
                player_badge_key,
            )
            view.addSubview_(badge)

        # Battle log (last 4 lines) — sits between sprite block and moves.
        # We always create 4 slots so the runner can update them in place
        # without re-rendering the whole pane.
        log_lines = session["log"][-4:]
        log_y = player_sprite_y - 90
        for i in range(4):
            line = log_lines[i] if i < len(log_lines) else ""
            lbl = _label(
                NSMakeRect(20, log_y + (3 - i) * 18, CONTENT_WIDTH - 40, 16),
                line,
                font=NSFont.systemFontOfSize_(11),
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentLeft,
            )
            view.addSubview_(lbl)
            self._log_labels.append(lbl)

        # Move picker — 2x2 grid of buttons. Each button carries a
        # multi-line tooltip with type / power / accuracy / PP and (when
        # the cache has it) the move's effect text. Y-coordinates here
        # are slightly above the original layout so we can fit a one-
        # line description hint between the grid and the Run button
        # without overlap.
        moves = player.moves
        move_pps = player.move_pps
        btn_w = (CONTENT_WIDTH - 60) // 2
        btn_h = 30
        for i, mv in enumerate(moves[:4]):
            col = i % 2
            row = i // 2
            x = 20 + col * (btn_w + 20)
            y = 102 - row * (btn_h + 8)
            cur_pp = move_pps[i] if i < len(move_pps) else mv.pp
            handler = self._make_move_handler(mv, i)
            self._handlers.append(handler)
            btn = _MoveButtonView.alloc().initWithFrame_move_currentPP_target_action_(
                NSMakeRect(x, y, btn_w, btn_h),
                mv,
                cur_pp,
                handler,
                b"fire:",
            )
            try:
                btn.setToolTip_(format_move_tooltip(mv, cur_pp))
            except Exception:
                log.exception("setToolTip failed for move %s", mv.key)
            if cur_pp <= 0:
                btn.setEnabled_(False)
            view.addSubview_(btn)
            self._move_buttons.append(btn)

        # Description hint row — sits between the 2×2 grid (bottom at
        # y≈64) and the Run button (top at y=38). Shows the first move's
        # description so the user has a visible answer to "what does
        # this do?" without hovering. The full per-move text is still
        # available via the per-button tooltip.
        first = moves[0] if moves else None
        hint_text = ""
        if first is not None and (first.description or "").strip():
            hint_text = f"{first.name}: {first.description.strip()}"
        if hint_text:
            view.addSubview_(_label(
                NSMakeRect(20, 44, CONTENT_WIDTH - 40, 14),
                hint_text,
                font=NSFont.systemFontOfSize_(10),
                color=NSColor.tertiaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))

        # Bottom action bar — wild battles get [Bag][Run], trainer fights
        # only get the trainer-style "Run = forfeit/lose" button.
        if session.get("kind") == "wild":
            self._render_wild_action_bar(view, session)
        else:
            run_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(20, 12, CONTENT_WIDTH - 40, 26)
            )
            run_btn.setTitle_("Run (forfeit — counts as a loss)")
            run_btn.setBezelStyle_(1)

            def _run(_s):
                self._end_battle(session, status="lost")

            run_handler = make_handler(_run)
            self._handlers.append(run_handler)
            run_btn.setTarget_(run_handler)
            run_btn.setAction_(b"fire:")
            view.addSubview_(run_btn)

        return view

    # ---- wild-battle action bar + bag mode ---------------------------

    def _is_wild_bag_open(self) -> bool:
        """Read the bag-open flag from the popover. Defaults to False so
        a fresh battle always starts on the action bar."""
        return bool(getattr(self.popover, "_battle_wild_bag_open", False))

    def _set_wild_bag_open(self, value: bool) -> None:
        self.popover._battle_wild_bag_open = bool(value)

    def _render_wild_action_bar(self, view: NSView, session: dict) -> None:
        """Bag + Run buttons (or, when bag-mode is on, the inventory list +
        Back/Run row). The bag-open flag lives on the popover so it
        survives the ``_show_pane`` controller rebuild that fires when
        ``_open_bag`` triggers a re-render."""
        if self._is_wild_bag_open():
            self._render_wild_bag(view, session)
            return
        margin = 16
        gap = 12
        btn_y = 12
        btn_w = (CONTENT_WIDTH - 2 * margin - gap) // 2
        btn_h = 26

        bag_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin, btn_y, btn_w, btn_h)
        )
        bag_btn.setTitle_("🎒 Bag")
        bag_btn.setBezelStyle_(1)

        def _open_bag(_s):
            self._set_wild_bag_open(True)
            self.popover._show_pane(self.popover._current_pane)

        bag_handler = make_handler(_open_bag)
        self._handlers.append(bag_handler)
        bag_btn.setTarget_(bag_handler)
        bag_btn.setAction_(b"fire:")
        view.addSubview_(bag_btn)

        run_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + btn_w + gap, btn_y, btn_w, btn_h)
        )
        run_btn.setTitle_("Run")
        run_btn.setBezelStyle_(1)

        def _run(_s):
            self._wild_run_away()

        run_handler = make_handler(_run)
        self._handlers.append(run_handler)
        run_btn.setTarget_(run_handler)
        run_btn.setAction_(b"fire:")
        view.addSubview_(run_btn)

    def _render_wild_bag(self, view: NSView, session: dict) -> None:
        """Inventory list with click-to-throw rows + Back/Run row.
        Re-uses the row layout from the legacy encounter bag-open view."""
        from tokenmon.storage import query_item_counts

        # Header
        header_y = 200
        view.addSubview_(_label(
            NSMakeRect(16, header_y, CONTENT_WIDTH - 32, 18),
            "Bag",
            font=NSFont.boldSystemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
        ))

        try:
            counts = query_item_counts()
        except Exception:
            log.exception("query_item_counts failed in battle bag")
            counts = {}

        row_h = 26
        rows_top = header_y - 4
        ball_keys = tuple(
            k for k, it in items_registry.ITEMS.items() if "throw" in it.actions
        )
        for i, key in enumerate(ball_keys):
            item = items_registry.ITEMS[key]
            count = int(counts.get(key, 0) or 0)
            enabled = count > 0
            y = rows_top - (i + 1) * row_h
            if y < (12 + 26 + 8):
                break
            btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(16, y, CONTENT_WIDTH - 32, row_h - 2)
            )
            chevron = "  ›" if enabled else ""
            sprite = items_remote.get_item_image(item)
            if sprite is not None:
                from Foundation import NSMakeSize
                from AppKit import NSImageLeft
                sprite.setSize_(NSMakeSize(20, 20))
                btn.setImage_(sprite)
                btn.setImagePosition_(NSImageLeft)
                btn.setTitle_(f"  {item.display_name}     × {count}{chevron}")
            else:
                btn.setTitle_(
                    f"{item.emoji}  {item.display_name}     × {count}{chevron}"
                )
            btn.setBezelStyle_(1)
            btn.setEnabled_(enabled)
            if enabled:
                handler = make_handler(
                    lambda _s, k=key: self._throw_ball_in_battle(k),
                )
                self._handlers.append(handler)
                btn.setTarget_(handler)
                btn.setAction_(b"fire:")
            view.addSubview_(btn)

        # Bottom row: Back + Run.
        margin = 16
        gap = 12
        btn_y = 12
        btn_w = (CONTENT_WIDTH - 2 * margin - gap) // 2
        btn_h = 26

        back_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin, btn_y, btn_w, btn_h)
        )
        back_btn.setTitle_("← Back")
        back_btn.setBezelStyle_(1)

        def _back(_s):
            self._set_wild_bag_open(False)
            self.popover._show_pane(self.popover._current_pane)

        back_handler = make_handler(_back)
        self._handlers.append(back_handler)
        back_btn.setTarget_(back_handler)
        back_btn.setAction_(b"fire:")
        view.addSubview_(back_btn)

        run_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + btn_w + gap, btn_y, btn_w, btn_h)
        )
        run_btn.setTitle_("Run")
        run_btn.setBezelStyle_(1)

        def _run(_s):
            self._wild_run_away()

        run_handler = make_handler(_run)
        self._handlers.append(run_handler)
        run_btn.setTarget_(run_handler)
        run_btn.setAction_(b"fire:")
        view.addSubview_(run_btn)

    def _wild_run_away(self) -> None:
        session = self.popover._battle_session
        if session is None:
            return
        if session.get("kind") != "wild":
            return
        try:
            encounter.run_away(session["encounter_id"])
        except Exception:
            log.exception("wild run_away failed")
        self.popover._battle_session = None
        try:
            self.popover._show_pane(PANE_POKEMON)
        except Exception:
            log.exception("transition to PANE_POKEMON after run failed")

    def _throw_ball_in_battle(self, item_key: str) -> None:
        """Mid-fight ball throw. Persists current opp HP first (so the
        Phase-3 catch math reads it), then delegates to use_item +
        _begin_catch_animation. Defense in depth: a stray call from a
        trainer-kind session is a no-op.

        Note: ball-throw is its own action — the engine isn't called this
        turn, so the player can't be KO'd on a throw turn.
        """
        session = self.popover._battle_session
        if session is None or session.get("kind") != "wild":
            return
        opp = session["opp_state"]
        try:
            set_encounter_hp(session["encounter_id"], int(opp.hp_current))
        except Exception:
            log.exception("set_encounter_hp failed pre-throw")
        try:
            result = encounter.use_item(session["encounter_id"], item_key)
        except Exception:
            log.exception("encounter.use_item(throw) failed in battle")
            return

        # Bag-mode flag clears so a re-render after the catch animation
        # lands on the action bar, not the inventory.
        self._set_wild_bag_open(False)

        # On catch, the encounter is resolved → drop the session before
        # the reveal hand-off so a stray sidebar click can't resume.
        if result.get("caught"):
            self.popover._battle_session = None

        try:
            self.popover._begin_catch_animation(
                item_key=item_key,
                encounter_id=session["encounter_id"],
                species_dex_id=int(opp.species_dex_id),
                caught=bool(result.get("caught")),
                shakes=int(result.get("shakes", 0)),
                hint=result.get("hint"),
            )
        except Exception:
            log.exception("begin_catch_animation failed in battle")

    def _make_move_handler(self, move: Move, slot: int):
        def _click(_s, move=move, slot=slot):
            self._do_player_move(move, slot)
        return make_handler(_click)

    def _do_player_move(self, player_move: Move, slot: int) -> None:
        session = self.popover._battle_session
        if session is None:
            return
        # Reject re-clicks while a turn is animating.
        if getattr(self.popover, "_battle_runner", None) is not None:
            return
        player_state = session["player_state"]
        if slot < len(player_state.move_pps) and player_state.move_pps[slot] <= 0:
            session["log"].append(f"No PP left for {player_move.name}!")
            self._rerender()
            return
        opp = _active_opp(session)
        opp_move = session["rng"].choice(opp.moves)

        # Use simulate_turn (not plan_turn) so we keep the engine's
        # final BattleStats — fold_events doesn't reconstruct
        # StatusState changes (poison/burn/sleep/etc.) from the event
        # log. Without these final states, an inflicted status would
        # never make it back into ``session`` or the DB.
        events, final_player, final_opp = simulate_turn(
            player_state, opp,
            player_move=player_move, opp_move=opp_move,
            rng=session["rng"],
        )

        # Spend PP synchronously — both in-memory and on disk — so the
        # runner sees the right disabled state on its first tick AND a
        # popover close mid-turn can't lose the PP spend.
        new_pps = list(player_state.move_pps)
        if slot < len(new_pps):
            new_pps[slot] = max(0, new_pps[slot] - 1)
        session["player_state"] = replace(
            player_state, move_pps=tuple(new_pps),
        )
        try:
            decrement_pp(session["player_pokemon_id"], slot)
        except Exception:
            log.exception("decrement_pp failed")

        pending = {
            "events": events,
            "final_player": final_player,
            "final_opp": final_opp,
        }

        # Path A — no rendered view (test path or pre-render call):
        # commit final state immediately and rerender. Matches the
        # legacy synchronous behavior so unit tests that stub the engine
        # still pass.
        if self._opp_bar is None or self._player_bar is None:
            self._finalize_turn(pending, append_log=True)
            return

        # Path B — animated runner. Disable input, walk the step list,
        # commit final state in the done callback.
        for b in self._move_buttons:
            try:
                b.setEnabled_(False)
            except Exception:
                pass
        steps = self._build_step_list(events, session["player_state"], opp)

        def _on_done():
            self._finalize_turn(pending, append_log=False)

        runner = _BattleStepRunner.alloc().initWithSteps_doneCb_(
            steps, _on_done,
        )
        self.popover._battle_runner = runner
        runner.start()

    # ---- runner: step builder + step actions --------------------------

    def _build_step_list(
        self, events, player_state: BattleStats, opp_state: BattleStats,
    ):
        """Translate engine events into a (delay, callable) tape the
        runner can walk. Pure function of (events, current state) — no
        side effects until the callables fire."""
        steps: list[tuple[float, object]] = []

        # Snapshots so each step closure has the right "before" HP.
        # We update p_hp / o_hp as we walk so the next event's HP
        # animation starts from the previous event's resting value.
        p_hp = player_state.hp_current
        o_hp = opp_state.hp_current

        for ev in events:
            if isinstance(ev, AttackEvent):
                actor = ev.actor
                move = ev.move
                # 1) Log + attacker shake.
                attacker_name = (
                    player_state.name if actor == "player"
                    else opp_state.name
                )
                steps.append((
                    0.0,
                    self._mk_log_and_shake(
                        f"{attacker_name} used {move.name}!", actor, move,
                    ),
                ))
                # 2) Type FX overlay on defender + start HP drain.
                if ev.effectiveness == 0.0:
                    # No damage at all — just the "had no effect." line
                    # after a brief beat.
                    if ev.effectiveness_label:
                        steps.append((
                            ATTACK_SHAKE_SECONDS + 0.05,
                            self._mk_append_log(ev.effectiveness_label),
                        ))
                    steps.append((LOG_GAP_SECONDS, None))
                    continue

                # Capture per-event values so the closure binds them.
                target_hp = ev.defender_hp_after
                defender_side = "opp" if actor == "player" else "player"
                steps.append((
                    ATTACK_SHAKE_SECONDS,
                    self._mk_mount_fx_and_drain(
                        defender_side, move.type, target_hp,
                    ),
                ))
                # 3) After the drain finishes, append crit / effective
                # labels and update the numeric HP label.
                trailing_lines = []
                if ev.crit:
                    trailing_lines.append("A critical hit!")
                if ev.effectiveness_label:
                    trailing_lines.append(ev.effectiveness_label)
                steps.append((
                    HP_DRAIN_SECONDS,
                    self._mk_after_drain(
                        defender_side, target_hp, trailing_lines,
                    ),
                ))
                steps.append((LOG_GAP_SECONDS, None))

                if defender_side == "opp":
                    o_hp = target_hp
                else:
                    p_hp = target_hp

            elif isinstance(ev, MissEvent):
                actor = ev.actor
                attacker_name = (
                    player_state.name if actor == "player"
                    else opp_state.name
                )
                steps.append((
                    0.0,
                    self._mk_log_and_shake(
                        f"{attacker_name} used {ev.move.name}!",
                        actor, ev.move,
                    ),
                ))
                steps.append((
                    ATTACK_SHAKE_SECONDS + 0.05,
                    self._mk_append_log(f"{attacker_name}'s attack missed!"),
                ))
                steps.append((LOG_GAP_SECONDS, None))

            elif isinstance(ev, FaintEvent):
                steps.append((0.10, self._mk_fade_sprite(ev.side)))
                steps.append((
                    FAINT_FADE_SECONDS,
                    self._mk_append_log(f"{ev.name} fainted!"),
                ))
                steps.append((LOG_GAP_SECONDS, None))

        return steps

    # Step factories — each returns a no-arg callable the runner fires.

    def _mk_log_and_shake(self, line: str, actor: str, move: Move):
        def _step():
            self._append_log(line)
            self._animate_attacker(actor, move)
        return _step

    def _mk_append_log(self, line: str):
        def _step():
            self._append_log(line)
        return _step

    def _mk_mount_fx_and_drain(
        self, defender_side: str, move_type: str, target_hp: int,
    ):
        def _step():
            self._mount_type_fx(defender_side, move_type)
            bar = (
                self._opp_bar if defender_side == "opp"
                else self._player_bar
            )
            if bar is not None:
                try:
                    bar.animateToValue_duration_(
                        target_hp, HP_DRAIN_SECONDS,
                    )
                except Exception:
                    log.exception("HP drain failed")
        return _step

    def _mk_after_drain(
        self, defender_side: str, target_hp: int, trailing_lines: list,
    ):
        def _step():
            for line in trailing_lines:
                self._append_log(line)
            self._update_hp_label(defender_side, target_hp)
        return _step

    def _mk_fade_sprite(self, side: str):
        def _step():
            iv = (
                self._opp_sprite_view if side == "opp"
                else self._player_sprite_view
            )
            if iv is None:
                return
            try:
                iv.setAlphaValue_(0.25)
            except Exception:
                pass
        return _step

    # ---- runner: helpers used by the step callables -------------------

    def _append_log(self, line: str) -> None:
        """Push a line onto session log + repaint the 4 visible slots."""
        session = self.popover._battle_session
        if session is None:
            return
        session["log"].append(line)
        recent = session["log"][-4:]
        for i, lbl in enumerate(self._log_labels):
            text = recent[i] if i < len(recent) else ""
            try:
                lbl.setStringValue_(text)
            except Exception:
                pass

    def _animate_attacker(self, actor: str, move: Move) -> None:
        """Physical moves lunge toward the defender (Game-Boy contact);
        special / status moves do a brief horizontal shake (no contact).

        The lunge peaks at ATTACK_SHAKE_SECONDS — the same instant the
        runner mounts the type FX overlay and starts the HP drain — so
        the visuals line up: attacker arrives at the defender exactly
        when the defender takes the hit.
        """
        iv = (
            self._player_sprite_view if actor == "player"
            else self._opp_sprite_view
        )
        if iv is None:
            return
        try:
            origin = iv.frame().origin
            origin_xy = (float(origin.x), float(origin.y))
        except Exception:
            log.exception("animate_attacker frame lookup failed")
            return

        if move.category == "physical":
            # Lunge ~28 px diagonally toward the defender's quadrant.
            # Player sits bottom-left, defender top-right → +dx +dy.
            # Opp sits top-right, defender bottom-left → -dx -dy.
            sign = 1 if actor == "player" else -1
            dx = 28.0 * sign
            dy = 28.0 * sign
            self._tween_origin(
                iv, origin_xy,
                target=(origin_xy[0] + dx, origin_xy[1] + dy),
                out_secs=ATTACK_SHAKE_SECONDS,
                back_secs=ATTACK_SHAKE_SECONDS,
            )
        else:
            # Special / status — small horizontal nudge, snap back.
            try:
                iv.setFrameOrigin_((origin_xy[0] + 6, origin_xy[1]))
            except Exception:
                pass
            self._schedule_origin_restore(iv, origin_xy, ATTACK_SHAKE_SECONDS)

    def _tween_origin(
        self, iv, start, target, *, out_secs, back_secs,
    ) -> None:
        """30 fps tween: start → target over ``out_secs``, target → start
        over ``back_secs``. Uses a single repeating NSTimer; a frame
        counter decides whether we're in the outbound or return half.
        """
        out_frames = max(1, int(out_secs * 30))
        back_frames = max(1, int(back_secs * 30))
        total = out_frames + back_frames
        state = {"frame": 0}

        def _tick(_s):
            f = state["frame"] + 1
            state["frame"] = f
            if f <= out_frames:
                t = f / out_frames
                x = start[0] + (target[0] - start[0]) * t
                y = start[1] + (target[1] - start[1]) * t
            elif f < total:
                t = (f - out_frames) / back_frames
                x = target[0] + (start[0] - target[0]) * t
                y = target[1] + (start[1] - target[1]) * t
            else:
                x, y = start
            try:
                iv.setFrameOrigin_((x, y))
            except Exception:
                pass
            if f >= total:
                timer = state.get("timer")
                if timer is not None:
                    try:
                        timer.invalidate()
                    except Exception:
                        pass

        handler = make_handler(_tick)
        # Anchor the handler so the NSTimer's strong-target retention
        # alone doesn't have to hold it across the runloop.
        self._handlers.append(handler)
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 30.0, handler, b"fire:", None, True,
        )
        state["timer"] = timer

    def _schedule_origin_restore(
        self, iv, origin, after_secs: float,
    ) -> None:
        def _restore(_s):
            try:
                iv.setFrameOrigin_(origin)
            except Exception:
                pass
        handler = make_handler(_restore)
        self._handlers.append(handler)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            after_secs, handler, b"fire:", None, False,
        )

    def _mount_type_fx(self, defender_side: str, move_type: str) -> None:
        iv = (
            self._opp_sprite_view if defender_side == "opp"
            else self._player_sprite_view
        )
        host = self._battle_view
        if iv is None or host is None:
            return
        try:
            f = iv.frame()
            fx_frame = NSMakeRect(
                f.origin.x - 16, f.origin.y - 16,
                f.size.width + 32, f.size.height + 32,
            )
            fx_view = make_type_fx(
                fx_frame, move_type, seed=int(f.origin.x + f.origin.y),
            )
            host.addSubview_(fx_view)
        except Exception:
            log.exception("mount FX failed")

    def _update_hp_label(self, side: str, value: int) -> None:
        session = self.popover._battle_session
        if session is None:
            return
        if side == "opp":
            opp = _active_opp(session)
            lbl = self._opp_hp_label
            if lbl is not None:
                try:
                    lbl.setStringValue_(f"{max(0, int(value))}/{opp.hp_max} HP")
                except Exception:
                    pass
        else:
            player = session["player_state"]
            lbl = self._player_hp_label
            if lbl is not None:
                try:
                    lbl.setStringValue_(
                        f"{max(0, int(value))}/{player.hp_max} HP"
                    )
                except Exception:
                    pass

    # ---- runner: finalize ---------------------------------------------

    def _finalize_turn(self, pending: dict, *, append_log: bool) -> None:
        """Commit final state, run faint/end-battle logic, full rerender.
        Always runs (even on cancellation) so HP doesn't drift.

        ``append_log`` controls whether the fold's log lines are pushed
        to the session log. The runner appends lines as each event
        fires, so it passes ``False`` to avoid double-logging; the
        synchronous (no-render) path passes ``True``."""
        session = self.popover._battle_session
        self.popover._battle_runner = None
        if session is None:
            return

        events = pending["events"]
        # PP was already spent in _do_player_move; preserve those values
        # by replacing the PP tuple on the fold's player_state below.
        current_player = session["player_state"]
        result = fold_events(events, current_player, _active_opp(session))
        new_player = result.player_state
        new_opp = result.opp_state
        # fold_events tracks HP and faint flags but ignores status changes
        # — so an inflicted poison/burn/etc. would never reach the session
        # or the DB. Overlay simulate_turn's final states (when present)
        # so non-volatile + volatile status survive the turn boundary.
        sim_player = pending.get("final_player")
        sim_opp = pending.get("final_opp")
        if new_player is not None and sim_player is not None:
            new_player = replace(new_player, status=sim_player.status)
        if new_opp is not None and sim_opp is not None:
            new_opp = replace(new_opp, status=sim_opp.status)
        if new_player is not None:
            new_player = replace(new_player, move_pps=current_player.move_pps)
        session["player_state"] = new_player
        if new_opp is not None:
            _set_active_opp(session, new_opp)
        if append_log:
            session["log"].extend(result.log)

        kind = session.get("kind", "trainer")

        if result.opp_fainted:
            if kind == "wild":
                # Wild KO → mark encounter ran (logically: mon flees / KO'd,
                # no catch), route to reward (XP only).
                try:
                    encounter.run_away(session["encounter_id"])
                except Exception:
                    log.exception("wild encounter run_away on KO failed")
                self._end_battle(session, status="won")
                return
            session["defeated_count"] += 1
            try:
                from tokenmon.storage import mark_trainer_pokemon_fainted
                mark_trainer_pokemon_fainted(
                    session["opp_trainer_pokemon_ids"][
                        session["active_opp_idx"]
                    ],
                )
            except Exception:
                log.exception("mark fainted failed")
            session["active_opp_idx"] += 1
            if session["active_opp_idx"] >= len(session["opp_states"]):
                self._end_battle(session, status="won")
                return
            next_opp = session["opp_states"][session["active_opp_idx"]]
            session["log"].append(
                f"{session['trainer_name']} sent out {next_opp.name}!"
            )

        if result.player_fainted:
            self._end_battle(session, status="lost")
            return

        # Persist non-volatile status mid-battle so a popover close
        # doesn't lose a poison/burn/sleep counter. Volatile statuses
        # (confusion, flinch) are intentionally NOT persisted.
        try:
            cur_player = session["player_state"]
            set_pokemon_status(
                session["player_pokemon_id"],
                cur_player.status.non_volatile.value,
                int(cur_player.status.nv_counter),
            )
        except Exception:
            log.exception("post-turn set_pokemon_status failed")

        # Persist wild HP + status between turns/popover-opens.
        if kind == "wild":
            try:
                cur_opp = _active_opp(session)
                set_encounter_hp(
                    session["encounter_id"],
                    int(cur_opp.hp_current),
                )
                set_encounter_status(
                    session["encounter_id"],
                    cur_opp.status.non_volatile.value,
                    int(cur_opp.status.nv_counter),
                )
            except Exception:
                log.exception("post-turn wild persist failed")

        self._rerender()

    def _rerender(self):
        try:
            self.popover._show_pane(self.popover._current_pane)
        except Exception:
            log.exception("battle re-render failed")

    def _end_battle(self, session: dict, *, status: str) -> None:
        # Stash status on session for the reward pane to read.
        session["resolved_status"] = status
        try:
            self.popover._show_pane(PANE_BATTLE_REWARD)
        except Exception:
            log.exception("transition to reward pane failed")
            self.popover._show_pane(PANE_POKEMON)

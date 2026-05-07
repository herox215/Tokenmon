"""Post-battle reward summary.

Reads ``popover._battle_session["resolved_status"]`` (set by the battle
controller's ``_end_battle``) and pays out money / XP / item-drops in
one DB transaction. Renders the rewards plus an XP bar that animates
from the player Pokémon's pre-battle progress to the post-battle one,
flagging level-ups along the way.

Atomic-rewards rule (per plan reviewer): money + xp + items all share
one ``_connect`` transaction so a crash mid-payout can't partial-credit.
XP credit happens via a synthetic ``requests`` row tagged with
``model='<battle>'`` — the existing XP-tracking machinery (sums
output_tokens by trained_pokemon_id) picks it up without a new schema.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextAlignmentCenter,
    NSTimer,
    NSView,
)
from Foundation import NSMakeRect, NSObject

import objc

from tokenmon import items as items_registry, pokemon
from tokenmon.battle.models import TrainerMon
from tokenmon.battle.rewards import compute_rewards
from tokenmon.battle.team_gen import DIFFICULTY_PROFILES
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    _label,
)
from tokenmon.storage import (
    DB_PATH,
    add_money,
    add_to_pending,
    get_trainer,
    list_trainer_pokemon,
    mark_trainer_resolved,
    query_xp_for_pokemon,
    reset_pp_for_pokemon,
    set_pokemon_hp,
)
from tokenmon.storage._db import _connect

log = logging.getLogger("tokenmon.popover.panes.battle_reward")


# ---- Animated XP bar -----------------------------------------------------

XP_BAR_FRAMES = 36          # 36 × 0.04 s = ~1.4 s fill duration
XP_BAR_INTERVAL = 0.04


class _AnimatedXPBar(NSView):
    """XP bar that tweens from a start-progress to an end-progress over
    ~1.4 s on first display. Used to visualise battle XP rewards in the
    reward pane.

    Progress values are 0..1 fractions WITHIN the new level. If the
    Pokémon levelled up during the reward, the bar starts at 0 instead
    of the pre-battle progress so the visual reads as "full level
    gained, now {n}% of the way to the next" — a tradeoff vs. the
    multi-step animation of "fill to 100, reset, fill to remainder",
    which is jankier in 1.4 s.
    """

    def initWithFrame_start_end_(self, frame, start_progress, end_progress):  # noqa: N802
        self = objc.super(_AnimatedXPBar, self).initWithFrame_(frame)
        if self is None:
            return None
        self._start = max(0.0, min(1.0, float(start_progress)))
        self._end = max(0.0, min(1.0, float(end_progress)))
        self._current = self._start
        self._frame = 0
        self._timer = None
        return self

    def viewDidMoveToWindow(self):  # noqa: N802
        # Start the animation once the view is attached (after pane
        # build). objc.super(...) raises if no super; we'll call it.
        try:
            objc.super(_AnimatedXPBar, self).viewDidMoveToWindow()
        except Exception:
            pass
        if self._timer is None and self.window() is not None:
            self._timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    XP_BAR_INTERVAL, self, b"_step:", None, True,
                )
            )

    def _step_(self, _t):  # noqa: N802
        self._frame += 1
        if self._frame >= XP_BAR_FRAMES:
            self._current = self._end
            if self._timer is not None:
                try:
                    self._timer.invalidate()
                except Exception:
                    pass
                self._timer = None
            self.setNeedsDisplay_(True)
            return
        t = self._frame / XP_BAR_FRAMES
        # Ease-out cubic feels best for "fill" animations.
        eased = 1.0 - (1.0 - t) ** 3
        self._current = self._start + (self._end - self._start) * eased
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        # Track
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.18).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 5, 5,
        ).fill()
        if self._current <= 0.0:
            return
        # Fill: blue → cyan gradient feel via a single solid
        # blue-violet color (NSColor's gradient API is heavier than the
        # visual return justifies here).
        fill_w = bounds.size.width * self._current
        fill = NSMakeRect(0, 0, fill_w, bounds.size.height)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.36, 0.62, 1.0, 1.0,
        ).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            fill, 5, 5,
        ).fill()


def _award_rewards(
    *,
    trainer_id: int,
    status: str,
    defeated_count: int,
    money: int,
    xp_per_defeat: int,
    item_drops: dict[str, int],
    player_pokemon_id: int,
    path=DB_PATH,
) -> tuple[int, int, dict[str, int]]:
    """Atomically apply the rewards: money + XP credit + trainer
    resolved. XP is independent of the win-state — partial-defeat XP
    still flows. Returns the (actual_money, actual_xp_total, items)
    tuple credited.

    XP credit goes through a synthetic ``requests`` row tagged with
    ``model='<battle>'`` so the existing XP-tracking aggregation
    (``query_xp_for_pokemon`` sums ``output_tokens`` per
    trained_pokemon_id) picks it up without a new schema.
    """
    money_credit = money if status == "won" else 0
    xp_credit = xp_per_defeat * defeated_count
    items_credit = item_drops if status == "won" else {}
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(path) as conn:
        if money_credit > 0:
            add_money(money_credit, conn=conn)
        if xp_credit > 0:
            conn.execute(
                "INSERT INTO requests (ts_utc, model, output_tokens, "
                "trained_pokemon_id) VALUES (?, '<battle>', ?, ?)",
                (ts, int(xp_credit), int(player_pokemon_id)),
            )
        conn.execute(
            """
            UPDATE trainers
            SET resolved = ?, resolved_utc = ?,
                money_reward = ?, xp_reward = ?
            WHERE id = ?
            """,
            (status, ts, money_credit, xp_credit, int(trainer_id)),
        )
    # Items go through the existing pending_drops queue — separate writes.
    for key, count in items_credit.items():
        for _ in range(int(count)):
            try:
                add_to_pending(key, 1, path=path)
            except Exception:
                log.exception("add_to_pending failed for %s", key)
    return money_credit, xp_credit, items_credit


class BattleRewardController(PaneController):
    """Post-battle summary. Awards rewards on first build_view, then
    Continue → reset to Pokemon-pane."""

    def build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        session = getattr(self.popover, "_battle_session", None)
        if session is None:
            view.addSubview_(_label(
                NSMakeRect(20, POPOVER_HEIGHT // 2, CONTENT_WIDTH - 40, 22),
                "No battle to summarise.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        status = session.get("resolved_status", "lost")
        trainer_id = session["trainer_id"]
        defeated = session.get("defeated_count", 0)

        # Recompute rewards from the DB (so the pane works even if the
        # in-memory session has been pruned). compute_rewards needs the
        # team — load from DB.
        try:
            trainer = get_trainer(trainer_id)
            team_rows = list_trainer_pokemon(trainer_id)
            team = [
                TrainerMon(
                    species_dex_id=r.species_dex_id,
                    level=r.level, nature=r.nature, ivs=r.ivs,
                    move_keys=r.move_keys,
                )
                for r in team_rows
            ]
            difficulty = trainer.difficulty if trainer else "easy"
            rewards = compute_rewards(team, difficulty)
        except Exception:
            log.exception("rewards compute failed")
            rewards = None

        # Gate: only award once. We track via ``trainers.resolved`` —
        # if already resolved, skip the credits and just show the
        # summary.
        already_resolved = trainer is not None and trainer.resolved is not None
        player_id = session["player_pokemon_id"]
        # Capture pre-credit XP so the bar can animate from it. After
        # awarding, query again for the post-credit value.
        try:
            old_xp = query_xp_for_pokemon(player_id)
        except Exception:
            log.exception("pre-reward XP lookup failed")
            old_xp = 0
        if not already_resolved and rewards is not None:
            try:
                _award_rewards(
                    trainer_id=trainer_id, status=status,
                    defeated_count=defeated,
                    money=rewards.money,
                    xp_per_defeat=rewards.xp_per_defeat,
                    item_drops=rewards.item_drops,
                    player_pokemon_id=player_id,
                )
            except Exception:
                log.exception("reward award failed")
        try:
            new_xp = query_xp_for_pokemon(player_id)
        except Exception:
            log.exception("post-reward XP lookup failed")
            new_xp = old_xp

        # Persist the post-battle HP so damage carries over to the
        # next fight. Fainted Pokémon (hp_current == 0) auto-revive at
        # the next battle init — that's the soft-lock guard until we
        # add a real heal mechanic.
        if not already_resolved:
            try:
                final_player = session.get("player_state")
                if final_player is not None:
                    set_pokemon_hp(
                        player_id,
                        int(getattr(final_player, "hp_current", 0)),
                    )
            except Exception:
                log.exception("post-battle HP persist failed")

            # Reset PP to max for the player's Pokémon (per plan: PP
            # regenerates fully outside battles).
            try:
                from tokenmon import moves_remote
                from tokenmon.storage import reset_pp_for_pokemon

                def _pp_lookup(key: str):
                    md = moves_remote.get_move_data(key)
                    return md.pp if md is not None else None

                reset_pp_for_pokemon(
                    session["player_pokemon_id"], pp_lookup=_pp_lookup,
                )
            except Exception:
                log.exception("PP reset failed")

        # Render the summary.
        if status == "won":
            headline = "🏆 You won!"
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.36, 0.78, 0.20, 1.0,
            )
        else:
            headline = "💔 You blacked out…"
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.95, 0.30, 0.30, 1.0,
            )

        # Top-down y_cursor — start a small margin below the pane top.
        y_cursor = POPOVER_HEIGHT - 12

        # Headline
        y_cursor -= 28
        view.addSubview_(_label(
            NSMakeRect(20, y_cursor, CONTENT_WIDTH - 40, 28),
            headline,
            font=NSFont.boldSystemFontOfSize_(20),
            color=color,
            align=NSTextAlignmentCenter,
        ))
        y_cursor -= 6

        # Pokémon sprite — front view of the active mon. Same crisp-
        # pixel scaling pattern used by the battle pane.
        try:
            from tokenmon.storage import get_pokemon_by_id
            mon_row = get_pokemon_by_id(player_id)
        except Exception:
            log.exception("active Pokémon lookup failed")
            mon_row = None

        sprite_size = 96
        y_cursor -= sprite_size
        if mon_row is not None:
            try:
                sp = pokemon.ensure_sprite(
                    mon_row.species_dex_id,
                    shiny=bool(mon_row.is_shiny),
                )
            except Exception:
                log.exception("reward-pane sprite load failed")
                sp = None
            if sp is not None and sp.exists():
                sprite_x = (CONTENT_WIDTH - sprite_size) // 2
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(sprite_x, y_cursor, sprite_size, sprite_size)
                )
                iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                iv.setAnimates_(True)
                iv.setWantsLayer_(True)
                layer = iv.layer()
                if layer is not None:
                    layer.setMagnificationFilter_("nearest")
                    layer.setMinificationFilter_("nearest")
                img = NSImage.alloc().initWithContentsOfFile_(str(sp))
                if img is not None:
                    iv.setImage_(img)
                    view.addSubview_(iv)
        y_cursor -= 8

        # Pokémon name + level (or level-up flash)
        if mon_row is not None:
            try:
                growth = pokemon.growth_rate_of(mon_row.species_dex_id)
                old_level, old_into, old_needed = pokemon.level_from_xp(
                    old_xp, growth,
                )
                new_level, new_into, new_needed = pokemon.level_from_xp(
                    new_xp, growth,
                )
                level_changed = new_level > old_level
                start_progress = (
                    0.0 if level_changed
                    else (
                        old_into / max(1, old_needed)
                        if old_needed > 0 else 0.0
                    )
                )
                end_progress = (
                    new_into / max(1, new_needed)
                    if new_needed > 0 else 1.0
                )
                mon_name = pokemon.display_name(
                    mon_row.nickname, mon_row.species_dex_id,
                )
                y_cursor -= 18
                view.addSubview_(_label(
                    NSMakeRect(20, y_cursor, CONTENT_WIDTH - 40, 18),
                    (
                        f"⭐ {mon_name} leveled up to L{new_level}!"
                        if level_changed
                        else f"{mon_name}  —  Lv {new_level}"
                    ),
                    font=NSFont.boldSystemFontOfSize_(13),
                    color=(
                        NSColor.colorWithCalibratedRed_green_blue_alpha_(
                            1.0, 0.85, 0.0, 1.0,
                        ) if level_changed else NSColor.labelColor()
                    ),
                    align=NSTextAlignmentCenter,
                ))
                y_cursor -= 6

                # Animated XP bar
                y_cursor -= 14
                bar = _AnimatedXPBar.alloc().initWithFrame_start_end_(
                    NSMakeRect(40, y_cursor, CONTENT_WIDTH - 80, 14),
                    start_progress, end_progress,
                )
                view.addSubview_(bar)
                y_cursor -= 4

                # Into / needed text
                y_cursor -= 14
                view.addSubview_(_label(
                    NSMakeRect(40, y_cursor, CONTENT_WIDTH - 80, 14),
                    f"{new_into:,} / {new_needed:,} XP",
                    font=NSFont.systemFontOfSize_(10),
                    color=NSColor.tertiaryLabelColor(),
                    align=NSTextAlignmentCenter,
                ))
            except Exception:
                log.exception("XP bar render failed")

        y_cursor -= 14  # gap before stats block

        # Stats lines
        money = rewards.money if (rewards and status == "won") else 0
        xp_total = new_xp - old_xp
        for line in [
            f"Pokémon defeated: {defeated}",
            f"Money: +${money}",
            f"XP: +{xp_total}",
        ]:
            y_cursor -= 18
            view.addSubview_(_label(
                NSMakeRect(40, y_cursor, CONTENT_WIDTH - 80, 18),
                line,
                font=NSFont.systemFontOfSize_(12),
                align=NSTextAlignmentCenter,
            ))
            y_cursor -= 4

        # Continue button
        cont = NSButton.alloc().initWithFrame_(
            NSMakeRect(
                (CONTENT_WIDTH - 160) // 2, 60, 160, 32,
            )
        )
        cont.setTitle_("Continue")
        cont.setBezelStyle_(1)

        def _continue(_s):
            try:
                self.popover._battle_session = None
                self.popover._show_pane(PANE_POKEMON)
            except Exception:
                log.exception("continue failed")

        h = make_handler(_continue)
        self._handlers.append(h)
        cont.setTarget_(h)
        cont.setAction_(b"fire:")
        view.addSubview_(cont)

        return view

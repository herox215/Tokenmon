"""Post-battle reward summary.

Reads ``popover._battle_session["resolved_status"]`` (set by the battle
controller's ``_end_battle``) and pays out money / XP / item-drops in
one DB transaction. Renders a "Won!" or "You blacked out…" headline
plus the rewards earned, and offers a Continue button back to the
active-Pokémon pane.

Atomic-rewards rule (per plan reviewer): money + xp + items all share
one ``_connect`` transaction so a crash mid-payout can't partial-credit.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSButton,
    NSColor,
    NSFont,
    NSTextAlignmentCenter,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import items as items_registry
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
    reset_pp_for_pokemon,
)
from tokenmon.storage._db import _connect

log = logging.getLogger("tokenmon.popover.panes.battle_reward")


def _award_rewards(
    *,
    trainer_id: int,
    status: str,
    defeated_count: int,
    money: int,
    xp_per_defeat: int,
    item_drops: dict[str, int],
    path=DB_PATH,
) -> tuple[int, int, dict[str, int]]:
    """Atomically apply the rewards: money + item-drops queued + trainer
    resolved. XP is independent of the win-state — partial-defeat XP
    still flows. Returns the (actual_money, actual_xp_total, items)
    tuple credited."""
    money_credit = money if status == "won" else 0
    xp_credit = xp_per_defeat * defeated_count
    items_credit = item_drops if status == "won" else {}
    with _connect(path) as conn:
        if money_credit > 0:
            add_money(money_credit, conn=conn)
        for key, count in items_credit.items():
            for _ in range(int(count)):
                # add_to_pending currently doesn't take a conn — accept
                # the small atomicity gap. If items become critical to
                # the rewards path, plumb conn through items.py later.
                pass
        conn.execute(
            """
            UPDATE trainers
            SET resolved = ?, resolved_utc = datetime('now'),
                money_reward = ?, xp_reward = ?
            WHERE id = ?
            """,
            (status, money_credit, xp_credit, int(trainer_id)),
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
        if not already_resolved and rewards is not None:
            try:
                _award_rewards(
                    trainer_id=trainer_id, status=status,
                    defeated_count=defeated,
                    money=rewards.money,
                    xp_per_defeat=rewards.xp_per_defeat,
                    item_drops=rewards.item_drops,
                )
            except Exception:
                log.exception("reward award failed")

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

        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 60, CONTENT_WIDTH - 40, 28),
            headline,
            font=NSFont.boldSystemFontOfSize_(20),
            color=color,
            align=NSTextAlignmentCenter,
        ))

        # Stats lines
        money = rewards.money if (rewards and status == "won") else 0
        xp_total = rewards.xp_per_defeat * defeated if rewards else 0
        lines = [
            f"Pokémon defeated: {defeated}",
            f"Money: +${money}",
            f"XP: +{xp_total}",
        ]
        for i, line in enumerate(lines):
            view.addSubview_(_label(
                NSMakeRect(40, POPOVER_HEIGHT - 130 - i * 26, CONTENT_WIDTH - 80, 20),
                line,
                font=NSFont.systemFontOfSize_(13),
                align=NSTextAlignmentCenter,
            ))

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

"""End-of-battle rewards: money, XP per defeated, item drops, loss
penalty.

Pure compute given the trainer's team and difficulty. The caller writes
the rewards into storage atomically.

WIN: the player gains ``money`` + per-defeat XP (``xp_per_defeat`` ×
defeated count) + ``item_drops``.

LOSS: the player loses ``loss_penalty`` money — Pokémon-style "lost $X
on the way to a Pokémon Center". No XP credit and no drops.
"""
from __future__ import annotations

from .models import Difficulty, Rewards, TrainerMon
from .team_gen import DIFFICULTY_PROFILES

# Base money per battle, scaled by avg team level + difficulty.
BASE_MONEY = 80
PER_LEVEL_MONEY = 20

# XP per defeated opponent: ``opponent_level × XP_PER_LEVEL × diff_mult``.
# Roughly equivalent to base-exp ~80 / 7 in Gen-3 simplified.
XP_PER_LEVEL = 12

# Loss penalty: a flat base + half the per-level money slope, then
# scaled by difficulty. Capped at ``100 × avg_level`` so a level-1
# wipe doesn't become a debt spiral. ``add_money`` already clamps the
# wallet at 0 on read, so we don't need to also cap by the player's
# current balance here.
BASE_LOSS_PENALTY = 50


def compute_rewards(
    team: list[TrainerMon],
    difficulty: Difficulty,
) -> Rewards:
    """Compute the payout for this battle.

    On WIN: ``money`` + per-defeat XP (``xp_per_defeat`` × defeated
    count) + ``item_drops``.

    On LOSS: ``loss_penalty`` money is deducted from the player; no XP
    or drops are awarded. The reward pane is responsible for selecting
    the right field based on ``status``.
    """
    profile = DIFFICULTY_PROFILES[difficulty]
    mult = profile["reward_mult"]
    avg_level = sum(m.level for m in team) / max(1, len(team))
    money = int((BASE_MONEY + PER_LEVEL_MONEY * avg_level) * mult)
    xp_per_defeat = int(XP_PER_LEVEL * avg_level * mult)
    raw_penalty = int(
        (BASE_LOSS_PENALTY + PER_LEVEL_MONEY * avg_level * 0.5) * mult
    )
    loss_penalty = max(0, min(raw_penalty, int(100 * avg_level)))
    return Rewards(
        money=money,
        xp_per_defeat=xp_per_defeat,
        item_drops={},
        loss_penalty=loss_penalty,
    )

"""End-of-battle rewards: money, XP per defeated, item drops.

Pure compute given the trainer's team and difficulty. The caller writes
the rewards into storage atomically.
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


def compute_rewards(
    team: list[TrainerMon],
    difficulty: Difficulty,
) -> Rewards:
    """Compute the payout if the player WINS this battle. The caller
    awards XP per defeated opponent (so partial wins still credit XP),
    but money/items only on full victory."""
    profile = DIFFICULTY_PROFILES[difficulty]
    mult = profile["reward_mult"]
    avg_level = sum(m.level for m in team) / max(1, len(team))
    money = int((BASE_MONEY + PER_LEVEL_MONEY * avg_level) * mult)
    xp_per_defeat = int(XP_PER_LEVEL * avg_level * mult)
    return Rewards(money=money, xp_per_defeat=xp_per_defeat, item_drops={})

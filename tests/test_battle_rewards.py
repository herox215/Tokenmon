"""Reward-calculation tests."""
from __future__ import annotations

from tokenmon.battle.models import TrainerMon
from tokenmon.battle.rewards import compute_rewards


def _team(levels: list[int]) -> list[TrainerMon]:
    return [
        TrainerMon(
            species_dex_id=1,
            level=lv,
            nature="Hardy",
            ivs=(0, 0, 0, 0, 0, 0),
            move_keys=("tackle",),
        )
        for lv in levels
    ]


def test_money_scales_with_difficulty():
    team = _team([20])
    easy = compute_rewards(team, "easy").money
    medium = compute_rewards(team, "medium").money
    hard = compute_rewards(team, "hard").money
    assert easy < medium < hard
    # Hard is 2× easy (per multipliers).
    assert hard == easy * 2
    # Medium is 1.5× easy.
    assert medium == int(easy * 1.5)


def test_money_scales_with_average_level():
    low = compute_rewards(_team([5]), "easy").money
    high = compute_rewards(_team([50]), "easy").money
    assert high > low


def test_xp_scales_with_difficulty():
    team = _team([20])
    easy = compute_rewards(team, "easy").xp_per_defeat
    hard = compute_rewards(team, "hard").xp_per_defeat
    assert hard > easy


def test_item_drops_empty_for_v1():
    """Items aren't part of the pure compute — they're rolled at
    award-time. The Rewards dataclass exposes the slot so v2 can fill
    it without API churn."""
    r = compute_rewards(_team([20]), "easy")
    assert r.item_drops == {}

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


def test_loss_penalty_scales_and_caps():
    """Loss penalty is positive, scales with avg level + difficulty,
    and is capped by ``100 × avg_level`` so a level-1 wipe doesn't
    bankrupt the player."""
    low = compute_rewards(_team([5]), "easy").loss_penalty
    high = compute_rewards(_team([50]), "easy").loss_penalty
    assert low > 0
    assert high > low

    # Difficulty multiplier flows through.
    easy_pen = compute_rewards(_team([20]), "easy").loss_penalty
    hard_pen = compute_rewards(_team([20]), "hard").loss_penalty
    assert hard_pen > easy_pen

    # Cap: at avg_level = 10 with mult=1.0 the raw formula returns
    # 50 + 20*10*0.5 = 150, well under the 100*10 = 1000 cap. Sanity
    # check that the value is in range and never negative.
    r10 = compute_rewards(_team([10]), "easy")
    assert 0 < r10.loss_penalty <= 100 * 10


def test_loss_penalty_default_zero_on_dataclass():
    """Rewards.loss_penalty defaults to 0 so callers that pre-date
    Bug 3 keep working."""
    from tokenmon.battle.models import Rewards
    r = Rewards(money=10, xp_per_defeat=5, item_drops={})
    assert r.loss_penalty == 0


def test_compute_wild_kos_reward_xp_only():
    """Wild KOs grant XP only — no money, no items."""
    from tokenmon.battle.rewards import compute_wild_kos_reward
    xp = compute_wild_kos_reward(opponent_level=10)
    assert xp > 0


def test_compute_wild_kos_reward_scales_with_level():
    from tokenmon.battle.rewards import compute_wild_kos_reward
    low = compute_wild_kos_reward(opponent_level=5)
    high = compute_wild_kos_reward(opponent_level=50)
    assert high > low

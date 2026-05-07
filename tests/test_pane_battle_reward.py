"""Battle-reward pane: ``_award_rewards`` win/loss branches.

The pure function is testable without AppKit. We import the module
through a guard so the suite still runs on non-macOS CI.
"""
from __future__ import annotations

import pytest

pytest.importorskip("AppKit", reason="AppKit unavailable")

from tokenmon import storage
from tokenmon.popover.panes.battle_reward import _award_rewards


def _seed_trainer(db_path) -> int:
    return storage.insert_trainer(
        name="Tobi", title="Bug Catcher", difficulty="easy",
        seed=1, team=[{
            "species_dex_id": 16, "level": 8, "nature": "Hardy",
            "ivs": (0, 0, 0, 0, 0, 0), "move_keys": ("tackle",),
        }],
        path=db_path,
    )


def _seed_player_pokemon(db_path) -> int:
    """Insert a single-row player Pokémon and return its id."""
    from datetime import date
    return storage.insert_pokemon(
        caught_date=date(2026, 1, 1),
        species_dex_id=1,
        nature="Hardy",
        characteristic="loves to eat",
        ivs=(0, 0, 0, 0, 0, 0),
        path=db_path,
    )


def test_award_rewards_won_credits_money_and_xp(db_path):
    tid = _seed_trainer(db_path)
    pid = _seed_player_pokemon(db_path)
    starting = storage.get_money(db_path)
    money_delta, xp_total, items = _award_rewards(
        trainer_id=tid, status="won",
        defeated_count=2, money=200, xp_per_defeat=30,
        item_drops={}, loss_penalty=999,  # ignored on win
        player_pokemon_id=pid, path=db_path,
    )
    assert money_delta == 200
    assert xp_total == 60
    assert items == {}
    assert storage.get_money(db_path) == starting + 200
    t = storage.get_trainer(tid, path=db_path)
    assert t.resolved == "won"
    assert t.money_reward == 200
    assert t.xp_reward == 60


def test_award_rewards_lost_deducts_loss_penalty_no_xp_no_drops(db_path):
    """The whole point of Bug 3: defeat costs money, no XP, no drops."""
    tid = _seed_trainer(db_path)
    pid = _seed_player_pokemon(db_path)
    storage.set_money(500, path=db_path)
    money_delta, xp_total, items = _award_rewards(
        trainer_id=tid, status="lost",
        defeated_count=1, money=100, xp_per_defeat=20,
        item_drops={"potion": 3},  # MUST be ignored on loss
        loss_penalty=75,
        player_pokemon_id=pid, path=db_path,
    )
    assert money_delta == -75
    assert xp_total == 0
    assert items == {}
    # Wallet went down by exactly the penalty.
    assert storage.get_money(db_path) == 500 - 75
    # Trainer row records the negative value (negative = loss).
    t = storage.get_trainer(tid, path=db_path)
    assert t.resolved == "lost"
    assert t.money_reward == -75
    assert t.xp_reward == 0


def test_award_rewards_lost_clamps_wallet_at_zero(db_path):
    """add_money's max(0, ...) clamp protects against debt."""
    tid = _seed_trainer(db_path)
    pid = _seed_player_pokemon(db_path)
    storage.set_money(20, path=db_path)
    _award_rewards(
        trainer_id=tid, status="lost",
        defeated_count=0, money=0, xp_per_defeat=0,
        item_drops={}, loss_penalty=500,
        player_pokemon_id=pid, path=db_path,
    )
    assert storage.get_money(db_path) == 0


def test_award_rewards_won_does_not_credit_negative_money(db_path):
    """A win must not accidentally deduct money even if the caller
    passes a non-zero loss_penalty (defensive against later refactors)."""
    tid = _seed_trainer(db_path)
    pid = _seed_player_pokemon(db_path)
    storage.set_money(200, path=db_path)
    money_delta, _xp, _items = _award_rewards(
        trainer_id=tid, status="won",
        defeated_count=1, money=100, xp_per_defeat=10,
        item_drops={}, loss_penalty=500,
        player_pokemon_id=pid, path=db_path,
    )
    assert money_delta == 100
    assert storage.get_money(db_path) == 300


def test_award_rewards_ran_does_not_charge_or_pay(db_path):
    tid = _seed_trainer(db_path)
    pid = _seed_player_pokemon(db_path)
    storage.set_money(200, path=db_path)
    money_delta, xp_total, items = _award_rewards(
        trainer_id=tid, status="ran",
        defeated_count=0, money=100, xp_per_defeat=10,
        item_drops={"potion": 1}, loss_penalty=50,
        player_pokemon_id=pid, path=db_path,
    )
    assert money_delta == 0
    assert xp_total == 0
    assert items == {}
    assert storage.get_money(db_path) == 200
    t = storage.get_trainer(tid, path=db_path)
    assert t.resolved == "ran"

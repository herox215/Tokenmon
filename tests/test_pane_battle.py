"""Battle-pane PP-decrement tests (Bug 8).

The battle pane must decrement PP both in-memory and in the DB on every
player move, and refuse to launch a turn when the chosen slot is out of
PP. Engine resolution itself is covered by ``test_battle_engine.py``;
here we only exercise the pane's PP plumbing using a stubbed engine.
"""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

pytestmark = pytest.mark.skipif(
    pytest.importorskip("AppKit", reason="AppKit unavailable") is None,
    reason="AppKit not available",
)


class _FakePopover:
    def __init__(self):
        self._show_pane_calls: list[int] = []
        self._battle_session = None
        self._current_pane = 0

    def _show_pane(self, idx):
        self._show_pane_calls.append(int(idx))


def _build_session(player_state, opp_state, *, player_pokemon_id: int) -> dict:
    return {
        "trainer_id": 1,
        "trainer_name": "Bug Catcher Tobi",
        "trainer_difficulty": "easy",
        "player_pokemon_id": player_pokemon_id,
        "player_state": player_state,
        "opp_states": [opp_state],
        "opp_trainer_pokemon_ids": [99],
        "active_opp_idx": 0,
        "log": [],
        "defeated_count": 0,
        "rng": random.Random(0),
    }


def _make_states(*, player_pps=(35, 35, 35, 35)):
    from tokenmon.battle.models import BattleStats, Move

    tackle = Move(
        key="tackle", name="Tackle", type="normal", category="physical",
        power=40, accuracy=100, pp=35,
    )
    growl = Move(
        key="growl", name="Growl", type="normal", category="status",
        power=None, accuracy=100, pp=40,
    )
    moves = (tackle, growl, tackle, tackle)
    player = BattleStats(
        species_dex_id=1, level=5, types=("grass",),
        hp_max=20, hp_current=20,
        attack=10, defense=10, sp_attack=10, sp_defense=10, speed=10,
        moves=moves, move_pps=tuple(player_pps), name="Bulba",
    )
    opp = BattleStats(
        species_dex_id=4, level=5, types=("fire",),
        hp_max=20, hp_current=20,
        attack=10, defense=10, sp_attack=10, sp_defense=10, speed=10,
        moves=(tackle,), move_pps=(35,), name="Foe Charmander",
    )
    return player, opp


def _seed_pokemon_with_moves(db_path) -> int:
    """Insert a real Pokémon row + 4 move rows so decrement_pp can hit
    the DB. Returns the pokemon_id."""
    from datetime import date

    from tokenmon import storage

    pid = storage.insert_pokemon(
        caught_date=date.today(),
        species_dex_id=1, nature="Hardy",
        characteristic="Loves to eat",
        path=db_path,
    )
    for slot, key in enumerate(("tackle", "growl", "tackle", "tackle")):
        storage.set_pokemon_move(pid, slot, key, max_pp=35, path=db_path)
    return pid


def test_move_decrements_pp_in_memory_and_db(db_path, monkeypatch):
    """Clicking a move spends 1 PP for that slot, both in the session
    and on disk."""
    from tokenmon import storage
    from tokenmon.battle.engine import resolve_turn as _real_resolve  # noqa: F401
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController
    from tokenmon.battle.models import TurnResult

    pid = _seed_pokemon_with_moves(db_path)
    player, opp = _make_states()
    pop = _FakePopover()
    pop._battle_session = _build_session(player, opp, player_pokemon_id=pid)

    # Stub the engine: returns the inputs unchanged so we can assert PP
    # plumbing without dragging real damage rolls into the test.
    def _stub_resolve(player_state, opp_state, *, player_move, opp_move, rng):
        return TurnResult(
            log=["stubbed turn"],
            player_state=player_state,
            opp_state=opp_state,
            player_fainted=False,
            opp_fainted=False,
        )
    monkeypatch.setattr(battle_mod, "resolve_turn", _stub_resolve)
    # Suppress the re-render so we don't need a real popover.
    monkeypatch.setattr(BattleController, "_rerender", lambda self: None)

    ctrl = BattleController(pop)
    ctrl._do_player_move(player.moves[1], 1)  # spend Growl (slot 1)

    # In-memory: slot 1 went from 35 -> 34, others unchanged.
    new_pps = pop._battle_session["player_state"].move_pps
    assert new_pps == (35, 34, 35, 35)

    # DB: storage.get_pokemon_moves reflects the same spend.
    rows = storage.get_pokemon_moves(pid, path=db_path)
    by_slot = {r.slot: r.current_pp for r in rows}
    assert by_slot[1] == 34
    assert by_slot[0] == 35
    assert by_slot[2] == 35
    assert by_slot[3] == 35


def test_move_with_zero_pp_blocked(db_path, monkeypatch):
    """If the chosen slot has 0 PP, resolve_turn must NOT be called and a
    log entry is emitted."""
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController

    pid = _seed_pokemon_with_moves(db_path)
    player, opp = _make_states(player_pps=(0, 5, 5, 5))
    pop = _FakePopover()
    pop._battle_session = _build_session(player, opp, player_pokemon_id=pid)

    calls: list[int] = []

    def _spy_resolve(*args, **kwargs):
        calls.append(1)
        raise AssertionError("resolve_turn should not be called when PP=0")

    monkeypatch.setattr(battle_mod, "resolve_turn", _spy_resolve)
    monkeypatch.setattr(BattleController, "_rerender", lambda self: None)

    ctrl = BattleController(pop)
    ctrl._do_player_move(player.moves[0], 0)  # slot 0 is empty

    assert calls == []
    log = pop._battle_session["log"]
    assert any("No PP left" in line for line in log)
    # In-memory state unchanged.
    assert pop._battle_session["player_state"].move_pps == (0, 5, 5, 5)


def test_move_decrement_floors_at_zero(db_path, monkeypatch):
    """Spending the last PP leaves the slot at 0, never negative."""
    from tokenmon import storage
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController
    from tokenmon.battle.models import TurnResult

    pid = _seed_pokemon_with_moves(db_path)
    # Manually set slot 0 to 1 PP in the DB so decrement_pp lands on 0.
    storage.set_pokemon_move(pid, 0, "tackle", max_pp=1, path=db_path)
    player, opp = _make_states(player_pps=(1, 35, 35, 35))
    pop = _FakePopover()
    pop._battle_session = _build_session(player, opp, player_pokemon_id=pid)

    def _stub_resolve(player_state, opp_state, *, player_move, opp_move, rng):
        return TurnResult(
            log=[], player_state=player_state, opp_state=opp_state,
            player_fainted=False, opp_fainted=False,
        )
    monkeypatch.setattr(battle_mod, "resolve_turn", _stub_resolve)
    monkeypatch.setattr(BattleController, "_rerender", lambda self: None)

    ctrl = BattleController(pop)
    ctrl._do_player_move(player.moves[0], 0)

    assert pop._battle_session["player_state"].move_pps[0] == 0
    rows = {r.slot: r.current_pp
            for r in storage.get_pokemon_moves(pid, path=db_path)}
    assert rows[0] == 0

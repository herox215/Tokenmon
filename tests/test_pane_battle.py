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


# ---- Phase 5: wild battles ---------------------------------------------


def _build_wild_session(player_state, opp_state, *, player_pokemon_id, encounter_id):
    return {
        "kind": "wild",
        "encounter_id": encounter_id,
        "player_pokemon_id": player_pokemon_id,
        "player_state": player_state,
        "opp_state": opp_state,
        "log": [],
        "rng": random.Random(0),
    }


def test_wild_battle_init_builds_one_mon_session(db_path, monkeypatch):
    from tokenmon import encounter as enc_mod
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import _init_wild_battle_session

    pid = _seed_pokemon_with_moves(db_path)
    enc = enc_mod.maybe_spawn(force=True, path=db_path)
    assert enc is not None

    # Stub _player_battle_stats / _opp_battle_stats to skip remote fetch.
    player, opp = _make_states()
    monkeypatch.setattr(battle_mod, "_player_battle_stats", lambda _a: player)
    monkeypatch.setattr(battle_mod, "_wild_battle_stats", lambda _e: opp)

    pop = _FakePopover()

    class _Active:
        id = pid
        species_dex_id = 1
        nature = "Hardy"
        is_shiny = False
        ivs = (0, 0, 0, 0, 0, 0)
        hp_current = None

    session = _init_wild_battle_session(pop, enc, _Active())
    assert session["kind"] == "wild"
    assert session["encounter_id"] == enc.id
    assert "opp_state" in session
    assert "opp_states" not in session


def test_wild_battle_action_bar_has_bag_button(db_path, monkeypatch):
    """A wild battle's action bar exposes a Bag button alongside Run."""
    from AppKit import NSButton
    from tokenmon import encounter as enc_mod
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController

    pid = _seed_pokemon_with_moves(db_path)
    enc = enc_mod.maybe_spawn(force=True, path=db_path)
    player, opp = _make_states()

    pop = _FakePopover()
    pop._battle_session = _build_wild_session(
        player, opp, player_pokemon_id=pid, encounter_id=enc.id,
    )

    # Stub trainer lookup to return None — wild path.
    monkeypatch.setattr(battle_mod, "get_pending_trainer", lambda: None)
    monkeypatch.setattr(
        battle_mod, "get_pending_encounter",
        lambda: enc,
    )
    # Active mon for build_view's box.get_active_pokemon call.
    from tokenmon.storage import get_pokemon_by_id
    active_row = get_pokemon_by_id(pid, path=db_path)
    monkeypatch.setattr(battle_mod.box, "get_active_pokemon", lambda *_a, **_k: active_row)

    monkeypatch.setattr(battle_mod, "_player_battle_stats", lambda _a: player)
    monkeypatch.setattr(battle_mod, "_wild_battle_stats", lambda _e: opp)

    ctrl = BattleController(pop)
    view = ctrl.build_view()

    # Walk the view tree and collect button titles.
    titles: list[str] = []

    def _collect(v):
        for sub in v.subviews():
            if isinstance(sub, NSButton):
                t = str(sub.title())
                if t:
                    titles.append(t)
            _collect(sub)

    _collect(view)
    assert any("Bag" in t for t in titles), titles
    # And no trainer-style "forfeit — counts as a loss" Run text.
    assert all("forfeit" not in t for t in titles), titles


def test_wild_battle_run_does_not_resolve_loss(db_path, monkeypatch):
    """Wild Run → encounter.run_away + back to Pokemon, no loss/blackout."""
    from tokenmon import encounter as enc_mod
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController
    from tokenmon.popover.widgets import PANE_POKEMON

    pid = _seed_pokemon_with_moves(db_path)
    enc = enc_mod.maybe_spawn(force=True, path=db_path)
    player, opp = _make_states()
    pop = _FakePopover()
    pop._battle_session = _build_wild_session(
        player, opp, player_pokemon_id=pid, encounter_id=enc.id,
    )

    called = {"ran": False}
    monkeypatch.setattr(
        battle_mod.encounter, "run_away",
        lambda eid, **kw: called.__setitem__("ran", True),
    )

    ctrl = BattleController(pop)
    ctrl._wild_run_away()
    assert called["ran"] is True
    assert PANE_POKEMON in pop._show_pane_calls
    # Battle session is cleared so the next sidebar click doesn't resume.
    assert pop._battle_session is None


def test_throw_ball_in_battle_persists_hp_pre_throw(db_path, monkeypatch):
    """Before delegating to _begin_catch_animation, _throw_ball_in_battle
    must persist the in-memory opp HP into encounters.hp_current so the
    catch math reads the right value."""
    from tokenmon import encounter as enc_mod, storage
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController

    pid = _seed_pokemon_with_moves(db_path)
    enc = enc_mod.maybe_spawn(force=True, path=db_path)
    player, opp = _make_states()
    # Damaged opp.
    from dataclasses import replace as dc_replace
    opp = dc_replace(opp, hp_current=4)

    pop = _FakePopover()
    pop._battle_session = _build_wild_session(
        player, opp, player_pokemon_id=pid, encounter_id=enc.id,
    )

    catch_calls: list[dict] = []

    def _spy_begin(**kw):
        catch_calls.append(kw)

    pop._begin_catch_animation = _spy_begin

    monkeypatch.setattr(
        battle_mod.encounter, "use_item",
        lambda eid, key: {"caught": False, "shakes": 0, "hint": None},
    )

    ctrl = BattleController(pop)
    ctrl._throw_ball_in_battle("pokeball")

    # Persisted HP matches the in-memory opp_state.
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT hp_current FROM encounters WHERE id = ?", (enc.id,),
    ).fetchone()
    conn.close()
    assert row[0] == 4

    # Catch animation fired.
    assert len(catch_calls) == 1
    assert catch_calls[0]["item_key"] == "pokeball"


def test_throw_ball_caught_ends_battle_session(db_path, monkeypatch):
    """A successful catch clears _battle_session before the reveal hand-off."""
    from tokenmon import encounter as enc_mod
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController

    pid = _seed_pokemon_with_moves(db_path)
    enc = enc_mod.maybe_spawn(force=True, path=db_path)
    player, opp = _make_states()

    pop = _FakePopover()
    pop._battle_session = _build_wild_session(
        player, opp, player_pokemon_id=pid, encounter_id=enc.id,
    )
    pop._begin_catch_animation = lambda **kw: None

    monkeypatch.setattr(
        battle_mod.encounter, "use_item",
        lambda eid, key: {"caught": True, "shakes": 3, "hint": None,
                          "pokemon_id": 99},
    )

    ctrl = BattleController(pop)
    ctrl._throw_ball_in_battle("pokeball")

    # Battle session cleared on catch.
    assert pop._battle_session is None


def test_throw_ball_failed_returns_to_battle(db_path, monkeypatch):
    """A failed catch keeps the battle session intact so the runner
    returns to the same fight."""
    from tokenmon import encounter as enc_mod
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController

    pid = _seed_pokemon_with_moves(db_path)
    enc = enc_mod.maybe_spawn(force=True, path=db_path)
    player, opp = _make_states()
    pop = _FakePopover()
    pop._battle_session = _build_wild_session(
        player, opp, player_pokemon_id=pid, encounter_id=enc.id,
    )
    pop._begin_catch_animation = lambda **kw: None

    monkeypatch.setattr(
        battle_mod.encounter, "use_item",
        lambda eid, key: {"caught": False, "shakes": 1, "hint": "Looks fast!"},
    )

    ctrl = BattleController(pop)
    ctrl._throw_ball_in_battle("pokeball")

    # Session intact (caller will re-mount battle on catch-anim.end).
    assert pop._battle_session is not None
    assert pop._battle_session["kind"] == "wild"


def test_wild_battle_opp_ko_routes_to_reward_with_xp_only(db_path, monkeypatch):
    """When the wild mon faints, transition to PANE_BATTLE_REWARD; no
    money, no items in the eventual award."""
    from tokenmon.battle.rewards import compute_wild_kos_reward
    from tokenmon import encounter as enc_mod
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController
    from tokenmon.popover.widgets import PANE_BATTLE_REWARD

    pid = _seed_pokemon_with_moves(db_path)
    enc = enc_mod.maybe_spawn(force=True, path=db_path)
    player, opp = _make_states()
    # Set opp to 1 HP so any nonzero damage KOs.
    from dataclasses import replace as dc_replace
    opp = dc_replace(opp, hp_current=1)
    pop = _FakePopover()
    pop._battle_session = _build_wild_session(
        player, opp, player_pokemon_id=pid, encounter_id=enc.id,
    )

    # Stub fold_events to return an opp_fainted=True turn result.
    from tokenmon.battle.models import TurnResult

    def _spy_fold(_events, p, o):
        return TurnResult(
            log=["foe fainted"], player_state=p, opp_state=o,
            player_fainted=False, opp_fainted=True,
        )

    monkeypatch.setattr(battle_mod, "fold_events", _spy_fold)
    monkeypatch.setattr(
        battle_mod, "plan_turn", lambda *a, **kw: [],
    )

    # XP reward is computed from level — non-zero.
    assert compute_wild_kos_reward(opp.level) > 0

    ctrl = BattleController(pop)
    ctrl._do_player_move(player.moves[0], 0)
    assert PANE_BATTLE_REWARD in pop._show_pane_calls
    # The session carries kind='wild' so the reward pane branches XP-only.
    assert pop._battle_session["kind"] == "wild"


def test_wild_battle_player_ko_routes_to_blackout(db_path, monkeypatch):
    """Player faint in a wild battle still routes to reward pane (blackout)."""
    from tokenmon import encounter as enc_mod
    from tokenmon.popover.panes import battle as battle_mod
    from tokenmon.popover.panes.battle import BattleController
    from tokenmon.popover.widgets import PANE_BATTLE_REWARD

    pid = _seed_pokemon_with_moves(db_path)
    enc = enc_mod.maybe_spawn(force=True, path=db_path)
    player, opp = _make_states()
    from dataclasses import replace as dc_replace
    player = dc_replace(player, hp_current=1)
    pop = _FakePopover()
    pop._battle_session = _build_wild_session(
        player, opp, player_pokemon_id=pid, encounter_id=enc.id,
    )

    from tokenmon.battle.models import TurnResult

    def _spy_fold(_events, p, o):
        return TurnResult(
            log=["you fainted"], player_state=p, opp_state=o,
            player_fainted=True, opp_fainted=False,
        )

    monkeypatch.setattr(battle_mod, "fold_events", _spy_fold)
    monkeypatch.setattr(battle_mod, "plan_turn", lambda *a, **kw: [])

    ctrl = BattleController(pop)
    ctrl._do_player_move(player.moves[0], 0)
    assert PANE_BATTLE_REWARD in pop._show_pane_calls

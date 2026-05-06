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

from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import box, learnsets_remote, moves_remote, pokemon
from tokenmon.battle.engine import resolve_turn
from tokenmon.battle.models import BattleStats, Move
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_BATTLE_REWARD,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    _label,
)
from tokenmon.storage import (
    get_pending_trainer,
    list_trainer_pokemon,
)

log = logging.getLogger("tokenmon.popover.panes.battle")

_FALLBACK_MOVE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)


class _HPBar(NSView):
    """Simple HP bar — green > 50%, orange 20-50%, red < 20%."""

    def initWithFrame_current_max_(self, frame, current, hp_max):  # noqa: N802
        import objc
        self = objc.super(_HPBar, self).initWithFrame_(frame)
        if self is None:
            return None
        self._current = max(0, int(current))
        self._max = max(1, int(hp_max))
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


def _player_battle_stats(active) -> BattleStats:
    """Build BattleStats for the player's active Pokémon. Uses
    pokemon.stats.final_stats for HP/Atk/Def/etc. Moves come from the
    pokemon_moves table (backfilled via learnsets if empty)."""
    from tokenmon.pokemon.stats import final_stats
    from tokenmon.storage import get_pokemon_moves, set_pokemon_move

    ivs = (
        active.iv_hp, active.iv_attack, active.iv_defense,
        active.iv_sp_attack, active.iv_sp_defense, active.iv_speed,
    )
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
    return BattleStats(
        species_dex_id=active.species_dex_id, level=level, types=types,
        hp_max=hp_max, hp_current=hp_max,
        attack=atk, defense=defn, sp_attack=spa, sp_defense=spd,
        speed=spe, moves=tuple(moves), move_pps=tuple(pps), name=name,
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


class BattleController(PaneController):
    """The active battle pane. Renders sprites + HP + move-picker + log
    based on the popover's battle session."""

    def build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )
        try:
            trainer = get_pending_trainer()
        except Exception:
            log.exception("get_pending_trainer failed")
            trainer = None
        if trainer is None:
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
            session = _init_battle_session(self.popover, trainer, active)
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
        opp = session["opp_states"][session["active_opp_idx"]]
        player = session["player_state"]

        # Opponent block (top)
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 40, CONTENT_WIDTH - 40, 20),
            f"{opp.name}    Lv {opp.level}",
            font=NSFont.boldSystemFontOfSize_(13),
        ))
        opp_bar = _HPBar.alloc().initWithFrame_current_max_(
            NSMakeRect(20, POPOVER_HEIGHT - 60, CONTENT_WIDTH - 40, 12),
            opp.hp_current, opp.hp_max,
        )
        view.addSubview_(opp_bar)
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 80, CONTENT_WIDTH - 40, 14),
            f"{opp.hp_current}/{opp.hp_max} HP",
            font=NSFont.systemFontOfSize_(10),
            color=NSColor.secondaryLabelColor(),
        ))

        # Player block (middle)
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 130, CONTENT_WIDTH - 40, 20),
            f"{player.name}    Lv {player.level}",
            font=NSFont.boldSystemFontOfSize_(13),
        ))
        player_bar = _HPBar.alloc().initWithFrame_current_max_(
            NSMakeRect(20, POPOVER_HEIGHT - 150, CONTENT_WIDTH - 40, 12),
            player.hp_current, player.hp_max,
        )
        view.addSubview_(player_bar)
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 170, CONTENT_WIDTH - 40, 14),
            f"{player.hp_current}/{player.hp_max} HP",
            font=NSFont.systemFontOfSize_(10),
            color=NSColor.secondaryLabelColor(),
        ))

        # Battle log (last 4 lines)
        log_lines = session["log"][-4:]
        log_y = POPOVER_HEIGHT - 270
        for i, line in enumerate(log_lines):
            view.addSubview_(_label(
                NSMakeRect(20, log_y + (3 - i) * 18, CONTENT_WIDTH - 40, 16),
                line,
                font=NSFont.systemFontOfSize_(11),
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentLeft,
            ))

        # Move picker — 2x2 grid of buttons
        moves = player.moves
        btn_w = (CONTENT_WIDTH - 60) // 2
        btn_h = 30
        for i, mv in enumerate(moves[:4]):
            col = i % 2
            row = i // 2
            x = 20 + col * (btn_w + 20)
            y = 80 - row * (btn_h + 8)
            btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(x, y, btn_w, btn_h)
            )
            label = f"{mv.name}  ({mv.type})"
            btn.setTitle_(label)
            btn.setBezelStyle_(1)
            handler = self._make_move_handler(mv)
            self._handlers.append(handler)
            btn.setTarget_(handler)
            btn.setAction_(b"fire:")
            view.addSubview_(btn)

        # Run button
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

    def _make_move_handler(self, move: Move):
        def _click(_s):
            self._do_player_move(move)
        return make_handler(_click)

    def _do_player_move(self, player_move: Move) -> None:
        session = self.popover._battle_session
        if session is None:
            return
        opp = session["opp_states"][session["active_opp_idx"]]
        # Pick opponent's move randomly from its moveset.
        opp_move = session["rng"].choice(opp.moves)
        result = resolve_turn(
            session["player_state"], opp,
            player_move=player_move, opp_move=opp_move,
            rng=session["rng"],
        )
        session["player_state"] = result.player_state
        session["opp_states"][session["active_opp_idx"]] = result.opp_state
        session["log"].extend(result.log)

        if result.opp_fainted:
            session["defeated_count"] += 1
            # Mark fainted in DB so list_trainer_pokemon reflects it.
            try:
                from tokenmon.storage import mark_trainer_pokemon_fainted
                mark_trainer_pokemon_fainted(
                    session["opp_trainer_pokemon_ids"][session["active_opp_idx"]]
                )
            except Exception:
                log.exception("mark fainted failed")
            # Next opp or win.
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

        # Re-render the pane to reflect new HP / log / active opp.
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

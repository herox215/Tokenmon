"""Level-up detection + move-learning side-effects for the menubar app.

``compute_current_level`` reads XP for the active Pokémon (or for today
if none is active) and resolves it through the species' growth-rate
table. ``check_level_up`` runs every activity poll: when the level
increased it fires the rumps notification + companion overlay
animation and walks the species learnset to either auto-learn the new
move(s) or queue them for the modal forget-which-move pane.

State (``app._last_known_level``, ``app._line_base_id``,
``app._pokemon_dex_id``, ``app._pokemon_sprite``, ``app._companion_mode``,
``app._overlay``) lives on TokenmonApp.
"""
from __future__ import annotations

import logging
from datetime import date

import rumps

from tokenmon import box, pokemon
from tokenmon.storage import query_xp_for_date, query_xp_for_pokemon

TZ = "Europe/Berlin"

log = logging.getLogger("tokenmon.menubar.levelup")


def compute_current_level(app) -> int:
    try:
        active = box.get_active_pokemon()
        if active is not None:
            xp = query_xp_for_pokemon(active.id)
        else:
            xp = query_xp_for_date(date.today(), TZ)
    except Exception:
        return 1
    rate = pokemon.growth_rate_of(app._line_base_id)
    level, _, _ = pokemon.level_from_xp(xp, rate)
    return level


def check_level_up(app, now: float) -> None:
    """Detect a level increase since the last poll and fire visuals/notification."""
    new_level = compute_current_level(app)
    if new_level > app._last_known_level:
        old_level = app._last_known_level
        app._last_known_level = new_level
        try:
            active = box.get_active_pokemon()
        except Exception:
            active = None
        display = pokemon.display_name(
            active.nickname if active is not None else None,
            app._pokemon_dex_id,
        )
        try:
            rumps.notification(
                title="Tokenmon",
                subtitle="Level up!",
                message=f"{display} leveled up!",
            )
        except Exception:
            log.exception("level-up notification failed")
        # Pop the overlay for the duration of the level-up banner. The
        # overlay hides itself again when the banner timer expires.
        # Suppress the level-up animation while an evolution animation is
        # already running (level-up and evolution often coincide).
        if app._companion_mode and not app._overlay.evolution_running:
            try:
                app._overlay.update_sprite(
                    app._pokemon_sprite,
                    speed=app._companion_sprite_speed(),
                )
                app._overlay.show_level_up()
            except Exception:
                log.exception("overlay level-up animation failed")
        # Move-learn handling: for every level the Pokémon just
        # gained, walk the species learnset and either auto-learn
        # the move (free slot available, no duplicate) or queue
        # it for the modal forget-which-move pane (4 slots full).
        if active is not None:
            try:
                apply_level_up_moves(app, active, old_level, new_level)
            except Exception:
                log.exception("level-up move application failed")
    elif new_level < app._last_known_level:
        # Defensive: data shrunk (manual DB edit?). Track silently.
        app._last_known_level = new_level


def apply_level_up_moves(app, active, old_level: int, new_level: int) -> None:
    """For each level gained, learn the species' level-up moves.

    Auto-learn rules:
      1. If the move is already known → skip (no duplicate).
      2. If <4 slots filled → write to the lowest free slot at full
         PP and fire a "X learned Foo!" notification.
      3. Otherwise → queue via ``queue_move_learn`` so the modal
         pane can present the forget-which-move flow next time the
         popover opens.

    First-time level-ups for a Pokémon with no ``pokemon_moves``
    rows trigger a backfill from ``initial_moves`` so the lower-
    level moves don't get lost.
    """
    from tokenmon import learnsets_remote, moves_remote
    from tokenmon.storage import (
        get_pokemon_moves,
        queue_move_learn,
        set_pokemon_move,
    )

    # Backfill: a Pokémon caught before this feature has an empty
    # pokemon_moves table; the level-up walk below would only ever
    # add the new-level moves and miss earlier ones.
    existing = get_pokemon_moves(active.id)
    if not existing:
        try:
            seed_keys = learnsets_remote.initial_moves(
                active.species_dex_id, max(1, old_level),
            )
            for slot, key in enumerate(seed_keys[:4]):
                md = moves_remote.get_move_data(key)
                max_pp = md.pp if md is not None else 35
                set_pokemon_move(active.id, slot, key, max_pp=max_pp)
        except Exception:
            log.exception("level-up backfill failed")

    existing_keys = {
        m.move_key for m in get_pokemon_moves(active.id)
    }
    display = pokemon.display_name(
        active.nickname, active.species_dex_id,
    )

    for lv in range(old_level + 1, new_level + 1):
        try:
            lv_moves = learnsets_remote.moves_at_level(
                active.species_dex_id, lv,
            )
        except Exception:
            log.exception("moves_at_level lookup failed for L%d", lv)
            continue
        for move_key in lv_moves:
            if move_key in existing_keys:
                continue
            current = get_pokemon_moves(active.id)
            if len(current) < 4:
                occupied = {m.slot for m in current}
                free = next(
                    s for s in range(4) if s not in occupied
                )
                md = moves_remote.get_move_data(move_key)
                max_pp = md.pp if md is not None else 35
                try:
                    set_pokemon_move(
                        active.id, free, move_key, max_pp=max_pp,
                    )
                except Exception:
                    log.exception(
                        "auto-learn set_pokemon_move failed for %s",
                        move_key,
                    )
                    continue
                existing_keys.add(move_key)
                move_display = (
                    md.name if md is not None
                    else move_key.replace("-", " ").title()
                )
                try:
                    rumps.notification(
                        title="Tokenmon",
                        subtitle="New move!",
                        message=f"{display} learned {move_display}!",
                    )
                except Exception:
                    log.exception("auto-learn notification failed")
            else:
                try:
                    queue_move_learn(active.id, move_key, lv)
                except Exception:
                    log.exception(
                        "queue_move_learn failed for %s", move_key,
                    )
                    continue
                existing_keys.add(move_key)

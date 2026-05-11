"""High-level PC Box lifecycle.

Owns the "what Pokemon do we have, and for which days" question. Two
responsibilities:

1. ``ensure_today_pokemon()`` — make sure today (Europe/Berlin) has a row in
   the ``pokemon`` table, creating one with random species/nature/characteristic
   if it doesn't. Idempotent.

2. ``migrate_legacy_days()`` — backfill the ``pokemon`` table from the
   ``requests`` history. Each historical day with ``output_tokens > 0`` gets a
   deterministic entry seeded from the user salt, so the legacy "this was
   Magnemite day" attribution stays intact across reinstalls.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tokenmon import config, pokemon
from tokenmon.storage import (
    DB_PATH,
    Pokemon,
    _tokens_per_local_day,
    get_pokemon_by_id,
    get_pokemon_for_date,
    insert_pokemon,
    list_pokemon,
    query_xp_for_pokemon,
    update_pokemon_species,
)

# Species handed out as the very first Pokémon on a fresh box. Picked so the
# user always starts with a recognizable mascot instead of whatever
# ``random_species`` happens to roll on day one.
STARTER_SPECIES_DEX_ID = 25  # Pikachu

TZ = ZoneInfo("Europe/Berlin")
TZ_NAME = "Europe/Berlin"


def _today_local() -> date:
    return datetime.now(TZ).date()


def seed_initial_moves(
    pokemon_id: int,
    species_dex_id: int,
    level: int,
    *,
    path: Path = DB_PATH,
) -> None:
    """Seed up to 4 starter moves for a freshly inserted Pokémon.

    Writes to ``pokemon_moves`` (so the slot grid renders something the
    moment the user opens the box detail) and ``pokemon_unlocked_moves``
    (so the swap UI lists them in the unlocked pool).

    No-op on lookup failure — the lazy backfill paths in pokemon.py /
    battle.py / levelup.py still cover the offline case.
    """
    try:
        from tokenmon import learnsets_remote, moves_remote
        from tokenmon.storage import (
            get_pokemon_moves,
            set_pokemon_move,
            unlock_move,
        )
    except Exception:  # pragma: no cover — import failure is fatal elsewhere
        return

    try:
        if get_pokemon_moves(pokemon_id, path=path):
            return  # Already seeded — caller is racing with backfill.
        keys = learnsets_remote.initial_moves(
            species_dex_id, max(1, level),
        )
        for slot, key in enumerate(keys[:4]):
            md = moves_remote.get_move_data(key)
            max_pp = md.pp if md is not None else 35
            set_pokemon_move(pokemon_id, slot, key, max_pp=max_pp, path=path)
            try:
                unlock_move(pokemon_id, key, max(1, level), path=path)
            except Exception:  # pragma: no cover — non-fatal
                pass
    except Exception:  # pragma: no cover — non-fatal seed
        pass


def ensure_today_pokemon(path: Path = DB_PATH) -> Pokemon:
    """Return today's Pokemon, creating it on first call of the day.

    Random (not seeded) — a fresh install on a new day rolls a fresh species,
    nature and characteristic. Subsequent calls on the same local day return
    the existing row.
    """
    today = _today_local()
    existing = get_pokemon_for_date(today, path=path)
    if existing is not None:
        return existing

    if not list_pokemon(path=path):
        species = STARTER_SPECIES_DEX_ID
    else:
        species = pokemon.random_species()
    nature = pokemon.random_nature()
    ivs = pokemon.roll_ivs()
    characteristic = pokemon.characteristic_for_ivs(ivs)

    new_id = insert_pokemon(
        caught_date=today,
        species_dex_id=species,
        nature=nature["name"],
        characteristic=characteristic,
        ivs=ivs,
        path=path,
    )
    try:
        from tokenmon.storage import mark_caught as _pokedex_mark_caught
        _pokedex_mark_caught(species, path=path)
    except Exception:  # pragma: no cover — non-fatal
        pass
    seed_initial_moves(new_id, species, level=1, path=path)
    row = get_pokemon_by_id(new_id, path=path)
    if row is None:  # pragma: no cover — insert just succeeded
        raise RuntimeError(f"failed to read back pokemon id={new_id}")
    return row


def get_today_pokemon_id(path: Path = DB_PATH) -> int | None:
    """Return today's box entry id (today in Europe/Berlin), or ``None`` if no
    row exists yet for today. Used by storage._resolve_trained_pokemon_id as
    the fallback when no active_pokemon_id is set in config.
    """
    today = _today_local()
    row = get_pokemon_for_date(today, path=path)
    return row.id if row is not None else None


def get_active_pokemon_id(path: Path = DB_PATH) -> int | None:
    """Return ``config['active_pokemon_id']`` if it points at an existing row,
    else fall back to today's box id (which may itself be ``None`` if the box
    is empty). The fallback ensures a sensible default before the user has
    ever touched the active selector.
    """
    pinned = config.get("active_pokemon_id")
    if isinstance(pinned, int):
        if get_pokemon_by_id(pinned, path=path) is not None:
            return pinned
        # Stale pin — fall through to today's id rather than returning a dead id.
    return get_today_pokemon_id(path=path)


def get_active_pokemon(path: Path = DB_PATH) -> Pokemon | None:
    """Convenience: get_pokemon_by_id(get_active_pokemon_id())."""
    pid = get_active_pokemon_id(path=path)
    if pid is None:
        return None
    return get_pokemon_by_id(pid, path=path)


def set_active_pokemon(pokemon_id: int, path: Path = DB_PATH) -> None:
    """Persist ``config['active_pokemon_id'] = pokemon_id``.

    Validates the id refers to an existing row — raises ``ValueError`` if not,
    so the caller can't accidentally pin a non-existent pokemon.
    """
    if get_pokemon_by_id(pokemon_id, path=path) is None:
        raise ValueError(f"no pokemon with id={pokemon_id}")
    config.set_("active_pokemon_id", pokemon_id)


def add_caught_pokemon(
    species_dex_id: int,
    nature: str,
    characteristic: str,
    *,
    caught_date: date | None = None,
    is_shiny: bool = False,
    gender: str | None = None,
    ivs: tuple[int, int, int, int, int, int] | None = None,
    level: int = 1,
    path: Path = DB_PATH,
) -> int:
    """Insert a Pokemon row for a wild encounter that the user just caught.

    Stores with ``source='wild'`` so it can coexist with the day's daily
    pick. The legacy "backdate one day at a time on UNIQUE collision" kludge
    is gone — the unique constraint was dropped in the source-column
    migration.

    ``ivs`` should be the encounter's IVs so the caught Pokemon's stats
    match what the encounter UI showed before the throw. Falls through to a
    fresh roll inside ``insert_pokemon`` when omitted.

    ``level`` is the encounter's level — used to seed the appropriate
    starter moves. Defaults to 1 for callers without level info.
    """
    new_id = insert_pokemon(
        caught_date=caught_date or _today_local(),
        species_dex_id=species_dex_id,
        nature=nature,
        characteristic=characteristic,
        is_shiny=is_shiny,
        gender=gender,
        ivs=ivs,
        source="wild",
        path=path,
    )
    # Belt-and-suspenders Pokedex update — encounter._resolve_throw also
    # promotes, but a future direct caller (e.g. CLI debug command) should
    # still update the Pokedex.
    try:
        from tokenmon.storage import mark_caught as _pokedex_mark_caught
        _pokedex_mark_caught(species_dex_id, path=path)
    except Exception:  # pragma: no cover — non-fatal
        pass
    seed_initial_moves(new_id, species_dex_id, level=level, path=path)
    return new_id


def use_stone(
    pokemon_id: int, stone_key: str, *, path: Path = DB_PATH,
) -> int | None:
    """Apply an evolution stone to ``pokemon_id``.

    On success: mutates the row's species_dex_id to the evolved form,
    decrements one ``stone_key`` from the inventory, marks the Pokedex
    entry caught for the new form, and returns the new dex_id. Returns
    ``None`` when the stone has no effect (wrong species or unknown
    stone). Does NOT decrement the inventory on a no-op so the player
    doesn't lose a stone for trying.
    """
    row = get_pokemon_by_id(pokemon_id, path=path)
    if row is None:
        return None
    evolved = pokemon.stone_evolution_for(row.species_dex_id, stone_key)
    if evolved is None:
        return None
    update_pokemon_species(pokemon_id, evolved, path=path)
    try:
        from tokenmon.storage import (
            decrement_inventory,
            mark_caught as _pokedex_mark_caught,
        )
        decrement_inventory(stone_key, 1, path=path)
        _pokedex_mark_caught(evolved, path=path)
    except Exception:  # pragma: no cover — non-fatal
        pass
    return evolved


def use_ether(
    pokemon_id: int, ether_key: str, *, path: Path = DB_PATH,
) -> tuple[str, int, int, int] | None:
    """Restore PP to the active Pokémon's lowest-PP move.

    Returns ``(move_key, slot, old_pp, new_pp)`` on success — the items
    pane uses these for the user-facing notification. Returns ``None``
    when:

      * ``ether_key`` isn't registered in ``ETHER_PP_AMOUNTS``,
      * the Pokémon has no moves yet (fresh catch pre-backfill), or
      * every slot is already at max PP (we don't burn an Ether on a
        no-op restore — mirrors ``use_potion``'s "already full" guard).

    Auto-picks the slot with the lowest current PP (relative to that
    move's max PP) so the user doesn't need a slot-picker UI. Ties go
    to the lowest slot index — deterministic, matches Gen-3 cursor
    behavior of "first move in the list".
    """
    from tokenmon.items import ETHER_PP_AMOUNTS
    from tokenmon import moves_remote
    from tokenmon.storage import (
        decrement_inventory,
        get_pokemon_moves,
        set_pokemon_move,
    )

    heal = ETHER_PP_AMOUNTS.get(ether_key)
    if heal is None or heal <= 0:
        return None
    rows = get_pokemon_moves(pokemon_id, path=path)
    if not rows:
        return None

    # Pick the slot most in need of PP. Comparing absolute deficit
    # (max_pp - current_pp) gives the "biggest restore impact" slot;
    # works even when slots have different max-PP totals.
    best_slot = -1
    best_deficit = 0
    best_max = 0
    for r in rows:
        md = moves_remote.get_move_data(r.move_key)
        if md is None:
            continue
        deficit = md.pp - r.current_pp
        if deficit > best_deficit:
            best_slot = r.slot
            best_deficit = deficit
            best_max = md.pp
    if best_slot < 0:
        return None  # every slot at max PP

    target_row = next(r for r in rows if r.slot == best_slot)
    new_pp = min(best_max, target_row.current_pp + heal)
    set_pokemon_move(
        pokemon_id, best_slot, target_row.move_key,
        max_pp=new_pp, path=path,
    )
    try:
        decrement_inventory(ether_key, 1, path=path)
    except Exception:  # pragma: no cover — non-fatal
        pass
    return target_row.move_key, best_slot, target_row.current_pp, new_pp


def use_potion(
    pokemon_id: int, potion_key: str, *, path: Path = DB_PATH,
) -> tuple[int, int, int] | None:
    """Apply a healing item to ``pokemon_id``.

    Returns ``(old_hp, new_hp, hp_max)`` on success. Returns ``None``
    when the potion is unknown OR the Pokémon is already at max HP
    (we don't burn a potion on a no-op heal). Decrements one
    ``potion_key`` from inventory on success only.
    """
    from tokenmon.items import POTION_HEAL_AMOUNTS
    from tokenmon.pokemon.stats import final_stats
    from tokenmon.storage import (
        decrement_inventory,
        query_xp_for_pokemon,
        set_pokemon_hp,
    )

    heal = POTION_HEAL_AMOUNTS.get(potion_key)
    if heal is None or heal <= 0:
        return None
    row = get_pokemon_by_id(pokemon_id, path=path)
    if row is None:
        return None
    try:
        xp = query_xp_for_pokemon(pokemon_id, path=path)
        growth = pokemon.growth_rate_of(row.species_dex_id)
        level, _, _ = pokemon.level_from_xp(xp, growth)
        hp_max = final_stats(
            row.species_dex_id, row.ivs, max(1, level), row.nature,
        )[0]
    except Exception:  # pragma: no cover — defensive
        return None
    old_hp = int(row.hp_current) if row.hp_current is not None else hp_max
    if old_hp >= hp_max:
        return None  # already full — don't waste the potion
    new_hp = min(hp_max, old_hp + heal)
    if new_hp >= hp_max:
        set_pokemon_hp(pokemon_id, None, path=path)  # NULL = full
    else:
        set_pokemon_hp(pokemon_id, new_hp, path=path)
    try:
        decrement_inventory(potion_key, 1, path=path)
    except Exception:  # pragma: no cover — non-fatal
        pass
    return old_hp, new_hp, hp_max


def maybe_evolve(pokemon_id: int, path: Path = DB_PATH) -> int | None:
    """If the Pokemon's trained XP has crossed an evolution threshold, mutate
    its species_dex_id to the new form in the DB and return the new species
    id. Returns None when nothing changed.

    Pokemon-game semantics: a Bulbasaur trained past Lv 16 *becomes* Ivysaur —
    the row's species permanently advances; the original Bulbasaur is gone.
    Nature and characteristic carry over (those are inherited traits).
    """
    row = get_pokemon_by_id(pokemon_id, path=path)
    if row is None:
        return None
    try:
        xp = query_xp_for_pokemon(pokemon_id, path=path)
    except Exception:
        return None
    base = pokemon.line_of(row.species_dex_id)
    new_species = pokemon.current_stage_of(base, xp)
    if new_species == row.species_dex_id:
        return None
    chain = pokemon.evolution_chain(base)
    try:
        # Defensive: only allow forward evolution; never devolve.
        if chain.index(new_species) <= chain.index(row.species_dex_id):
            return None
    except ValueError:
        return None
    update_pokemon_species(pokemon_id, new_species, path=path)
    # Persistent Pokedex entry for the new form. The pre-evolution stays
    # 'caught' on its own row from the original catch.
    try:
        from tokenmon.storage import mark_caught as _pokedex_mark_caught
        _pokedex_mark_caught(new_species, path=path)
    except Exception:  # pragma: no cover — non-fatal
        pass
    return new_species


def migrate_legacy_days(path: Path = DB_PATH) -> int:
    """Backfill `pokemon` rows for every historical day in `requests` that has
    output_tokens > 0 and no Pokemon row yet.

    Uses the seeded helpers so the chosen species/nature/characteristic for
    each day match what `pick_for_today` would have returned — i.e. the user's
    historical attribution is preserved across reinstalls.

    Returns the number of rows inserted. Idempotent: re-running after all days
    are migrated is a single SELECT and returns 0.
    """
    salt = config.get_user_salt()
    inserted = 0
    for day, _tokens in _tokens_per_local_day(TZ_NAME, path):
        if get_pokemon_for_date(day, path=path) is not None:
            continue
        date_iso = day.isoformat()
        species = pokemon.seeded_species(date_iso, salt)
        nature = pokemon.seeded_nature(date_iso, salt)
        ivs = pokemon.seeded_ivs(date_iso, salt)
        characteristic = pokemon.characteristic_for_ivs(ivs)
        insert_pokemon(
            caught_date=day,
            species_dex_id=species,
            nature=nature["name"],
            characteristic=characteristic,
            ivs=ivs,
            path=path,
        )
        inserted += 1
    return inserted

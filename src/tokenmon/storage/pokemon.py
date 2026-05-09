"""Pokemon table layer — dataclass + per-row CRUD helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ._db import AFFECTION_MAX, DB_PATH, _connect

__all__ = [
    "Pokemon",
    "insert_pokemon",
    "get_pokemon_for_date",
    "get_pokemon_by_id",
    "list_pokemon",
    "update_pokemon_species",
    "update_pokemon_nickname",
    "bump_affection",
    "set_pokemon_hp",
    "set_pokemon_status",
    "clear_pokemon_status",
]


@dataclass(slots=True)
class Pokemon:
    id: int
    caught_date: date
    species_dex_id: int
    nature: str
    characteristic: str
    nickname: str | None
    is_shiny: bool
    affection: int = 0
    gender: str | None = None  # 'M', 'F', or None for genderless species
    # Per-instance IVs (0..31), order: HP, Attack, Defense, Sp.Atk, Sp.Def, Speed.
    ivs: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0)
    # Persisted current-HP across battles. None means "full" — either the
    # Pokémon hasn't fought yet or it healed to max. A positive integer
    # is the actual remaining HP. Battle init reads this; battle reward
    # writes the post-battle HP back here.
    hp_current: int | None = None
    # Persisted non-volatile status. ``"healthy"`` is the default; a
    # poisoned / burned / paralyzed / asleep / frozen / bad-poison mon
    # carries the status between battles per Pokémon canon. Volatile
    # statuses (confusion, flinch) are in-memory only and never land
    # here.
    status_non_volatile: str = "healthy"
    # Counter meaning depends on the status — see battle.status.StatusState.
    status_counter: int = 0


_POKEMON_COLUMNS = (
    "id, caught_date, species_dex_id, nature, characteristic, "
    "nickname, is_shiny, affection, gender, "
    "iv_hp, iv_attack, iv_defense, iv_sp_attack, iv_sp_defense, iv_speed, "
    "hp_current, status_non_volatile, status_counter"
)


def _row_to_pokemon(row: tuple) -> Pokemon:
    return Pokemon(
        id=row[0],
        caught_date=date.fromisoformat(row[1]),
        species_dex_id=row[2],
        nature=row[3],
        characteristic=row[4],
        nickname=row[5],
        is_shiny=bool(row[6]),
        affection=int(row[7]) if len(row) > 7 and row[7] is not None else 0,
        gender=row[8] if len(row) > 8 else None,
        ivs=(
            int(row[9] or 0), int(row[10] or 0), int(row[11] or 0),
            int(row[12] or 0), int(row[13] or 0), int(row[14] or 0),
        ) if len(row) > 14 else (0, 0, 0, 0, 0, 0),
        hp_current=(
            int(row[15]) if len(row) > 15 and row[15] is not None
            else None
        ),
        status_non_volatile=(
            str(row[16]) if len(row) > 16 and row[16] is not None
            else "healthy"
        ),
        status_counter=(
            int(row[17]) if len(row) > 17 and row[17] is not None
            else 0
        ),
    )


def insert_pokemon(
    caught_date: date,
    species_dex_id: int,
    nature: str,
    characteristic: str,
    *,
    nickname: str | None = None,
    is_shiny: bool = False,
    gender: str | None = None,
    source: str = "daily",
    ivs: tuple[int, int, int, int, int, int] | None = None,
    path: Path | None = None,
) -> int:
    """Insert a Pokemon row and return its id.

    Caller is responsible for "is the daily already inserted?" idempotency —
    we no longer rely on a UNIQUE(caught_date) constraint because wild
    catches can legitimately share a calendar day with the daily.

    ``ivs`` is a 6-tuple in (HP, ATK, DEF, Sp.Atk, Sp.Def, Speed) order. When
    omitted we roll a fresh set so legacy callers keep working without
    silently inserting an all-zero stat sheet.
    """
    if path is None:
        path = DB_PATH
    if ivs is None:
        from tokenmon.pokemon.stats import roll_ivs  # lazy: avoid import cycle
        ivs = roll_ivs()
    with _connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO pokemon (
                caught_date, species_dex_id, nature, characteristic,
                nickname, is_shiny, gender, source,
                iv_hp, iv_attack, iv_defense,
                iv_sp_attack, iv_sp_defense, iv_speed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                caught_date.isoformat(),
                species_dex_id,
                nature,
                characteristic,
                nickname,
                1 if is_shiny else 0,
                gender,
                source,
                int(ivs[0]), int(ivs[1]), int(ivs[2]),
                int(ivs[3]), int(ivs[4]), int(ivs[5]),
            ),
        )
    return int(cur.lastrowid)


def get_pokemon_for_date(d: date, path: Path | None = None) -> Pokemon | None:
    """Return the *daily* Pokemon for date ``d`` (source='daily'), or None."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            f"SELECT {_POKEMON_COLUMNS} FROM pokemon "
            "WHERE caught_date = ? AND source = 'daily' LIMIT 1",
            (d.isoformat(),),
        ).fetchone()
    return _row_to_pokemon(row) if row else None


def get_pokemon_by_id(pokemon_id: int, path: Path | None = None) -> Pokemon | None:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            f"SELECT {_POKEMON_COLUMNS} FROM pokemon WHERE id = ?",
            (pokemon_id,),
        ).fetchone()
    return _row_to_pokemon(row) if row else None


def list_pokemon(path: Path | None = None) -> list[Pokemon]:
    """Sorted by caught_date desc (newest first)."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT {_POKEMON_COLUMNS} FROM pokemon ORDER BY caught_date DESC"
        ).fetchall()
    return [_row_to_pokemon(r) for r in rows]


def update_pokemon_species(
    pokemon_id: int, new_species_dex_id: int, path: Path | None = None,
) -> None:
    """Mutate a Pokemon row's species_dex_id in place — used after evolution."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "UPDATE pokemon SET species_dex_id = ? WHERE id = ?",
            (int(new_species_dex_id), int(pokemon_id)),
        )


def update_pokemon_nickname(
    pokemon_id: int, nickname: str | None, path: Path | None = None,
) -> None:
    """Set or clear the per-instance nickname.

    Empty / whitespace-only strings collapse to NULL so callers don't need
    to special-case "user typed nothing" — the detail view treats NULL
    nickname as "fall back to the species name".
    """
    if path is None:
        path = DB_PATH
    nick = nickname.strip() if isinstance(nickname, str) else None
    if not nick:
        nick = None
    with _connect(path) as conn:
        conn.execute(
            "UPDATE pokemon SET nickname = ? WHERE id = ?",
            (nick, int(pokemon_id)),
        )


def bump_affection(
    pokemon_id: int, amount: int = 1, *, path: Path | None = None,
) -> int:
    """Increment ``pokemon.affection`` by ``amount``, capped at AFFECTION_MAX.
    Returns the new affection value, or 0 if the row doesn't exist."""
    if amount <= 0:
        return 0
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "UPDATE pokemon SET affection = MIN(?, affection + ?) WHERE id = ?",
            (AFFECTION_MAX, int(amount), int(pokemon_id)),
        )
        row = conn.execute(
            "SELECT affection FROM pokemon WHERE id = ?", (int(pokemon_id),),
        ).fetchone()
    return int(row[0]) if row is not None else 0


def set_pokemon_hp(
    pokemon_id: int, hp_current: int | None, *, path: Path | None = None,
) -> None:
    """Persist a Pokémon's post-battle current HP. ``None`` clears the
    column (= "full HP" semantics). Negative values clamp to 0
    (fainted); the next battle init auto-revives a 0-HP Pokémon to
    full so the user isn't soft-locked without a heal mechanic."""
    if path is None:
        path = DB_PATH
    if hp_current is None:
        value = None
    else:
        value = max(0, int(hp_current))
    with _connect(path) as conn:
        conn.execute(
            "UPDATE pokemon SET hp_current = ? WHERE id = ?",
            (value, int(pokemon_id)),
        )


def set_pokemon_status(
    pokemon_id: int,
    status: str,
    counter: int = 0,
    *,
    path: Path | None = None,
) -> None:
    """Persist a Pokémon's non-volatile status. ``status`` is one of the
    ``NonVolatileStatus`` string values ("healthy", "poison", "burn",
    "paralysis", "sleep", "freeze", "bad-poison"). ``counter`` carries
    sleep-turns / toxic-ramp / etc. Volatile statuses do not land here.
    """
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "UPDATE pokemon SET status_non_volatile = ?, status_counter = ? "
            "WHERE id = ?",
            (str(status), int(counter), int(pokemon_id)),
        )


def clear_pokemon_status(
    pokemon_id: int, *, path: Path | None = None,
) -> None:
    """Reset a Pokémon to ``healthy`` with counter=0. Used by Pokémon
    Center heals / status-cure items / battle-end cleanup for fainted
    Pokémon (faint clears all status per Gen-3 canon)."""
    set_pokemon_status(pokemon_id, "healthy", 0, path=path)

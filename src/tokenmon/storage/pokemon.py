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
    "bump_affection",
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


_POKEMON_COLUMNS = (
    "id, caught_date, species_dex_id, nature, characteristic, "
    "nickname, is_shiny, affection, gender"
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
    path: Path | None = None,
) -> int:
    """Insert a Pokemon row and return its id.

    Caller is responsible for "is the daily already inserted?" idempotency —
    we no longer rely on a UNIQUE(caught_date) constraint because wild
    catches can legitimately share a calendar day with the daily.
    """
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO pokemon (
                caught_date, species_dex_id, nature, characteristic,
                nickname, is_shiny, gender, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

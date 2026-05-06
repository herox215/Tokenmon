"""Per-Pokémon move slots (0..3) + current PP."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._db import DB_PATH, _connect

__all__ = [
    "PokemonMove",
    "get_pokemon_moves",
    "set_pokemon_move",
    "decrement_pp",
    "reset_pp_for_pokemon",
    "delete_pokemon_moves",
]


@dataclass(frozen=True, slots=True)
class PokemonMove:
    pokemon_id: int
    slot: int
    move_key: str
    current_pp: int


def get_pokemon_moves(
    pokemon_id: int, *, path: Path | None = None,
) -> list[PokemonMove]:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT pokemon_id, slot, move_key, current_pp "
            "FROM pokemon_moves WHERE pokemon_id = ? ORDER BY slot ASC",
            (int(pokemon_id),),
        ).fetchall()
    return [PokemonMove(*r) for r in rows]


def set_pokemon_move(
    pokemon_id: int,
    slot: int,
    move_key: str,
    *,
    max_pp: int,
    path: Path | None = None,
) -> None:
    """Upsert a move into a slot with full PP. Used by initial-moves
    backfill at catch + by the move-learn dialog."""
    if not 0 <= slot < 4:
        raise ValueError(f"slot must be in [0..4), got {slot}")
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO pokemon_moves (pokemon_id, slot, move_key, current_pp)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(pokemon_id, slot) DO UPDATE SET
                move_key = excluded.move_key,
                current_pp = excluded.current_pp
            """,
            (int(pokemon_id), int(slot), str(move_key), int(max_pp)),
        )


def decrement_pp(
    pokemon_id: int, slot: int, *, path: Path | None = None,
) -> int:
    """Decrement PP by 1 (floor 0). Returns new PP."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT current_pp FROM pokemon_moves "
            "WHERE pokemon_id = ? AND slot = ?",
            (int(pokemon_id), int(slot)),
        ).fetchone()
        if row is None:
            return 0
        new_pp = max(0, int(row[0]) - 1)
        conn.execute(
            "UPDATE pokemon_moves SET current_pp = ? "
            "WHERE pokemon_id = ? AND slot = ?",
            (new_pp, int(pokemon_id), int(slot)),
        )
    return new_pp


def reset_pp_for_pokemon(
    pokemon_id: int,
    *,
    pp_lookup,
    path: Path | None = None,
) -> None:
    """Reset every slot's current_pp to its move's max_pp.

    ``pp_lookup`` is a callable ``move_key -> max_pp`` (typically
    backed by ``moves_remote.get_move_data``). Resilient if a move
    can't be looked up — that slot stays at its current value.
    """
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT slot, move_key FROM pokemon_moves WHERE pokemon_id = ?",
            (int(pokemon_id),),
        ).fetchall()
        for slot, move_key in rows:
            try:
                max_pp = pp_lookup(move_key)
            except Exception:
                max_pp = None
            if max_pp is None:
                continue
            conn.execute(
                "UPDATE pokemon_moves SET current_pp = ? "
                "WHERE pokemon_id = ? AND slot = ?",
                (int(max_pp), int(pokemon_id), int(slot)),
            )


def delete_pokemon_moves(
    pokemon_id: int, *, path: Path | None = None,
) -> None:
    """Wipe all moves for a Pokémon — used when the row is replaced or
    re-initialised."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "DELETE FROM pokemon_moves WHERE pokemon_id = ?",
            (int(pokemon_id),),
        )

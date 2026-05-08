"""Per-Pokémon "unlocked moves" pool.

Holds every move a Pokémon has ever known: initial seed moves, level-up
auto-learns, and level-up overflow moves that didn't fit into the four
``pokemon_moves`` slots. The Box-detail "switch attack" UI reads this
table to populate its swap list.

This is a permanent record — it never auto-clears. The ``pokemon_moves``
table holds the (currently equipped) subset of these.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._db import DB_PATH, _connect

__all__ = [
    "UnlockedMove",
    "unlock_move",
    "get_unlocked_moves",
    "delete_unlocked_moves",
]


@dataclass(frozen=True, slots=True)
class UnlockedMove:
    pokemon_id: int
    move_key: str
    learned_at_level: int
    unlocked_utc: str


def unlock_move(
    pokemon_id: int,
    move_key: str,
    learned_at_level: int,
    *,
    path: Path | None = None,
) -> None:
    """Idempotent insert. Existing (pokemon_id, move_key) rows are left
    untouched so the original learned_at_level / unlocked_utc stick."""
    if path is None:
        path = DB_PATH
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO pokemon_unlocked_moves "
            "(pokemon_id, move_key, learned_at_level, unlocked_utc) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(pokemon_id, move_key) DO NOTHING",
            (int(pokemon_id), str(move_key), int(learned_at_level), ts),
        )


def get_unlocked_moves(
    pokemon_id: int, *, path: Path | None = None,
) -> list[UnlockedMove]:
    """All moves this Pokémon has ever unlocked, ordered by learn level."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT pokemon_id, move_key, learned_at_level, unlocked_utc "
            "FROM pokemon_unlocked_moves WHERE pokemon_id = ? "
            "ORDER BY learned_at_level ASC, unlocked_utc ASC",
            (int(pokemon_id),),
        ).fetchall()
    return [UnlockedMove(*r) for r in rows]


def delete_unlocked_moves(
    pokemon_id: int, *, path: Path | None = None,
) -> None:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "DELETE FROM pokemon_unlocked_moves WHERE pokemon_id = ?",
            (int(pokemon_id),),
        )

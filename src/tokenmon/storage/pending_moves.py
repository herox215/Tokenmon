"""Move-learn queue: queued at level-up, consumed by the inline dialog."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._db import DB_PATH, _connect

__all__ = [
    "PendingMoveLearn",
    "queue_move_learn",
    "query_pending_move_learns",
    "claim_pending_move_learn",
    "clear_pending_for_pokemon",
]


@dataclass(frozen=True, slots=True)
class PendingMoveLearn:
    id: int
    pokemon_id: int
    move_key: str
    learned_at_level: int
    queued_utc: str


def queue_move_learn(
    pokemon_id: int,
    move_key: str,
    learned_at_level: int,
    *,
    path: Path | None = None,
) -> int:
    """Insert a queue row. Idempotent on (pokemon_id, move_key) — if a
    pending row already exists for this move, return the existing id
    rather than queuing a duplicate."""
    if path is None:
        path = DB_PATH
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(path) as conn:
        existing = conn.execute(
            "SELECT id FROM pending_move_learns "
            "WHERE pokemon_id = ? AND move_key = ?",
            (int(pokemon_id), str(move_key)),
        ).fetchone()
        if existing is not None:
            return int(existing[0])
        cur = conn.execute(
            "INSERT INTO pending_move_learns "
            "(pokemon_id, move_key, learned_at_level, queued_utc) "
            "VALUES (?, ?, ?, ?)",
            (int(pokemon_id), str(move_key), int(learned_at_level), ts),
        )
        return int(cur.lastrowid)


def query_pending_move_learns(
    pokemon_id: int, *, path: Path | None = None,
) -> list[PendingMoveLearn]:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT id, pokemon_id, move_key, learned_at_level, queued_utc "
            "FROM pending_move_learns WHERE pokemon_id = ? "
            "ORDER BY id ASC",
            (int(pokemon_id),),
        ).fetchall()
    return [PendingMoveLearn(*r) for r in rows]


def claim_pending_move_learn(
    pending_id: int, *, path: Path | None = None,
) -> None:
    """Drop a queue row (after Learn or Skip in the UI)."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "DELETE FROM pending_move_learns WHERE id = ?",
            (int(pending_id),),
        )


def clear_pending_for_pokemon(
    pokemon_id: int, *, path: Path | None = None,
) -> None:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "DELETE FROM pending_move_learns WHERE pokemon_id = ?",
            (int(pokemon_id),),
        )

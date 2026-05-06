"""Singleton player-stats row (currently just money).

Schema enforces ``id = 1`` so all writes target the same row. The
``init_db`` migration ladder seeds the row with money=0 if missing.
"""
from __future__ import annotations

from pathlib import Path

from ._db import DB_PATH, _connect

__all__ = ["get_money", "set_money", "add_money"]


def get_money(path: Path | None = None) -> int:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT money FROM player_stats WHERE id = 1"
        ).fetchone()
    return int(row[0]) if row is not None else 0


def set_money(amount: int, *, path: Path | None = None) -> None:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "UPDATE player_stats SET money = ? WHERE id = 1",
            (max(0, int(amount)),),
        )


def add_money(delta: int, *, path: Path | None = None,
              conn=None) -> int:
    """Add (or subtract — clamped at 0) money. When ``conn`` is given,
    runs in that transaction so callers can group the money write with
    XP/item writes atomically. Returns the new balance.
    """
    if conn is not None:
        return _add_money_with_conn(conn, delta)
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        return _add_money_with_conn(conn, delta)


def _add_money_with_conn(conn, delta: int) -> int:
    row = conn.execute(
        "SELECT money FROM player_stats WHERE id = 1"
    ).fetchone()
    current = int(row[0]) if row is not None else 0
    new = max(0, current + int(delta))
    conn.execute(
        "UPDATE player_stats SET money = ? WHERE id = 1", (new,),
    )
    return new

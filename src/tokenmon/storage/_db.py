"""Connection helper + path constants. The lowest layer in the storage
package; every other submodule imports from here.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = [
    "DB_DIR",
    "DB_PATH",
    "BALL_THRESHOLDS",
    "BALL_CAP",
    "AFFECTION_MAX",
    "_connect",
]

DB_DIR = Path.home() / ".tokenmon"
DB_PATH = DB_DIR / "usage.db"

BALL_CAP = 99
AFFECTION_MAX = 255  # Gen-2 friendship cap; we use the same scale.


def _build_ball_thresholds() -> dict[str, int]:
    """Re-exported for compat — derived from the items registry so a single
    edit in ``tokenmon.items`` propagates everywhere."""
    from tokenmon.items import ITEMS  # local import to avoid cycle
    return {
        k: ITEMS[k].threshold
        for k in ("pokeball", "greatball", "ultraball", "masterball")
        if k in ITEMS
    }


BALL_THRESHOLDS: dict[str, int] = _build_ball_thresholds()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a short-lived SQLite connection with WAL + reasonable defaults.

    ``path=None`` uses the module-level ``DB_PATH``; tests pass a tmp path
    so they never touch the user's real DB.
    """
    if path is None:
        path = DB_PATH
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

"""Active-Pokemon resolution — leaf module that storage.usage and box can
both import without going through each other.

The function answers "which Pokemon row should this request's XP go to?"
and tries (in order) the user's pinned active, then today's daily row.
Returns None when the box is empty.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def resolve_trained_pokemon_id(
    conn: sqlite3.Connection, tz_name: str = "Europe/Berlin"
) -> int | None:
    """Best-effort lookup. Tries config['active_pokemon_id'] then today's
    daily row. Returns None if neither resolves.

    Imports config + box lazily so this module stays a leaf — callers that
    only want the resolver can import without dragging the whole box layer.
    """
    try:
        from tokenmon import config
        active = config.get("active_pokemon_id")
    except Exception:
        active = None
    if isinstance(active, int):
        return active

    try:
        from tokenmon.box import get_today_pokemon_id
        try:
            tid = get_today_pokemon_id()
        except TypeError:
            tid = None
        if isinstance(tid, int):
            return tid
    except (ImportError, AttributeError):
        pass
    except Exception:
        pass

    try:
        today_iso = datetime.now(ZoneInfo(tz_name)).date().isoformat()
        row = conn.execute(
            "SELECT id FROM pokemon WHERE caught_date = ?",
            (today_iso,),
        ).fetchone()
        if row is not None:
            return int(row[0])
    except Exception:
        pass
    return None

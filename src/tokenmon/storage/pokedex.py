"""Pokedex aggregation queries over the requests table.

These look at "the daily species for each historical day with traffic" and
sum XP per dex_id — the per-species view the Pokedex pane renders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ._db import DB_PATH, _connect

__all__ = [
    "PokedexEntry",
    "query_pokedex",
    "query_pokemon_xp",
    "mark_seen",
    "mark_caught",
    "query_pokedex_seen",
]


@dataclass(slots=True)
class PokedexEntry:
    dex_id: int
    xp: int
    days: int
    first_seen: date
    last_seen: date


def _tokens_per_local_day(
    tz_name: str, path: Path
) -> list[tuple[date, int]]:
    """Return [(local_date, sum_output_tokens), ...] sorted by date.

    XP buckets count output tokens only — input tokens are skewed by each
    agent's system-prompt overhead, which differs across providers and
    isn't comparable.
    """
    tz = ZoneInfo(tz_name)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT ts_utc, output_tokens FROM requests"
        ).fetchall()
    by_day: dict[date, int] = {}
    for ts_str, tokens in rows:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        local_day = ts.astimezone(tz).date()
        by_day[local_day] = by_day.get(local_day, 0) + int(tokens or 0)
    return sorted((d, t) for d, t in by_day.items() if t > 0)


def query_pokedex(
    tz_name: str = "Europe/Berlin", path: Path | None = None
) -> dict[int, PokedexEntry]:
    """Returns {dex_id: PokedexEntry} for every Pokemon that has been the
    daily pick on at least one date with traffic."""
    from tokenmon.pokemon import pick_for_today

    if path is None:
        path = DB_PATH
    out: dict[int, PokedexEntry] = {}
    for day, tokens in _tokens_per_local_day(tz_name, path):
        dex_id = pick_for_today(day)
        entry = out.get(dex_id)
        if entry is None:
            out[dex_id] = PokedexEntry(
                dex_id=dex_id, xp=tokens, days=1, first_seen=day, last_seen=day
            )
        else:
            entry.xp += tokens
            entry.days += 1
            entry.first_seen = min(entry.first_seen, day)
            entry.last_seen = max(entry.last_seen, day)
    return out


def query_pokemon_xp(
    dex_id: int, tz_name: str = "Europe/Berlin", path: Path | None = None
) -> int:
    """Total XP for a single Pokemon across all days it was the daily pick."""
    from tokenmon.pokemon import pick_for_today

    if path is None:
        path = DB_PATH
    return sum(
        tokens
        for day, tokens in _tokens_per_local_day(tz_name, path)
        if pick_for_today(day) == dex_id
    )


# --- pokedex_seen — persistent dex-entry log -----------------------------
#
# This table is the source of truth for "have I seen / caught this species?".
# It's append-only (never deleted), so a future "release" feature that
# removes a pokemon row from the box will not lose the Pokedex entry.
# Status promotes 'seen' → 'caught' when the user catches a previously-only-
# encountered species, and once 'caught' it stays 'caught'.


def mark_seen(dex_id: int, *, path: Path | None = None) -> None:
    """Record that the user has encountered ``dex_id`` (no catch needed).

    No-op if the species already has a 'seen' or 'caught' entry — only the
    very first encounter timestamp is preserved.
    """
    if path is None:
        path = DB_PATH
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO pokedex_seen (dex_id, status, first_seen_utc)
            VALUES (?, 'seen', ?)
            ON CONFLICT(dex_id) DO NOTHING
            """,
            (int(dex_id), now),
        )


def mark_caught(dex_id: int, *, path: Path | None = None) -> None:
    """Promote ``dex_id`` to 'caught'. If the species was previously only
    'seen' (or wasn't tracked at all), the row is created or upgraded; the
    earliest seen timestamp is preserved, and ``first_caught_utc`` is set
    on the very first promotion."""
    if path is None:
        path = DB_PATH
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO pokedex_seen
                (dex_id, status, first_seen_utc, first_caught_utc)
            VALUES (?, 'caught', ?, ?)
            ON CONFLICT(dex_id) DO UPDATE SET
                status = 'caught',
                first_caught_utc = COALESCE(
                    first_caught_utc, excluded.first_caught_utc
                )
            """,
            (int(dex_id), now, now),
        )


def query_pokedex_seen(
    path: Path | None = None,
) -> dict[int, str]:
    """Return ``{dex_id: status}`` for every Pokedex entry on file.

    ``status`` is ``'seen'`` or ``'caught'``. The Pokedex pane reads from
    this so it doesn't have to derive caught-state from live box rows.
    """
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT dex_id, status FROM pokedex_seen"
        ).fetchall()
    return {int(d): s for d, s in rows}

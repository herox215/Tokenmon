"""Usage / requests table layer + active-pokemon resolver.

The active-pokemon resolver lives here for now; Wave C moves it to a
dedicated ``tokenmon/active.py`` and breaks the lazy box import.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ._db import DB_PATH, _connect

__all__ = [
    "Usage",
    "Totals",
    "insert_usage",
    "query_today",
    "query_today_by_model",
    "query_today_token_buckets",
    "query_xp_for_date",
    "query_xp_for_pokemon",
    "latest_request_ts",
    "backfill_trained_pokemon_ids",
]


@dataclass(slots=True)
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stop_reason: str | None = None
    request_id: str | None = None
    duration_ms: int | None = None


@dataclass(slots=True)
class Totals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    request_count: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


def insert_usage(usage: Usage, path: Path | None = None) -> None:
    from tokenmon.active import resolve_trained_pokemon_id
    from tokenmon.items import roll_item_drops
    from .encounter import add_to_pending

    if path is None:
        path = DB_PATH
    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        trained_id = resolve_trained_pokemon_id(conn)
        conn.execute(
            """
            INSERT INTO requests (
                ts_utc, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens,
                stop_reason, request_id, duration_ms,
                trained_pokemon_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                usage.model,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_creation_tokens,
                usage.stop_reason,
                usage.request_id,
                usage.duration_ms,
                trained_id,
            ),
        )
    # After the request lands, roll drops for every item with a tok_chance.
    # Lives outside the request-insert connection so a drop failure can't
    # roll back the usage record.
    import logging
    log = logging.getLogger("tokenmon.proxy")
    out_tokens = usage.output_tokens or 0
    drops = roll_item_drops(out_tokens)
    log.info("item-drop roll: model=%s output_tokens=%s drops=%s",
             usage.model, out_tokens, drops or "{}")
    for key, count in drops.items():
        try:
            # Drops park in pending_drops until the user opens the Items
            # pane and claims them — gives them a chance to see what
            # they found via the claim animation.
            add_to_pending(key, count, path=path)
        except Exception:
            log.exception("add_to_pending(%s, %s) failed", key, count)


def _today_utc_bounds(tz_name: str) -> tuple[str, str]:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        end_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
    )


def _local_day_utc_bounds(d: date, tz_name: str) -> tuple[str, str]:
    """Return (start_utc_iso, end_utc_iso) for local date `d`."""
    tz = ZoneInfo(tz_name)
    start_local = datetime(d.year, d.month, d.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        end_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
    )


def query_today(tz_name: str = "Europe/Berlin", path: Path | None = None) -> Totals:
    if path is None:
        path = DB_PATH
    start, end = _today_utc_bounds(tz_name)
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(output_tokens), 0),
                COALESCE(SUM(cache_read_tokens), 0),
                COALESCE(SUM(cache_creation_tokens), 0),
                COUNT(*)
            FROM requests
            WHERE ts_utc >= ? AND ts_utc < ?
            """,
            (start, end),
        ).fetchone()
    return Totals(*row)


def query_today_by_model(
    tz_name: str = "Europe/Berlin", path: Path | None = None
) -> dict[str, Totals]:
    if path is None:
        path = DB_PATH
    start, end = _today_utc_bounds(tz_name)
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT
                model,
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(output_tokens), 0),
                COALESCE(SUM(cache_read_tokens), 0),
                COALESCE(SUM(cache_creation_tokens), 0),
                COUNT(*)
            FROM requests
            WHERE ts_utc >= ? AND ts_utc < ?
            GROUP BY model
            ORDER BY SUM(input_tokens + output_tokens) DESC
            """,
            (start, end),
        ).fetchall()
    return {row[0]: Totals(*row[1:]) for row in rows}


def query_today_token_buckets(
    tz_name: str = "Europe/Berlin",
    bucket_minutes: int = 15,
    path: Path | None = None,
) -> list[int]:
    """Output-token sums per fixed-size time bucket across the local day.

    Returns a list of length ``1440 // bucket_minutes`` (default 96 for
    15 min). Index 0 is 00:00–00:15 local, index -1 is 23:45–24:00 local.
    Empty buckets sit at 0.

    DST note: on fall-back days the local clock visits 02:00–03:00 twice,
    so those buckets accumulate both passes. On spring-forward days that
    hour simply stays empty. We don't try to disentangle the wall-clock
    duplicate — `_today_utc_bounds` already clamps the day window, and
    visually showing the second pass merged into the same slot is the
    least surprising option for a usage chart.
    """
    if path is None:
        path = DB_PATH
    if 1440 % bucket_minutes != 0:
        raise ValueError(
            f"bucket_minutes must divide 1440 evenly, got {bucket_minutes}"
        )
    n_buckets = 1440 // bucket_minutes
    buckets = [0] * n_buckets
    start, end = _today_utc_bounds(tz_name)
    tz = ZoneInfo(tz_name)
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT ts_utc, output_tokens FROM requests
            WHERE ts_utc >= ? AND ts_utc < ?
            """,
            (start, end),
        ).fetchall()
    for ts_str, out_tokens in rows:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        local = ts.astimezone(tz)
        idx = (local.hour * 60 + local.minute) // bucket_minutes
        if 0 <= idx < n_buckets:
            buckets[idx] += int(out_tokens or 0)
    return buckets


def query_xp_for_date(
    d: date, tz_name: str = "Europe/Berlin", path: Path | None = None
) -> int:
    """Sum of output_tokens whose local date equals ``d``."""
    if path is None:
        path = DB_PATH
    start, end = _local_day_utc_bounds(d, tz_name)
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(output_tokens), 0) FROM requests
            WHERE ts_utc >= ? AND ts_utc < ?
            """,
            (start, end),
        ).fetchone()
    return int(row[0])


def query_xp_for_pokemon(pokemon_id: int, path: Path | None = None) -> int:
    """Sum of output_tokens for requests trained against this Pokemon."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(output_tokens), 0) FROM requests
            WHERE trained_pokemon_id = ?
            """,
            (pokemon_id,),
        ).fetchone()
    return int(row[0])


def latest_request_ts(path: Path | None = None) -> datetime | None:
    """Timestamp of the most recent ``requests`` row (UTC), or None."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT ts_utc FROM requests ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    ts = datetime.fromisoformat(row[0])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def backfill_trained_pokemon_ids(
    tz_name: str = "Europe/Berlin", path: Path | None = None
) -> int:
    """For every requests row where trained_pokemon_id IS NULL, set it to the
    pokemon row whose caught_date matches the request's local date. Returns
    the number of rows updated. Idempotent."""
    if path is None:
        path = DB_PATH
    tz = ZoneInfo(tz_name)
    updated = 0
    with _connect(path) as conn:
        pokemon_by_date: dict[str, int] = {
            row[1]: int(row[0])
            for row in conn.execute("SELECT id, caught_date FROM pokemon")
        }
        if not pokemon_by_date:
            return 0
        rows = conn.execute(
            "SELECT id, ts_utc FROM requests WHERE trained_pokemon_id IS NULL"
        ).fetchall()
        for req_id, ts_str in rows:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            local_iso = ts.astimezone(tz).date().isoformat()
            pid = pokemon_by_date.get(local_iso)
            if pid is None:
                continue
            conn.execute(
                "UPDATE requests SET trained_pokemon_id = ? WHERE id = ?",
                (pid, req_id),
            )
            updated += 1
    return updated

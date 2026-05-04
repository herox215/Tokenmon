"""SQLite storage for token usage records."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DB_DIR = Path.home() / ".tokenmon"
DB_PATH = DB_DIR / "usage.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    request_id TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_requests_ts_utc ON requests(ts_utc);
CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model);

CREATE TABLE IF NOT EXISTS pokemon (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caught_date TEXT NOT NULL,
    species_dex_id INTEGER NOT NULL,
    nature TEXT NOT NULL,
    characteristic TEXT NOT NULL,
    nickname TEXT,
    is_shiny INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pokemon_caught_date ON pokemon(caught_date);
CREATE INDEX IF NOT EXISTS idx_pokemon_species ON pokemon(species_dex_id);
"""


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


def _connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(SCHEMA)


def insert_usage(usage: Usage, path: Path = DB_PATH) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO requests (
                ts_utc, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens,
                stop_reason, request_id, duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )


def _today_utc_bounds(tz_name: str) -> tuple[str, str]:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        end_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
    )


def query_today(tz_name: str = "Europe/Berlin", path: Path = DB_PATH) -> Totals:
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
    tz_name: str = "Europe/Berlin", path: Path = DB_PATH
) -> dict[str, Totals]:
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

    XP buckets count output tokens only — input tokens are heavily skewed by
    each agent's system-prompt / tool-definitions overhead, and the size of
    that overhead differs wildly across providers (Anthropic with caching vs
    OpenRouter without). Output is the only token category that consistently
    reflects actual model engagement and is comparable across providers.

    Days with zero output tokens are dropped so the Tokendex never shows a
    Pokemon you didn't earn any XP for.
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
    tz_name: str = "Europe/Berlin", path: Path = DB_PATH
) -> dict[int, PokedexEntry]:
    """Returns {dex_id: PokedexEntry} for every Pokemon that has ever been the
    daily pick on a date with at least one recorded request. XP carries across
    repeated days for the same Pokemon (since the daily pick is deterministic)."""
    from tokenmon.pokemon import pick_for_today  # avoid import cycle at module load

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
    dex_id: int, tz_name: str = "Europe/Berlin", path: Path = DB_PATH
) -> int:
    """Total XP for a single Pokemon across all days it was the daily pick."""
    from tokenmon.pokemon import pick_for_today

    return sum(
        tokens
        for day, tokens in _tokens_per_local_day(tz_name, path)
        if pick_for_today(day) == dex_id
    )


@dataclass(slots=True)
class Pokemon:
    id: int
    caught_date: date
    species_dex_id: int
    nature: str
    characteristic: str
    nickname: str | None
    is_shiny: bool


def _row_to_pokemon(row: tuple) -> Pokemon:
    return Pokemon(
        id=row[0],
        caught_date=date.fromisoformat(row[1]),
        species_dex_id=row[2],
        nature=row[3],
        characteristic=row[4],
        nickname=row[5],
        is_shiny=bool(row[6]),
    )


def insert_pokemon(
    caught_date: date,
    species_dex_id: int,
    nature: str,
    characteristic: str,
    *,
    nickname: str | None = None,
    is_shiny: bool = False,
    path: Path = DB_PATH,
) -> int:
    """Insert a Pokemon row. Returns the new id. Idempotent on (caught_date) —
    if a row already exists for that date, returns the existing id without
    overwriting."""
    caught_date_str = caught_date.isoformat()
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO pokemon (
                caught_date, species_dex_id, nature, characteristic,
                nickname, is_shiny
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(caught_date) DO NOTHING
            """,
            (
                caught_date_str,
                species_dex_id,
                nature,
                characteristic,
                nickname,
                1 if is_shiny else 0,
            ),
        )
        row = conn.execute(
            "SELECT id FROM pokemon WHERE caught_date = ?",
            (caught_date_str,),
        ).fetchone()
    return int(row[0])


def get_pokemon_for_date(d: date, path: Path = DB_PATH) -> Pokemon | None:
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT id, caught_date, species_dex_id, nature, characteristic,
                   nickname, is_shiny
            FROM pokemon
            WHERE caught_date = ?
            """,
            (d.isoformat(),),
        ).fetchone()
    return _row_to_pokemon(row) if row else None


def get_pokemon_by_id(pokemon_id: int, path: Path = DB_PATH) -> Pokemon | None:
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT id, caught_date, species_dex_id, nature, characteristic,
                   nickname, is_shiny
            FROM pokemon
            WHERE id = ?
            """,
            (pokemon_id,),
        ).fetchone()
    return _row_to_pokemon(row) if row else None


def list_pokemon(path: Path = DB_PATH) -> list[Pokemon]:
    """Sorted by caught_date desc (newest first)."""
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT id, caught_date, species_dex_id, nature, characteristic,
                   nickname, is_shiny
            FROM pokemon
            ORDER BY caught_date DESC
            """
        ).fetchall()
    return [_row_to_pokemon(r) for r in rows]


def _local_day_utc_bounds(d: date, tz_name: str) -> tuple[str, str]:
    """Return (start_utc_iso, end_utc_iso) for local date `d` in `tz_name`."""
    tz = ZoneInfo(tz_name)
    start_local = datetime(d.year, d.month, d.day, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
        end_local.astimezone(timezone.utc).isoformat(timespec="microseconds"),
    )


def query_xp_for_date(
    d: date, tz_name: str = "Europe/Berlin", path: Path = DB_PATH
) -> int:
    """Sum of output_tokens from `requests` whose ts_utc, converted to
    `tz_name`, falls on local date `d`. Output-tokens-only — same XP rule
    as the rest of the codebase."""
    start, end = _local_day_utc_bounds(d, tz_name)
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(output_tokens), 0)
            FROM requests
            WHERE ts_utc >= ? AND ts_utc < ?
            """,
            (start, end),
        ).fetchone()
    return int(row[0])

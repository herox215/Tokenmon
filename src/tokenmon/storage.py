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

CREATE TABLE IF NOT EXISTS encounters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spawned_utc TEXT NOT NULL,
    species_dex_id INTEGER NOT NULL,
    nature TEXT NOT NULL,
    characteristic TEXT NOT NULL,
    level INTEGER NOT NULL,
    catch_rate INTEGER NOT NULL,
    pokeballs_used INTEGER NOT NULL DEFAULT 0,
    greatballs_used INTEGER NOT NULL DEFAULT 0,
    ultraballs_used INTEGER NOT NULL DEFAULT 0,
    masterballs_used INTEGER NOT NULL DEFAULT 0,
    resolved TEXT,
    resolved_utc TEXT,
    pokemon_id INTEGER,
    last_hint TEXT
);
CREATE INDEX IF NOT EXISTS idx_encounters_resolved ON encounters(resolved);

CREATE TABLE IF NOT EXISTS encounter_item_uses (
    encounter_id INTEGER NOT NULL,
    item_key     TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (encounter_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_encounter_item_uses_encounter ON encounter_item_uses(encounter_id);
CREATE INDEX IF NOT EXISTS idx_encounter_item_uses_key ON encounter_item_uses(item_key);
"""


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
BALL_CAP = 99


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


def _ensure_trained_pokemon_column(conn: sqlite3.Connection) -> None:
    """SQLite ALTER TABLE has no IF NOT EXISTS, so we check pragma first.
    Idempotent — safe to call on every init_db()."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
    if "trained_pokemon_id" not in cols:
        conn.execute("ALTER TABLE requests ADD COLUMN trained_pokemon_id INTEGER")
    # Index creation is naturally idempotent.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_requests_trained "
        "ON requests(trained_pokemon_id)"
    )


AFFECTION_MAX = 255  # Gen-2 friendship cap; we use the same scale.


def _ensure_affection_column(conn: sqlite3.Connection) -> None:
    """Idempotent migration: adds ``affection`` to ``pokemon`` if missing."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pokemon)")}
    if "affection" not in cols:
        conn.execute(
            "ALTER TABLE pokemon ADD COLUMN affection INTEGER NOT NULL DEFAULT 0"
        )


# Mapping of legacy ``encounters.<col>`` columns to the corresponding item key
# in the new generic ``encounter_item_uses`` junction table. Used only by the
# one-shot migration helper below.
_LEGACY_BALL_COLUMNS: dict[str, str] = {
    "pokeballs_used": "pokeball",
    "greatballs_used": "greatball",
    "ultraballs_used": "ultraball",
    "masterballs_used": "masterball",
}


def _migrate_encounter_balls_to_items(conn: sqlite3.Connection) -> None:
    """One-shot migration: copy legacy ``encounters.*_used`` columns into
    ``encounter_item_uses`` rows.

    Idempotent: only runs when ``encounter_item_uses`` is empty AND the legacy
    columns still exist. Skips zero-count entries and never overwrites an
    existing (encounter_id, item_key) row. Old columns stay in place — we
    don't ALTER TABLE DROP COLUMN; they simply go unused.
    """
    # If anything has been written to the new table, the migration has
    # effectively already happened — bail to keep things idempotent.
    existing = conn.execute(
        "SELECT 1 FROM encounter_item_uses LIMIT 1"
    ).fetchone()
    if existing is not None:
        return

    enc_cols = {row[1] for row in conn.execute("PRAGMA table_info(encounters)")}
    legacy_cols = [c for c in _LEGACY_BALL_COLUMNS if c in enc_cols]
    if not legacy_cols:
        return

    select_sql = "SELECT id, " + ", ".join(legacy_cols) + " FROM encounters"
    for row in conn.execute(select_sql).fetchall():
        enc_id = int(row[0])
        for idx, col in enumerate(legacy_cols, start=1):
            count = int(row[idx] or 0)
            if count <= 0:
                continue
            item_key = _LEGACY_BALL_COLUMNS[col]
            conn.execute(
                """
                INSERT INTO encounter_item_uses (encounter_id, item_key, count)
                VALUES (?, ?, ?)
                ON CONFLICT(encounter_id, item_key) DO NOTHING
                """,
                (enc_id, item_key, count),
            )


def init_db(path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        _ensure_trained_pokemon_column(conn)
        _ensure_affection_column(conn)
        _migrate_encounter_balls_to_items(conn)


def _resolve_trained_pokemon_id(
    conn: sqlite3.Connection, tz_name: str = "Europe/Berlin"
) -> int | None:
    """Best-effort lookup for the Pokemon a request should be attributed to.

    Tries (in order):
    1. ``config.get("active_pokemon_id")`` — the user's manually-pinned pick.
    2. The pokemon row whose ``caught_date`` equals today's local date.

    Returns None if neither resolves. Lazy imports keep this safe against
    import cycles since ``config`` imports from this module.
    """
    # Step 1: active pokemon from config.
    try:
        from tokenmon import config  # local import to avoid cycle
        active = config.get("active_pokemon_id")
    except Exception:
        active = None
    if isinstance(active, int):
        return active

    # Step 2: today's catch via box helper if available (Phase 2 will add it).
    try:
        from tokenmon.box import get_today_pokemon_id  # type: ignore[attr-defined]
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

    # Step 3: direct query — today's row in pokemon table.
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


def insert_usage(usage: Usage, path: Path = DB_PATH) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        trained_id = _resolve_trained_pokemon_id(conn)
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
    affection: int = 0


_POKEMON_COLUMNS = (
    "id, caught_date, species_dex_id, nature, characteristic, "
    "nickname, is_shiny, affection"
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
            f"SELECT {_POKEMON_COLUMNS} FROM pokemon WHERE caught_date = ?",
            (d.isoformat(),),
        ).fetchone()
    return _row_to_pokemon(row) if row else None


def get_pokemon_by_id(pokemon_id: int, path: Path = DB_PATH) -> Pokemon | None:
    with _connect(path) as conn:
        row = conn.execute(
            f"SELECT {_POKEMON_COLUMNS} FROM pokemon WHERE id = ?",
            (pokemon_id,),
        ).fetchone()
    return _row_to_pokemon(row) if row else None


def latest_request_ts(path: Path = DB_PATH) -> datetime | None:
    """Timestamp of the most recent ``requests`` row (UTC), or None when the
    table is empty. Used by the affection idle gate."""
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


def bump_affection(
    pokemon_id: int, amount: int = 1, *, path: Path = DB_PATH,
) -> int:
    """Increment ``pokemon.affection`` by ``amount``, capped at AFFECTION_MAX.

    Returns the new affection value, or 0 if the row doesn't exist.
    """
    if amount <= 0:
        return 0
    with _connect(path) as conn:
        conn.execute(
            "UPDATE pokemon SET affection = MIN(?, affection + ?) WHERE id = ?",
            (AFFECTION_MAX, int(amount), int(pokemon_id)),
        )
        row = conn.execute(
            "SELECT affection FROM pokemon WHERE id = ?", (int(pokemon_id),),
        ).fetchone()
    return int(row[0]) if row is not None else 0


def list_distinct_encounter_species(path: Path = DB_PATH) -> set[int]:
    """Returns the set of species_dex_id values that appear in the encounters
    table — used by the Pokedex to mark species you've SEEN (encountered but
    haven't necessarily caught)."""
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT species_dex_id FROM encounters"
        ).fetchall()
    return {int(row[0]) for row in rows}


def update_pokemon_species(pokemon_id: int, new_species_dex_id: int,
                            path: Path = DB_PATH) -> None:
    """Mutate a Pokemon row's species_dex_id in place. Used when an instance
    evolves so the row reflects its new form (instead of always showing the
    base species and deriving the displayed form at render time)."""
    with _connect(path) as conn:
        conn.execute(
            "UPDATE pokemon SET species_dex_id = ? WHERE id = ?",
            (int(new_species_dex_id), int(pokemon_id)),
        )


def list_pokemon(path: Path = DB_PATH) -> list[Pokemon]:
    """Sorted by caught_date desc (newest first)."""
    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT {_POKEMON_COLUMNS} FROM pokemon ORDER BY caught_date DESC"
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


def query_xp_for_pokemon(pokemon_id: int, path: Path = DB_PATH) -> int:
    """Sum of output_tokens for requests trained against this Pokemon."""
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(output_tokens), 0)
            FROM requests
            WHERE trained_pokemon_id = ?
            """,
            (pokemon_id,),
        ).fetchone()
    return int(row[0])


# --- Encounters -----------------------------------------------------------


@dataclass(slots=True)
class Encounter:
    id: int
    spawned_utc: datetime
    species_dex_id: int
    nature: str
    characteristic: str
    level: int
    catch_rate: int
    pokeballs_used: int
    greatballs_used: int
    ultraballs_used: int
    masterballs_used: int
    resolved: str | None
    resolved_utc: datetime | None
    pokemon_id: int | None
    last_hint: str | None


def _parse_utc(ts: str | None) -> datetime | None:
    if ts is None:
        return None
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_encounter(row: tuple) -> Encounter:
    return Encounter(
        id=int(row[0]),
        spawned_utc=_parse_utc(row[1]),  # type: ignore[arg-type]
        species_dex_id=int(row[2]),
        nature=row[3],
        characteristic=row[4],
        level=int(row[5]),
        catch_rate=int(row[6]),
        pokeballs_used=int(row[7]),
        greatballs_used=int(row[8]),
        ultraballs_used=int(row[9]),
        masterballs_used=int(row[10]),
        resolved=row[11],
        resolved_utc=_parse_utc(row[12]),
        pokemon_id=int(row[13]) if row[13] is not None else None,
        last_hint=row[14],
    )


_ENCOUNTER_COLS = (
    "id, spawned_utc, species_dex_id, nature, characteristic, level, "
    "catch_rate, pokeballs_used, greatballs_used, ultraballs_used, "
    "masterballs_used, resolved, resolved_utc, pokemon_id, last_hint"
)


def insert_encounter(
    species_dex_id: int,
    nature: str,
    characteristic: str,
    level: int,
    catch_rate: int,
    *,
    path: Path = DB_PATH,
) -> int:
    """Insert a fresh pending encounter and return its id.

    The caller is responsible for ensuring no other pending encounter exists —
    we don't enforce that at the DB layer.
    """
    spawned = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO encounters (
                spawned_utc, species_dex_id, nature, characteristic,
                level, catch_rate
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (spawned, species_dex_id, nature, characteristic, level, catch_rate),
        )
        return int(cur.lastrowid)


def get_pending_encounter(path: Path = DB_PATH) -> Encounter | None:
    """Return the unresolved encounter (resolved IS NULL), or None."""
    with _connect(path) as conn:
        row = conn.execute(
            f"""
            SELECT {_ENCOUNTER_COLS}
            FROM encounters
            WHERE resolved IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
        ).fetchone()
    return _row_to_encounter(row) if row else None


def increment_item_used(
    encounter_id: int, item_key: str, n: int = 1, path: Path = DB_PATH
) -> None:
    """Add ``n`` to the count of ``item_key`` used against ``encounter_id``.

    Inserts a row on first use and increments thereafter. Raises ``ValueError``
    if ``item_key`` is unknown to the items registry.
    """
    from tokenmon.items import ITEMS  # local import to avoid cycle
    if item_key not in ITEMS:
        raise ValueError(f"unknown item_key: {item_key!r}")
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO encounter_item_uses (encounter_id, item_key, count)
            VALUES (?, ?, ?)
            ON CONFLICT(encounter_id, item_key)
            DO UPDATE SET count = count + excluded.count
            """,
            (encounter_id, item_key, int(n)),
        )


def increment_ball_used(
    encounter_id: int, ball_type: str, path: Path = DB_PATH
) -> None:
    """Backwards-compat shim — delegates to :func:`increment_item_used`."""
    increment_item_used(encounter_id, ball_type, n=1, path=path)


def mark_encounter_caught(
    encounter_id: int, pokemon_id: int, path: Path = DB_PATH
) -> None:
    """Set resolved='caught', resolved_utc=now, pokemon_id."""
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        conn.execute(
            """
            UPDATE encounters
            SET resolved = 'caught', resolved_utc = ?, pokemon_id = ?
            WHERE id = ?
            """,
            (now, pokemon_id, encounter_id),
        )


def mark_encounter_ran(encounter_id: int, path: Path = DB_PATH) -> None:
    """Set resolved='ran', resolved_utc=now."""
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        conn.execute(
            """
            UPDATE encounters
            SET resolved = 'ran', resolved_utc = ?
            WHERE id = ?
            """,
            (now, encounter_id),
        )


def update_encounter_hint(
    encounter_id: int, hint: str, path: Path = DB_PATH
) -> None:
    """Store the most recent flavour hint shown to the user."""
    with _connect(path) as conn:
        conn.execute(
            "UPDATE encounters SET last_hint = ? WHERE id = ?",
            (hint, encounter_id),
        )


def query_item_counts(
    item_keys: list[str] | None = None, path: Path = DB_PATH
) -> dict[str, int]:
    """Return ``{item_key: count}`` clamped to ``[0, item.cap]``.

    Earned = ``floor(SUM(output_tokens) / item.threshold)`` over all requests.
    Used  = ``SUM(count)`` over ``encounter_item_uses`` for that key. Result =
    earned − used, clamped to the item's cap.

    If ``item_keys`` is None, returns counts for every key registered in
    :mod:`tokenmon.items`.
    """
    from tokenmon.items import ITEMS  # local import to avoid cycle

    keys = list(item_keys) if item_keys is not None else list(ITEMS.keys())
    out: dict[str, int] = {}
    if not keys:
        return out

    with _connect(path) as conn:
        total_out_row = conn.execute(
            "SELECT COALESCE(SUM(output_tokens), 0) FROM requests"
        ).fetchone()
        total_out = int(total_out_row[0] or 0)
        # One query per key keeps the SQL trivial and avoids dynamic IN-list
        # binding gymnastics. Item lists are tiny (<10 entries today).
        used_by_key: dict[str, int] = {}
        for key in keys:
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM encounter_item_uses "
                "WHERE item_key = ?",
                (key,),
            ).fetchone()
            used_by_key[key] = int(row[0] or 0)

    for key in keys:
        item = ITEMS.get(key)
        if item is None:
            # Unknown key — surface 0 rather than raise; lets callers query
            # legacy keys safely during transitions.
            out[key] = 0
            continue
        earned = total_out // item.threshold if item.threshold > 0 else 0
        remaining = earned - used_by_key.get(key, 0)
        if remaining < 0:
            remaining = 0
        if remaining > item.cap:
            remaining = item.cap
        out[key] = remaining
    return out


def query_ball_counts(path: Path = DB_PATH) -> dict[str, int]:
    """Backwards-compat shim — returns counts for the four ball items in the
    legacy order. Delegates to :func:`query_item_counts`."""
    return query_item_counts(
        ["pokeball", "greatball", "ultraball", "masterball"], path=path
    )


def backfill_trained_pokemon_ids(
    tz_name: str = "Europe/Berlin", path: Path = DB_PATH
) -> int:
    """For every requests row where trained_pokemon_id IS NULL, set it to the
    pokemon row whose caught_date matches the request's local date. Returns
    the number of rows updated. Idempotent — already-set rows are skipped."""
    tz = ZoneInfo(tz_name)
    updated = 0
    with _connect(path) as conn:
        # Build a date -> pokemon_id map once.
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

"""Schema definition + idempotent migration helpers.

``init_db()`` runs the full migration ladder. Each ``_ensure_*`` and
``_migrate_*`` helper is idempotent so repeat calls are cheap and safe.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ._db import DB_PATH, _connect

__all__ = ["SCHEMA", "init_db"]

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

CREATE TABLE IF NOT EXISTS pokedex_seen (
    dex_id            INTEGER PRIMARY KEY,
    status            TEXT    NOT NULL CHECK (status IN ('seen', 'caught')),
    first_seen_utc    TEXT    NOT NULL,
    first_caught_utc  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pokedex_seen_status ON pokedex_seen(status);

CREATE TABLE IF NOT EXISTS inventory (
    item_key TEXT PRIMARY KEY,
    count    INTEGER NOT NULL DEFAULT 0
);

-- Items dropped via the per-token lottery land here first; the user sees a
-- "you found N items" badge and a small claim animation surfaces them
-- before they merge into the regular inventory. Persistent so a restart
-- doesn't swallow drops the user hasn't seen yet.
CREATE TABLE IF NOT EXISTS pending_drops (
    item_key TEXT PRIMARY KEY,
    count    INTEGER NOT NULL DEFAULT 0
);
"""


def _ensure_trained_pokemon_column(conn: sqlite3.Connection) -> None:
    """SQLite ALTER TABLE has no IF NOT EXISTS, so we check pragma first."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
    if "trained_pokemon_id" not in cols:
        conn.execute("ALTER TABLE requests ADD COLUMN trained_pokemon_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_requests_trained "
        "ON requests(trained_pokemon_id)"
    )


def _ensure_affection_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pokemon)")}
    if "affection" not in cols:
        conn.execute(
            "ALTER TABLE pokemon ADD COLUMN affection INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_gender_shiny_columns(conn: sqlite3.Connection) -> None:
    """Adds ``gender`` to pokemon/encounters and ``is_shiny`` to encounters.
    ``pokemon.is_shiny`` already exists in the original schema."""
    pcols = {row[1] for row in conn.execute("PRAGMA table_info(pokemon)")}
    if "gender" not in pcols:
        conn.execute("ALTER TABLE pokemon ADD COLUMN gender TEXT")
    ecols = {row[1] for row in conn.execute("PRAGMA table_info(encounters)")}
    if "gender" not in ecols:
        conn.execute("ALTER TABLE encounters ADD COLUMN gender TEXT")
    if "is_shiny" not in ecols:
        conn.execute(
            "ALTER TABLE encounters ADD COLUMN is_shiny INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_pokemon_source_column(conn: sqlite3.Connection) -> None:
    """Add ``pokemon.source`` ('daily' vs 'wild') and drop the legacy
    UNIQUE(caught_date) index so multiple wild catches share a date.
    Backfills oldest-per-date as 'daily'; tries to recover real catch dates
    for wilds via the encounters table."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(pokemon)")}
    if "source" in cols:
        return
    conn.execute(
        "ALTER TABLE pokemon ADD COLUMN source TEXT NOT NULL DEFAULT 'wild'"
    )
    conn.execute(
        """
        UPDATE pokemon SET source = 'daily'
        WHERE id IN (
            SELECT MIN(id) FROM pokemon GROUP BY caught_date
        )
        """
    )
    conn.execute(
        """
        UPDATE pokemon SET caught_date = (
            SELECT date(e.resolved_utc) FROM encounters e
            WHERE e.pokemon_id = pokemon.id AND e.resolved_utc IS NOT NULL
        )
        WHERE source = 'wild' AND id IN (
            SELECT pokemon_id FROM encounters
            WHERE pokemon_id IS NOT NULL AND resolved_utc IS NOT NULL
        )
        """
    )
    conn.execute("DROP INDEX IF EXISTS idx_pokemon_caught_date")
    conn.execute("CREATE INDEX idx_pokemon_caught_date ON pokemon(caught_date)")


_LEGACY_BALL_COLUMNS: dict[str, str] = {
    "pokeballs_used": "pokeball",
    "greatballs_used": "greatball",
    "ultraballs_used": "ultraball",
    "masterballs_used": "masterball",
}


def _migrate_encounter_balls_to_items(conn: sqlite3.Connection) -> None:
    """Copy the legacy per-ball columns on ``encounters`` into the generic
    ``encounter_item_uses`` junction table. Idempotent — only runs while
    the new table is empty."""
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


def _backfill_pokedex_seen(conn: sqlite3.Connection) -> None:
    """One-shot population of ``pokedex_seen`` from existing rows.

    For each encounter row's species_dex_id → 'seen' (with the encounter's
    spawned_utc as the seen timestamp). For each pokemon row, expand via
    the line — every pre-evolution stage gets marked 'caught' so a row
    that has since evolved doesn't lose its earlier-form Pokedex entry,
    and so this stays the source of truth even after a future "release"
    feature deletes the pokemon row itself.

    Idempotent — only runs while the table is empty.
    """
    existing = conn.execute("SELECT 1 FROM pokedex_seen LIMIT 1").fetchone()
    if existing is not None:
        return

    # Lazy import — pokemon depends on storage, so we can't import at module
    # load time without a cycle.
    from tokenmon.pokemon import species_seen_through

    # Step 1: every encounter species → 'seen'.
    for row in conn.execute(
        "SELECT species_dex_id, MIN(spawned_utc) FROM encounters GROUP BY species_dex_id"
    ):
        dex_id, ts = int(row[0]), row[1]
        conn.execute(
            """
            INSERT INTO pokedex_seen (dex_id, status, first_seen_utc)
            VALUES (?, 'seen', ?)
            ON CONFLICT(dex_id) DO NOTHING
            """,
            (dex_id, ts),
        )

    # Step 2: every pokemon row's full chain up to its current form → 'caught'.
    # This overwrites any 'seen' entry we just inserted for those species.
    for row in conn.execute(
        "SELECT species_dex_id, caught_date FROM pokemon"
    ):
        current, caught_date = int(row[0]), row[1]
        # caught_date is a local-date ISO string; convert to a naive UTC
        # ISO timestamp for the seen/caught fields. Off-by-a-few-hours is
        # fine for this one-shot recovery.
        ts = f"{caught_date}T00:00:00+00:00"
        for dex in species_seen_through(current):
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
                (int(dex), ts, ts),
            )


def _backfill_inventory(conn: sqlite3.Connection) -> None:
    """One-shot snapshot of the legacy "earned − used" formula into the new
    ``inventory`` table. Runs while the table is empty so users don't lose
    the items they've already accumulated when we switch to lottery drops.

    Idempotent — once any inventory row exists we skip.
    """
    existing = conn.execute("SELECT 1 FROM inventory LIMIT 1").fetchone()
    if existing is not None:
        return

    from tokenmon.items import ITEMS  # lazy — items doesn't depend on storage

    total_out_row = conn.execute(
        "SELECT COALESCE(SUM(output_tokens), 0) FROM requests"
    ).fetchone()
    total_out = int(total_out_row[0] or 0)

    for key, item in ITEMS.items():
        if item.threshold <= 0:
            count = 0
        else:
            earned = total_out // item.threshold
            used_row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM encounter_item_uses "
                "WHERE item_key = ?",
                (key,),
            ).fetchone()
            used = int(used_row[0] or 0)
            count = max(0, min(item.cap, earned - used))
        conn.execute(
            "INSERT INTO inventory (item_key, count) VALUES (?, ?) "
            "ON CONFLICT(item_key) DO NOTHING",
            (key, count),
        )


def init_db(path: Path | None = None) -> None:
    """Apply schema + every idempotent migration. Safe to call repeatedly."""
    if path is None:
        path = DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        _ensure_trained_pokemon_column(conn)
        _ensure_affection_column(conn)
        _ensure_gender_shiny_columns(conn)
        _ensure_pokemon_source_column(conn)
        _migrate_encounter_balls_to_items(conn)
        _backfill_pokedex_seen(conn)
        _backfill_inventory(conn)

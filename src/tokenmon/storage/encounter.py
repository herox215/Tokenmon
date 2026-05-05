"""Encounter table layer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._db import DB_PATH, _connect

__all__ = [
    "Encounter",
    "insert_encounter",
    "get_pending_encounter",
    "mark_encounter_caught",
    "mark_encounter_ran",
    "update_encounter_hint",
    "increment_item_used",
    "increment_ball_used",
    "query_item_counts",
    "list_distinct_encounter_species",
    "add_to_inventory",
    "decrement_inventory",
    "add_to_pending",
    "query_pending_drops",
    "claim_pending_drops",
]


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
    gender: str | None = None
    is_shiny: bool = False
    ivs: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0)


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
        gender=row[15] if len(row) > 15 else None,
        is_shiny=bool(row[16]) if len(row) > 16 and row[16] is not None else False,
        ivs=(
            int(row[17] or 0), int(row[18] or 0), int(row[19] or 0),
            int(row[20] or 0), int(row[21] or 0), int(row[22] or 0),
        ) if len(row) > 22 else (0, 0, 0, 0, 0, 0),
    )


_ENCOUNTER_COLS = (
    "id, spawned_utc, species_dex_id, nature, characteristic, level, "
    "catch_rate, pokeballs_used, greatballs_used, ultraballs_used, "
    "masterballs_used, resolved, resolved_utc, pokemon_id, last_hint, "
    "gender, is_shiny, "
    "iv_hp, iv_attack, iv_defense, iv_sp_attack, iv_sp_defense, iv_speed"
)


def insert_encounter(
    species_dex_id: int,
    nature: str,
    characteristic: str,
    level: int,
    catch_rate: int,
    *,
    gender: str | None = None,
    is_shiny: bool = False,
    ivs: tuple[int, int, int, int, int, int] | None = None,
    path: Path | None = None,
) -> int:
    if path is None:
        path = DB_PATH
    if ivs is None:
        from tokenmon.pokemon.stats import roll_ivs  # lazy: avoid import cycle
        ivs = roll_ivs()
    spawned = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO encounters (
                spawned_utc, species_dex_id, nature, characteristic,
                level, catch_rate, gender, is_shiny,
                iv_hp, iv_attack, iv_defense,
                iv_sp_attack, iv_sp_defense, iv_speed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spawned, species_dex_id, nature, characteristic,
                level, catch_rate, gender, 1 if is_shiny else 0,
                int(ivs[0]), int(ivs[1]), int(ivs[2]),
                int(ivs[3]), int(ivs[4]), int(ivs[5]),
            ),
        )
        return int(cur.lastrowid)


def get_pending_encounter(path: Path | None = None) -> Encounter | None:
    """Return the unresolved encounter (resolved IS NULL), or None."""
    if path is None:
        path = DB_PATH
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
    encounter_id: int, item_key: str, n: int = 1, path: Path | None = None,
) -> None:
    """Add ``n`` to the count of ``item_key`` used against ``encounter_id``,
    AND decrement the live inventory by the same amount.

    The encounter ledger is kept for stats / hindsight; the inventory row
    is now load-bearing for "how many do I have"."""
    from tokenmon.items import ITEMS

    if item_key not in ITEMS:
        raise ValueError(f"unknown item_key: {item_key!r}")
    if path is None:
        path = DB_PATH
    n_int = int(n)
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO encounter_item_uses (encounter_id, item_key, count)
            VALUES (?, ?, ?)
            ON CONFLICT(encounter_id, item_key)
            DO UPDATE SET count = count + excluded.count
            """,
            (encounter_id, item_key, n_int),
        )
        conn.execute(
            """
            INSERT INTO inventory (item_key, count) VALUES (?, 0)
            ON CONFLICT(item_key) DO NOTHING
            """,
            (item_key,),
        )
        conn.execute(
            "UPDATE inventory SET count = MAX(0, count - ?) WHERE item_key = ?",
            (n_int, item_key),
        )


def add_to_inventory(
    item_key: str, n: int = 1, *, path: Path | None = None,
) -> int:
    """Add ``n`` of ``item_key`` to the inventory, capped at ``Item.cap``.
    Returns the new count, or 0 if the item is unknown / n <= 0."""
    if n <= 0:
        return 0
    from tokenmon.items import ITEMS

    item = ITEMS.get(item_key)
    if item is None:
        return 0
    if path is None:
        path = DB_PATH
    cap = int(item.cap)
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO inventory (item_key, count) VALUES (?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
                count = MIN(?, count + excluded.count)
            """,
            (item_key, min(cap, int(n)), cap),
        )
        row = conn.execute(
            "SELECT count FROM inventory WHERE item_key = ?", (item_key,),
        ).fetchone()
    return int(row[0]) if row else 0


def decrement_inventory(
    item_key: str, n: int = 1, *, path: Path | None = None,
) -> int:
    """Subtract ``n`` from the inventory, clamped to 0. Returns new count."""
    if n <= 0:
        return 0
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO inventory (item_key, count) VALUES (?, 0)
            ON CONFLICT(item_key) DO NOTHING
            """,
            (item_key,),
        )
        conn.execute(
            "UPDATE inventory SET count = MAX(0, count - ?) WHERE item_key = ?",
            (int(n), item_key),
        )
        row = conn.execute(
            "SELECT count FROM inventory WHERE item_key = ?", (item_key,),
        ).fetchone()
    return int(row[0]) if row else 0


def increment_ball_used(
    encounter_id: int, ball_type: str, path: Path | None = None,
) -> None:
    """Backwards-compat shim — delegates to :func:`increment_item_used`."""
    increment_item_used(encounter_id, ball_type, n=1, path=path)


def mark_encounter_caught(
    encounter_id: int, pokemon_id: int, path: Path | None = None,
) -> None:
    if path is None:
        path = DB_PATH
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


def mark_encounter_ran(encounter_id: int, path: Path | None = None) -> None:
    if path is None:
        path = DB_PATH
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect(path) as conn:
        conn.execute(
            """
            UPDATE encounters SET resolved = 'ran', resolved_utc = ? WHERE id = ?
            """,
            (now, encounter_id),
        )


def update_encounter_hint(
    encounter_id: int, hint: str, path: Path | None = None,
) -> None:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "UPDATE encounters SET last_hint = ? WHERE id = ?",
            (hint, encounter_id),
        )


def query_item_counts(
    item_keys: list[str] | None = None, path: Path | None = None,
) -> dict[str, int]:
    """Return ``{item_key: count}`` from the live ``inventory`` table.

    Item counts used to be derived from a `floor(tokens / threshold) − used`
    formula. They're now persisted explicitly: ``add_to_inventory`` grows
    the count (driven by the per-request lottery in ``items.roll_item_drops``),
    ``increment_item_used`` shrinks it.
    """
    from tokenmon.items import ITEMS

    if path is None:
        path = DB_PATH
    keys = list(item_keys) if item_keys is not None else list(ITEMS.keys())
    out: dict[str, int] = {k: 0 for k in keys}
    if not keys:
        return out

    placeholders = ",".join(["?"] * len(keys))
    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT item_key, count FROM inventory WHERE item_key IN ({placeholders})",
            tuple(keys),
        ).fetchall()
    for key, count in rows:
        out[key] = int(count or 0)
    return out


def list_distinct_encounter_species(path: Path | None = None) -> set[int]:
    """Set of species_dex_id values that appear in the encounters table —
    used by the Pokedex pane to mark species you've SEEN."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT species_dex_id FROM encounters"
        ).fetchall()
    return {int(row[0]) for row in rows}


# --- Pending drops --------------------------------------------------------
# Loot from the per-token lottery first lands in ``pending_drops`` so the
# user can see what they found via the Items-pane claim animation, then
# claim it into the regular inventory.


def add_to_pending(
    item_key: str, n: int = 1, *, path: Path | None = None,
) -> int:
    """Add ``n`` of ``item_key`` to ``pending_drops``. No cap here — caps
    apply on claim, when items move into the real inventory. Returns the
    new pending count, or 0 for unknown items / non-positive n."""
    if n <= 0:
        return 0
    from tokenmon.items import ITEMS

    if item_key not in ITEMS:
        return 0
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO pending_drops (item_key, count) VALUES (?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
                count = count + excluded.count
            """,
            (item_key, int(n)),
        )
        row = conn.execute(
            "SELECT count FROM pending_drops WHERE item_key = ?", (item_key,),
        ).fetchone()
    return int(row[0]) if row else 0


def query_pending_drops(path: Path | None = None) -> dict[str, int]:
    """Return ``{item_key: count}`` of everything currently waiting to be
    claimed. Empty dict when there's nothing pending."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT item_key, count FROM pending_drops WHERE count > 0"
        ).fetchall()
    return {k: int(c or 0) for k, c in rows}


def claim_pending_drops(path: Path | None = None) -> dict[str, int]:
    """Atomically move pending drops into the inventory (capped at the
    item's ``cap``), returning ``{item_key: count}`` of what was actually
    transferred. Overflow that doesn't fit under the cap stays in
    ``pending_drops`` so a full bag doesn't silently destroy loot — the
    user can claim it after using items down to free up space.
    """
    from tokenmon.items import ITEMS

    if path is None:
        path = DB_PATH
    transferred: dict[str, int] = {}
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT item_key, count FROM pending_drops WHERE count > 0"
        ).fetchall()
        for key, pending in rows:
            item = ITEMS.get(key)
            if item is None:
                continue
            cap = int(item.cap)
            pending = int(pending)
            cur_row = conn.execute(
                "SELECT count FROM inventory WHERE item_key = ?", (key,),
            ).fetchone()
            cur = int(cur_row[0]) if cur_row else 0
            target = min(cap, cur + pending)
            granted = max(0, target - cur)
            leftover = pending - granted
            if granted > 0:
                conn.execute(
                    """
                    INSERT INTO inventory (item_key, count) VALUES (?, ?)
                    ON CONFLICT(item_key) DO UPDATE SET count = ?
                    """,
                    (key, target, target),
                )
                transferred[key] = granted
            if leftover > 0:
                conn.execute(
                    "UPDATE pending_drops SET count = ? WHERE item_key = ?",
                    (leftover, key),
                )
            else:
                conn.execute(
                    "DELETE FROM pending_drops WHERE item_key = ?", (key,),
                )
    return transferred

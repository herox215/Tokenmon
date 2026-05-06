"""Trainers + trainer_pokemon storage. Mirrors the encounters/encounter
table shape but for full opposing teams."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._db import DB_PATH, _connect

__all__ = [
    "Trainer",
    "TrainerPokemonRow",
    "insert_trainer",
    "get_pending_trainer",
    "get_trainer",
    "list_trainer_pokemon",
    "mark_trainer_pokemon_fainted",
    "mark_trainer_resolved",
    "latest_trainer_spawn_ts",
]


@dataclass(frozen=True, slots=True)
class Trainer:
    id: int
    spawned_utc: str
    name: str
    title: str
    difficulty: str
    seed: int
    resolved: str | None
    resolved_utc: str | None
    money_reward: int | None
    xp_reward: int | None


@dataclass(frozen=True, slots=True)
class TrainerPokemonRow:
    id: int
    trainer_id: int
    slot: int
    species_dex_id: int
    level: int
    nature: str
    ivs: tuple[int, int, int, int, int, int]
    move_keys: tuple[str, ...]
    fainted: bool


def insert_trainer(
    *,
    name: str,
    title: str,
    difficulty: str,
    seed: int,
    team: list[dict],
    path: Path | None = None,
) -> int:
    """Insert a trainer + all team rows in one transaction. ``team`` is
    a list of dicts with keys: species_dex_id, level, nature, ivs (6-
    tuple), move_keys (tuple of strings)."""
    if path is None:
        path = DB_PATH
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(path) as conn:
        cur = conn.execute(
            """
            INSERT INTO trainers
                (spawned_utc, name, title, difficulty, seed)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ts, str(name), str(title), str(difficulty), int(seed)),
        )
        trainer_id = int(cur.lastrowid)
        for slot, mon in enumerate(team):
            ivs = mon["ivs"]
            conn.execute(
                """
                INSERT INTO trainer_pokemon
                    (trainer_id, slot, species_dex_id, level, nature,
                     iv_hp, iv_attack, iv_defense, iv_sp_attack,
                     iv_sp_defense, iv_speed, moves_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trainer_id, slot,
                    int(mon["species_dex_id"]), int(mon["level"]),
                    str(mon["nature"]),
                    int(ivs[0]), int(ivs[1]), int(ivs[2]),
                    int(ivs[3]), int(ivs[4]), int(ivs[5]),
                    json.dumps(list(mon["move_keys"])),
                ),
            )
    return trainer_id


def _row_to_trainer(row) -> Trainer:
    return Trainer(
        id=row[0], spawned_utc=row[1], name=row[2], title=row[3],
        difficulty=row[4], seed=row[5], resolved=row[6],
        resolved_utc=row[7], money_reward=row[8], xp_reward=row[9],
    )


def get_pending_trainer(path: Path | None = None) -> Trainer | None:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT id, spawned_utc, name, title, difficulty, seed, "
            "resolved, resolved_utc, money_reward, xp_reward "
            "FROM trainers WHERE resolved IS NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _row_to_trainer(row) if row is not None else None


def get_trainer(trainer_id: int, *, path: Path | None = None) -> Trainer | None:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT id, spawned_utc, name, title, difficulty, seed, "
            "resolved, resolved_utc, money_reward, xp_reward "
            "FROM trainers WHERE id = ?",
            (int(trainer_id),),
        ).fetchone()
    return _row_to_trainer(row) if row is not None else None


def list_trainer_pokemon(
    trainer_id: int, *, path: Path | None = None,
) -> list[TrainerPokemonRow]:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT id, trainer_id, slot, species_dex_id, level, nature,
                   iv_hp, iv_attack, iv_defense, iv_sp_attack,
                   iv_sp_defense, iv_speed, moves_json, fainted
            FROM trainer_pokemon
            WHERE trainer_id = ?
            ORDER BY slot ASC
            """,
            (int(trainer_id),),
        ).fetchall()
    out: list[TrainerPokemonRow] = []
    for r in rows:
        out.append(TrainerPokemonRow(
            id=r[0], trainer_id=r[1], slot=r[2], species_dex_id=r[3],
            level=r[4], nature=r[5],
            ivs=(r[6], r[7], r[8], r[9], r[10], r[11]),
            move_keys=tuple(json.loads(r[12])),
            fainted=bool(r[13]),
        ))
    return out


def mark_trainer_pokemon_fainted(
    trainer_pokemon_id: int, *, path: Path | None = None,
) -> None:
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        conn.execute(
            "UPDATE trainer_pokemon SET fainted = 1 WHERE id = ?",
            (int(trainer_pokemon_id),),
        )


def mark_trainer_resolved(
    trainer_id: int,
    *,
    status: str,
    money_reward: int = 0,
    xp_reward: int = 0,
    path: Path | None = None,
) -> None:
    """Close out a trainer with status ('won', 'lost', 'ran') and
    record the rewards delivered."""
    if status not in ("won", "lost", "ran"):
        raise ValueError(f"invalid trainer status: {status!r}")
    if path is None:
        path = DB_PATH
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(path) as conn:
        conn.execute(
            """
            UPDATE trainers
            SET resolved = ?, resolved_utc = ?,
                money_reward = ?, xp_reward = ?
            WHERE id = ?
            """,
            (status, ts, int(money_reward), int(xp_reward), int(trainer_id)),
        )


def latest_trainer_spawn_ts(path: Path | None = None) -> datetime | None:
    """Most-recent trainer spawn (UTC). Used by the cooldown gate."""
    if path is None:
        path = DB_PATH
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT spawned_utc FROM trainers ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    ts = datetime.fromisoformat(row[0])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts

"""Wild-encounter and trainer-spawn rolling, plus the SQLite high-water-mark
queries that drive them.

Per-call semantics: ``maybe_roll_encounters`` walks every new request id
since the last poll, giving ``encounter.maybe_spawn`` and
``trainer.maybe_spawn`` one shot per request. The spawn logic guards on
cooldown / pending status / token-weighted probability internally, so the
loop honours the same gating as before extraction.

State (``app._last_seen_request_id``, ``app._companion_mode``,
``app._overlay``) lives on TokenmonApp; this module only reads/writes it
through the ``app`` argument.
"""
from __future__ import annotations

import logging

import rumps

log = logging.getLogger("tokenmon.menubar.encounter_roll")


def query_max_request_id(app) -> int:
    try:
        import sqlite3
        from tokenmon.storage import DB_PATH
        with sqlite3.connect(DB_PATH, timeout=2.0) as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()
            return int(row[0] or 0)
    except Exception:
        log.exception("max request id query failed")
        return 0


def query_new_requests(app, since: int) -> tuple[list[int], int]:
    """Return (output_token counts of new requests, max_id seen).

    The token list drives encounter.spawn_probability — one entry per
    new request, in id order. ``max_id`` is the high-water mark we'll
    hand back to the next poll."""
    try:
        import sqlite3
        from tokenmon.storage import DB_PATH
        with sqlite3.connect(DB_PATH, timeout=2.0) as conn:
            rows = conn.execute(
                "SELECT id, output_tokens FROM requests "
                "WHERE id > ? ORDER BY id ASC",
                (since,),
            ).fetchall()
    except Exception:
        return [], since
    if not rows:
        return [], since
    tokens = [int(ot or 0) for _, ot in rows]
    max_id = int(rows[-1][0])
    return tokens, max_id


def maybe_roll_encounters(app) -> None:
    """For every new request that landed since the last poll, give
    encounter.maybe_spawn() a chance to spawn. The spawn logic itself
    guards on cooldown + pending status + a token-weighted probability,
    so calling it once per new request honours the per-call semantics."""
    tokens_per_req, max_id = query_new_requests(app, app._last_seen_request_id)
    if not tokens_per_req:
        return
    app._last_seen_request_id = max_id
    try:
        from tokenmon import encounter
    except Exception:
        log.exception("encounter import failed")
        return
    for output_tokens in tokens_per_req:
        try:
            spawned = encounter.maybe_spawn(output_tokens=output_tokens)
        except Exception:
            log.exception("maybe_spawn failed")
            spawned = None
        if spawned is not None:
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle="A wild Pokemon appeared!",
                    message="Click the menubar to investigate.",
                )
            except Exception:
                log.exception("encounter notification failed")
            # Companion-mode flash so the user sees the spawn even when
            # the menubar isn't where their eyes are.
            if app._companion_mode and app._overlay.visible:
                try:
                    app._overlay.flash_alert("⚡ wild!", duration_s=4.0)
                except Exception:
                    log.exception("encounter flash_alert failed")
            break  # only one pending encounter at a time

    # Trainer-spawn rolls run in parallel to wild encounters but
    # with their own gating (own cooldown, lower probability,
    # additional guard against spawning while a wild is pending).
    try:
        from tokenmon import trainer
    except Exception:
        log.exception("trainer import failed")
        return
    for output_tokens in tokens_per_req:
        try:
            t = trainer.maybe_spawn(output_tokens=output_tokens)
        except Exception:
            log.exception("trainer.maybe_spawn failed")
            t = None
        if t is not None:
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle=f"{t.title} {t.name} wants to battle!",
                    message=f"Difficulty: {t.difficulty.title()}",
                )
            except Exception:
                log.exception("trainer notification failed")
            if app._companion_mode and app._overlay.visible:
                try:
                    app._overlay.flash_alert(
                        "⚔️ trainer!", duration_s=4.0,
                    )
                except Exception:
                    log.exception("trainer flash_alert failed")
            break

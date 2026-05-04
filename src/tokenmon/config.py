"""Tiny JSON-backed config persisted to ~/.tokenmon/config.json."""

from __future__ import annotations

import json
import logging
from typing import Any

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.config")

CONFIG_PATH = DB_DIR / "config.json"

_DEFAULTS: dict[str, Any] = {
    "show_pokemon_in_menubar": True,
}


def load() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        log.exception("failed to read config; using defaults")
        return dict(_DEFAULTS)
    return {**_DEFAULTS, **data}


def save(cfg: dict[str, Any]) -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get(key: str) -> Any:
    return load().get(key, _DEFAULTS.get(key))


def set_(key: str, value: Any) -> None:
    cfg = load()
    cfg[key] = value
    save(cfg)

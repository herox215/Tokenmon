"""Tiny JSON-backed config persisted to ~/.tokenmon/config.json."""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from tokenmon.storage import DB_DIR

log = logging.getLogger("tokenmon.config")

CONFIG_PATH = DB_DIR / "config.json"

_DEFAULTS: dict[str, Any] = {
    "show_pokemon_in_menubar": True,
    "overlay_corner": "bottom-right",
    "overlay_size": 128,
    # Companion mode: gates the entire desktop overlay. When on, the
    # Pokémon is permanently visible, docked to the focused app window,
    # and level-up / evolution / item-drop events surface attached to it.
    # When off, none of the desktop overlay shows. Replaces the older
    # ``show_overlay`` flag (silently ignored if present in legacy
    # config).
    "companion_mode": False,
    # Weather-aware spawning: when on, encounter rolls bias species
    # selection toward types matching the local weather (rain → water,
    # thunderstorm → electric, etc.). Off by default — opt-in via menubar.
    "use_weather": False,
    # Provider strategies the user has installed proxies for. Each becomes its
    # own LaunchAgent at com.tokenmon.proxy.{name} listening on the strategy's
    # default port.
    "proxy_providers": ["anthropic"],
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


def get_user_salt() -> str:
    """Per-install random salt used to make the daily Pokemon pick user-specific.
    Generated and persisted on first call."""
    cfg = load()
    salt = cfg.get("user_salt")
    if not salt:
        salt = secrets.token_hex(16)
        cfg["user_salt"] = salt
        save(cfg)
    return salt

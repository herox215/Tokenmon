"""Random rolls + deterministic seeded picks + time-of-day spawn windows."""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime

from .data import (
    _BASE_IDS,
    CHARACTERISTICS,
    GEN1_DAY_ONLY,
    GEN1_GENDERLESS,
    GEN1_NIGHT_ONLY,
    NATURES,
)

_RNG = random.SystemRandom()


# --- Time-of-day window ---------------------------------------------------

DAY_HOUR_START = 6
DAY_HOUR_END = 20  # exclusive — 20:00 onward counts as night


def current_time_window(now: datetime | None = None) -> str:
    """Returns 'day' or 'night' based on the local hour.

    Tests inject a fixed time via the ``now`` arg.
    """
    h = (now or datetime.now()).hour
    return "day" if DAY_HOUR_START <= h < DAY_HOUR_END else "night"


def can_spawn_now(dex_id: int, *, window: str | None = None) -> bool:
    """True iff ``dex_id`` is allowed to spawn in ``window``.
    Species not in either restricted set spawn 24/7.
    """
    if window is None:
        window = current_time_window()
    if window == "day" and int(dex_id) in GEN1_NIGHT_ONLY:
        return False
    if window == "night" and int(dex_id) in GEN1_DAY_ONLY:
        return False
    return True


# --- Random picks ---------------------------------------------------------


def random_species() -> int:
    """Uniform random pick from base forms that can spawn at the current
    local time of day. Falls back to the unfiltered pool if curated lists
    ever drain it (defensive)."""
    window = current_time_window()
    pool = [d for d in _BASE_IDS if can_spawn_now(d, window=window)]
    if not pool:
        pool = _BASE_IDS
    return _RNG.choice(pool)


def random_nature() -> dict:
    return _RNG.choice(NATURES)


def random_characteristic() -> str:
    return _RNG.choice(CHARACTERISTICS)


# --- Gender + shiny rolls -------------------------------------------------

# Modern (Gen 6+) shiny rate. "Very rare" — about 1 in 4096.
SHINY_RATE = 1 / 4096


def is_genderless(dex_id: int) -> bool:
    return int(dex_id) in GEN1_GENDERLESS


def roll_gender(dex_id: int) -> str | None:
    """'M'/'F' for normal species, None for genderless."""
    if is_genderless(dex_id):
        return None
    return "M" if _RNG.random() < 0.5 else "F"


def roll_shiny() -> bool:
    """Independent 1/4096 shiny roll."""
    return _RNG.random() < SHINY_RATE


def gender_symbol(gender: str | None) -> str:
    """UI helper — '♂', '♀', or '' for genderless."""
    if gender == "M":
        return "♂"
    if gender == "F":
        return "♀"
    return ""


# --- Deterministic seeded picks (for daily / migrations) ------------------


def _seed_index(date_iso: str, salt: str, kind: str, n: int) -> int:
    h = int(hashlib.sha256(f"{date_iso}:{salt}:{kind}".encode()).hexdigest(), 16)
    return h % n


def pick_for_today(today: date | None = None, salt: str | None = None) -> int:
    """Deterministic pick for the given calendar date.

    Salts the hash with a per-install user_salt so two people running
    Tokenmon on the same day see different Pokemon.
    """
    today = today or date.today()
    if salt is None:
        from tokenmon import config  # local import to avoid cycle
        salt = config.get_user_salt()
    seed = f"{today.isoformat()}:{salt}".encode()
    h = int(hashlib.sha256(seed).hexdigest(), 16)
    return _BASE_IDS[h % len(_BASE_IDS)]


def seeded_species(date_iso: str, salt: str) -> int:
    """Deterministic species pick — same algorithm as pick_for_today, used
    when migrating historical days so attribution stays stable."""
    seed = f"{date_iso}:{salt}".encode()
    h = int(hashlib.sha256(seed).hexdigest(), 16)
    return _BASE_IDS[h % len(_BASE_IDS)]


def seeded_nature(date_iso: str, salt: str) -> dict:
    return NATURES[_seed_index(date_iso, salt, "nature", len(NATURES))]


def seeded_characteristic(date_iso: str, salt: str) -> str:
    return CHARACTERISTICS[_seed_index(date_iso, salt, "characteristic", len(CHARACTERISTICS))]

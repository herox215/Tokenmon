"""Time-of-day + weather mood adjustments for the companion overlay.

Pure helpers — no AppKit, no I/O. Caller (menubar/_main.py) feeds in
``datetime.now(tz)`` and an optional weather snapshot, gets back a small
dict the overlay can apply.

Phase 5 of the companion roadmap. Intentionally narrow:
- Night dims the sprite slightly and shortens idle thresholds.
- Rain shows a tiny umbrella overlay.
Sunny / cloudy / clear: no visual change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

# Local clock hours (24h) defining "night" when the user usually isn't
# actively watching the screen, so the overlay should be quieter.
NIGHT_START_HOUR = 22  # 22:00 onward
NIGHT_END_HOUR = 6     # … through 06:00

# WMO weather codes that should trigger the umbrella overlay. Subset of
# the open-meteo WMO weather code table — anything in 51..67 (drizzle/rain)
# or 80..82 (rain showers).
RAIN_WMO_CODES = frozenset(
    list(range(51, 68)) + list(range(80, 83))
)


@dataclass(frozen=True, slots=True)
class MoodModifiers:
    """What the overlay should apply on top of its base state.

    ``alpha_multiplier`` chains with the idle-state alpha so a sleeping
    Pokémon at night gets dimmer than a sleeping Pokémon at noon.
    """
    alpha_multiplier: float = 1.0
    show_umbrella: bool = False
    is_night: bool = False


def is_night(now: datetime) -> bool:
    """22:00–05:59 local clock counts as night."""
    h = now.hour
    return h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR


def mood_modifiers(
    now: datetime,
    *,
    wmo_code: int | None = None,
) -> MoodModifiers:
    """Compute the overlay modifiers for the current time + weather.

    ``wmo_code`` is the open-meteo numeric weather code (or None when
    weather data isn't available / disabled).
    """
    night = is_night(now)
    alpha = 0.85 if night else 1.0
    rain = wmo_code is not None and int(wmo_code) in RAIN_WMO_CODES
    return MoodModifiers(
        alpha_multiplier=alpha,
        show_umbrella=rain,
        is_night=night,
    )

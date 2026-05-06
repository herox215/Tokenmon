"""Background-particle specs for the popover weather layer (pure data).

Each ``ParticleSpec`` is a recipe the popover animator follows: how many
particles, what color, how fast they fall, etc. Not every WMO code maps
to a spec — clear and "lightly cloudy" weather returns ``None`` so the
popover renders its normal vibrant background untouched.
"""
from __future__ import annotations

from dataclasses import dataclass

from .remote import WeatherSnapshot


@dataclass(frozen=True, slots=True)
class ParticleSpec:
    kind: str  # 'rain' | 'snow' | 'thunder' | 'fog' | 'sun' | 'cloud'
    count: int  # particles on screen at once (0 for static-overlay specs)
    color_rgba: tuple[float, float, float, float]
    width: float
    height: float
    # px / sec along the y-axis. Positive = falls down, negative = rises.
    speed_min: float
    speed_max: float
    # Horizontal sine-wobble (px). 0 disables drift.
    drift_amplitude: float
    drift_period: float  # seconds per full sine cycle
    # For thunderstorms only — average seconds between flashes. ``None``
    # disables the flash.
    flash_interval: float | None
    # For static overlays (fog) — when True, the animator paints a single
    # full-host rectangle and skips the per-tick update.
    static_overlay: bool = False
    # Optional secondary tint for two-tone particles (used by snow on a
    # dark popover to keep the flake visible). ``None`` = single tone.
    border_rgba: tuple[float, float, float, float] | None = None


# WMO code groups, mirroring the labels in ``bias.py``. Keep these
# tables aligned when adding new codes — they're the two halves of the
# same weather model.

_DRIZZLE = (51, 53, 55)
_FREEZING_DRIZZLE = (56, 57)
_RAIN = (61, 63, 65)
_FREEZING_RAIN = (66, 67)
_SHOWERS = (80, 81, 82)
_SNOW = (71, 73, 75, 77)
_SNOW_SHOWERS = (85, 86)
_THUNDERSTORM = (95, 96, 99)
_FOG = (45, 48)
_CLEAR = (0, 1)
_CLOUDY = (2, 3)


def particles_for(snap: WeatherSnapshot) -> ParticleSpec | None:
    """Return a particle spec for the given weather snapshot, or ``None``
    when the weather doesn't warrant a background effect (clear sky,
    light overcast)."""
    code = int(snap.wmo)

    if code in _DRIZZLE:
        return ParticleSpec(
            kind="rain", count=18,
            color_rgba=(0.55, 0.75, 1.0, 0.45),
            width=1.5, height=7,
            speed_min=140, speed_max=200,
            drift_amplitude=0, drift_period=1, flash_interval=None,
        )
    if code in _FREEZING_DRIZZLE or code in _FREEZING_RAIN:
        return ParticleSpec(
            kind="rain", count=24,
            color_rgba=(0.7, 0.85, 1.0, 0.6),
            width=2, height=8,
            speed_min=180, speed_max=260,
            drift_amplitude=0, drift_period=1, flash_interval=None,
        )
    if code in _RAIN:
        return ParticleSpec(
            kind="rain", count=32,
            color_rgba=(0.4, 0.6, 1.0, 0.6),
            width=2, height=10,
            speed_min=240, speed_max=380,
            drift_amplitude=0, drift_period=1, flash_interval=None,
        )
    if code in _SHOWERS:
        return ParticleSpec(
            kind="rain", count=40,
            color_rgba=(0.35, 0.55, 1.0, 0.65),
            width=2, height=12,
            speed_min=300, speed_max=460,
            drift_amplitude=0, drift_period=1, flash_interval=None,
        )
    if code in _SNOW or code in _SNOW_SHOWERS:
        return ParticleSpec(
            kind="snow", count=24,
            color_rgba=(1.0, 1.0, 1.0, 0.85),
            width=4, height=4,
            speed_min=20, speed_max=55,
            drift_amplitude=18, drift_period=4.0,
            flash_interval=None,
            border_rgba=(0.7, 0.85, 1.0, 0.4),
        )
    if code in _THUNDERSTORM:
        return ParticleSpec(
            kind="thunder", count=38,
            color_rgba=(0.35, 0.55, 1.0, 0.65),
            width=2, height=11,
            speed_min=320, speed_max=480,
            drift_amplitude=0, drift_period=1,
            flash_interval=5.0,
        )
    if code in _FOG:
        return ParticleSpec(
            kind="fog", count=0,
            color_rgba=(0.78, 0.80, 0.85, 0.32),
            width=0, height=0,
            speed_min=0, speed_max=0,
            drift_amplitude=0, drift_period=1,
            flash_interval=None,
            static_overlay=True,
        )
    if code in _CLEAR:
        # Subtle warm-glow overlay. No animated particles — just a
        # gentle yellow tint that hints at sunshine without obscuring
        # the popover content.
        return ParticleSpec(
            kind="sun", count=0,
            color_rgba=(1.0, 0.92, 0.55, 0.10),
            width=0, height=0,
            speed_min=0, speed_max=0,
            drift_amplitude=0, drift_period=1,
            flash_interval=None,
            static_overlay=True,
        )
    if code in _CLOUDY:
        # Faint gray-white cloud blobs drifting horizontally. Slow and
        # large so they read as atmospheric rather than as falling rain.
        return ParticleSpec(
            kind="cloud", count=4,
            color_rgba=(1.0, 1.0, 1.0, 0.18),
            width=80, height=18,
            speed_min=-25, speed_max=-12,  # negative speed_y = drifts UP
            drift_amplitude=0, drift_period=1,
            flash_interval=None,
        )
    # Unmapped weather code → no effect.
    return None

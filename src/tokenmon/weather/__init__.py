"""Weather-aware spawning helpers.

Public surface:
- ``WeatherSnapshot`` — value object returned by ``get_weather``
- ``get_weather()`` — IP-geo + Open-Meteo fetch with on-disk cache, returns
  ``None`` on any error so callers can gracefully fall back to neutral
  spawning behavior
- ``type_weights(snap)`` — pure mapping from a snapshot to per-type weight
  multipliers (used by ``pokemon.random_species``)
- ``emoji_label(snap)`` — short human-readable line for the encounter pane
"""
from .bias import emoji_label, type_weights
from .particles import ParticleSpec, particles_for
from .remote import WeatherSnapshot, clear_cache, get_weather

__all__ = [
    "ParticleSpec",
    "WeatherSnapshot",
    "clear_cache",
    "emoji_label",
    "get_weather",
    "particles_for",
    "type_weights",
]

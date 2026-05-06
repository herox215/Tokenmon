"""WMO weather code + temperature → Pokémon type bias (pure, no I/O).

WMO codes follow the Open-Meteo schema documented at
https://open-meteo.com/en/docs (clear / cloudy / fog / drizzle / rain /
snow / thunderstorm). The boost factor is intentionally subtle (2x) so
favored types are visibly more common without crowding out the rest of
the pool.
"""
from __future__ import annotations

from .remote import WeatherSnapshot

BOOST_FACTOR = 2.0


# WMO code → set of Pokémon types to boost. Codes not listed contribute
# nothing on the weather-condition axis (temperature overrides may still
# add types below).
_WMO_TYPE_BOOSTS: dict[int, frozenset[str]] = {
    # Clear / mainly clear
    0: frozenset({"fire", "normal"}),
    1: frozenset({"fire", "normal"}),
    # Partly cloudy / overcast — no boost (neutral weather)
    # Fog
    45: frozenset({"ghost", "psychic"}),
    48: frozenset({"ghost", "psychic"}),
    # Drizzle
    51: frozenset({"water", "bug"}),
    53: frozenset({"water", "bug"}),
    55: frozenset({"water", "bug"}),
    # Freezing drizzle
    56: frozenset({"water", "ice"}),
    57: frozenset({"water", "ice"}),
    # Rain
    61: frozenset({"water", "bug"}),
    63: frozenset({"water", "bug"}),
    65: frozenset({"water", "bug"}),
    # Freezing rain
    66: frozenset({"water", "ice"}),
    67: frozenset({"water", "ice"}),
    # Snow
    71: frozenset({"ice"}),
    73: frozenset({"ice"}),
    75: frozenset({"ice"}),
    77: frozenset({"ice"}),
    # Rain showers
    80: frozenset({"water"}),
    81: frozenset({"water"}),
    82: frozenset({"water"}),
    # Snow showers
    85: frozenset({"ice"}),
    86: frozenset({"ice"}),
    # Thunderstorm
    95: frozenset({"electric"}),
    96: frozenset({"electric"}),
    99: frozenset({"electric"}),
}


# Human-readable summaries paired with an emoji and the canonical short
# label used in the encounter pane sub-header. Mapping is grouped — every
# WMO code listed in _WMO_TYPE_BOOSTS has a matching label here.
_WMO_LABELS: dict[int, tuple[str, str]] = {
    0: ("☀️", "Sonnig"),
    1: ("🌤️", "Heiter"),
    2: ("⛅", "Wolkig"),
    3: ("☁️", "Bewölkt"),
    45: ("🌫️", "Nebel"),
    48: ("🌫️", "Nebel"),
    51: ("🌦️", "Nieselregen"),
    53: ("🌦️", "Nieselregen"),
    55: ("🌦️", "Nieselregen"),
    56: ("🌧️", "Eisregen"),
    57: ("🌧️", "Eisregen"),
    61: ("🌧️", "Regen"),
    63: ("🌧️", "Regen"),
    65: ("🌧️", "Starkregen"),
    66: ("🌧️", "Eisregen"),
    67: ("🌧️", "Eisregen"),
    71: ("🌨️", "Schnee"),
    73: ("🌨️", "Schnee"),
    75: ("❄️", "Starker Schnee"),
    77: ("🌨️", "Schneegriesel"),
    80: ("🌧️", "Regenschauer"),
    81: ("🌧️", "Regenschauer"),
    82: ("⛈️", "Heftiger Schauer"),
    85: ("🌨️", "Schneeschauer"),
    86: ("❄️", "Schneeschauer"),
    95: ("⛈️", "Gewitter"),
    96: ("⛈️", "Gewitter"),
    99: ("⛈️", "Schweres Gewitter"),
}


def type_weights(snap: WeatherSnapshot) -> dict[str, float]:
    """Return ``{type: BOOST_FACTOR}`` for every Pokémon type favored by
    the given weather snapshot. Types not in the dict default to 1.0 in
    the species selector (see ``pokemon.random_species``).

    Empty dict means no bias — the snapshot didn't trigger any rule, so
    spawning behaves identically to having weather disabled.
    """
    boosted: set[str] = set()
    boosted |= _WMO_TYPE_BOOSTS.get(int(snap.wmo), frozenset())
    # Temperature overrides — additive on top of the WMO-code boosts so a
    # cold drizzle still nudges Ice in addition to Water.
    if snap.temp_c < 0:
        boosted.add("ice")
    if snap.temp_c > 28:
        boosted.add("fire")
    return {t: BOOST_FACTOR for t in boosted}


def emoji_label(snap: WeatherSnapshot) -> str:
    """Single-line summary for the encounter pane: emoji, condition, and
    the favored types (if any). e.g. '🌧️ Regen · Wasser/Bug bevorzugt'."""
    emoji, condition = _WMO_LABELS.get(int(snap.wmo), ("🌡️", "Unbekannt"))
    weights = type_weights(snap)
    if not weights:
        return f"{emoji} {condition} · {snap.temp_c:.0f}°C"
    favored = "/".join(sorted(t.capitalize() for t in weights))
    return f"{emoji} {condition} · {favored} bevorzugt"

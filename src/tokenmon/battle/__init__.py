"""Pure battle-engine helpers for Gen-3-style trainer battles.

The battle engine is split into pure modules so each piece can be unit-
tested without AppKit / SQLite. The menubar + popover panes glue these
together with side-effect storage calls.

Public surface:
- ``models``     — dataclasses (BattleStats, Move, TrainerMon, …)
- ``types``      — type-effectiveness chart + ``effectiveness()``
- ``damage``     — damage formula
- ``engine``     — turn resolution
- ``team_gen``   — deterministic trainer-team generation
- ``rewards``    — money / XP / item-drop calculations
- ``names``      — random trainer titles + first names
"""
from __future__ import annotations

from .models import (
    BattleStats,
    DamageResult,
    Difficulty,
    Move,
    Rewards,
    TrainerMon,
    TurnResult,
)

__all__ = [
    "BattleStats",
    "DamageResult",
    "Difficulty",
    "Move",
    "Rewards",
    "TrainerMon",
    "TurnResult",
]

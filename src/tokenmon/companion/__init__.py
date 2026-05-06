"""Companion-mode helpers: app classification + active-app observer +
mood/state machines used by the desktop overlay when the Pokémon is in
permanent-presence mode (config flag ``companion_mode``).

Split out of menubar/_main.py to keep that module focused on rumps
status-bar lifecycle.
"""
from __future__ import annotations

from .app_classes import classify, ENGAGEMENT_BUNDLE_IDS

__all__ = ["classify", "ENGAGEMENT_BUNDLE_IDS"]

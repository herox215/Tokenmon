"""Smoke tests: every public module must import cleanly. Refactor wave
splits are most likely to break this — keep the test list bigger than what
strictly needs to import for production code, so we catch indirect import
loops too."""
from __future__ import annotations

import importlib

import pytest

CORE_MODULES = [
    "tokenmon",
    "tokenmon.cli",
    "tokenmon.config",
    "tokenmon.box",
    "tokenmon.encounter",
    "tokenmon.items",
    "tokenmon.items_remote",
    "tokenmon.launchd",
    "tokenmon.pokemon",
    "tokenmon.pricing",
    "tokenmon.proxy",
    "tokenmon.storage",
]

# These pull in pyobjc/rumps. Skip if AppKit unavailable.
APPKIT_MODULES = [
    "tokenmon.menubar",
    "tokenmon.menubar_sprite",
    "tokenmon.overlay",
    "tokenmon.popover",
    "tokenmon.tokendex",
    "tokenmon.pokedex_remote",
    "tokenmon.pricing_remote",
]


@pytest.mark.parametrize("mod", CORE_MODULES)
def test_core_module_imports(mod):
    importlib.import_module(mod)


@pytest.mark.parametrize("mod", APPKIT_MODULES)
def test_appkit_module_imports(mod):
    pytest.importorskip("AppKit")
    importlib.import_module(mod)

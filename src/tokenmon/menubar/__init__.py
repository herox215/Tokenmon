"""Menubar package — used to be a single 780-line module.

The Wave-F split kept ``TokenmonApp`` together in ``_main.py`` and broke
off only the health-check helpers. External callers should keep importing
``main`` (and, less commonly, ``TokenmonApp``) from this package.
"""
from __future__ import annotations

from ._main import TokenmonApp, main

__all__ = ["TokenmonApp", "main"]

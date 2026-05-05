"""Popover package — used to be a single 2782-line module.

The Wave-E split kept ``TokenmonPopover`` together in ``_main.py`` and
broke off only the file-disjoint pieces (widgets, animation handlers).
External callers should keep importing ``TokenmonPopover`` from this
package; everything else is internal.
"""
from __future__ import annotations

from ._main import (
    TokenmonPopover,
    # Re-exported for tests that exercise pure helpers.
    _fmt_tokens,
    _fmt_usd,
    _fmt_affection,
    _build_catch_steps,
    _build_pat_steps,
)

__all__ = [
    "TokenmonPopover",
    "_fmt_tokens",
    "_fmt_usd",
    "_fmt_affection",
    "_build_catch_steps",
    "_build_pat_steps",
]

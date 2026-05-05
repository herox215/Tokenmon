"""Pure formatting helpers shared across UI modules.

These were duplicated between ``popover.py`` and ``menubar.py`` before the
refactor. They have no AppKit dependency on purpose so they're trivially
testable.
"""
from __future__ import annotations


def fmt_tokens(n: int) -> str:
    """``1234 -> '1.2K'``, ``1_234_567 -> '1.23M'``, ``2_500_000_000 -> '2.50B'``.

    Pure-int formatter; never returns scientific notation.
    """
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000:.1f}K"
    if n < 1_000_000_000:
        return f"{n/1_000_000:.2f}M"
    return f"{n/1_000_000_000:.2f}B"


def fmt_usd(amount: float) -> str:
    """Adaptive precision so very small values are still legible.

    ``< $0.01`` → 4 decimals, ``< $1`` → 3 decimals, else 2.
    """
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 1:
        return f"${amount:.3f}"
    return f"${amount:.2f}"


# Affection display constants — shared by popover renderers (Today + Box detail).
AFFECTION_HEARTS = 5
AFFECTION_MAX = 255


def fmt_affection(value: int) -> str:
    """Render affection as filled/empty hearts plus the raw count.

    Example: ``fmt_affection(102)`` → ``'♥♥♡♡♡  102 / 255'``.
    """
    v = max(0, min(int(value), AFFECTION_MAX))
    if v == 0:
        filled = 0
    elif v >= AFFECTION_MAX:
        filled = AFFECTION_HEARTS
    else:
        filled = max(1, (v * AFFECTION_HEARTS + AFFECTION_MAX - 1) // AFFECTION_MAX)
    return "♥" * filled + "♡" * (AFFECTION_HEARTS - filled) + f"  {v} / {AFFECTION_MAX}"

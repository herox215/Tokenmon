"""Cursor-distance → alpha fade curve.

Pure helper, no AppKit. Used by the menubar's proximity tick to fade
the companion overlay out of the way when the mouse approaches and
fade it back in when the cursor leaves.
"""
from __future__ import annotations

# Distances in pixels from the centre of the overlay sprite. The fade is
# piecewise-linear: opaque outside the outer ring, ramps from full alpha
# to ``MIN_ALPHA`` between outer and inner, holds at ``MIN_ALPHA`` inside.
DEFAULT_INNER = 100.0
DEFAULT_OUTER = 200.0
MIN_ALPHA = 0.15


def proximity_alpha(
    distance: float,
    *,
    inner: float = DEFAULT_INNER,
    outer: float = DEFAULT_OUTER,
    min_alpha: float = MIN_ALPHA,
) -> float:
    """Map cursor-distance-from-sprite-centre to an alpha multiplier in
    ``[min_alpha, 1.0]``. Closer cursor → lower alpha (= more transparent
    sprite).

    ``distance <= inner``  → alpha = ``min_alpha``  (sprite gets out of the way)
    ``distance >= outer``  → alpha = 1.0           (sprite fully opaque)
    in between             → linear ramp

    The fade curve is intentionally piecewise-linear rather than a smooth
    cosine-out so the sprite reaches its minimum alpha promptly when the
    cursor crosses inside the inner ring — feels less laggy.
    """
    if outer <= inner:
        # Degenerate range — treat as an instant cliff at ``inner``.
        return min_alpha if distance <= inner else 1.0
    if distance <= inner:
        return min_alpha
    if distance >= outer:
        return 1.0
    t = (distance - inner) / (outer - inner)
    return min_alpha + t * (1.0 - min_alpha)

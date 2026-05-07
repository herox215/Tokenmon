"""Shared helper that renders a move's tooltip text.

Used by both the per-Pokémon move grid and the battle pane so the
two surfaces stay in sync — same multi-line layout, same handling of
missing data and empty descriptions.

Pure: no AppKit imports, no side effects, deterministic output. Easy
to unit-test in isolation.
"""
from __future__ import annotations


def format_move_tooltip(move, current_pp: int | None = None) -> str:
    """Format the hover tooltip for one move.

    ``move`` is a ``battle.models.Move`` dataclass (or None — cache miss
    fallback). ``current_pp`` is optional; when provided, the PP line
    becomes ``cur/max`` instead of just ``max``.

    The returned string is deliberately newline-separated rather than
    HTML/Markdown so AppKit's ``setToolTip_`` renders it verbatim.
    """
    if move is None:
        return "Move data not loaded yet."

    type_str = move.type.title()
    cat_str = move.category.title()
    power = move.power if move.power is not None else "—"
    acc = f"{move.accuracy}%" if move.accuracy is not None else "Always hits"
    if current_pp is None:
        pp_line = f"PP: {move.pp}"
    else:
        pp_line = f"PP: {current_pp}/{move.pp}"

    lines = [
        move.name,
        f"Type: {type_str} · {cat_str}",
        f"Power: {power} · Accuracy: {acc}",
        pp_line,
    ]
    desc = (move.description or "").strip()
    if desc:
        lines.append("")  # blank separator
        lines.append(desc)
    return "\n".join(lines)

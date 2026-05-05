"""Pure action helpers for the popover.

Extracted from the two near-duplicate ``_title_for_action`` methods that
lived on ``_ItemRowHandler`` and ``_ItemsPaneRowHandler`` in
``popover/_main.py``. Keeping them in one pure module means:

  * The encounter bag and the items pane format menu titles identically.
  * Tests run without AppKit (apart from the existing transitive import).
  * Future pane controllers can call this directly instead of duplicating
    the title-formatting logic again.
"""
from __future__ import annotations

from tokenmon import items


# Pretty per-action menu titles for the bag's right-click-style flyout.
# ``{name}`` is substituted with the item's display_name when present.
ACTION_TITLES: dict[str, str] = {
    "throw": "Throw at wild Pokemon",
    "use": "Use {name}",
    "evolve": "Use on a Pokemon",
}


def title_for_action(item_key: str, action: str) -> str:
    """Render a menu-item title for ``action`` performed with ``item_key``.

    Looks up the item's ``display_name`` for templates that contain
    ``{name}`` (currently only ``use``). Unknown items fall back to the
    raw key, so menus still render something readable. Unknown actions
    pass through verbatim — same fallback the legacy implementations
    used.
    """
    item = items.get(item_key)
    name = item.display_name if item is not None else item_key
    template = ACTION_TITLES.get(action, action)
    try:
        return template.format(name=name)
    except (KeyError, IndexError):
        return template

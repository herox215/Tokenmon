"""Base class for popover pane controllers.

Plain-Python class — *not* an NSObject subclass. Pane controllers don't
sit in any selector chain; they own state, build a view, and hold strong
references to whatever NSObject handlers their view wires up.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tokenmon.popover._main import TokenmonPopover
    from AppKit import NSView


class PaneController:
    """Owns a single pane's view, state and handler GC anchors."""

    def __init__(self, popover: "TokenmonPopover") -> None:
        self.popover = popover
        # Strong refs to NSObject handlers (button targets, NSTimer targets) —
        # AppKit keeps weak refs so we anchor them here for the lifetime of
        # the controller.
        self._handlers: list = []

    def build_view(self) -> "NSView":
        raise NotImplementedError

    def teardown(self) -> None:
        """Called by ``_show_pane`` before the next pane takes over.
        Subclasses override to invalidate timers, drop view refs, etc."""
        self._handlers.clear()

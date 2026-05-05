"""Generic NSObject click-target that forwards to a Python callable.

Replaces the legacy boilerplate where every button needed its own
``NSObject`` subclass (``_BoxBackHandler``, ``_BagOpenHandler`` etc.) just
to bridge a click into a Python method. Use a closure or bound-method
instead and keep a strong reference to the returned handler so AppKit's
weak target reference stays alive.
"""
from __future__ import annotations

import logging
from typing import Callable

import objc
from Foundation import NSObject

log = logging.getLogger("tokenmon.popover.handlers")


class _ActionHandler(NSObject):
    """Bridges ``button -> Python callable`` with a single ``fire:`` selector.

    Use::

        handler = _ActionHandler.alloc().initWithCallback_(self._on_box_back)
        button.setTarget_(handler)
        button.setAction_(b"fire:")
        self._handlers.append(handler)  # GC anchor
    """

    def initWithCallback_(self, callback: Callable):  # noqa: N802
        self = objc.super(_ActionHandler, self).init()
        if self is None:
            return None
        self._cb = callback
        return self

    def fire_(self, sender):  # noqa: N802
        try:
            self._cb(sender)
        except Exception:
            qual = getattr(self._cb, "__qualname__", repr(self._cb))
            log.exception("action handler failed: %s", qual)


def make_handler(callback: Callable) -> _ActionHandler:
    """Convenience wrapper — saves the ``alloc().initWithCallback_(...)`` ritual."""
    return _ActionHandler.alloc().initWithCallback_(callback)

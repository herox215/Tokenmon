"""Global input-event monitor for companion-mode orientation.

Subscribes to NSEvent's global event stream (events delivered to OTHER
apps — i.e. anything the user does outside our menubar/popover) and
records the wall-clock time of the most recent interaction. The
companion overlay polls ``seconds_since_last_input()`` every few seconds
and flips back/front sprite based on whether the user has been active
recently.

NSEvent's global monitor doesn't require Accessibility permission for
key-down + mouse-down + scroll events (which is all we need). Mouse-move
events would be useful but are far too noisy — we leave them out.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from AppKit import (
    NSEvent,
    NSEventMaskKeyDown,
    NSEventMaskLeftMouseDown,
    NSEventMaskOtherMouseDown,
    NSEventMaskRightMouseDown,
    NSEventMaskScrollWheel,
)

log = logging.getLogger("tokenmon.companion.input_monitor")

_INPUT_MASK = (
    NSEventMaskKeyDown
    | NSEventMaskLeftMouseDown
    | NSEventMaskRightMouseDown
    | NSEventMaskOtherMouseDown
    | NSEventMaskScrollWheel
)


class InputActivityMonitor:
    """Global NSEvent monitor that tracks the timestamp of the most recent
    user input event delivered to any other app.

    Use as a context-manager-ish object: ``start()`` installs the monitor,
    ``stop()`` removes it. ``seconds_since_last_input()`` returns the
    elapsed time in seconds, or ``None`` when no input has been seen yet.
    """

    def __init__(self, on_input: Callable[[], None] | None = None) -> None:
        self._monitor = None
        self._last_ts_monotonic: float | None = None
        self._on_input = on_input

    def start(self) -> None:
        if self._monitor is not None:
            return

        def _handler(_event):
            self._last_ts_monotonic = time.monotonic()
            cb = self._on_input
            if cb is not None:
                try:
                    cb()
                except Exception:
                    log.exception("input callback raised")

        try:
            self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                _INPUT_MASK, _handler,
            )
        except Exception:
            log.exception("global input monitor install failed")
            self._monitor = None

    def stop(self) -> None:
        if self._monitor is None:
            return
        try:
            NSEvent.removeMonitor_(self._monitor)
        except Exception:
            log.exception("global input monitor remove failed")
        self._monitor = None

    def seconds_since_last_input(self) -> float | None:
        if self._last_ts_monotonic is None:
            return None
        return time.monotonic() - self._last_ts_monotonic

    def mark_input_now(self) -> None:
        """Manually bump the timestamp — useful when an event the global
        monitor can't see (e.g. our own popover toggling) should still
        count as 'user is active'."""
        self._last_ts_monotonic = time.monotonic()

"""NSWorkspace observer for the foreground application.

Subscribes to ``NSWorkspaceDidActivateApplicationNotification`` and
invokes a Python callback with the new app's bundle identifier. Used
by the companion overlay to flip front/back sprite orientation when
the user switches apps.

Event-driven — no polling. The observer holds a strong ref to itself
via the menubar app's attribute so PyObjC doesn't GC it before
notifications arrive.
"""
from __future__ import annotations

import logging
from typing import Callable

import objc
from AppKit import NSWorkspace
from Foundation import NSObject

log = logging.getLogger("tokenmon.companion.active_app")

# Foundation's notification name strings are bridged automatically.
_ACTIVATE_NAME = "NSWorkspaceDidActivateApplicationNotification"
_USER_INFO_APP_KEY = "NSWorkspaceApplicationKey"


class ActiveAppObserver(NSObject):
    """NSObject bridge that turns NSWorkspace activation notifications
    into a Python callback receiving the new bundle identifier."""

    def initWithCallback_(self, callback: Callable[[str | None], None]):  # noqa: N802
        self = objc.super(ActiveAppObserver, self).init()
        if self is None:
            return None
        self._callback = callback
        self._subscribed = False
        return self

    def start(self) -> None:
        if self._subscribed:
            return
        center = NSWorkspace.sharedWorkspace().notificationCenter()
        center.addObserver_selector_name_object_(
            self, b"appActivated:", _ACTIVATE_NAME, None,
        )
        self._subscribed = True

    def stop(self) -> None:
        if not self._subscribed:
            return
        center = NSWorkspace.sharedWorkspace().notificationCenter()
        try:
            center.removeObserver_(self)
        except Exception:
            log.exception("removeObserver failed")
        self._subscribed = False

    def appActivated_(self, notification):  # noqa: N802
        bundle_id: str | None = None
        try:
            info = notification.userInfo()
            app = info.objectForKey_(_USER_INFO_APP_KEY) if info is not None else None
            if app is not None:
                bid = app.bundleIdentifier()
                bundle_id = str(bid) if bid is not None else None
        except Exception:
            log.exception("failed to extract bundle id from notification")
        try:
            self._callback(bundle_id)
        except Exception:
            log.exception("active-app callback raised")


def current_bundle_id() -> str | None:
    """Return the bundle identifier of whatever app is currently in the
    foreground, or None if it can't be resolved. Used to seed the
    overlay's initial orientation so we don't need to wait for the first
    activation event."""
    try:
        ws = NSWorkspace.sharedWorkspace()
        app = ws.frontmostApplication()
        if app is None:
            return None
        bid = app.bundleIdentifier()
        return str(bid) if bid is not None else None
    except Exception:
        log.exception("frontmostApplication lookup failed")
        return None

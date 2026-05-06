"""Look up the focused window's screen-space bounds for a given PID.

Uses Quartz' ``CGWindowListCopyWindowInfo`` — no Accessibility permission
required (unlike the AX API). Returns the bounding rect already
converted to AppKit's bottom-origin coordinate system so callers don't
have to think about the flip, plus the CG window-id so callers can
detect "same app, different window" transitions cheaply.

Filtering catches several real-world traps:

- Minimized / off-screen ghost windows (CG keeps those in the list at
  -∞ coordinates) are filtered via screen-frame intersection.
- Hidden / nearly-transparent windows (kCGWindowAlpha < 0.5) are skipped
  so a fade-out sheet doesn't get picked over the real window beneath.
- Tiny palette / splash windows are skipped so the sprite doesn't dock
  to a 200×100 popup that's about to disappear.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from AppKit import NSScreen
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionOnScreenOnly,
)

log = logging.getLogger("tokenmon.companion.window_geom")


@dataclass(frozen=True, slots=True)
class WindowRect:
    """Window bounds in AppKit coordinates (y grows upward, origin = bottom-
    left of the primary screen) plus the CG window number for change
    detection."""
    x: float
    y: float          # AppKit y of the window's BOTTOM edge
    width: float
    height: float
    window_id: int

    @property
    def top(self) -> float:
        return self.y + self.height


def _primary_screen_height() -> float | None:
    """Height of the primary screen — used to flip CG y to AppKit y.

    The CG global coordinate system has its origin at the top-left of
    the primary screen, with y growing downward. AppKit's global
    coordinate system has its origin at the bottom-left of the primary
    screen, with y growing upward. The flip is:

        appkit_y_bottom = primary_h - cg_y_top - height

    This formula is correct regardless of which screen the rect is on —
    multi-monitor setups place secondary screens at negative or
    positive y in CG space, and the same flip yields the right AppKit
    coordinates because both spaces share the primary's origin.
    """
    screens = NSScreen.screens()
    if not screens:
        return None
    return float(screens[0].frame().size.height)


def _cg_screen_rects() -> list[tuple[float, float, float, float]]:
    """Each connected screen's frame in CG coords (x, y_top, w, h).

    Used to verify that a window's CG rect actually intersects a real
    display — minimized windows often live at coordinates like
    (-29000, -29000) and would otherwise pass the bounds-size filter.
    """
    primary_h = _primary_screen_height()
    if primary_h is None:
        return []
    out: list[tuple[float, float, float, float]] = []
    for screen in NSScreen.screens():
        f = screen.frame()
        sx = float(f.origin.x)
        sw = float(f.size.width)
        sh = float(f.size.height)
        # AppKit y of the screen's BOTTOM edge → CG y of the screen's TOP.
        ak_y_bottom = float(f.origin.y)
        cg_y_top = primary_h - (ak_y_bottom + sh)
        out.append((sx, cg_y_top, sw, sh))
    return out


def _intersects_any_screen(cx: float, cy: float, cw: float, ch: float) -> bool:
    """True if the CG rect overlaps with at least one connected screen."""
    rects = _cg_screen_rects()
    if not rects:
        return True  # can't verify — assume yes
    for sx, sy, sw, sh in rects:
        if (cx < sx + sw and cx + cw > sx and
                cy < sy + sh and cy + ch > sy):
            return True
    return False


def focused_window_bounds(pid: int) -> WindowRect | None:
    """Return the bounding rect of the topmost on-screen window owned by
    ``pid``, or None when nothing usable is found.

    CGWindowListCopyWindowInfo returns windows in front-to-back z-order,
    so the first PID match at layer 0 is the focused window of that app.
    We additionally filter:
      - layer != 0 (skip status bars, panels, screensaver overlays)
      - kCGWindowAlpha < 0.5 (skip fading-out / hidden windows)
      - bounds < 200×150 (skip palettes, tooltips, splash screens)
      - rect doesn't intersect any connected screen (skip minimized/
        off-screen ghosts at -29000-ish coords)
    """
    primary_h = _primary_screen_height()
    if primary_h is None:
        return None
    try:
        info_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
    except Exception:
        log.exception("CGWindowListCopyWindowInfo failed")
        return None
    if info_list is None:
        return None
    for entry in info_list:
        try:
            owner_pid = int(entry.get("kCGWindowOwnerPID", -1))
            layer = int(entry.get("kCGWindowLayer", -1))
        except Exception:
            continue
        if owner_pid != int(pid):
            continue
        if layer != 0:
            continue
        try:
            alpha = float(entry.get("kCGWindowAlpha", 1.0))
        except Exception:
            alpha = 1.0
        if alpha < 0.5:
            continue
        bounds = entry.get("kCGWindowBounds")
        if bounds is None:
            continue
        try:
            cx = float(bounds.get("X", 0.0))
            cy = float(bounds.get("Y", 0.0))
            cw = float(bounds.get("Width", 0.0))
            ch = float(bounds.get("Height", 0.0))
        except Exception:
            continue
        if cw < 200 or ch < 150:
            continue
        if not _intersects_any_screen(cx, cy, cw, ch):
            continue
        try:
            window_id = int(entry.get("kCGWindowNumber", 0))
        except Exception:
            window_id = 0
        ak_y = primary_h - cy - ch
        return WindowRect(
            x=cx, y=ak_y, width=cw, height=ch, window_id=window_id,
        )
    return None


def frontmost_pid() -> int | None:
    """PID of whatever app is currently frontmost, or None."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid = app.processIdentifier()
        return int(pid) if pid is not None else None
    except Exception:
        log.exception("frontmostApplication() PID lookup failed")
        return None


def screen_containing_point(x: float, y: float):
    """The NSScreen whose AppKit frame contains the given AppKit point,
    or None when the point is off all screens.

    Used to fall back to the screen the docked window is on (rather than
    main screen) when we need to compute a corner position after the
    window disappears.
    """
    for screen in NSScreen.screens():
        f = screen.frame()
        if (f.origin.x <= x < f.origin.x + f.size.width and
                f.origin.y <= y < f.origin.y + f.size.height):
            return screen
    return None

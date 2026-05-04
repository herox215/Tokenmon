"""Click-through desktop overlay showing the current Pokemon sprite.

A borderless, transparent NSWindow that floats above other windows on every
Space, ignores all mouse and keyboard events (so it can never steal focus
or interfere with what's underneath), and just animates the GIF.
"""

from __future__ import annotations

import logging
from pathlib import Path

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSScreen,
    NSShadow,
    NSTextAlignmentCenter,
    NSTextField,
    NSTimer,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorTransient,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject

log = logging.getLogger("tokenmon.overlay")

DEFAULT_SIZE = 128
DEFAULT_MARGIN = 40
DEFAULT_CORNER = "bottom-right"
LEVEL_UP_BANNER_HEIGHT = 36
LEVEL_UP_DISPLAY_SEC = 4.0


class _LevelUpHandler(NSObject):
    """NSTimer target that tears down the level-up banner when the timer fires."""

    def initWithOverlay_(self, overlay):  # noqa: N802
        self = objc.super(_LevelUpHandler, self).init()
        if self is None:
            return None
        self._overlay = overlay
        return self

    def fire_(self, _timer):  # noqa: N802
        try:
            self._overlay._end_level_up()
        except Exception:
            log.exception("level-up teardown failed")


def _position_for(corner: str, screen_frame, size: int, margin: int) -> tuple[float, float]:
    sx, sy, sw, sh = (
        screen_frame.origin.x, screen_frame.origin.y,
        screen_frame.size.width, screen_frame.size.height,
    )
    if corner == "top-left":
        return sx + margin, sy + sh - size - margin
    if corner == "top-right":
        return sx + sw - size - margin, sy + sh - size - margin
    if corner == "bottom-left":
        return sx + margin, sy + margin
    return sx + sw - size - margin, sy + margin  # bottom-right (default)


class PokemonOverlay:
    """Manages a single floating, click-through window that displays the sprite."""

    def __init__(self, size: int = DEFAULT_SIZE, margin: int = DEFAULT_MARGIN,
                 corner: str = DEFAULT_CORNER) -> None:
        self._size = size
        self._margin = margin
        self._corner = corner
        self._window: NSWindow | None = None
        self._image_view: NSImageView | None = None
        self._visible = False
        self._level_up_label: NSTextField | None = None
        self._level_up_handler: _LevelUpHandler | None = None
        self._level_up_timer: NSTimer | None = None

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        rect = NSMakeRect(0, 0, self._size, self._size)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False,
        )
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setHasShadow_(False)
        win.setIgnoresMouseEvents_(True)
        win.setMovable_(False)
        win.setLevel_(NSFloatingWindowLevel)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorTransient
        )
        win.setReleasedWhenClosed_(False)

        img_view = NSImageView.alloc().initWithFrame_(rect)
        img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        img_view.setAnimates_(True)
        win.setContentView_(img_view)

        self._window = win
        self._image_view = img_view
        self._reposition()

    def _reposition(self) -> None:
        if self._window is None:
            return
        screen = self._window.screen() or NSScreen.mainScreen()
        if screen is None:
            return
        x, y = _position_for(self._corner, screen.visibleFrame(), self._size, self._margin)
        self._window.setFrameOrigin_((x, y))

    def update_sprite(self, sprite_path: Path | None) -> None:
        if sprite_path is None or not sprite_path.exists():
            return
        self._ensure_window()
        if self._image_view is None:
            return
        img = NSImage.alloc().initWithContentsOfFile_(str(sprite_path))
        if img is None:
            log.warning("could not load sprite %s into overlay", sprite_path)
            return
        self._image_view.setImage_(img)
        self._image_view.setAnimates_(True)

    def show(self) -> None:
        self._ensure_window()
        if self._window is None:
            return
        self._reposition()
        self._window.orderFrontRegardless()
        self._visible = True

    def hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)
        self._visible = False

    @property
    def visible(self) -> bool:
        return self._visible

    def set_corner(self, corner: str) -> None:
        self._corner = corner
        self._reposition()

    def show_level_up(self) -> None:
        """Pop the overlay window with a transient "Level up!" banner above the
        sprite. Window grows upward to fit, then hides entirely when the banner
        timer expires."""
        # Tear down any previous level-up animation in flight (without hiding —
        # we'll re-show right after).
        if self._level_up_timer is not None:
            self._level_up_timer.invalidate()
            self._level_up_timer = None
        if self._level_up_label is not None:
            self._level_up_label.removeFromSuperview()
            self._level_up_label = None
        self._level_up_handler = None

        self._ensure_window()
        if self._window is None:
            return
        if not self._visible:
            self._reposition()
            self._window.orderFrontRegardless()
            self._visible = True

        size = self._size
        banner_h = LEVEL_UP_BANNER_HEIGHT
        win = self._window
        screen = win.screen() or NSScreen.mainScreen()
        if screen is None:
            return
        x, y = _position_for(self._corner, screen.visibleFrame(), size, self._margin)
        # Grow upward so the sprite stays in place.
        win.setFrame_display_animate_(
            NSMakeRect(x, y, size, size + banner_h), True, True
        )

        content = win.contentView()
        content.setFrame_(NSMakeRect(0, 0, size, size + banner_h))
        if self._image_view is not None:
            self._image_view.setFrame_(NSMakeRect(0, 0, size, size))

        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, size, size, banner_h)
        )
        label.setStringValue_("Level up!")
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setAlignment_(NSTextAlignmentCenter)
        label.setFont_(NSFont.boldSystemFontOfSize_(15))
        label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.85, 0.0, 1.0)
        )
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(NSColor.blackColor())
        shadow.setShadowOffset_(NSMakeSize(0, -1))
        shadow.setShadowBlurRadius_(3)
        label.setShadow_(shadow)
        content.addSubview_(label)
        self._level_up_label = label

        self._level_up_handler = _LevelUpHandler.alloc().initWithOverlay_(self)
        self._level_up_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                LEVEL_UP_DISPLAY_SEC, self._level_up_handler, b"fire:", None, False
            )
        )

    def _end_level_up(self) -> None:
        if self._level_up_timer is not None:
            self._level_up_timer.invalidate()
            self._level_up_timer = None
        if self._level_up_label is not None:
            self._level_up_label.removeFromSuperview()
            self._level_up_label = None
        self._level_up_handler = None
        if self._window is None:
            return
        # Shrink window back to sprite-only, then hide it. Overlay only ever
        # appears for level-up events, so we want it gone afterwards.
        screen = self._window.screen() or NSScreen.mainScreen()
        if screen is not None:
            x, y = _position_for(self._corner, screen.visibleFrame(), self._size, self._margin)
            self._window.setFrame_display_animate_(
                NSMakeRect(x, y, self._size, self._size), True, False
            )
        content = self._window.contentView()
        content.setFrame_(NSMakeRect(0, 0, self._size, self._size))
        if self._image_view is not None:
            self._image_view.setFrame_(NSMakeRect(0, 0, self._size, self._size))
        self.hide()

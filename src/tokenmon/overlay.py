"""Desktop overlay showing the current Pokemon sprite.

A borderless, transparent NSWindow that floats above other windows on every
Space. In event-only mode it ignores mouse and keyboard events; in companion
mode the sprite accepts a double-click to open the companion chat panel.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSCompositingOperationSourceAtop,
    NSCompositingOperationSourceOver,
    NSFloatingWindowLevel,
    NSPopUpMenuWindowLevel,
    NSFont,
    NSGraphicsContext,
    NSImage,
    NSImageInterpolationNone,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSPanel,
    NSRectFillUsingOperation,
    NSScreen,
    NSShadow,
    NSWindowStyleMaskFullSizeContentView,
    NSTextAlignmentCenter,
    NSTextField,
    NSTimer,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorTransient,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
    NSEvent,
    NSEventMaskLeftMouseDown,
    NSEventMaskOtherMouseDown,
    NSEventMaskRightMouseDown,
)
from Foundation import NSDate, NSMakeRect, NSMakeSize, NSObject
from AppKit import NSAnimationContext

log = logging.getLogger("tokenmon.overlay")

DEFAULT_SIZE = 128
DEFAULT_MARGIN = 40
DEFAULT_CORNER = "bottom-right"
LEVEL_UP_BANNER_HEIGHT = 36
LEVEL_UP_DISPLAY_SEC = 4.0
CHAT_SCREEN_WIDTH_RATIO = 0.48
CHAT_SCREEN_HEIGHT_RATIO = 0.38
CHAT_MIN_WIDTH = 500
CHAT_MAX_WIDTH = 900
CHAT_MIN_HEIGHT = 300
CHAT_MAX_HEIGHT = 440
CHAT_BOTTOM_MARGIN = 44

# Evolution sequence: (delay_before_step_seconds, step_name).
# Mirrors the Gen-3 Pokémon evolution feel: silhouette flicker that accelerates
# from slow swaps to rapid blinking, then a white flash, then a held reveal of
# the new form with a banner. Total runtime ~10 s.
EVOLUTION_STEPS: list[tuple[float, str]] = [
    (0.00, "sil_old"),
    # Slow build — anticipation
    (0.55, "sil_new"),
    (0.50, "sil_old"),
    (0.46, "sil_new"),
    (0.42, "sil_old"),
    (0.38, "sil_new"),
    (0.34, "sil_old"),
    (0.30, "sil_new"),
    (0.26, "sil_old"),
    (0.22, "sil_new"),
    # Acceleration
    (0.19, "sil_old"),
    (0.16, "sil_new"),
    (0.14, "sil_old"),
    (0.12, "sil_new"),
    (0.10, "sil_old"),
    (0.09, "sil_new"),
    (0.08, "sil_old"),
    (0.07, "sil_new"),
    # Rapid blinking climax
    (0.06, "sil_old"),
    (0.05, "sil_new"),
    (0.05, "sil_old"),
    (0.05, "sil_new"),
    # Flash + reveal
    (0.12, "flash"),
    (0.90, "reveal"),
    (6.00, "done"),
]


def _silhouette_image(src: NSImage, color: NSColor) -> NSImage:
    """Render `src` as a solid-colour silhouette (alpha mask filled with color)."""
    size = src.size()
    out = NSImage.alloc().initWithSize_(size)
    out.lockFocus()
    src.drawAtPoint_fromRect_operation_fraction_(
        (0, 0), NSMakeRect(0, 0, size.width, size.height),
        NSCompositingOperationSourceOver, 1.0,
    )
    color.set()
    NSRectFillUsingOperation(
        NSMakeRect(0, 0, size.width, size.height),
        NSCompositingOperationSourceAtop,
    )
    out.unlockFocus()
    return out


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


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(float(value), float(low)), float(high))


def _chat_frame_for_screen(screen_frame):
    """Bottom-centred chat frame sized to roughly 40% of the active screen."""
    sw = float(screen_frame.size.width)
    sh = float(screen_frame.size.height)
    width = _clamp(sw * CHAT_SCREEN_WIDTH_RATIO, CHAT_MIN_WIDTH, CHAT_MAX_WIDTH)
    height = _clamp(sh * CHAT_SCREEN_HEIGHT_RATIO, CHAT_MIN_HEIGHT, CHAT_MAX_HEIGHT)
    x = float(screen_frame.origin.x) + (sw - width) / 2.0
    y = float(screen_frame.origin.y) + CHAT_BOTTOM_MARGIN
    return NSMakeRect(x, y, width, height)


def _chat_start_frame(final_frame):
    """Offscreen frame below the final position so the panel slides up into view."""
    return NSMakeRect(
        float(final_frame.origin.x),
        float(final_frame.origin.y) - float(final_frame.size.height),
        float(final_frame.size.width),
        float(final_frame.size.height),
    )


def _screen_under_mouse():
    """Return the NSScreen the mouse cursor is currently on, or None.

    The chat panel is a global-hotkey trigger — the user expects it to
    appear on the display they're looking at, not on whatever monitor
    the companion sprite happens to live on. We iterate NSScreen.screens()
    and pick the one whose frame contains ``NSEvent.mouseLocation()``;
    AppKit's frame coords are bottom-up so a direct point-in-rect test
    works without conversion.
    """
    try:
        loc = NSEvent.mouseLocation()
    except Exception:
        return None
    try:
        screens = NSScreen.screens()
    except Exception:
        return None
    for screen in screens or []:
        frame = screen.frame()
        x = float(loc.x)
        y = float(loc.y)
        left = float(frame.origin.x)
        bottom = float(frame.origin.y)
        right = left + float(frame.size.width)
        top = bottom + float(frame.size.height)
        if left <= x < right and bottom <= y < top:
            return screen
    return None


# Vertical gap between the chat panel's top edge and the sprite's
# bottom. Used instead of an overlap because the sprite + chat share
# the floating window level — any overlap would put the sprite's lower
# half behind the chat panel (the chat is most-recently-front-ordered
# at show time). A small positive gap also leaves room for BOB's ±3 px
# breath sine without dipping into the chat.
_CHAT_SPRITE_GAP_PX = 6
# Horizontal inset from the chat panel's right edge so the sprite isn't
# flush with the macOS window-shadow gradient.
_CHAT_SPRITE_RIGHT_INSET = 8


def _sprite_origin_for_chat(chat_frame, sprite_size: int) -> tuple[float, float]:
    """Top-right anchor of ``chat_frame`` with the sprite resting just
    above the panel's top edge. AppKit origin is bottom-left, so y
    grows upward.

    Pure function — no AppKit calls — so it's covered by a unit test.
    """
    chat_x = float(chat_frame.origin.x)
    chat_y = float(chat_frame.origin.y)
    chat_w = float(chat_frame.size.width)
    chat_h = float(chat_frame.size.height)
    size = float(sprite_size)
    x = chat_x + chat_w - size - _CHAT_SPRITE_RIGHT_INSET
    # Sprite sits ABOVE the chat top with a small gap. Without this the
    # bottom of the sprite ends up behind the chat panel because both
    # windows live at NSFloatingWindowLevel and the chat is front-of-
    # level after show_chat's makeKeyAndOrderFront_.
    y = chat_y + chat_h + _CHAT_SPRITE_GAP_PX
    return x, y


class _ChatCardView(NSView):
    """Subtle border over the system blur backing."""

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 10, 10,
        )
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22).set()
        path.setLineWidth_(1.0)
        path.stroke()


class _ChatWindow(NSPanel):
    """Borderless chat window that can still accept keyboard focus."""

    def canBecomeKeyWindow(self):  # noqa: N802
        return True

    def canBecomeMainWindow(self):  # noqa: N802
        return True

    def worksWhenModal(self):  # noqa: N802
        return True

    def constrainFrameRect_toScreen_(self, frame_rect, _screen):  # noqa: N802
        # Default NSWindow behaviour clamps the frame onto the visible screen,
        # which kills the slide-up: a start frame below visibleFrame gets
        # snapped to the bottom of the screen before the animation begins.
        return frame_rect


class _ChatSlideHandler(NSObject):
    """NSTimer-driven slide-up: interpolates window y from start to end.

    macOS' built-in window animations (`animator().setFrame:`,
    `setFrame:display:animate:`) refused to actually move our floating panel
    — frame mutations through both paths snapped instantly to the target. A
    manual ~60 Hz timer with ease-out cubic gives us a reliable slide and
    co-animates alpha for a fade-in.
    """

    def initWithWindow_startFrame_endFrame_startAlpha_endAlpha_duration_onComplete_(  # noqa: N802
        self, window, start_frame, end_frame, start_alpha, end_alpha, duration, on_complete,
    ):
        self = objc.super(_ChatSlideHandler, self).init()
        if self is None:
            return None
        self._window = window
        self._start_x = float(start_frame.origin.x)
        self._start_y = float(start_frame.origin.y)
        self._start_w = float(start_frame.size.width)
        self._start_h = float(start_frame.size.height)
        self._end_x = float(end_frame.origin.x)
        self._end_y = float(end_frame.origin.y)
        self._end_w = float(end_frame.size.width)
        self._end_h = float(end_frame.size.height)
        self._start_alpha = float(start_alpha)
        self._end_alpha = float(end_alpha)
        self._duration = float(duration)
        self._on_complete = on_complete  # callable or None
        self._t0 = NSDate.date().timeIntervalSinceReferenceDate()
        self._timer = None
        return self

    def start(self):
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 60.0, self, "tick:", None, True,
        )

    def cancel(self):
        """Tear down the timer mid-flight. Called when a new dock /
        slide is scheduled before this one completed (e.g. user
        toggles the chat panel rapidly). Drops the on_complete so it
        doesn't fire after cancellation."""
        if self._timer is not None:
            try:
                self._timer.invalidate()
            except Exception:
                log.exception("chat slide cancel failed")
            self._timer = None
        self._on_complete = None

    def tick_(self, _timer):  # noqa: N802
        now = NSDate.date().timeIntervalSinceReferenceDate()
        t = (now - self._t0) / self._duration
        if t >= 1.0:
            t = 1.0
        # ease-out cubic — fast at the start, soft landing at the end.
        eased = 1.0 - (1.0 - t) ** 3
        x = self._start_x + (self._end_x - self._start_x) * eased
        y = self._start_y + (self._end_y - self._start_y) * eased
        w = self._start_w + (self._end_w - self._start_w) * eased
        h = self._start_h + (self._end_h - self._start_h) * eased
        alpha = self._start_alpha + (self._end_alpha - self._start_alpha) * eased
        self._window.setFrame_display_(NSMakeRect(x, y, w, h), True)
        self._window.setAlphaValue_(alpha)
        if t >= 1.0:
            if self._timer is not None:
                self._timer.invalidate()
                self._timer = None
            if self._on_complete is not None:
                try:
                    self._on_complete()
                except Exception:
                    log.exception("chat slide on_complete failed")
                self._on_complete = None


class _ChatWindowDelegate(NSObject):
    def initWithOverlay_(self, overlay):  # noqa: N802
        self = objc.super(_ChatWindowDelegate, self).init()
        if self is None:
            return None
        self._overlay = overlay
        return self

    def windowDidResignKey_(self, _notification):  # noqa: N802
        self._overlay.hide_chat()


class _CompanionImageView(NSImageView):
    """NSImageView subclass that disables image interpolation in its
    draw path so animated pixel-art GIFs stay crisp when scaled
    (96×96 native → 128×128 view → ±1.05 layer-zoom transform).

    Mirrors the popover's ``_CrispImageView`` — layer-level
    magnification filters alone aren't enough for animated GIFs
    because each frame goes through NSImageView's draw pipeline,
    which would otherwise apply default bilinear interpolation.

    Event-only overlays stay click-through. Companion mode lets this view
    receive a double-click so the user can open the companion chat panel.
    """

    def initWithFrame_overlay_(self, frame, overlay):  # noqa: N802
        self = objc.super(_CompanionImageView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._overlay = overlay
        return self

    def drawRect_(self, rect):  # noqa: N802
        ctx = NSGraphicsContext.currentContext()
        if ctx is not None:
            ctx.setImageInterpolation_(NSImageInterpolationNone)
        objc.super(_CompanionImageView, self).drawRect_(rect)

    def acceptsFirstMouse_(self, _event):  # noqa: N802
        return True

    def mouseDown_(self, event):  # noqa: N802
        overlay = getattr(self, "_overlay", None)
        if overlay is None:
            return
        try:
            if event.clickCount() >= 2:
                overlay.toggle_chat()
        except Exception:
            log.exception("companion double-click failed")


WIGGLE_FRAMES = 6         # 6 frames @ 50 ms = 300 ms total wiggle duration
WIGGLE_INTERVAL = 0.05
WIGGLE_AMPLITUDE_PX = 8


class _WiggleHandler(NSObject):
    """NSTimer-driven horizontal-shake animation for the companion sprite.

    Used to announce an event (e.g. items about to drop) without actually
    moving the sprite away from its docked position. Each frame nudges
    the window's origin by ±amp px around the start origin with linear
    damping so the last swing is smaller than the first.

    During the wiggle the menubar's ``_tick_dock`` early-exits on the
    overlay's ``wiggling`` property — without that, the periodic re-dock
    would fight the per-frame setFrameOrigin calls and flatten the
    visible motion.
    """

    def initWithOverlay_originX_originY_amplitude_frames_(  # noqa: N802
        self, overlay, ox, oy, amplitude, frames,
    ):
        self = objc.super(_WiggleHandler, self).init()
        if self is None:
            return None
        self._overlay = overlay
        self._ox = float(ox)
        self._oy = float(oy)
        self._amp = int(amplitude)
        self._frames = int(frames)
        self._frame = 0
        return self

    def start(self):
        self._scheduleNext()

    def _scheduleNext(self):
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            WIGGLE_INTERVAL, self, b"fire:", None, False,
        )

    def fire_(self, _timer):  # noqa: N802
        # If the overlay was reset / replaced, abort.
        if getattr(self._overlay, "_wiggle_handler", None) is not self:
            return
        self._frame += 1
        try:
            if self._frame >= self._frames:
                # Snap to the original origin and clear the wiggling flag.
                if self._overlay._window is not None:
                    self._overlay._window.setFrameOrigin_((self._ox, self._oy))
                self._overlay._wiggling = False
                self._overlay._wiggle_handler = None
                return
            # Alternating direction with linear damping so the last swing
            # is smaller than the first.
            direction = 1 if self._frame % 2 == 1 else -1
            decay = (self._frames - self._frame) / self._frames
            offset = direction * self._amp * decay
            if self._overlay._window is not None:
                self._overlay._window.setFrameOrigin_(
                    (self._ox + offset, self._oy),
                )
        except Exception:
            log.exception("wiggle frame failed")
            self._overlay._wiggling = False
            self._overlay._wiggle_handler = None
            return
        self._scheduleNext()


TURN_FRAMES = 12          # 12 frames @ 25 ms = 300 ms total turn duration
TURN_INTERVAL = 0.025
TURN_HALF = 6             # midpoint where the sprite is edge-on (scale_x=0)


class _TurnHandler(NSObject):
    """NSTimer-driven 2D turn animation for the companion sprite.

    Frame plan:
      0..HALF      : scale_x ramps x_start → 0 (sprite squishes horizontally
                     while y stays at start_zoom)
      HALF         : sprite image is swapped to the target path; scale snaps
                     to (0, target_zoom) so the new sprite emerges at its
                     intended size
      HALF+1..N-1  : scale_x ramps 0 → ±target_zoom (sign = target_mirrored)

    The image swap at the edge-on midpoint reads as a 2D vertical-axis
    rotation; the y-zoom snap at the same instant lets the back sprite
    appear at a larger scale than the front to compensate for PokeAPI
    back sprites drawing their character smaller within the canvas.
    """

    def initWithOverlay_target_xEnd_yEnd_xStart_yStart_speed_(  # noqa: N802
        self, overlay, target_path, x_end, y_end, x_start, y_start, speed,
    ):
        self = objc.super(_TurnHandler, self).init()
        if self is None:
            return None
        self._overlay = overlay
        self._target_path = target_path
        self._x_end = float(x_end)
        self._y_end = float(y_end)
        self._x_start = float(x_start)
        self._y_start = float(y_start)
        self._speed = float(speed)
        self._frame = 0
        return self

    def start(self):
        self._scheduleNext()

    def _scheduleNext(self):
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            TURN_INTERVAL, self, b"fire:", None, False,
        )

    def fire_(self, _timer):  # noqa: N802
        if getattr(self._overlay, "_turn_handler", None) is not self:
            return  # newer turn cancelled us
        self._frame += 1
        try:
            if self._frame < TURN_HALF:
                t = self._frame / TURN_HALF
                self._overlay._apply_scale(
                    self._x_start * (1.0 - t), self._y_start,
                )
            elif self._frame == TURN_HALF:
                # Edge-on swap: new sprite emerges at the target zoom.
                # Speed parameter is honoured here so the post-turn
                # animation pacing matches the active Pokémon's HP.
                self._overlay.update_sprite(
                    self._target_path, speed=self._speed,
                )
                self._overlay._apply_scale(0.0, self._y_end)
            elif self._frame < TURN_FRAMES:
                t = (self._frame - TURN_HALF) / (TURN_FRAMES - TURN_HALF)
                self._overlay._apply_scale(self._x_end * t, self._y_end)
            else:
                self._overlay._end_turn(self._x_end, self._y_end)
                return
        except Exception:
            log.exception("turn animation step failed")
            self._overlay._end_turn(self._x_end, self._y_end)
            return
        self._scheduleNext()


class _AlertHandler(NSObject):
    """NSTimer target that clears a transient flash_alert banner."""

    def initWithOverlay_(self, overlay):  # noqa: N802
        self = objc.super(_AlertHandler, self).init()
        if self is None:
            return None
        self._overlay = overlay
        return self

    def fire_(self, _timer):  # noqa: N802
        try:
            self._overlay._end_alert()
        except Exception:
            log.exception("alert teardown failed")


class _FloatingItemHandler(NSObject):
    """One floating-item drift animation. Each instance owns its own
    transparent click-through NSWindow showing a single item sprite,
    drifts it upward over a fixed duration, fades out, then closes the
    window.

    PokemonOverlay creates one handler per dropped item and staggers
    their starts so a multi-item drop arrives as a small shower."""

    FLOAT_FRAMES = 40
    FRAME_INTERVAL = 0.05  # → 2.0 s total
    STEP_DY = 4.5           # px per frame; ~180 px float distance
    FADE_IN_FRAMES = 5
    FADE_OUT_FRAMES = 9
    SIZE = 32
    # Side-to-side wobble — sinusoidal x-offset around the start column
    # gives the floater a "balloon drifting upward" feel rather than a
    # rigid vertical slide.
    WOBBLE_AMPLITUDE = 11.0  # px to either side of the start x
    WOBBLE_PERIOD = 14       # frames per full sine cycle (~0.7 s)

    def initWithSprite_x_y_delay_(  # noqa: N802
        self, sprite, x, y, initial_delay,
    ):
        self = objc.super(_FloatingItemHandler, self).init()
        if self is None:
            return None
        self._sprite = sprite
        self._start_x = float(x)
        self._start_y = float(y)
        self._initial_delay = float(initial_delay)
        self._frame = 0
        self._window = None
        self._image_view = None
        return self

    def _build_window(self):
        size = _FloatingItemHandler.SIZE
        rect = NSMakeRect(self._start_x, self._start_y, size, size)
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
        win.setAlphaValue_(0.0)

        iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, size, size))
        iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        iv.setImage_(self._sprite)
        iv.setWantsLayer_(True)
        layer = iv.layer()
        if layer is not None:
            layer.setMagnificationFilter_("nearest")
            layer.setMinificationFilter_("nearest")
        win.setContentView_(iv)
        self._window = win
        self._image_view = iv

    def start(self):
        delay = max(0.001, self._initial_delay)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay, self, b"begin:", None, False,
        )

    def begin_(self, _timer):  # noqa: N802
        try:
            self._build_window()
        except Exception:
            log.exception("floating item window build failed")
            return
        if self._window is None:
            return
        self._window.orderFrontRegardless()
        self._scheduleNext()

    def _scheduleNext(self):
        if self._window is None or self._frame >= _FloatingItemHandler.FLOAT_FRAMES:
            self._end()
            return
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _FloatingItemHandler.FRAME_INTERVAL, self, b"fire:", None, False,
        )

    def fire_(self, _timer):  # noqa: N802
        if self._window is None:
            return
        self._frame += 1
        new_y = self._start_y + self._frame * _FloatingItemHandler.STEP_DY
        # Sinusoidal x-wobble around the start column.
        wobble = _FloatingItemHandler.WOBBLE_AMPLITUDE * math.sin(
            2.0 * math.pi * self._frame / _FloatingItemHandler.WOBBLE_PERIOD
        )
        self._window.setFrameOrigin_((self._start_x + wobble, new_y))
        # Fade in the first FADE_IN_FRAMES, hold, fade out the last FADE_OUT_FRAMES.
        fi = _FloatingItemHandler.FADE_IN_FRAMES
        fo = _FloatingItemHandler.FADE_OUT_FRAMES
        total = _FloatingItemHandler.FLOAT_FRAMES
        if self._frame <= fi:
            alpha = self._frame / float(fi)
        elif self._frame >= total - fo:
            alpha = max(0.0, (total - self._frame) / float(fo))
        else:
            alpha = 1.0
        self._window.setAlphaValue_(alpha)
        if self._frame >= total:
            self._end()
            return
        self._scheduleNext()

    def _end(self):
        if self._window is None:
            return
        try:
            self._window.orderOut_(None)
            self._window.close()
        except Exception:
            log.exception("floating item teardown failed")
        self._window = None
        self._image_view = None


class _EvolutionHandler(NSObject):
    """NSTimer target that drives the evolution animation step-by-step."""

    def initWithOverlay_silOld_silNew_newImage_newName_(  # noqa: N802
        self, overlay, sil_old, sil_new, new_img, new_name
    ):
        self = objc.super(_EvolutionHandler, self).init()
        if self is None:
            return None
        self._overlay = overlay
        self._sil_old = sil_old
        self._sil_new = sil_new
        self._new_img = new_img
        self._new_name = new_name
        self._idx = 0
        return self

    def start(self):
        self._scheduleNext()

    def _scheduleNext(self):
        if self._idx >= len(EVOLUTION_STEPS):
            return
        delay, _ = EVOLUTION_STEPS[self._idx]
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.001, delay), self, b"fire:", None, False
        )

    def fire_(self, _timer):  # noqa: N802
        if self._idx >= len(EVOLUTION_STEPS):
            return
        _, action = EVOLUTION_STEPS[self._idx]
        self._idx += 1
        try:
            if action == "sil_old":
                self._overlay._set_evolution_image(self._sil_old, animated=False)
            elif action == "sil_new":
                self._overlay._set_evolution_image(self._sil_new, animated=False)
            elif action == "flash":
                self._overlay._show_flash()
            elif action == "reveal":
                self._overlay._evolution_reveal(self._new_img, self._new_name)
            elif action == "done":
                self._overlay._end_evolution()
                return
        except Exception:
            log.exception("evolution step %s failed", action)
            self._overlay._end_evolution()
            return
        self._scheduleNext()


# Badge geometry, sprite-relative. _BADGE_INSET is positive: an
# overlap *into* the sprite (so the badge peeks under the sprite's
# bottom-right corner rather than free-floating outside it).
_BADGE_SIZE = 48
_BADGE_INSET = 6


class _PokeballBadgeHandler:
    """Floating rotating-Pokéball window pinned to the companion sprite.

    Owns its own borderless transparent click-through NSPanel — same
    construction shape as ``_FloatingItemHandler`` so the badge composes
    cleanly with the sprite window without interfering with mouse
    routing.

    Rotation is driven by a single ``CABasicAnimation`` on the image
    view's CALayer (``transform.rotation.z``). Core Animation runs the
    spin on the render server so the badge costs zero Python cycles
    while visible — unlike the four NSTimer-driven handlers elsewhere
    in this module, which all need per-frame Python logic for things
    like wiggle amplitude decay or evolution silhouette swaps.
    """

    def __init__(self, overlay) -> None:
        self._overlay = overlay
        self._window: NSWindow | None = None
        # The CALayer that actually holds the Pokéball image. We attach
        # the rotation animation to this sublayer, NOT to the host view's
        # backing layer — that one is managed by AppKit and would have
        # its anchorPoint reset on layout, making the rotation drift
        # off-centre. See ``_build_window`` for the layer-hosting setup.
        self._spinner_layer = None

    def start(self) -> None:
        """Build the window (lazy) and install the spin animation."""
        if self._window is None:
            self._build_window()
            self._install_spin()
        try:
            # orderFrontRegardless — non-key, non-activating; the chat
            # panel / sprite still keep their focus state.
            if self._window is not None:
                self._window.orderFrontRegardless()
        except Exception:
            log.exception("badge order-front failed")

    def stop(self) -> None:
        """Tear the window down. The CABasicAnimation dies with the
        layer, which dies with the view, which dies with the window —
        no explicit ``removeAnimationForKey_`` needed."""
        if self._window is not None:
            try:
                self._window.orderOut_(None)
                self._window.close()
            except Exception:
                log.exception("badge close failed")
        self._window = None
        self._spinner_layer = None

    def set_alpha(self, alpha: float) -> None:
        """Mirror the sprite's effective alpha onto the badge window.

        Driven by ``PokemonOverlay._apply_alpha`` so the badge fades
        and un-fades in lock-step with the companion sprite — cursor
        proximity + night-mood share one pipeline.
        """
        if self._window is None:
            return
        try:
            self._window.setAlphaValue_(max(0.0, min(1.0, float(alpha))))
        except Exception:
            log.exception("badge setAlphaValue_ failed")

    def reposition_to_sprite_frame(self, rect, *, animate: bool = False) -> None:
        """Pin the badge to the bottom-right of ``rect`` with overlap.

        ``rect`` is the sprite window's frame in AppKit screen coords.
        Caller supplies ``animate=True`` to match a sprite slide
        animation — the badge then animates its frame in lockstep
        (~250 ms, Cocoa default).
        """
        if self._window is None:
            return
        sx = float(rect.origin.x)
        sy = float(rect.origin.y)
        sw = float(rect.size.width)
        # Bottom-right corner with inward overlap on both axes.
        bx = sx + sw - _BADGE_SIZE + _BADGE_INSET
        by = sy - _BADGE_INSET
        frame = NSMakeRect(bx, by, _BADGE_SIZE, _BADGE_SIZE)
        try:
            self._window.setFrame_display_animate_(frame, True, bool(animate))
        except Exception:
            log.exception("badge reposition failed")

    def _build_window(self) -> None:
        rect = NSMakeRect(0, 0, _BADGE_SIZE, _BADGE_SIZE)
        # Same flags as ``_FloatingItemHandler`` so the badge behaves
        # like a sibling of the sprite window across every Space and
        # never steals mouse events.
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
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorTransient
        )
        win.setReleasedWhenClosed_(False)

        # Layer-HOSTING view (not layer-backed): we own the layer
        # entirely. AppKit doesn't auto-manage its geometry — without
        # this, the host's anchorPoint gets reset on layout and the
        # rotation drifts off-centre. The order matters: setLayer_
        # *before* setWantsLayer_(True) switches the view into hosting
        # mode (the opposite of the usual layer-backed setup).
        from Quartz import CALayer  # type: ignore

        container = NSView.alloc().initWithFrame_(rect)
        host = CALayer.layer()
        host.setFrame_(rect)
        container.setLayer_(host)
        container.setWantsLayer_(True)

        # The rotating sublayer. AppKit doesn't touch sublayers we
        # add ourselves, so the anchorPoint we set here is permanent
        # and the spin pivots around the badge centre.
        spinner = CALayer.layer()
        spinner.setFrame_(rect)
        spinner.setAnchorPoint_((0.5, 0.5))
        half = float(_BADGE_SIZE) / 2.0
        spinner.setPosition_((half, half))
        spinner.setMagnificationFilter_("nearest")
        spinner.setMinificationFilter_("nearest")

        # NSImage → CGImage so the sublayer can render it as its
        # ``contents``. TIFFRepresentation + NSBitmapImageRep is the
        # boring, no-deprecation-warnings route; works for any image
        # loaded via NSImage.
        cg = None
        try:
            from tokenmon import items_remote
            img = items_remote.get_sprite_by_name("poke-ball")
            if img is not None:
                from AppKit import NSBitmapImageRep  # type: ignore
                tiff = img.TIFFRepresentation()
                if tiff is not None:
                    rep = NSBitmapImageRep.alloc().initWithData_(tiff)
                    if rep is not None:
                        cg = rep.CGImage()
        except Exception:
            log.exception("badge sprite load failed")
        if cg is not None:
            spinner.setContents_(cg)

        host.addSublayer_(spinner)
        win.setContentView_(container)

        self._window = win
        self._spinner_layer = spinner

    def _install_spin(self) -> None:
        """Attach the infinite z-rotation animation to the spinner sublayer.

        We animate the *sublayer* (which we own) rather than the host
        view's backing layer (which AppKit manages). The sublayer's
        anchorPoint stays put at (0.5, 0.5) and Core Animation pivots
        the rotation around its centre.
        """
        layer = self._spinner_layer
        if layer is None:
            return
        try:
            import math
            from Quartz import CABasicAnimation  # type: ignore
            anim = CABasicAnimation.animationWithKeyPath_("transform.rotation.z")
            anim.setFromValue_(0.0)
            anim.setToValue_(-2.0 * math.pi)  # negative → clockwise
            anim.setDuration_(1.6)
            anim.setRepeatCount_(float("inf"))
            anim.setRemovedOnCompletion_(False)
            layer.addAnimation_forKey_(anim, "spin")
        except Exception:
            log.exception("badge spin install failed")


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
    """Manages the floating companion sprite window and its transient UI."""

    def __init__(self, size: int = DEFAULT_SIZE, margin: int = DEFAULT_MARGIN,
                 corner: str = DEFAULT_CORNER) -> None:
        self._size = size
        self._margin = margin
        self._corner = corner
        self._window: NSWindow | None = None
        self._image_view: NSImageView | None = None
        self._visible = False
        # Companion mode: when True, the overlay stays visible after
        # level-up / evolution events finish instead of hiding. Set via
        # set_persistent() from the menubar app, which also calls show()
        # immediately when companion_mode flips on.
        self._persistent: bool = False
        # Alpha factors composited multiplicatively. ``_mood_alpha`` carries
        # the time-of-day modifier (1.0 daytime, 0.85 night), and
        # ``_proximity_alpha`` gets out of the way when the cursor
        # approaches the sprite. Final window alpha is the product of the
        # two. ``_last_applied_alpha`` short-circuits repeated
        # setAlphaValue_ calls when nothing changed — relevant for the
        # 20 Hz proximity tick.
        self._mood_alpha: float = 1.0
        self._proximity_alpha: float = 1.0
        self._last_applied_alpha: float = 1.0
        # In-flight turn animation handler (None when no turn running).
        # Strong-ref kept so PyObjC doesn't GC the NSTimer target before
        # all 12 frames fire.
        self._turn_handler = None
        # Currently-applied (x, y) layer scale — tracked so the next
        # ``animate_sprite_turn`` knows where to start interpolating from.
        # Sign carries mirror state, magnitude carries zoom.
        self._current_scale: tuple[float, float] = (1.0, 1.0)
        # Wiggle (item-drop announce) state. ``_wiggling`` gates the
        # menubar's _tick_dock so the periodic re-dock doesn't fight the
        # per-frame setFrameOrigin nudges. Strong-ref to the handler
        # keeps PyObjC from GC'ing the NSTimer target.
        self._wiggling: bool = False
        self._wiggle_handler = None
        # Generic alert banner (encounter pending, token burst, …).
        self._alert_label: NSTextField | None = None
        self._alert_handler: _LevelUpHandler | None = None
        self._alert_timer: NSTimer | None = None
        self._level_up_label: NSTextField | None = None
        self._level_up_handler: _LevelUpHandler | None = None
        self._level_up_timer: NSTimer | None = None
        self._evolution_handler: _EvolutionHandler | None = None
        self._evolution_flash_view: NSView | None = None
        self._evolution_banner: NSTextField | None = None
        self._evolution_running: bool = False
        # Strong refs to in-flight floating-item handlers — without these
        # the NSObject subclasses get garbage-collected before their
        # NSTimers fire and the windows never animate.
        self._floating_item_handlers: list[_FloatingItemHandler] = []
        # The chat window is a thin shell around a long-lived terminal
        # view. Once created, both window and view persist across hide/
        # show cycles — only the outside-click monitor and delegate are
        # torn down on hide. The PTY-backed session lives in
        # ``tokenmon.claude_session`` (module-level singleton) and
        # outlives even this overlay; tmux keeps the actual shell
        # alive across Tokenmon restarts.
        self._chat_window: NSWindow | None = None
        self._terminal_view = None  # claude_session.TerminalWebView | None
        # Strong-ref to the session that ``_terminal_view`` is wired to
        # so we can detect that the local PTY died while the panel was
        # hidden (e.g. user typed ``exit`` inside the shell) and
        # rebuild the WKWebView before showing it again.
        self._chat_session = None  # claude_session.ClaudeSession | None
        self._chat_window_delegate: _ChatWindowDelegate | None = None
        self._chat_outside_monitor = None
        # NSTimer-backed slide-up handler for the chat window opener;
        # GC anchor so the timer keeps firing until the slide completes.
        self._chat_slide_handler = None
        # While the chat panel is on-screen the sprite is pinned to the
        # top-right of that panel so the user has a visual anchor
        # between the companion and its terminal. companion_drv.py
        # checks this flag and skips its own bottom-right-of-focused-
        # window docking while it's True — without that the periodic
        # _tick_dock would yank the sprite back down within ~5 s.
        self._sprite_pinned_to_chat: bool = False
        # Callback the menubar installs so the sprite can be redocked
        # immediately on chat close (rather than waiting up to 5 s for
        # the next _tick_dock pass). Optional — overlay still works
        # without it, the redock just lands on the next periodic tick.
        self._on_chat_hidden = None  # type: ignore[assignment]
        # Ambient idle animator (BOB / HOP / SHAKE / PACE) — lives in
        # tokenmon.companion.chat_idle. Spawned by _dock_sprite_to_chat
        # and torn down by _stop_chat_idle_animator. Strong-ref kept
        # because the NSTimer target needs to outlive this method.
        self._chat_idle_animator = None
        # _ChatSlideHandler instance that animates the sprite from its
        # current position to the chat-panel dock target, started in
        # lockstep with the chat panel's own slide handler so the two
        # windows arrive together. Strong-ref so PyObjC doesn't GC the
        # NSTimer target before its on_complete fires.
        self._sprite_dock_handler = None
        self._sprite_click_monitor = None
        # Strong-ref to the rotating Pokéball badge that signals
        # "claude is actively working" in the companion terminal.
        # The handler owns its own NSPanel + CABasicAnimation; without
        # the strong-ref PyObjC would GC it and the window would close.
        self._claude_badge_handler: _PokeballBadgeHandler | None = None

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
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorTransient
        )
        win.setReleasedWhenClosed_(False)

        img_view = _CompanionImageView.alloc().initWithFrame_overlay_(rect, self)
        img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        img_view.setAnimates_(True)
        # Crisp pixel-art scaling — sprites stay sharp when scaled to 128×128.
        img_view.setWantsLayer_(True)
        if img_view.layer() is not None:
            img_view.layer().setMagnificationFilter_("nearest")
            img_view.layer().setMinificationFilter_("nearest")
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
        self._reposition_claude_badge(animate=False)

    def move_to(self, x: float, y: float, *, animate: bool = True) -> None:
        """Slide the overlay to absolute AppKit coordinates ``(x, y)``.

        Used by companion mode to dock the sprite to the bottom-left of
        the active app's window. ``animate=True`` uses Cocoa's built-in
        ~250 ms slide so the move feels like the Pokémon walked there
        rather than teleported.
        """
        if self._window is None:
            return
        rect = NSMakeRect(float(x), float(y), self._size, self._size)
        try:
            self._window.setFrame_display_animate_(rect, True, bool(animate))
        except Exception:
            log.exception("move_to failed")
        self._reposition_claude_badge(animate=animate)

    def _dock_sprite_to_chat(self, chat_frame) -> None:
        """Slide the companion sprite to the top-right of the chat panel
        and set ``_sprite_pinned_to_chat`` so companion_drv stops
        re-docking it to the focused window's bottom-right.

        The sprite slide is driven by the same NSTimer-based
        ``_ChatSlideHandler`` the chat panel uses, with the same 0.28 s
        duration and ease-out cubic curve. Both handlers are armed
        back-to-back from ``show_chat`` so they tick in lockstep —
        the panel and the sprite arrive at their targets visually
        glued together rather than racing on independent curves.

        Once the slide completes, hands off to the ambient idle
        animator (BOB baseline + frequent HOP/SHAKE/PACE) so the
        sprite *lives* on the chat panel instead of standing still.

        Safe to call repeatedly (e.g. on a reattach); each call
        invalidates any in-flight dock handler / idle animator before
        starting a fresh one.
        """
        if self._window is None:
            return
        target_x, target_y = _sprite_origin_for_chat(chat_frame, self._size)
        self._sprite_pinned_to_chat = True
        # Lift the sprite above the chat panel's window level. Both
        # windows otherwise live at NSFloatingWindowLevel, and the
        # chat panel is most-recently-front-ordered → without this,
        # any BOB dip or HOP that touches the chat's bounds would
        # clip behind the panel. PopUpMenu level sits comfortably
        # above floating and below status icons.
        try:
            self._window.setLevel_(NSPopUpMenuWindowLevel)
        except Exception:
            log.exception("sprite level lift failed")

        # x-range for PACE — sprite must stay above the chat panel's
        # horizontal extent. Match the dock helper's 8 px right inset
        # on both sides so PACE doesn't slide the sprite over the
        # window-shadow gradient.
        chat_x = float(chat_frame.origin.x)
        chat_w = float(chat_frame.size.width)
        x_lo = chat_x + 8
        x_hi = chat_x + chat_w - self._size - 8
        if x_hi < x_lo:
            x_lo = x_hi = target_x  # degenerate-narrow chat; pace no-op

        # Stop any prior idle animator and dock handler first so they
        # don't fight the fresh slide. The slide handler self-cancels
        # on completion; dropping the strong-ref here lets PyObjC GC
        # any leftover from a previous reattach.
        self._stop_chat_idle_animator()
        prior = self._sprite_dock_handler
        if prior is not None:
            try:
                prior.cancel()
            except Exception:
                pass
        self._sprite_dock_handler = None

        try:
            start_frame = self._window.frame()
        except Exception:
            log.exception("sprite frame read failed")
            return
        end_frame = NSMakeRect(target_x, target_y, self._size, self._size)
        try:
            current_alpha = float(self._window.alphaValue())
        except Exception:
            current_alpha = 1.0

        def _on_dock_complete():
            # Keep the badge with the sprite once it's settled. We
            # didn't animate the badge during the slide (it'd lag a
            # frame behind the manual interpolator) — re-anchor now.
            try:
                self._reposition_claude_badge(animate=False)
            except Exception:
                log.exception("badge reposition after dock failed")
            try:
                from tokenmon.companion.chat_idle import ChatIdleAnimator
                anim = ChatIdleAnimator.alloc().initWithWindow_anchor_xRange_(
                    self._window, (target_x, target_y), (x_lo, x_hi),
                )
                self._chat_idle_animator = anim
                anim.start()
            except Exception:
                log.exception("chat idle animator start failed")

        try:
            self._sprite_dock_handler = (
                _ChatSlideHandler.alloc().initWithWindow_startFrame_endFrame_startAlpha_endAlpha_duration_onComplete_(
                    self._window, start_frame, end_frame,
                    current_alpha, current_alpha,
                    0.28, _on_dock_complete,
                )
            )
            self._sprite_dock_handler.start()
        except Exception:
            log.exception("sprite dock slide failed")
            # Fall back to a snap-to-target so the dock still happens
            # and the idle animator picks up from the right anchor.
            try:
                self._window.setFrame_display_(end_frame, True)
            except Exception:
                log.exception("sprite snap-to-target failed")
            _on_dock_complete()

    def _stop_chat_idle_animator(self) -> None:
        anim = self._chat_idle_animator
        if anim is None:
            return
        try:
            anim.stop()
        except Exception:
            log.exception("chat idle animator stop failed")
        self._chat_idle_animator = None
        # Drop the sprite back to the default floating level so it
        # doesn't sit above e.g. system pop-up menus once the chat is
        # gone. Paired with the setLevel_ call in _dock_sprite_to_chat.
        if self._window is not None:
            try:
                self._window.setLevel_(NSFloatingWindowLevel)
            except Exception:
                log.exception("sprite level restore failed")

    def move_to_corner(self, *, animate: bool = True) -> None:
        """Slide back to the configured screen corner. Used when the user
        switches to a non-engagement app."""
        if self._window is None:
            return
        screen = self._window.screen() or NSScreen.mainScreen()
        if screen is None:
            return
        x, y = _position_for(
            self._corner, screen.visibleFrame(), self._size, self._margin,
        )
        self.move_to(x, y, animate=animate)

    def _reposition_claude_badge(self, *, animate: bool) -> None:
        """Pin the claude-active badge to the sprite's current frame.

        Called from each of the three sprite-move sites (`_reposition`,
        `move_to`, `move_to_corner`). No-op when the badge isn't visible.
        """
        handler = self._claude_badge_handler
        if handler is None or self._window is None:
            return
        try:
            handler.reposition_to_sprite_frame(
                self._window.frame(), animate=animate,
            )
        except Exception:
            log.exception("badge follow-sprite failed")

    def update_sprite(
        self,
        sprite_path: Path | None,
        *,
        speed: float = 1.0,
    ) -> None:
        """Set the companion's animated sprite. ``speed`` < 1.0 slows
        the GIF playback (used for low-HP "limp" pacing) by mutating
        each frame's duration on load."""
        if sprite_path is None or not sprite_path.exists():
            return
        self._ensure_window()
        if self._image_view is None:
            return
        try:
            from tokenmon.sprite_speed import load_animated_image
            img = load_animated_image(sprite_path, speed=speed)
        except Exception:
            log.exception("animated load failed; falling back")
            img = NSImage.alloc().initWithContentsOfFile_(str(sprite_path))
        if img is None:
            log.warning("could not load sprite %s into overlay", sprite_path)
            return
        self._image_view.setImage_(img)
        self._image_view.setAnimates_(True)

    def set_sprite_orientation(self, *, front_path: Path,
                               back_path: Path | None,
                               mirrored: bool = False,
                               speed: float = 1.0) -> None:
        """Swap between front and back sprite for the same species and
        optionally horizontally mirror the rendered sprite.

        Caller resolves both paths via ``pokemon.ensure_sprite(...,
        back=True/False)``; back_path may be None when no back sprite
        exists for that Pokémon (post-gen-V species without animated
        back, or download failure). In that case we stay on the front
        sprite — the overlay never errors out.

        ``mirrored=True`` flips the sprite around its vertical centre
        via the image view's CALayer transform. Useful when the Pokémon
        is anchored to one side of the focused window but should still
        appear to face the window content (e.g. anchor on the right →
        mirror back sprite so it faces left toward the content).
        """
        if back_path is not None and back_path.exists():
            target = back_path
        else:
            target = front_path
        if not target.exists():
            log.warning("set_sprite_orientation: missing sprite %s", target)
            return
        self.update_sprite(target, speed=speed)
        self.set_sprite_mirror(mirrored)

    def set_sprite_mirror(self, mirrored: bool) -> None:
        """Final-state mirror flip with zoom = 1.0. Convenience wrapper
        around ``_apply_scale``."""
        self._apply_scale(-1.0 if mirrored else 1.0, 1.0)

    def _apply_scale(self, x: float, y: float) -> None:
        """Set the sprite layer's transform to scale(x, y, 1) around the
        layer's centre.

        NSView's backing layer can default to an off-centre anchor (e.g.
        (0, 0) on older macOS), which would make a sign-flipped scale
        send the visible sprite off the view bounds. We center anchor +
        position before applying the transform.
        """
        self._current_scale = (float(x), float(y))
        if self._image_view is None:
            return
        layer = self._image_view.layer()
        if layer is None:
            return
        try:
            from Quartz import CATransform3DMakeScale
            bounds = layer.bounds()
            cx = float(bounds.size.width) / 2.0
            cy = float(bounds.size.height) / 2.0
            layer.setAnchorPoint_((0.5, 0.5))
            layer.setPosition_((cx, cy))
            layer.setTransform_(
                CATransform3DMakeScale(float(x), float(y), 1.0),
            )
        except Exception:
            log.exception("_apply_scale failed")

    def animate_sprite_turn(self, *, front_path: Path,
                            back_path: Path | None,
                            mirrored: bool = False,
                            zoom: float = 1.0,
                            speed: float = 1.0) -> None:
        """Animated front↔back swap that reads as a 2D turn: the sprite
        squishes horizontally to zero width, the image swaps at the
        edge-on point, and the new sprite expands back out (mirrored
        sign on x decides facing; zoom decides target render size).

        ``zoom > 1.0`` is useful for back sprites — PokeAPI gen-V back
        sprites draw the character smaller within the 96×96 canvas
        because the in-game camera shows them behind the trainer.
        Scaling the layer above 1.0 enlarges the visible character; the
        window's frame clips the overflow so neighbouring app pixels
        aren't affected.

        Cancels any in-flight turn so rapid orientation flips don't
        stack — only the latest target wins.
        """
        if back_path is not None and back_path.exists():
            target = back_path
        else:
            target = front_path
        if not target.exists():
            log.warning("animate_sprite_turn: missing sprite %s", target)
            return
        x_end = (-1.0 if mirrored else 1.0) * float(zoom)
        y_end = float(zoom)
        x_start, y_start = self._current_scale
        handler = _TurnHandler.alloc().initWithOverlay_target_xEnd_yEnd_xStart_yStart_speed_(
            self, target, x_end, y_end, x_start, y_start, float(speed),
        )
        self._turn_handler = handler
        handler.start()

    def _end_turn(self, x_end: float, y_end: float) -> None:
        # Snap to canonical end state to clear any per-frame rounding drift.
        self._apply_scale(x_end, y_end)
        self._turn_handler = None

    def reset_sprite_state(self) -> None:
        """Cancel any in-flight turn animation and reset the layer
        transform to identity (no mirror, no zoom). Called by the
        menubar when the active Pokémon changes so the new species
        doesn't inherit the previous one's engaged-state transform.
        """
        self._turn_handler = None
        self._apply_scale(1.0, 1.0)

    def show(self) -> None:
        self._ensure_window()
        if self._window is None:
            return
        self._window.setIgnoresMouseEvents_(True)
        if self._persistent:
            self._install_sprite_click_monitor()
        self._reposition()
        self._window.orderFrontRegardless()
        self._visible = True

    def hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)
        self._remove_sprite_click_monitor()
        self.hide_chat()
        self.hide_claude_badge()
        self._visible = False

    def set_persistent(self, persistent: bool) -> None:
        """Toggle companion-mode persistence. When True, level-up / evolution
        animations strip their banners but leave the sprite window on screen.
        Caller is responsible for showing/hiding the overlay when the flag
        flips — this method only stores the preference."""
        self._persistent = bool(persistent)
        if self._window is not None:
            self._window.setIgnoresMouseEvents_(True)
        if self._persistent and self._visible:
            self._install_sprite_click_monitor()
        else:
            self._remove_sprite_click_monitor()
        if not self._persistent:
            self.hide_chat()
            self.hide_claude_badge()

    # --- Claude-active badge --------------------------------------------

    @property
    def claude_badge_visible(self) -> bool:
        """True iff the rotating-Pokéball "claude is working" badge is up."""
        return self._claude_badge_handler is not None

    def show_claude_badge(self) -> None:
        """Show the rotating Pokéball badge anchored to the sprite's
        bottom-right corner. Idempotent. No-op when the sprite window
        doesn't exist (companion mode off / overlay hidden)."""
        if self._window is None:
            return
        if self._claude_badge_handler is not None:
            return
        handler = _PokeballBadgeHandler(self)
        handler.start()
        handler.reposition_to_sprite_frame(self._window.frame(), animate=False)
        # Seed the badge alpha to the sprite's current effective value so
        # it doesn't pop in at 1.0 while the cursor is hovering near.
        handler.set_alpha(self._mood_alpha * self._proximity_alpha)
        self._claude_badge_handler = handler

    def hide_claude_badge(self) -> None:
        """Tear down the badge window. Idempotent."""
        handler = self._claude_badge_handler
        if handler is None:
            return
        try:
            handler.stop()
        except Exception:
            log.exception("claude badge stop failed")
        self._claude_badge_handler = None

    # --- Companion chat --------------------------------------------------

    def toggle_chat(self) -> None:
        win = self._chat_window
        if win is not None and win.isVisible():
            self.hide_chat()
            return
        self.show_chat()

    def show_chat(self) -> None:
        """Show the terminal panel that hosts an interactive ``claude`` session.

        First call builds the window + WKWebView and spawns the PTY-backed
        session. Subsequent calls just re-order the existing window front;
        the session and its scrollback persist across hide/show cycles.
        """
        if not self._persistent:
            return

        # Reattach path: the window already exists from a previous open.
        # We only need to reinstall the focus-loss delegate / outside-click
        # monitor and bring the panel back on screen — the WKWebView still
        # holds xterm.js's full screen state, and the underlying
        # ClaudeSession kept reading PTY output the whole time.
        #
        # …unless the underlying session died while the panel was
        # hidden (most common cause: user typed ``/quit`` inside
        # claude before closing the window). In that case the
        # WKWebView is bound to a corpse — tear it down here so the
        # block below can rebuild from scratch with a fresh
        # ClaudeSession at whatever cwd the resolver picks now.
        if self._chat_window is not None:
            session = self._chat_session
            if session is None or not session.is_alive():
                log.info("chat session died while hidden — rebuilding window")
                self._tear_down_chat_window()
            else:
                self._reattach_chat_window()
                return

        # Pick the screen the user is currently looking at — the chat
        # panel is summoned via a global hotkey from arbitrary apps, so
        # "where the mouse is" is a much better answer than "where the
        # sprite happens to live". Falls back to the sprite's screen,
        # then mainScreen if all else fails.
        screen = _screen_under_mouse()
        if screen is None and self._window is not None:
            screen = self._window.screen()
        if screen is None:
            screen = NSScreen.mainScreen()
        if screen is None:
            return
        final_frame = _chat_frame_for_screen(screen.visibleFrame())
        start_frame = _chat_start_frame(final_frame)

        # Spawn (or reuse) the long-lived terminal session. If no shell
        # can be spawned we surface a graceful banner instead of
        # opening a doomed window. Imports done before the try/except
        # so the ``SessionUnavailable`` reference in the except clause
        # is bound even if the import itself were to fail (it
        # shouldn't, but be defensive — the chat panel is the user's
        # recovery path).
        from tokenmon import claude_session as _claude_session
        try:
            session = _claude_session.get_session()
        except _claude_session.SessionUnavailable as exc:
            log.warning("companion chat: cannot start terminal — %s", exc)
            self._show_chat_unavailable_banner(str(exc))
            return
        except Exception:
            log.exception("companion chat: terminal session spawn failed")
            self._show_chat_unavailable_banner(
                "Couldn't start the companion terminal — see logs.",
            )
            return

        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskFullSizeContentView
            | NSWindowStyleMaskNonactivatingPanel
        )
        win = _ChatWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            start_frame, style, NSBackingStoreBuffered, False,
        )
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setHasShadow_(True)
        win.setIgnoresMouseEvents_(False)
        win.setMovable_(False)
        win.setTitleVisibility_(NSWindowTitleHidden)
        win.setTitlebarAppearsTransparent_(True)
        win.setLevel_(NSFloatingWindowLevel)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorTransient
        )
        win.setReleasedWhenClosed_(False)
        win.setAlphaValue_(0.0)

        w = float(final_frame.size.width)
        h = float(final_frame.size.height)
        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        root.setWantsLayer_(True)
        blur = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        blur.setMaterial_(NSVisualEffectMaterialHUDWindow)
        blur.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        blur.setState_(NSVisualEffectStateActive)
        blur.setWantsLayer_(True)
        layer = blur.layer()
        if layer is not None:
            layer.setCornerRadius_(10)
            layer.setMasksToBounds_(True)
        root.addSubview_(blur)

        border = _ChatCardView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        border.setWantsLayer_(True)
        root.addSubview_(border)

        terminal_frame = NSMakeRect(12, 12, w - 24, h - 24)
        from tokenmon.claude_session.terminal_view import TerminalWebView
        terminal = TerminalWebView(session, terminal_frame)
        self._terminal_view = terminal
        self._chat_session = session
        # autoresize: width + height track the panel
        try:
            terminal.view.setAutoresizingMask_(2 | 16)  # Width | Height
        except Exception:
            pass
        root.addSubview_(terminal.view)

        win.setContentView_(root)
        self._chat_window = win
        delegate = _ChatWindowDelegate.alloc().initWithOverlay_(self)
        self._chat_window_delegate = delegate
        win.setDelegate_(delegate)
        self._install_chat_outside_monitor()
        win.makeKeyAndOrderFront_(None)
        try:
            win.makeKeyWindow()
        except Exception:
            pass
        # Manual NSTimer-driven slide: macOS' built-in window animations
        # refused to move this floating panel, so we interpolate the frame
        # ourselves at ~60 Hz. The handler is anchored on `self` to outlive
        # this method.
        try:
            self._chat_slide_handler = (
                _ChatSlideHandler.alloc().initWithWindow_startFrame_endFrame_startAlpha_endAlpha_duration_onComplete_(
                    win, start_frame, final_frame, 0.0, 1.0, 0.28, None,
                )
            )
            self._chat_slide_handler.start()
        except Exception:
            log.exception("chat slide animation failed")
            win.setFrame_display_(final_frame, True)
            win.setAlphaValue_(1.0)
        # Dock the companion sprite to the top-right of the chat panel
        # so the two move together — visually links the Pokémon to the
        # terminal it just summoned. companion_drv sees the pinned
        # flag and stops re-docking while the chat is open.
        try:
            self._dock_sprite_to_chat(final_frame)
        except Exception:
            log.exception("dock sprite to chat failed")
        # Hand keyboard focus to the WKWebView so xterm.js receives
        # keystrokes immediately — without this the user has to click
        # inside the terminal before they can type.
        try:
            win.makeFirstResponder_(terminal.view)
        except Exception:
            log.exception("chat first-responder failed")

    def _reattach_chat_window(self) -> None:
        """Re-show an existing chat window (the WKWebView + PTY are already
        alive). Only the AppKit-side observers were torn down on hide."""
        win = self._chat_window
        if win is None:
            return

        # Reinstall delegate (we set it to None on hide so a stray
        # resignKey notification can't fire while the panel is hidden).
        delegate = _ChatWindowDelegate.alloc().initWithOverlay_(self)
        self._chat_window_delegate = delegate
        try:
            win.setDelegate_(delegate)
        except Exception:
            log.exception("chat delegate reinstall failed")

        self._install_chat_outside_monitor()

        # Recompute the final frame (display config / screen may have
        # changed since last open) and park the window at the offscreen
        # start position before ordering it front, so the slide-up
        # handler has somewhere to slide from. We prefer the screen the
        # mouse is on so reattaches follow the user across displays;
        # only fall back to the window's last-known screen if the
        # mouse-lookup somehow returns nothing.
        screen = _screen_under_mouse() or win.screen() or NSScreen.mainScreen()
        if screen is not None:
            final_frame = _chat_frame_for_screen(screen.visibleFrame())
            start_frame = _chat_start_frame(final_frame)
            try:
                win.setFrame_display_(start_frame, False)
                win.setAlphaValue_(0.0)
            except Exception:
                log.exception("chat reattach start-frame failed")
        else:
            final_frame = None

        try:
            win.makeKeyAndOrderFront_(None)
            win.makeKeyWindow()
        except Exception:
            log.exception("chat re-order-front failed")

        if final_frame is not None:
            try:
                self._chat_slide_handler = (
                    _ChatSlideHandler.alloc().initWithWindow_startFrame_endFrame_startAlpha_endAlpha_duration_onComplete_(
                        win, start_frame, final_frame, 0.0, 1.0, 0.28, None,
                    )
                )
                self._chat_slide_handler.start()
            except Exception:
                log.exception("chat reattach slide failed")
                win.setFrame_display_(final_frame, True)
                win.setAlphaValue_(1.0)
            try:
                self._dock_sprite_to_chat(final_frame)
            except Exception:
                log.exception("dock sprite to chat on reattach failed")

        if self._terminal_view is not None:
            try:
                win.makeFirstResponder_(self._terminal_view.view)
            except Exception:
                log.exception("chat first-responder failed")

    def _show_chat_unavailable_banner(self, message: str) -> None:
        """Surface a one-shot AppKit alert when the terminal session can't
        start. Called instead of opening the chat window so the user gets
        an explanation instead of a silently empty panel."""
        try:
            from AppKit import NSAlert  # type: ignore
            alert = NSAlert.alloc().init()
            alert.setMessageText_("Companion chat unavailable")
            alert.setInformativeText_(message)
            alert.runModal()
        except Exception:
            # Fallback: log only. Better than crashing the menubar.
            log.error("chat unavailable: %s", message)

    def hide_chat(self) -> None:
        """Order the chat panel out without releasing it. The local PTY
        and the WKWebView's xterm.js state both keep running so the next
        ``show_chat()`` reattaches to a live terminal instead of
        spawning a fresh one.

        The local PTY only dies on menubar quit (see
        ``tokenmon.menubar._main`` for the ``atexit`` hook). The
        underlying tmux session keeps running across menubar restarts
        — that's what makes the terminal persist."""
        win = self._chat_window
        if win is None:
            return
        if not win.isVisible():
            return
        # Unpin the sprite and let the menubar redock it immediately to
        # the focused window's bottom-right. Without this the periodic
        # _tick_dock would do the redock with up to ~5 s of lag —
        # firing the callback gives a smooth hand-off where the chat
        # slides down and the sprite slides back at the same time.
        # Stop the idle animator BEFORE clearing the pin so its final
        # snap-to-anchor doesn't race the redock callback.
        self._stop_chat_idle_animator()
        was_pinned = self._sprite_pinned_to_chat
        self._sprite_pinned_to_chat = False
        if was_pinned and self._on_chat_hidden is not None:
            try:
                self._on_chat_hidden()
            except Exception:
                log.exception("chat-hidden callback raised")
        self._remove_chat_outside_monitor()
        try:
            # Drop the delegate so a stale ``windowDidResignKey:`` can't
            # call hide_chat() recursively while the panel is already
            # hidden.
            win.setDelegate_(None)
        except Exception:
            log.exception("chat delegate drop failed")
        self._chat_window_delegate = None

        # Slide-down: mirror the slide-up by interpolating from the current
        # frame down to a frame one panel-height below it, while fading
        # alpha to 0. orderOut runs in the on_complete so the window
        # actually disappears once the animation finishes.
        try:
            current_frame = win.frame()
            below_frame = NSMakeRect(
                float(current_frame.origin.x),
                float(current_frame.origin.y) - float(current_frame.size.height),
                float(current_frame.size.width),
                float(current_frame.size.height),
            )

            def _finish_hide(w=win):
                try:
                    w.orderOut_(None)
                except Exception:
                    log.exception("chat orderOut failed")

            self._chat_slide_handler = (
                _ChatSlideHandler.alloc().initWithWindow_startFrame_endFrame_startAlpha_endAlpha_duration_onComplete_(
                    win, current_frame, below_frame,
                    float(win.alphaValue()), 0.0, 0.22, _finish_hide,
                )
            )
            self._chat_slide_handler.start()
        except Exception:
            log.exception("chat slide-down failed")
            try:
                win.orderOut_(None)
            except Exception:
                log.exception("chat orderOut fallback failed")
        # NB: we deliberately do NOT call win.close() or null out
        # self._chat_window / self._terminal_view — they're meant to
        # survive across hide/show. Cleanup happens at app quit via
        # claude_session.shutdown() and PyObjC's normal teardown.

    def _tear_down_chat_window(self) -> None:
        """Fully release the chat window + WKWebView + terminal-view bindings
        so the next ``show_chat()`` rebuilds from scratch. Used when the
        local PTY has died while the panel was hidden (the WKWebView is
        still bound to the corpse and would no-op on keystrokes).
        Idempotent."""
        self._stop_chat_idle_animator()
        self._sprite_pinned_to_chat = False
        self._remove_chat_outside_monitor()
        win = self._chat_window
        if win is not None:
            try:
                win.setDelegate_(None)
            except Exception:
                pass
            try:
                win.orderOut_(None)
                win.close()
            except Exception:
                log.exception("chat full teardown failed")
        # Drop bindings so the next show_chat takes the cold-start path.
        if self._terminal_view is not None:
            try:
                self._terminal_view.detach()
            except Exception:
                log.exception("terminal_view.detach() failed")
        self._chat_window = None
        self._chat_window_delegate = None
        self._terminal_view = None
        self._chat_session = None

    def _install_chat_outside_monitor(self) -> None:
        self._remove_chat_outside_monitor()
        mask = (
            NSEventMaskLeftMouseDown
            | NSEventMaskRightMouseDown
            | NSEventMaskOtherMouseDown
        )

        def _handler(_event):
            self.hide_chat()

        try:
            self._chat_outside_monitor = (
                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    mask, _handler,
                )
            )
        except Exception:
            log.exception("chat outside-click monitor install failed")
            self._chat_outside_monitor = None

    def _remove_chat_outside_monitor(self) -> None:
        if self._chat_outside_monitor is None:
            return
        try:
            NSEvent.removeMonitor_(self._chat_outside_monitor)
        except Exception:
            log.exception("chat outside-click monitor remove failed")
        self._chat_outside_monitor = None

    def _install_sprite_click_monitor(self) -> None:
        if self._sprite_click_monitor is not None:
            return

        def _handler(event):
            if not self._persistent or not self._visible or self._window is None:
                return
            try:
                if event.clickCount() < 2:
                    return
                loc = NSEvent.mouseLocation()
                frame = self._window.frame()
                x = float(loc.x)
                y = float(loc.y)
                left = float(frame.origin.x)
                bottom = float(frame.origin.y)
                right = left + float(frame.size.width)
                top = bottom + float(frame.size.height)
                if left <= x <= right and bottom <= y <= top:
                    self.toggle_chat()
            except Exception:
                log.exception("sprite double-click monitor failed")

        try:
            self._sprite_click_monitor = (
                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    NSEventMaskLeftMouseDown, _handler,
                )
            )
        except Exception:
            log.exception("sprite click monitor install failed")
            self._sprite_click_monitor = None

    def _remove_sprite_click_monitor(self) -> None:
        if self._sprite_click_monitor is None:
            return
        try:
            NSEvent.removeMonitor_(self._sprite_click_monitor)
        except Exception:
            log.exception("sprite click monitor remove failed")
        self._sprite_click_monitor = None

    def set_mood_alpha(self, multiplier: float) -> None:
        """Apply a Phase-5 mood multiplier (e.g. night dimming) on top of
        whatever idle-state damping is currently active. Clamped to a
        sane range so a typo can't make the overlay invisible."""
        m = float(multiplier)
        if m < 0.5:
            m = 0.5
        if m > 1.0:
            m = 1.0
        if abs(m - self._mood_alpha) < 1e-3:
            return
        self._mood_alpha = m
        self._apply_alpha()

    def set_proximity_alpha(self, multiplier: float) -> None:
        """Cursor-proximity fade factor. 1.0 = cursor far away (full alpha
        per the other factors), 0.1ish = cursor on the sprite (mostly
        transparent so the sprite gets out of the way)."""
        m = float(multiplier)
        if m < 0.0:
            m = 0.0
        if m > 1.0:
            m = 1.0
        if abs(m - self._proximity_alpha) < 5e-3:
            return  # too small to matter — skip the redraw
        self._proximity_alpha = m
        self._apply_alpha()

    def _apply_alpha(self) -> None:
        if self._window is None:
            return
        alpha = self._mood_alpha * self._proximity_alpha
        # Idempotency — setAlphaValue_ triggers a window redisplay even
        # when the value is unchanged, so the 20 Hz proximity tick would
        # otherwise spam the compositor while the cursor sits still.
        if abs(alpha - self._last_applied_alpha) < 5e-3:
            return
        self._last_applied_alpha = alpha
        try:
            self._window.setAlphaValue_(alpha)
        except Exception:
            log.exception("setAlphaValue_ failed")
        # Mirror the alpha onto the claude-active badge so it fades with
        # the sprite on cursor proximity (and dims with night-mode mood).
        # The badge handler owns a separate NSWindow so we have to push
        # the value explicitly — it doesn't inherit window-level alpha
        # from the sprite.
        handler = self._claude_badge_handler
        if handler is not None:
            try:
                handler.set_alpha(alpha)
            except Exception:
                log.exception("badge alpha sync failed")

    # --- Generic alert flash ---------------------------------------------

    def flash_alert(self, text: str, duration_s: float = 4.0) -> None:
        """Show a small text/emoji banner above the sprite for ``duration_s``
        seconds, then auto-clear. Reuses the level-up banner geometry but
        leaves the underlying sprite untouched. If an alert is already in
        flight it gets replaced."""
        # Tear down any in-flight alert first.
        if self._alert_timer is not None:
            try:
                self._alert_timer.invalidate()
            except Exception:
                pass
            self._alert_timer = None
        if self._alert_label is not None:
            try:
                self._alert_label.removeFromSuperview()
            except Exception:
                pass
            self._alert_label = None
        if self._window is None:
            return
        size = self._size
        banner_h = 24  # smaller than LEVEL_UP_BANNER_HEIGHT — non-disruptive
        # Position the banner just above the sprite without resizing the
        # window — overlap with the sprite a few px is fine for a one-line
        # flash and keeps the layout simple.
        label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, size - banner_h, size, banner_h)
        )
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setAlignment_(NSTextAlignmentCenter)
        label.setFont_(NSFont.boldSystemFontOfSize_(13))
        label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.92, 0.4, 1.0)
        )
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(NSColor.blackColor())
        shadow.setShadowOffset_(NSMakeSize(0, -1))
        shadow.setShadowBlurRadius_(3)
        label.setShadow_(shadow)
        self._window.contentView().addSubview_(label)
        self._alert_label = label

        handler = _AlertHandler.alloc().initWithOverlay_(self)
        self._alert_handler = handler
        self._alert_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                max(0.5, duration_s), handler, b"fire:", None, False
            )
        )

    def _end_alert(self) -> None:
        if self._alert_timer is not None:
            try:
                self._alert_timer.invalidate()
            except Exception:
                pass
            self._alert_timer = None
        if self._alert_label is not None:
            try:
                self._alert_label.removeFromSuperview()
            except Exception:
                pass
            self._alert_label = None
        self._alert_handler = None

    # --- Floating-item drift animation -----------------------------------

    def show_floating_items(self, drops: dict[str, int]) -> None:
        """For each new drop, spawn a small click-through window above the
        nominal Pokemon-overlay position that drifts upward and fades out.

        Works even when the overlay window itself isn't on screen — the
        anchor is computed from the configured corner so floaters always
        appear in the spot the user mentally associates with the overlay.

        ``drops`` maps item_key → count. Multi-count items emit one
        floater per unit (capped at MAX_FLOATERS_PER_ITEM so a 50-pokeball
        haul doesn't spawn 50 windows).
        """
        if not drops:
            return
        # Lazy import — overlay is a leaf module that pokemon imports;
        # items_remote depends on storage, which is fine here.
        from tokenmon import items as items_registry, items_remote

        MAX_FLOATERS_PER_ITEM = 5
        MAX_FLOATERS_TOTAL = 8
        STAGGER_SEC = 0.18
        # Lead time so the items start floating AFTER the wiggle finishes.
        # Caller (menubar._tick_pending_drops) wiggles first, then calls
        # this — staggering the floater starts by WIGGLE_LEAD_S means
        # they appear to fly out of the wiggling sprite.
        WIGGLE_LEAD_S = 0.30

        # Anchor: in companion mode use the current docked window frame
        # so floaters spawn directly from the sprite. In default mode
        # fall back to the configured corner so floaters appear at the
        # overlay's nominal position even when the window isn't shown.
        if self._persistent and self._window is not None:
            frame = self._window.frame()
            anchor_x = float(frame.origin.x)
            anchor_y = float(frame.origin.y)
        else:
            screen = None
            if self._window is not None:
                screen = self._window.screen()
            if screen is None:
                screen = NSScreen.mainScreen()
            if screen is None:
                return
            anchor_x, anchor_y = _position_for(
                self._corner, screen.visibleFrame(), self._size, self._margin,
            )
        base_x = anchor_x + self._size / 2 - _FloatingItemHandler.SIZE / 2
        base_y = anchor_y + self._size + 4

        # Expand drops into a flat queue, capped per-item and overall.
        queue: list[str] = []
        for key, count in drops.items():
            if count <= 0:
                continue
            for _ in range(min(int(count), MAX_FLOATERS_PER_ITEM)):
                queue.append(key)
        if len(queue) > MAX_FLOATERS_TOTAL:
            queue = queue[:MAX_FLOATERS_TOTAL]

        for i, key in enumerate(queue):
            # Item KEY (e.g. "pokeball") and PokeAPI sprite NAME (e.g.
            # "poke-ball") are two different fields on the registry — use
            # the dedicated helper that already understands this mapping.
            item = items_registry.get(key)
            sprite = (
                items_remote.get_item_image(item) if item is not None
                else items_remote.get_sprite_by_name(key)
            )
            if sprite is None:
                continue
            # Slight horizontal jitter so a stack of identical items
            # doesn't look like a single window animating.
            jitter = (i % 3 - 1) * 10
            x = base_x + jitter
            y = base_y
            # Lead delay (so items emerge after the wiggle) only when
            # docked — corner-mode keeps the original instant stagger.
            lead = WIGGLE_LEAD_S if self._persistent else 0.0
            handler = _FloatingItemHandler.alloc().initWithSprite_x_y_delay_(
                sprite, x, y, lead + i * STAGGER_SEC,
            )
            self._floating_item_handlers.append(handler)
            handler.start()

        # Trim the strong-ref list — keep only the last ~30 handlers so
        # we don't accumulate forever. Closed handlers' windows are
        # already None'd out so they're harmless to keep around briefly.
        if len(self._floating_item_handlers) > 30:
            self._floating_item_handlers = self._floating_item_handlers[-30:]

    @property
    def visible(self) -> bool:
        return self._visible

    def set_corner(self, corner: str) -> None:
        self._corner = corner
        self._reposition()

    @property
    def evolution_running(self) -> bool:
        return self._evolution_running

    @property
    def wiggling(self) -> bool:
        return self._wiggling

    def wiggle(self, *, amplitude_px: int = WIGGLE_AMPLITUDE_PX,
               frames: int = WIGGLE_FRAMES) -> None:
        """Brief horizontal oscillation of the sprite window.

        Used to announce an event (e.g. items about to drop) without
        moving the sprite away from its docked position. Cancels any
        in-flight wiggle so back-to-back calls don't stack. No-op when
        the window isn't built yet.
        """
        if self._window is None:
            return
        # Cancel a previous wiggle by overwriting the handler ref — the
        # old handler's fire_ early-exits when it sees a stranger.
        # Origin source: while the chat-idle animator owns the window
        # we'd otherwise latch onto a mid-BOB / mid-PACE position and
        # snap there at the end of the wiggle. Read the animator's
        # anchor instead so wiggle ends at the *stable* dock position.
        # Also pause the animator so its 20 Hz ticks don't fight the
        # wiggle's per-frame setFrameOrigin.
        if self._chat_idle_animator is not None:
            ox, oy = self._chat_idle_animator.anchor()
            self._stop_chat_idle_animator()
        else:
            current = self._window.frame()
            ox = float(current.origin.x)
            oy = float(current.origin.y)
        handler = _WiggleHandler.alloc().initWithOverlay_originX_originY_amplitude_frames_(
            self, ox, oy, amplitude_px, frames,
        )
        self._wiggle_handler = handler
        self._wiggling = True
        handler.start()

    # --- Evolution animation ----------------------------------------------

    def show_evolution(self, old_dex_id: int, new_dex_id: int) -> None:
        """Run a Gen-3-style evolution animation and end with the new sprite +
        banner, then hide the overlay."""
        # Lazy import to avoid module-load circular ref.
        from tokenmon import pokemon as _pokemon

        # Cancel any in-flight level-up so we don't leak its UI.
        if self._level_up_timer is not None:
            self._level_up_timer.invalidate()
            self._level_up_timer = None
        if self._level_up_label is not None:
            self._level_up_label.removeFromSuperview()
            self._level_up_label = None
        self._level_up_handler = None

        # Cancel a prior evolution if one is running.
        self._end_evolution()

        old_path = _pokemon.ensure_sprite(old_dex_id)
        new_path = _pokemon.ensure_sprite(new_dex_id)
        if old_path is None or new_path is None:
            return
        old_img = NSImage.alloc().initWithContentsOfFile_(str(old_path))
        new_img = NSImage.alloc().initWithContentsOfFile_(str(new_path))
        if old_img is None or new_img is None:
            return
        sil_old = _silhouette_image(old_img, NSColor.whiteColor())
        sil_new = _silhouette_image(new_img, NSColor.whiteColor())
        # Re-load the reveal sprite from disk so the GIF's animation state is
        # untouched by the drawing operations we did to build the silhouettes.
        reveal_img = NSImage.alloc().initWithContentsOfFile_(str(new_path))
        if reveal_img is None:
            reveal_img = new_img

        self._ensure_window()
        if self._window is None:
            return
        if not self._visible:
            self._reposition()
            self._window.orderFrontRegardless()
            self._visible = True

        self._evolution_running = True
        handler = _EvolutionHandler.alloc().initWithOverlay_silOld_silNew_newImage_newName_(
            self, sil_old, sil_new, reveal_img, _pokemon.name_of(new_dex_id),
        )
        self._evolution_handler = handler
        handler.start()

    def _set_evolution_image(self, img: NSImage, *, animated: bool) -> None:
        if self._image_view is None:
            return
        self._image_view.setAnimates_(animated)
        self._image_view.setImage_(img)

    def _show_flash(self) -> None:
        """Cover the sprite area with an opaque white view."""
        if self._window is None or self._image_view is None:
            return
        if self._evolution_flash_view is not None:
            return
        bounds = self._image_view.frame()
        flash = NSView.alloc().initWithFrame_(bounds)
        flash.setWantsLayer_(True)
        # NSColor's CGColor is a method in pyobjc, not a property.
        flash.layer().setBackgroundColor_(NSColor.whiteColor().CGColor())
        self._window.contentView().addSubview_(flash)
        self._evolution_flash_view = flash

    def _evolution_reveal(self, new_img: NSImage, new_name: str) -> None:
        # Drop the flash overlay.
        if self._evolution_flash_view is not None:
            self._evolution_flash_view.removeFromSuperview()
            self._evolution_flash_view = None
        # Reveal the new sprite, animated. Set the image first, THEN turn on
        # animation, so the NSImageView picks up the new representations before
        # starting the timer.
        if self._image_view is not None:
            self._image_view.setImage_(new_img)
            self._image_view.setAnimates_(True)
        # Grow window upward and add a banner like the level-up one.
        if self._window is None:
            return
        size = self._size
        banner_h = LEVEL_UP_BANNER_HEIGHT
        # In companion mode the sprite is docked to the focused window —
        # don't teleport it to the screen corner; grow upward in place.
        # Default (event-only) mode keeps the corner anchor.
        if self._persistent:
            current = self._window.frame()
            x = float(current.origin.x)
            y = float(current.origin.y)
            self._window.setFrame_display_animate_(
                NSMakeRect(x, y, size, size + banner_h), True, True,
            )
        else:
            screen = self._window.screen() or NSScreen.mainScreen()
            if screen is not None:
                x, y = _position_for(self._corner, screen.visibleFrame(), size, self._margin)
                self._window.setFrame_display_animate_(
                    NSMakeRect(x, y, size, size + banner_h), True, True
                )
        content = self._window.contentView()
        content.setFrame_(NSMakeRect(0, 0, size, size + banner_h))
        if self._image_view is not None:
            self._image_view.setFrame_(NSMakeRect(0, 0, size, size))

        label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, size, size, banner_h))
        label.setStringValue_(f"{new_name} entwickelt!")
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setAlignment_(NSTextAlignmentCenter)
        label.setFont_(NSFont.boldSystemFontOfSize_(13))
        label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.85, 0.0, 1.0)
        )
        shadow = NSShadow.alloc().init()
        shadow.setShadowColor_(NSColor.blackColor())
        shadow.setShadowOffset_(NSMakeSize(0, -1))
        shadow.setShadowBlurRadius_(3)
        label.setShadow_(shadow)
        content.addSubview_(label)
        self._evolution_banner = label

    def _end_evolution(self) -> None:
        self._evolution_running = False
        self._evolution_handler = None
        if self._evolution_flash_view is not None:
            self._evolution_flash_view.removeFromSuperview()
            self._evolution_flash_view = None
        if self._evolution_banner is not None:
            self._evolution_banner.removeFromSuperview()
            self._evolution_banner = None
        if self._image_view is not None:
            self._image_view.setAnimates_(True)
        if self._window is None:
            return
        # Shrink window back to sprite-only. In companion mode keep the
        # current origin (companion docking owns the position); in default
        # mode return to the configured screen corner.
        if self._persistent:
            current = self._window.frame()
            x = float(current.origin.x)
            y = float(current.origin.y)
            self._window.setFrame_display_animate_(
                NSMakeRect(x, y, self._size, self._size), True, False,
            )
        else:
            screen = self._window.screen() or NSScreen.mainScreen()
            if screen is not None:
                x, y = _position_for(
                    self._corner, screen.visibleFrame(), self._size, self._margin,
                )
                self._window.setFrame_display_animate_(
                    NSMakeRect(x, y, self._size, self._size), True, False,
                )
        content = self._window.contentView()
        content.setFrame_(NSMakeRect(0, 0, self._size, self._size))
        if self._image_view is not None:
            self._image_view.setFrame_(NSMakeRect(0, 0, self._size, self._size))
        if not self._persistent:
            self.hide()
        else:
            # Companion mode: clear any inherited mirror/zoom transform
            # so the evolved sprite renders un-flipped. The next
            # _tick_orientation in the menubar will re-apply the correct
            # back/front + mirror based on input recency.
            self.reset_sprite_state()

    # --- Level-up animation -----------------------------------------------

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
        # In companion mode the sprite is docked to the focused window —
        # don't teleport to the configured screen corner; grow upward at
        # the current origin so the banner appears above the docked
        # sprite. Default mode keeps the corner anchor.
        if self._persistent:
            current = win.frame()
            x = float(current.origin.x)
            y = float(current.origin.y)
        else:
            screen = win.screen() or NSScreen.mainScreen()
            if screen is None:
                return
            x, y = _position_for(self._corner, screen.visibleFrame(), size, self._margin)
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
        # Shrink window back to sprite-only. In companion mode keep the
        # current origin (companion docking owns the position); in
        # default (event-only) mode return to the configured corner and
        # hide afterwards.
        if self._persistent:
            current = self._window.frame()
            x = float(current.origin.x)
            y = float(current.origin.y)
            self._window.setFrame_display_animate_(
                NSMakeRect(x, y, self._size, self._size), True, False,
            )
        else:
            screen = self._window.screen() or NSScreen.mainScreen()
            if screen is not None:
                x, y = _position_for(
                    self._corner, screen.visibleFrame(), self._size, self._margin,
                )
                self._window.setFrame_display_animate_(
                    NSMakeRect(x, y, self._size, self._size), True, False,
                )
        content = self._window.contentView()
        content.setFrame_(NSMakeRect(0, 0, self._size, self._size))
        if self._image_view is not None:
            self._image_view.setFrame_(NSMakeRect(0, 0, self._size, self._size))
        if not self._persistent:
            self.hide()

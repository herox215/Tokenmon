"""Desktop overlay showing the current Pokemon sprite.

A borderless, transparent NSWindow that floats above other windows on every
Space. In event-only mode it ignores mouse and keyboard events; in companion
mode the sprite accepts a double-click to open the lightweight session chat.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSCompositingOperationSourceAtop,
    NSCompositingOperationSourceOver,
    NSFloatingWindowLevel,
    NSFont,
    NSGraphicsContext,
    NSImage,
    NSImageInterpolationNone,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSLineBreakByWordWrapping,
    NSPanel,
    NSRectFillUsingOperation,
    NSScreen,
    NSScrollView,
    NSShadow,
    NSTextView,
    NSWindowStyleMaskFullSizeContentView,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
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
from Foundation import NSMakeRect, NSMakeSize, NSObject
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
CHAT_MORPH_SIZE = 36

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


def _chat_start_frame(sprite_frame, final_frame):
    """Tiny origin frame for the open animation, centred on the sprite."""
    cx = float(sprite_frame.origin.x) + float(sprite_frame.size.width) / 2.0
    cy = float(sprite_frame.origin.y) + float(sprite_frame.size.height) / 2.0
    size = min(
        CHAT_MORPH_SIZE,
        float(final_frame.size.width),
        float(final_frame.size.height),
    )
    return NSMakeRect(cx - size / 2.0, cy - size / 2.0, size, size)


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


class _ChatWindowDelegate(NSObject):
    def initWithOverlay_(self, overlay):  # noqa: N802
        self = objc.super(_ChatWindowDelegate, self).init()
        if self is None:
            return None
        self._overlay = overlay
        return self

    def windowDidResignKey_(self, _notification):  # noqa: N802
        self._overlay.hide_chat()


class _ChatInputHandler(NSObject):
    """NSTextField target/delegate for the mock session input."""

    def initWithOverlay_(self, overlay):  # noqa: N802
        self = objc.super(_ChatInputHandler, self).init()
        if self is None:
            return None
        self._overlay = overlay
        return self

    def send_(self, sender):  # noqa: N802
        text = sender.stringValue().strip()
        if not text:
            return
        sender.setStringValue_("")
        self._overlay._append_chat_message(text)

    def control_textView_doCommandBySelector_(  # noqa: N802
        self, _control, _text_view, command,
    ):
        sel = str(command) if command is not None else ""
        if sel in ("cancelOperation:", "cancel:"):
            self._overlay.hide_chat()
            return True
        return False


class _ChatCloseHandler(NSObject):
    def initWithOverlay_(self, overlay):  # noqa: N802
        self = objc.super(_ChatCloseHandler, self).init()
        if self is None:
            return None
        self._overlay = overlay
        return self

    def close_(self, _sender):  # noqa: N802
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
    receive a double-click so the user can open the mock session chat.
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
        self._chat_window: NSWindow | None = None
        # NSTextView (not NSTextField) so long conversations scroll
        # instead of clipping. Lives inside _chat_transcript_scroll.
        self._chat_transcript = None
        self._chat_transcript_scroll = None
        self._chat_input: NSTextField | None = None
        # Each entry is ``(role, text)`` where ``role`` is one of:
        #   "user"      — message the user typed
        #   "companion" — Claude's reply rendered in the Pokémon's voice
        #   "thinking"  — placeholder shown while a reply is in flight; gets
        #                 popped and replaced when the worker finishes
        #   "error"     — short failure note (claude missing, timeout, …)
        # Bounded slice keeps the transcript readable; ~12 entries gives
        # roughly 6 round-trips before the oldest scrolls off.
        self._chat_messages: list[tuple[str, str]] = []
        # Tracks whether a Claude request is currently in flight so the
        # input field is disabled and follow-up sends are dropped (instead
        # of stacking subprocesses for every Enter press).
        self._chat_pending: bool = False
        self._chat_input_handler: _ChatInputHandler | None = None
        self._chat_close_handler: _ChatCloseHandler | None = None
        self._chat_window_delegate: _ChatWindowDelegate | None = None
        self._chat_outside_monitor = None
        self._sprite_click_monitor = None
        # Window-context snapshot for the active chat session — captured
        # right before the chat panel is ordered front. Lazily-built
        # resolver so importing overlay.py on a system without PyObjC
        # (tests) doesn't pull in AppKit-only context providers.
        self._chat_context: "ContextSnapshot | None" = None
        self._context_resolver = None

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

    # --- Mock companion chat ---------------------------------------------

    def toggle_chat(self) -> None:
        if self._chat_window is not None:
            self.hide_chat()
            return
        self.show_chat()

    def _capture_active_window_context(self):
        """Snapshot whatever window the user was looking at, *before* we
        steal focus. Returns ``None`` on any failure — capture is
        best-effort and must never block the chat from opening."""
        try:
            from tokenmon.companion.active_app import current_bundle_id
            from tokenmon.companion.window_geom import frontmost_pid
            from tokenmon.context import build_default_resolver
            from tokenmon.context.providers.macos_screenshot import (
                has_screen_recording_permission,
                request_screen_recording_permission,
            )

            bundle_id = current_bundle_id()
            pid = frontmost_pid()
            if not bundle_id or pid is None:
                return None
            # Skip ourselves — capturing Tokenmon's own window is useless
            # noise. The bundle id when running under uv is typically
            # the Python launcher; either way it's not what the user
            # wants to talk about.
            if bundle_id.startswith(("org.python", "com.apple.python")):
                return None

            # First-time trigger of the system permission dialog.
            # macOS only shows it once per install; afterwards the
            # request call is a silent no-op and the user has to flip
            # the toggle in System Settings manually.
            if not has_screen_recording_permission():
                request_screen_recording_permission()

            if self._context_resolver is None:
                self._context_resolver = build_default_resolver()
            return self._context_resolver.resolve(bundle_id, pid)
        except Exception:
            log.exception("active-window context capture failed")
            return None

    def show_chat(self) -> None:
        """Open the bottom-centred mock chat for the active companion."""
        if not self._persistent:
            return
        if self._chat_window is not None:
            self._chat_window.makeKeyAndOrderFront_(None)
            if self._chat_input is not None:
                self._chat_window.makeFirstResponder_(self._chat_input)
            return
        # Capture context BEFORE we touch any window — we need
        # NSWorkspace.frontmostApplication() to still be the user's
        # previous app, not the chat panel.
        self._chat_context = self._capture_active_window_context()
        # Drop stale messages from the previous chat session so the
        # transcript doesn't keep extending each time we open it.
        self._chat_messages = []
        self._chat_pending = False
        # New session, fresh request-id counter — any in-flight worker
        # from a previous open is now stale and will be rejected by
        # _handle_chat_reply's token check.
        self._chat_request_token = 0
        screen = None
        if self._window is not None:
            screen = self._window.screen()
        if screen is None:
            screen = NSScreen.mainScreen()
        if screen is None:
            return
        final_frame = _chat_frame_for_screen(screen.visibleFrame())
        if self._window is not None:
            start_frame = _chat_start_frame(self._window.frame(), final_frame)
        else:
            start_frame = final_frame
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

        title = NSTextField.alloc().initWithFrame_(NSMakeRect(18, h - 44, w - 72, 24))
        title.setStringValue_(self._chat_title())
        title.setBezeled_(False)
        title.setDrawsBackground_(False)
        title.setEditable_(False)
        title.setSelectable_(False)
        title.setFont_(NSFont.boldSystemFontOfSize_(16))
        title.setTextColor_(NSColor.labelColor())
        root.addSubview_(title)

        close = NSButton.alloc().initWithFrame_(NSMakeRect(w - 44, h - 42, 24, 24))
        close.setTitle_("×")
        close.setBordered_(False)
        close.setFont_(NSFont.systemFontOfSize_(18))
        close.setAction_(b"close:")
        close_handler = _ChatCloseHandler.alloc().initWithOverlay_(self)
        self._chat_close_handler = close_handler
        close.setTarget_(close_handler)
        root.addSubview_(close)

        # Transcript: NSScrollView containing an NSTextView so long
        # conversations get a real scroll wheel + scrollbar instead of
        # silently clipping like an NSTextField does. Read-only,
        # selectable so the user can copy lines out.
        transcript_frame = NSMakeRect(18, 62, w - 36, h - 112)
        transcript_scroll = NSScrollView.alloc().initWithFrame_(transcript_frame)
        transcript_scroll.setHasVerticalScroller_(True)
        transcript_scroll.setHasHorizontalScroller_(False)
        transcript_scroll.setAutohidesScrollers_(True)
        transcript_scroll.setBorderType_(0)  # NSNoBorder
        transcript_scroll.setDrawsBackground_(False)
        transcript_scroll.contentView().setDrawsBackground_(False)

        text_view = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, transcript_frame.size.width, transcript_frame.size.height)
        )
        text_view.setEditable_(False)
        text_view.setSelectable_(True)
        text_view.setRichText_(False)
        text_view.setDrawsBackground_(False)
        text_view.setFont_(NSFont.systemFontOfSize_(13))
        text_view.setTextColor_(NSColor.secondaryLabelColor())
        text_view.setAlignment_(NSTextAlignmentLeft)
        # Vertical resize so the document grows past the scroll viewport
        # when the conversation is longer than the panel; horizontal
        # tracking pinned so word-wrap kicks in instead of horizontal
        # scrolling.
        text_view.setHorizontallyResizable_(False)
        text_view.setVerticallyResizable_(True)
        text_view.setAutoresizingMask_(2)  # NSViewWidthSizable
        text_view.setMinSize_(NSMakeSize(transcript_frame.size.width, 0))
        text_view.setMaxSize_(NSMakeSize(transcript_frame.size.width, 1e7))
        tc = text_view.textContainer()
        if tc is not None:
            tc.setContainerSize_(NSMakeSize(transcript_frame.size.width, 1e7))
            tc.setWidthTracksTextView_(True)
        text_view.setString_(
            "Type a message and press Enter to chat with your companion."
        )

        transcript_scroll.setDocumentView_(text_view)
        # We expose the text view as ``_chat_transcript`` so the
        # renderer can keep calling a single thing without caring that
        # the underlying control changed from NSTextField to NSTextView.
        transcript = text_view
        self._chat_transcript_scroll = transcript_scroll
        root.addSubview_(transcript_scroll)

        input_field = NSTextField.alloc().initWithFrame_(NSMakeRect(18, 18, w - 36, 30))
        input_field.setPlaceholderString_("Message your companion …")
        input_field.setFont_(NSFont.systemFontOfSize_(13))
        input_field.setBezeled_(True)
        input_field.setDrawsBackground_(True)
        input_field.setEditable_(True)
        input_field.setSelectable_(True)
        input_field.setAction_(b"send:")
        handler = _ChatInputHandler.alloc().initWithOverlay_(self)
        self._chat_input_handler = handler
        input_field.setTarget_(handler)
        input_field.setDelegate_(handler)
        root.addSubview_(input_field)

        win.setContentView_(root)
        self._chat_window = win
        self._chat_transcript = transcript
        self._chat_input = input_field
        delegate = _ChatWindowDelegate.alloc().initWithOverlay_(self)
        self._chat_window_delegate = delegate
        win.setDelegate_(delegate)
        self._install_chat_outside_monitor()
        self._render_chat_messages()
        win.makeKeyAndOrderFront_(None)
        try:
            win.makeKeyWindow()
        except Exception:
            pass
        try:
            NSAnimationContext.beginGrouping()
            try:
                NSAnimationContext.currentContext().setDuration_(0.22)
                win.animator().setFrame_display_(final_frame, True)
                win.animator().setAlphaValue_(1.0)
            finally:
                NSAnimationContext.endGrouping()
        except Exception:
            win.setFrame_display_(final_frame, True)
            win.setAlphaValue_(1.0)
        win.makeFirstResponder_(input_field)
        input_field.selectText_(None)

    def hide_chat(self) -> None:
        if self._chat_window is None:
            return
        self._remove_chat_outside_monitor()
        win = self._chat_window
        try:
            win.setDelegate_(None)
        except Exception:
            pass
        try:
            win.orderOut_(None)
            win.close()
        except Exception:
            log.exception("chat teardown failed")
        self._chat_window = None
        self._chat_transcript = None
        self._chat_transcript_scroll = None
        self._chat_input = None
        self._chat_input_handler = None
        self._chat_close_handler = None
        self._chat_window_delegate = None
        # Drop captured context so the next open re-scrapes whatever
        # window the user is on now, not the previous one.
        self._chat_context = None

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

    def _chat_title(self) -> str:
        try:
            from tokenmon import box, pokemon as _pokemon
            row = box.get_active_pokemon()
            if row is None:
                return "Pokémon"
            return _pokemon.display_name(row.nickname, row.species_dex_id)
        except Exception:
            log.exception("chat title lookup failed")
            return "Pokémon"

    def _append_chat_message(self, text: str) -> None:
        """Handle the user pressing Enter in the chat input.

        Appends the user's message to the transcript, kicks off a
        Claude Code subprocess on a background thread to generate the
        companion's reply, and shows a "thinking…" placeholder while
        it's in flight. The placeholder is replaced with the real reply
        (or an error line) when the subprocess returns.
        """
        if self._chat_pending:
            # Prevent stacking subprocesses while one is already running.
            # The input field is disabled by ``_set_chat_pending`` but a
            # racy Enter press could still slip through (NSPanel can
            # re-enter the runloop during commit; an in-flight focus
            # change can deliver a second action before the first one
            # has flipped the pending flag).
            log.warning(
                "companion chat: dropped duplicate send while pending "
                "(text=%r)", text[:60],
            )
            return
        # Each request gets a unique token. The worker stamps its
        # callback with this token and ``_handle_chat_reply`` ignores
        # callbacks whose token doesn't match the current request. That
        # guards against a stray ``addOperationWithBlock_`` firing
        # twice for the same dispatch — pathological, but cheap to
        # defend against and instantly visible in logs if it ever
        # happens.
        self._chat_request_token = getattr(self, "_chat_request_token", 0) + 1
        token = self._chat_request_token

        self._chat_messages.append(("user", text))
        identity = self._companion_identity()
        if identity is None:
            self._chat_messages.append(
                ("error", "(no active companion — pick a Pokémon first)"),
            )
            self._render_chat_messages()
            return

        # System prompt is built synchronously on the UI thread because
        # building it is cheap (string formatting only) and the worker
        # thread shouldn't have to reach back into Tokenmon state.
        from tokenmon.companion.persona import build_system_prompt
        from tokenmon.companion.llm import ask_claude
        from tokenmon import config as _config

        system_prompt = build_system_prompt(identity, self._chat_context)
        skip_permissions = bool(_config.get("companion_skip_permissions"))

        # Push "thinking…" placeholder and disable the input.
        self._chat_messages.append(("thinking", "thinking…"))
        self._set_chat_pending(True)
        self._render_chat_messages()

        # Snapshot the user message — the worker thread shouldn't read
        # ``self._chat_messages`` because the UI thread keeps mutating it.
        user_message = text

        def _worker() -> None:
            try:
                ok, reply = ask_claude(
                    user_message,
                    system_prompt=system_prompt,
                    skip_permissions=skip_permissions,
                )
            except Exception:
                log.exception("companion chat worker crashed")
                ok, reply = False, "(unexpected error — see logs)"

            # One-shot latch — even if the queued block fires twice
            # (PyObjC block-bridge edge cases have been observed in the
            # wild), we only let it land once.
            fired = [False]

            def _on_main() -> None:
                if fired[0]:
                    log.warning(
                        "companion chat: dropped duplicate _on_main "
                        "for token=%d", token,
                    )
                    return
                fired[0] = True
                self._handle_chat_reply(token, ok, reply)
            try:
                from Foundation import NSOperationQueue
                NSOperationQueue.mainQueue().addOperationWithBlock_(_on_main)
            except Exception:
                # If main-thread dispatch itself fails (test harness with
                # no run loop), at least update the in-memory state so a
                # later render picks it up.
                log.exception("main-thread dispatch failed; updating in place")
                self._handle_chat_reply(token, ok, reply)

        import threading
        threading.Thread(
            target=_worker,
            name="tokenmon.companion.chat",
            daemon=True,
        ).start()

    def _handle_chat_reply(self, token: int, ok: bool, reply: str) -> None:
        """Main-thread callback for the chat worker. Replaces the
        ``thinking…`` placeholder with the reply (or an error).

        ``token`` is the request id stamped by ``_append_chat_message``.
        We compare against the current ``_chat_request_token`` so a
        late-arriving worker for an already-replaced request can't
        clobber the live transcript. Also defends against a duplicate
        callback firing for the same token.
        """
        current = getattr(self, "_chat_request_token", 0)
        if token != current:
            log.warning(
                "companion chat: ignoring stale reply (token=%d, current=%d)",
                token, current,
            )
            return
        if not self._chat_pending:
            # Already handled — second callback for the same token.
            log.warning(
                "companion chat: ignoring duplicate reply for token=%d", token,
            )
            return
        # Drop the placeholder if it's still at the tail — defensive in
        # case the chat was closed/reopened mid-flight.
        if self._chat_messages and self._chat_messages[-1][0] == "thinking":
            self._chat_messages.pop()
        self._chat_messages.append(("companion" if ok else "error", reply))
        # Trim the transcript so the panel stays readable. ~12 entries =
        # 6 round-trips visible at once.
        self._chat_messages = self._chat_messages[-12:]
        self._set_chat_pending(False)
        self._render_chat_messages()

    def _set_chat_pending(self, pending: bool) -> None:
        """Enable/disable the chat input while a request is in flight.
        Blocks the user from queuing up a second subprocess and signals
        visually that something's happening."""
        self._chat_pending = bool(pending)
        if self._chat_input is None:
            return
        try:
            self._chat_input.setEnabled_(not self._chat_pending)
        except Exception:
            log.exception("chat input enable/disable failed")

    def _companion_identity(self):
        """Build a ``CompanionIdentity`` from the active Pokémon row.
        Returns ``None`` if there's no active companion (empty box)."""
        try:
            from tokenmon import box
            from tokenmon.companion.persona import CompanionIdentity
            row = box.get_active_pokemon()
            if row is None:
                return None
            return CompanionIdentity(
                species_dex_id=int(row.species_dex_id),
                nickname=row.nickname,
                nature=row.nature,
                is_shiny=bool(row.is_shiny),
            )
        except Exception:
            log.exception("companion identity lookup failed")
            return None

    def _render_chat_messages(self) -> None:
        if self._chat_transcript is None:
            return
        sections: list[str] = []
        # The OCR snapshot is passed to the LLM via the system prompt
        # (see ``_append_chat_message``), but we don't render it in the
        # chat panel anymore — the wall of scraped text dwarfed the
        # actual conversation. Permission errors still surface here so
        # the user knows why their companion can't "see" the screen.
        snap = self._chat_context
        if snap is not None:
            try:
                if snap.source.endswith(":no-permission"):
                    sections.append(
                        "[Window context unavailable]\n"
                        "Tokenmon needs Screen Recording permission to read "
                        "the active window.\n"
                        "→ System Settings › Privacy & Security › Screen "
                        "Recording, enable Tokenmon, then restart it."
                    )
            except Exception:
                log.exception("chat context render failed")

        # Per-role label so the user can tell apart their own lines from
        # the companion's reply and from system errors. Nickname falls
        # back to the species name via ``_chat_title``.
        companion_label = self._chat_title() or "Companion"
        lines: list[str] = []
        for role, text in self._chat_messages:
            if role == "user":
                lines.append(f"You: {text}")
            elif role == "companion":
                lines.append(f"{companion_label}: {text}")
            elif role == "thinking":
                lines.append(f"{companion_label}: {text}")
            elif role == "error":
                lines.append(text)
        if lines:
            sections.append("\n".join(lines))
        elif not sections:
            sections.append(
                "Type a message and press Enter to chat with your companion.",
            )
        rendered = "\n\n".join(sections)
        # The transcript moved from NSTextField → NSTextView so the
        # panel can scroll. NSTextView uses ``setString_`` rather than
        # ``setStringValue_``; a getattr-fallback would silently no-op
        # if the API drifts again, which is exactly the kind of bug we
        # don't want here.
        try:
            self._chat_transcript.setString_(rendered)
        except AttributeError:
            # Defensive — older macOS or a swapped-in fake in tests.
            self._chat_transcript.setStringValue_(rendered)
        # Auto-scroll to the bottom so the latest reply is always
        # visible. ``scrollRangeToVisible:`` on the text view handles
        # the math against the document's text container; the clip
        # view's bounds get adjusted as a side-effect.
        try:
            length = len(rendered)
            self._chat_transcript.scrollRangeToVisible_((length, 0))
        except Exception:
            log.exception("chat transcript auto-scroll failed")

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

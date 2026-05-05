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
    NSCompositingOperationSourceAtop,
    NSCompositingOperationSourceOver,
    NSFloatingWindowLevel,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSRectFillUsingOperation,
    NSScreen,
    NSShadow,
    NSTextAlignmentCenter,
    NSTextField,
    NSTimer,
    NSView,
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


class _FloatingItemHandler(NSObject):
    """One floating-item drift animation. Each instance owns its own
    transparent click-through NSWindow showing a single item sprite,
    drifts it upward over a fixed duration, fades out, then closes the
    window.

    PokemonOverlay creates one handler per dropped item and staggers
    their starts so a multi-item drop arrives as a small shower."""

    FLOAT_FRAMES = 24
    FRAME_INTERVAL = 0.05  # → 1.2 s total
    STEP_DY = 3.0           # px per frame; ~72 px float distance
    FADE_IN_FRAMES = 4
    FADE_OUT_FRAMES = 6
    SIZE = 32

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
        self._window.setFrameOrigin_((self._start_x, new_y))
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
        self._evolution_handler: _EvolutionHandler | None = None
        self._evolution_flash_view: NSView | None = None
        self._evolution_banner: NSTextField | None = None
        self._evolution_running: bool = False
        # Strong refs to in-flight floating-item handlers — without these
        # the NSObject subclasses get garbage-collected before their
        # NSTimers fire and the windows never animate.
        self._floating_item_handlers: list[_FloatingItemHandler] = []

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
        from tokenmon import items_remote

        MAX_FLOATERS_PER_ITEM = 5
        MAX_FLOATERS_TOTAL = 8
        STAGGER_SEC = 0.18

        # Compute the anchor from the configured corner regardless of
        # whether the overlay window is currently shown.
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
            sprite = items_remote.get_sprite_by_name(key)
            if sprite is None:
                continue
            # Slight horizontal jitter so a stack of identical items
            # doesn't look like a single window animating.
            jitter = (i % 3 - 1) * 10
            x = base_x + jitter
            y = base_y
            handler = _FloatingItemHandler.alloc().initWithSprite_x_y_delay_(
                sprite, x, y, i * STAGGER_SEC,
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

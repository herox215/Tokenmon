"""Tokendex window — list of every Pokemon you've ever had, with XP/level."""

from __future__ import annotations

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSClosableWindowMask,
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSResizableWindowMask,
    NSScrollView,
    NSTextField,
    NSTitledWindowMask,
    NSView,
    NSWindow,
)
from Foundation import NSMakeRect

from tokenmon import pokemon
from tokenmon.storage import PokedexEntry, query_pokedex


class _XPBarView(NSView):
    def initWithFrame_progress_(self, frame, progress):  # noqa: N802
        self = objc.super(_XPBarView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._progress = max(0.0, min(1.0, progress))
        return self

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.25).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 3.0, 3.0).fill()
        if self._progress > 0:
            fill = NSMakeRect(
                bounds.origin.x, bounds.origin.y,
                bounds.size.width * self._progress, bounds.size.height,
            )
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.36, 0.78, 0.20, 1.0).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(fill, 3.0, 3.0).fill()


def _label(frame, text, *, font=None, color=None) -> NSTextField:
    f = NSTextField.alloc().initWithFrame_(frame)
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setFont_(font or NSFont.systemFontOfSize_(13))
    f.setTextColor_(color or NSColor.labelColor())
    return f


ROW_HEIGHT = 76
ROW_PADDING = 8
SPRITE_SIZE = 60
WINDOW_WIDTH = 420


def _build_row(entry: PokedexEntry, y: float) -> NSView:
    container = NSView.alloc().initWithFrame_(
        NSMakeRect(0, y, WINDOW_WIDTH, ROW_HEIGHT - ROW_PADDING)
    )

    sprite = pokemon.ensure_sprite(entry.dex_id)
    img_view = NSImageView.alloc().initWithFrame_(
        NSMakeRect(8, (ROW_HEIGHT - ROW_PADDING - SPRITE_SIZE) / 2, SPRITE_SIZE, SPRITE_SIZE)
    )
    if sprite is not None and sprite.exists():
        img = NSImage.alloc().initWithContentsOfFile_(str(sprite))
        if img is not None:
            img_view.setImage_(img)
    img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    img_view.setAnimates_(True)
    container.addSubview_(img_view)

    name = pokemon.name_of(entry.dex_id)
    rate = pokemon.growth_rate_of(entry.dex_id)
    level, into, needed = pokemon.level_from_xp(entry.xp, rate)

    text_x = SPRITE_SIZE + 24
    text_w = WINDOW_WIDTH - text_x - 16

    container.addSubview_(_label(
        NSMakeRect(text_x, ROW_HEIGHT - ROW_PADDING - 22, text_w, 18),
        f"#{entry.dex_id:03d}  {name}",
        font=NSFont.boldSystemFontOfSize_(13),
    ))

    lvl_text = f"Lv {level}" if level < pokemon.MAX_LEVEL else "Lv MAX"
    container.addSubview_(_label(
        NSMakeRect(text_x, ROW_HEIGHT - ROW_PADDING - 40, text_w, 16),
        f"{lvl_text}    Total: {entry.xp:,} XP    Tage: {entry.days}",
        font=NSFont.systemFontOfSize_(11),
        color=NSColor.secondaryLabelColor(),
    ))

    bar_w = text_w - 4
    progress = into / needed if needed > 0 else (1.0 if level >= pokemon.MAX_LEVEL else 0.0)
    bar = _XPBarView.alloc().initWithFrame_progress_(
        NSMakeRect(text_x, 8, bar_w, 8), progress,
    )
    container.addSubview_(bar)

    return container


_window_ref: NSWindow | None = None  # keep strong reference so window isn't GC'd


def show() -> None:
    """Build (or rebuild) the Tokendex window and bring it to front."""
    global _window_ref

    pokedex = query_pokedex()
    rows = sorted(
        pokedex.values(),
        key=lambda e: (
            -pokemon.level_from_xp(e.xp, pokemon.growth_rate_of(e.dex_id))[0],
            -e.xp,
        ),
    )

    content_height = max(ROW_HEIGHT * len(rows), 40)
    content = NSView.alloc().initWithFrame_(
        NSMakeRect(0, 0, WINDOW_WIDTH, content_height)
    )

    if not rows:
        content.addSubview_(_label(
            NSMakeRect(16, content_height / 2 - 10, WINDOW_WIDTH - 32, 20),
            "Noch kein Pokemon erlebt — sammle Tokens!",
            color=NSColor.secondaryLabelColor(),
        ))
    else:
        for i, entry in enumerate(rows):
            y = content_height - (i + 1) * ROW_HEIGHT + ROW_PADDING
            content.addSubview_(_build_row(entry, y))

    win_h = min(540, max(140, content_height + 40))

    if _window_ref is None:
        style = NSTitledWindowMask | NSClosableWindowMask | NSResizableWindowMask
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, WINDOW_WIDTH, win_h),
            style, NSBackingStoreBuffered, False,
        )
        win.setTitle_("Tokendex")
        win.setReleasedWhenClosed_(False)
        _window_ref = win
    else:
        win = _window_ref
        frame = win.frame()
        win.setContentSize_((WINDOW_WIDTH, win_h))

    scroll = NSScrollView.alloc().initWithFrame_(win.contentView().bounds())
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(0)
    scroll.setDocumentView_(content)
    scroll.contentView().scrollToPoint_((0, max(0, content_height - scroll.frame().size.height)))
    win.setContentView_(scroll)

    win.makeKeyAndOrderFront_(None)
    win.orderFrontRegardless()

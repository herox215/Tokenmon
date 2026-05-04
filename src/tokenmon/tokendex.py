"""Tokendex window — list of every Pokemon line you've ever had, with XP/level
and switchable evolution stage tabs."""

from __future__ import annotations

import logging

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelStyleRecessed,
    NSBezierPath,
    NSButton,
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
from Foundation import NSMakeRect, NSObject

from tokenmon import pokemon
from tokenmon.storage import PokedexEntry, query_pokedex

log = logging.getLogger("tokenmon.tokendex")


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


# Module-level handlers so NSButton targets aren't garbage-collected.
class _StageButtonHandler(NSObject):
    """Per-row handler that swaps the displayed stage when a tab is clicked."""

    def initWithRow_(self, row_view):  # noqa: N802
        self = objc.super(_StageButtonHandler, self).init()
        if self is None:
            return None
        self._row = row_view
        return self

    def stageClicked_(self, sender):  # noqa: N802
        try:
            dex_id = int(sender.tag())
        except Exception:
            return
        self._row.showStage_(dex_id)


class _RowView(NSView):
    """A single Pokedex row that knows how to swap its sprite among unlocked stages."""

    def initWithFrame_entry_(self, frame, entry):  # noqa: N802
        self = objc.super(_RowView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._entry = entry
        self._unlocked = pokemon.unlocked_stages_of(entry.dex_id, entry.xp)
        self._chain = pokemon.evolution_chain(entry.dex_id)
        self._handler = _StageButtonHandler.alloc().initWithRow_(self)
        self._image_view: NSImageView | None = None
        self._name_field: NSTextField | None = None
        self._tab_buttons: list[NSButton] = []
        self._build()
        # Default selection: most-evolved unlocked stage.
        self.showStage_(self._unlocked[-1])
        return self

    def _build(self) -> None:
        bounds = self.bounds()
        height = bounds.size.height

        # Sprite (left)
        sprite_size = 64
        self._image_view = NSImageView.alloc().initWithFrame_(
            NSMakeRect(8, (height - sprite_size) / 2, sprite_size, sprite_size)
        )
        self._image_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self._image_view.setAnimates_(True)
        # Crisp pixel-art scaling so the BW sprites don't blur.
        self._image_view.setWantsLayer_(True)
        if self._image_view.layer() is not None:
            self._image_view.layer().setMagnificationFilter_("nearest")
            self._image_view.layer().setMinificationFilter_("nearest")
        self.addSubview_(self._image_view)

        text_x = sprite_size + 24
        text_w = bounds.size.width - text_x - 16

        # Name (top right)
        self._name_field = _label(
            NSMakeRect(text_x, height - 24, text_w, 18),
            "",
            font=NSFont.boldSystemFontOfSize_(13),
        )
        self.addSubview_(self._name_field)

        # Stats line (level + total xp + days)
        rate = pokemon.growth_rate_of(self._entry.dex_id)
        level, _, _ = pokemon.level_from_xp(self._entry.xp, rate)
        lvl_text = "Lv MAX" if level >= pokemon.MAX_LEVEL else f"Lv {level}"
        stats = _label(
            NSMakeRect(text_x, height - 42, text_w, 16),
            f"{lvl_text}    Total: {self._entry.xp:,} XP    Tage: {self._entry.days}",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
        )
        self.addSubview_(stats)

        # XP bar
        _, into, needed = pokemon.level_from_xp(self._entry.xp, rate)
        progress = into / needed if needed > 0 else (1.0 if level >= pokemon.MAX_LEVEL else 0.0)
        bar = _XPBarView.alloc().initWithFrame_progress_(
            NSMakeRect(text_x, 28, text_w - 4, 8), progress,
        )
        self.addSubview_(bar)

        # Stage tabs (bottom row of small buttons)
        tab_y = 4
        tab_x = text_x
        for dex_id in self._chain:
            unlocked = dex_id in self._unlocked
            name = pokemon.name_of(dex_id)
            label_text = name if unlocked else f"{name} 🔒"
            btn_w = max(82, 8 * len(label_text) + 16)
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(tab_x, tab_y, btn_w, 20))
            btn.setTitle_(label_text)
            btn.setBezelStyle_(NSBezelStyleRecessed)
            btn.setFont_(NSFont.systemFontOfSize_(11))
            btn.setTag_(dex_id)
            btn.setEnabled_(unlocked)
            btn.setTarget_(self._handler)
            btn.setAction_(b"stageClicked:")
            self.addSubview_(btn)
            self._tab_buttons.append(btn)
            tab_x += btn_w + 4

    def showStage_(self, dex_id):  # noqa: N802 — pyobjc selector
        if dex_id not in self._unlocked:
            return
        sprite = pokemon.ensure_sprite(int(dex_id))
        if sprite is not None and sprite.exists() and self._image_view is not None:
            img = NSImage.alloc().initWithContentsOfFile_(str(sprite))
            if img is not None:
                self._image_view.setImage_(img)
        if self._name_field is not None:
            self._name_field.setStringValue_(f"#{int(dex_id):03d}  {pokemon.name_of(int(dex_id))}")
        # Highlight active tab via NSButton state.
        for btn in self._tab_buttons:
            btn.setState_(1 if int(btn.tag()) == int(dex_id) else 0)


ROW_HEIGHT = 100
WINDOW_WIDTH = 460


def _build_row(entry: PokedexEntry, y: float) -> NSView:
    return _RowView.alloc().initWithFrame_entry_(
        NSMakeRect(0, y, WINDOW_WIDTH, ROW_HEIGHT - 8), entry,
    )


_window_ref: NSWindow | None = None


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
            y = content_height - (i + 1) * ROW_HEIGHT + 4
            content.addSubview_(_build_row(entry, y))

    win_h = min(620, max(160, content_height + 40))

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

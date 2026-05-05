"""Layout constants + standalone widget subclasses used by panes.

These have no dependency on TokenmonPopover, so they live in their own
module to keep _main.py focused on routing + state.
"""
from __future__ import annotations

import logging

import objc
from AppKit import (
    NSBezierPath,
    NSColor,
    NSCursor,
    NSFont,
    NSGraphicsContext,
    NSImageInterpolationNone,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextField,
    NSView,
    NSViewController,
)
from Foundation import NSMakeRect, NSObject, NSPointInRect

log = logging.getLogger("tokenmon.popover.widgets")

# Layout constants (also re-exported from _main for backwards compat).
POPOVER_WIDTH = 480
POPOVER_HEIGHT = 500
SIDEBAR_WIDTH = 60
CONTENT_WIDTH = POPOVER_WIDTH - SIDEBAR_WIDTH

# Pane id constants — sentinel value for the conditional encounter slot
# can never collide with the 0..4 indices used for the four base panes.
PANE_ENCOUNTER = -1
PANE_POKEMON = 0
PANE_TOKENDEX = 1
PANE_BOX = 2
PANE_ITEMS = 3
PANE_USAGE = 4

ROW_HEIGHT = 100  # mirrors tokendex.ROW_HEIGHT


class _CrispImageView(NSImageView):
    """NSImageView subclass that disables interpolation when drawing the image.
    Without this, animated GIF sprites get bilinear-blurred when scaled up
    from their native ~96×96 to 144×144 in the popover."""

    def drawRect_(self, rect):  # noqa: N802
        ctx = NSGraphicsContext.currentContext()
        if ctx is not None:
            ctx.setImageInterpolation_(NSImageInterpolationNone)
        objc.super(_CrispImageView, self).drawRect_(rect)


class _PatClickCatcher(NSView):
    """Transparent NSView layered on top of the active sprite to catch clicks.

    NSImageView's internal image cell intercepts ``mouseDown_`` before
    subclass overrides see it, so we put a vanilla NSView on top instead;
    it never overrides drawRect_ so the GIF underneath shows through.
    """

    def initWithFrame_target_(self, frame, target):  # noqa: N802
        self = objc.super(_PatClickCatcher, self).initWithFrame_(frame)
        if self is None:
            return None
        self._pat_target = target
        return self

    def acceptsFirstMouse_(self, _event):  # noqa: N802
        return True

    def resetCursorRects(self):  # noqa: N802
        # macOS calls this when it needs to recompute cursor regions.
        self.addCursorRect_cursor_(self.bounds(), NSCursor.pointingHandCursor())

    def hitTest_(self, point):  # noqa: N802
        if NSPointInRect(self.convertPoint_fromView_(point, self.superview()),
                         self.bounds()):
            return self
        return objc.super(_PatClickCatcher, self).hitTest_(point)

    def mouseDown_(self, _event):  # noqa: N802
        target = getattr(self, "_pat_target", None)
        if target is None:
            return
        try:
            target._begin_pat()
        except Exception:
            log.exception("pat handler failed in mouseDown_")


def _crisp_image_view(frame) -> NSImageView:
    """Build a layer-backed NSImageView with nearest-neighbor magnification
    AND a draw-time interpolation override — belt-and-suspenders so pixel-art
    sprites stay sharp at any zoom level."""
    iv = _CrispImageView.alloc().initWithFrame_(frame)
    iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    iv.setAnimates_(True)
    iv.setWantsLayer_(True)
    layer = iv.layer()
    if layer is not None:
        layer.setMagnificationFilter_("nearest")
        layer.setMinificationFilter_("nearest")
    return iv


def _label(frame, text, *, font=None, color=None, align=None,
           multiline=False) -> NSTextField:
    """Non-editable, non-bordered NSTextField — used for every static label
    in the popover. Defaults to system font 13pt, label text color."""
    f = NSTextField.alloc().initWithFrame_(frame)
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setFont_(font or NSFont.systemFontOfSize_(13))
    f.setTextColor_(color or NSColor.labelColor())
    if align is not None:
        f.setAlignment_(align)
    if multiline:
        f.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
    return f


class _ContentVC(NSViewController):
    """Trivial NSViewController subclass — NSPopover requires one."""

    def loadView(self):  # noqa: N802
        if hasattr(self, "_root_view") and self._root_view is not None:
            self.setView_(self._root_view)
        else:
            self.setView_(
                NSView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_HEIGHT)
                )
            )


def _new_vc(root_view: NSView) -> NSViewController:
    vc = _ContentVC.alloc().init()
    vc._root_view = root_view
    vc.view()  # force loadView
    return vc


class _SidebarView(NSView):
    """Background-tinted sidebar that highlights the selected slot.

    The sidebar has a *variable* number of slots: the four base panes are
    always present (Today / Pokedex / Box / Usage), and a 5th encounter
    slot is prepended at the top whenever a wild encounter is pending.
    """

    SLOT_HEIGHT = 60

    def initWithFrame_paneIds_selected_(self, frame, pane_ids, selected):  # noqa: N802
        self = objc.super(_SidebarView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._pane_ids = list(pane_ids)
        self._selected = selected
        return self

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.03).set()
        NSBezierPath.fillRect_(bounds)
        NSColor.separatorColor().set()
        NSBezierPath.fillRect_(
            NSMakeRect(bounds.size.width - 1, 0, 1, bounds.size.height)
        )
        try:
            slot_idx = self._pane_ids.index(self._selected)
        except ValueError:
            return
        slot_h = _SidebarView.SLOT_HEIGHT
        y = bounds.size.height - (slot_idx + 1) * slot_h
        rect = NSMakeRect(4, y + 4, bounds.size.width - 8, slot_h - 8)
        NSColor.controlAccentColor().colorWithAlphaComponent_(0.18).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 6, 6).fill()

    def setPaneIds_(self, pane_ids):  # noqa: N802
        self._pane_ids = list(pane_ids)
        self.setNeedsDisplay_(True)

    def setSelected_(self, pane_id):  # noqa: N802
        self._selected = int(pane_id)
        self.setNeedsDisplay_(True)

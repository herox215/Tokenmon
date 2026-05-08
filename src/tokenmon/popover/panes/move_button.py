"""Custom NSView used in place of NSButton for the battle pane's move grid.

Renders a type-coloured rounded background, the move name + PP, and a
small category badge (Physical / Special / Status) in the games' visual
language. Click handling mirrors what NSButton would have provided —
mouse-down arms, mouse-up inside bounds fires the action — minus the
fight with NSButton's bezel and attributed-title centring.

Used by ``battle.py`` only; named with a leading underscore to signal
that.
"""
from __future__ import annotations

import math
import logging
from typing import Final

import objc
from AppKit import (
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSTextAlignmentCenter,
    NSView,
)
from Foundation import NSMakeRect, NSMakePoint, NSAttributedString

from tokenmon.battle.models import Move
from tokenmon.popover.panes.move_styles import (
    darken,
    text_color_for_type,
    type_color,
)

log = logging.getLogger("tokenmon.popover.panes.move_button")

# Layout constants — kept here rather than at module top of battle.py so
# they live with the view that uses them.
_CORNER_RADIUS: Final = 6.0
_BORDER_WIDTH: Final = 1.0
_BADGE_SIZE: Final = 16.0
_BADGE_INSET: Final = 4.0           # gap between badge and button edge
_DISABLED_ALPHA: Final = 0.35       # button alpha when disabled / 0 PP


# ---------------------------------------------------------------------------
# Category badges — drawn with NSBezierPath so they stay sharp on retina.
# Each ``_draw_*_badge`` paints into ``rect`` (an NSRect), no return value.
# Colours are tuned to read like the Gen-IV+ category icons in the games.

_PHYSICAL_FILL = (0.94, 0.40, 0.18)   # orange-red
_SPECIAL_FILL = (0.55, 0.40, 0.92)    # violet
_STATUS_FILL = (0.55, 0.55, 0.58)     # neutral gray


def _set_rgb(rgb: tuple[float, float, float], alpha: float = 1.0) -> None:
    r, g, b = rgb
    NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha).set()


def _badge_background(rect, fill_rgb, alpha) -> None:
    """Rounded square + 1px darker border, shared by all three badges."""
    _set_rgb(fill_rgb, alpha)
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        rect, 3.0, 3.0,
    ).fill()
    _set_rgb(darken(fill_rgb, 0.30), alpha)
    border = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        rect, 3.0, 3.0,
    )
    border.setLineWidth_(0.8)
    border.stroke()


def _draw_physical_badge(rect, alpha: float) -> None:
    """Orange-red square with three short white diagonal strike-marks —
    evokes the Gen-IV "physical contact" icon (a fist + motion lines)."""
    _badge_background(rect, _PHYSICAL_FILL, alpha)
    x = rect.origin.x
    y = rect.origin.y
    w = rect.size.width
    h = rect.size.height
    NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha).set()
    path = NSBezierPath.bezierPath()
    # Three short parallel diagonals (top-left → bottom-right).
    for i, t in enumerate((0.30, 0.50, 0.70)):
        x0 = x + w * (t - 0.18)
        y0 = y + h * (1.0 - t + 0.18)
        x1 = x + w * (t + 0.18)
        y1 = y + h * (1.0 - t - 0.18)
        path.moveToPoint_(NSMakePoint(x0, y0))
        path.lineToPoint_(NSMakePoint(x1, y1))
    path.setLineWidth_(1.6)
    path.setLineCapStyle_(1)  # round caps
    path.stroke()


def _draw_special_badge(rect, alpha: float) -> None:
    """Violet square with three white rays radiating from one corner —
    evokes the "special energy" icon (a starburst)."""
    _badge_background(rect, _SPECIAL_FILL, alpha)
    cx = rect.origin.x + rect.size.width * 0.30
    cy = rect.origin.y + rect.size.height * 0.30
    radius = min(rect.size.width, rect.size.height) * 0.55
    NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha).set()
    path = NSBezierPath.bezierPath()
    # 3 rays from corner-ish anchor, sweeping outward.
    for angle_deg in (20, 50, 80):
        a = math.radians(angle_deg)
        path.moveToPoint_(NSMakePoint(cx, cy))
        path.lineToPoint_(NSMakePoint(
            cx + math.cos(a) * radius,
            cy + math.sin(a) * radius,
        ))
    path.setLineWidth_(1.6)
    path.setLineCapStyle_(1)
    path.stroke()
    # Small filled dot at the anchor for the "energy core" feel.
    dot = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - 1.5, cy - 1.5, 3.0, 3.0),
    )
    dot.fill()


def _draw_status_badge(rect, alpha: float) -> None:
    """Gray square with a small white 4-point star — the neutral
    "status / non-damaging" indicator."""
    _badge_background(rect, _STATUS_FILL, alpha)
    cx = rect.origin.x + rect.size.width / 2
    cy = rect.origin.y + rect.size.height / 2
    arm = min(rect.size.width, rect.size.height) * 0.30
    NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha).set()
    path = NSBezierPath.bezierPath()
    # 4-point star: vertical line + horizontal line.
    path.moveToPoint_(NSMakePoint(cx, cy - arm))
    path.lineToPoint_(NSMakePoint(cx, cy + arm))
    path.moveToPoint_(NSMakePoint(cx - arm, cy))
    path.lineToPoint_(NSMakePoint(cx + arm, cy))
    path.setLineWidth_(1.6)
    path.setLineCapStyle_(1)
    path.stroke()


def _draw_category_badge(category: str, rect, alpha: float) -> None:
    """Dispatch to the right badge painter — defaults to status for
    anything unrecognised so the view never draws nothing."""
    cat = (category or "").lower()
    if cat == "physical":
        _draw_physical_badge(rect, alpha)
    elif cat == "special":
        _draw_special_badge(rect, alpha)
    else:
        _draw_status_badge(rect, alpha)


# ---------------------------------------------------------------------------
# The view itself.

class _MoveButtonView(NSView):
    """Replacement for the NSButton used in the move grid.

    Owns its own click handling — on ``mouseUp:`` inside bounds, calls
    ``[target performSelector:action withObject:self]``. The ``fire:``
    selector used by ``_ActionHandler`` matches that signature, so
    existing handler bridges work unchanged.
    """

    def initWithFrame_move_currentPP_target_action_(  # noqa: N802
        self, frame, mv: Move, cur_pp: int, target, action: bytes,
    ):
        self = objc.super(_MoveButtonView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._move = mv
        self._cur_pp = int(cur_pp)
        self._target = target
        self._action = action
        self._enabled = True
        self._pressed = False
        return self

    # --- AppKit overrides -------------------------------------------------

    def isFlipped(self):  # noqa: N802
        # Match NSButton's coordinate convention so the badge "top-right"
        # math reads naturally even though sibling subviews in this pane
        # use the default (lower-left) origin.
        return False

    def acceptsFirstMouse_(self, _event):  # noqa: N802
        return True

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        bg = type_color(self._move.type)
        # Disabled or pressed states modulate alpha / fill so the click
        # state and the "out of PP" state are both visible.
        bg_alpha = 1.0 if self._enabled else _DISABLED_ALPHA
        if self._enabled and self._pressed:
            # Pressed look — push toward darker variant of same hue.
            bg = darken(bg, 0.18)

        # 1) Filled rounded rect.
        _set_rgb(bg, bg_alpha)
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, _CORNER_RADIUS, _CORNER_RADIUS,
        ).fill()

        # 2) Subtle darker border for definition against the popover.
        _set_rgb(darken(type_color(self._move.type), 0.25), bg_alpha)
        border = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, _CORNER_RADIUS, _CORNER_RADIUS,
        )
        border.setLineWidth_(_BORDER_WIDTH)
        border.stroke()

        # 3) Title — two lines: move name + PP counter. Inset the right
        # edge so the badge has reserved space.
        title_inset_right = _BADGE_SIZE + _BADGE_INSET * 2
        title_rect = NSMakeRect(
            6.0,
            4.0,
            max(0.0, bounds.size.width - 6.0 - title_inset_right),
            max(0.0, bounds.size.height - 8.0),
        )
        text_rgb = text_color_for_type(self._move.type)
        text_alpha = 1.0 if self._enabled else 0.65
        title_attrs = self._title_attrs(text_rgb, text_alpha, bold=True)
        sub_attrs = self._title_attrs(text_rgb, text_alpha, bold=False)
        # NSAttributedString doesn't easily mix two fonts in one draw call
        # without an NSMutableAttributedString — but two separate draws
        # are fine and keep this readable.
        line1 = NSAttributedString.alloc().initWithString_attributes_(
            self._move.name, title_attrs,
        )
        cur = max(0, self._cur_pp)
        line2 = NSAttributedString.alloc().initWithString_attributes_(
            f"PP {cur}/{self._move.pp}", sub_attrs,
        )
        # Stack: line1 on top, line2 just below.
        l1_size = line1.size()
        l2_size = line2.size()
        total_h = l1_size.height + l2_size.height + 1
        top = title_rect.origin.y + (title_rect.size.height - total_h) / 2 + total_h
        l1_origin = NSMakePoint(
            title_rect.origin.x,
            top - l1_size.height,
        )
        l1_rect = NSMakeRect(
            l1_origin.x, l1_origin.y,
            title_rect.size.width, l1_size.height,
        )
        line1.drawInRect_(l1_rect)
        l2_rect = NSMakeRect(
            title_rect.origin.x,
            l1_origin.y - l2_size.height - 1,
            title_rect.size.width,
            l2_size.height,
        )
        line2.drawInRect_(l2_rect)

        # 4) Category badge — top-right corner.
        badge_rect = NSMakeRect(
            bounds.origin.x + bounds.size.width - _BADGE_SIZE - _BADGE_INSET,
            bounds.origin.y + bounds.size.height - _BADGE_SIZE - _BADGE_INSET,
            _BADGE_SIZE,
            _BADGE_SIZE,
        )
        _draw_category_badge(self._move.category, badge_rect, bg_alpha)

    # --- Mouse handling ---------------------------------------------------

    def mouseDown_(self, _event):  # noqa: N802
        if not self._enabled:
            return
        self._pressed = True
        self.setNeedsDisplay_(True)

    def mouseDragged_(self, event):  # noqa: N802 — track press during drag
        if not self._enabled:
            return
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        inside = self._point_in_bounds(loc)
        if inside != self._pressed:
            self._pressed = inside
            self.setNeedsDisplay_(True)

    def mouseUp_(self, event):  # noqa: N802
        was_pressed = self._pressed
        self._pressed = False
        self.setNeedsDisplay_(True)
        if not (self._enabled and was_pressed):
            return
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        if not self._point_in_bounds(loc):
            return
        self._fire()

    @objc.python_method
    def _point_in_bounds(self, p) -> bool:
        b = self.bounds()
        return (
            b.origin.x <= p.x <= b.origin.x + b.size.width
            and b.origin.y <= p.y <= b.origin.y + b.size.height
        )

    @objc.python_method
    def _fire(self) -> None:
        if self._target is None or self._action is None:
            return
        try:
            # Use Objective-C messaging so the handler's ``fire:`` selector
            # is dispatched correctly (the same way NSButton does it).
            self._target.performSelector_withObject_(self._action, self)
        except Exception:
            log.exception("move button action failed for %s", self._move.key)

    # --- NSButton-compatible surface used by the battle runner ------------

    def setEnabled_(self, value):  # noqa: N802
        new = bool(value)
        if new == self._enabled:
            return
        self._enabled = new
        # Cancel any pressed state so re-enabling doesn't fire a stale click.
        self._pressed = False
        self.setNeedsDisplay_(True)

    def isEnabled(self):  # noqa: N802
        return self._enabled

    # --- Helpers ----------------------------------------------------------

    @objc.python_method
    def _title_attrs(self, rgb: tuple[float, float, float], alpha: float, *, bold: bool):
        r, g, b = rgb
        font = (
            NSFont.boldSystemFontOfSize_(11)
            if bold else NSFont.systemFontOfSize_(10)
        )
        para = NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(NSTextAlignmentCenter)
        para.setLineBreakMode_(4)  # NSLineBreakByTruncatingTail
        return {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName:
                NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha),
            NSParagraphStyleAttributeName: para,
        }

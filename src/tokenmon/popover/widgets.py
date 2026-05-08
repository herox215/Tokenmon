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
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImageInterpolationNone,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextField,
    NSView,
    NSViewController,
)
from Foundation import NSAttributedString, NSMakeRect, NSObject, NSPointInRect

log = logging.getLogger("tokenmon.popover.widgets")

# Layout constants (also re-exported from _main for backwards compat).
POPOVER_WIDTH = 480
POPOVER_HEIGHT = 500
SIDEBAR_WIDTH = 60
CONTENT_WIDTH = POPOVER_WIDTH - SIDEBAR_WIDTH

# Pane id constants — sentinel values for the conditional encounter slot.
# PANE_ENCOUNTER is the unified sidebar slot id AND the preview-pane id;
# trainer and wild fights share it. Battle + reward sub-panes are reached
# imperatively from the preview, so they don't need their own sidebar slots.
PANE_ENCOUNTER = -1
PANE_BATTLE = -3
PANE_BATTLE_REWARD = -4
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


# --- Type badges ----------------------------------------------------------

TYPE_BADGE_HEIGHT = 18
TYPE_BADGE_WIDTH = 64
TYPE_BADGE_GAP = 6


class _TypeBadge(NSView):
    """A rounded-rectangle pill rendered in the canonical Pokemon-game color
    for one elemental type, with the type name in white capitalised text.

    Drawn manually (not as a layer-backed view) so the rounded fill +
    centred text composite correctly without dragging in a custom layer
    delegate.
    """

    def initWithFrame_typeName_(self, frame, type_name):  # noqa: N802
        self = objc.super(_TypeBadge, self).initWithFrame_(frame)
        if self is None:
            return None
        self._type_name = (type_name or "normal").lower()
        return self

    def drawRect_(self, _rect):  # noqa: N802
        from tokenmon.pokemon import TYPE_COLORS

        bounds = self.bounds()
        r, g, b = TYPE_COLORS.get(self._type_name, (0.5, 0.5, 0.5))
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 4, 4).fill()

        # Centred uppercase label, white, bold 10pt.
        text = self._type_name.upper()
        attrs = {
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(10),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        astr = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        size = astr.size()
        x = (bounds.size.width - size.width) / 2
        y = (bounds.size.height - size.height) / 2
        astr.drawAtPoint_((x, y))


# --- Layout backdrops -----------------------------------------------------


class _CardView(NSView):
    """Soft-tinted rounded-rectangle backdrop used to visually group
    related content inside a pane. Drawn first so labels and controls
    layered on top read as living *inside* the card."""

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        # 6 % gray fill — readable in both light and dark appearance
        # because we use `colorWithCalibratedWhite_alpha_` rather than a
        # fixed RGB triplet.
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.06).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 10, 10,
        ).fill()
        # Hairline border at 18 % so the card edge stays legible against
        # the popover background even in dark mode.
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.18).set()
        border = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 10, 10,
        )
        border.setLineWidth_(0.5)
        border.stroke()


class _SeparatorView(NSView):
    """1-pixel hairline divider — used inside a _CardView to split the
    sprite column from the info column."""

    def drawRect_(self, _rect):  # noqa: N802
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.20).set()
        NSBezierPath.fillRect_(self.bounds())


# --- Stats radar ----------------------------------------------------------

import math


class _StatsRadarView(NSView):
    """Hexagonal radar chart for the six IVs (0..31) of one Pokemon instance.

    The polygon shape encodes per-instance *potential* — base stats are
    species-wide, IVs are what make this particular Charmander unique.
    Scale is fixed at IV_MAX (31) so each axis is comparable. The fill
    color is taken from the species' primary type so the chart visually
    matches the type badge above. Hovering near an axis label surfaces
    that stat's IV via tooltip; the explicit numbers are kept hidden in
    the main chrome so the chart reads as flavour, not min-max porn.
    """

    AXIS_LABELS_CW: tuple[str, ...] = ("HP", "ATK", "DEF", "SP.A", "SP.D", "SPD")
    # Stat order in the storage layer: HP, Atk, Def, Sp.Atk, Sp.Def, Speed.
    # We render them clockwise from the top: HP top, ATK upper-right, DEF
    # lower-right, Sp.A bottom, Sp.D lower-left, Speed upper-left.

    def initWithFrame_ivs_typeColor_(self, frame, ivs, type_color):  # noqa: N802
        self = objc.super(_StatsRadarView, self).initWithFrame_(frame)
        if self is None:
            return None
        if len(ivs) != 6:
            raise ValueError(f"_StatsRadarView expects 6 ivs, got {len(ivs)}")
        self._ivs = tuple(int(v) for v in ivs)
        self._type_color = type_color  # (r, g, b) in 0..1, or None for accent
        self._tooltip_views: list[NSView] = []
        self._install_tooltip_zones()
        return self

    # ------------------------------------------------------------------
    # Hover tooltips: one transparent subview per axis label, each with
    # its own ``setToolTip_``. Going through the per-view tooltip path
    # avoids the ``view:stringForToolTip:point:userData:`` protocol —
    # PyObjC can't always bridge that selector's ``void *`` userData
    # cleanly, so the override silently never fires. Subviews "just work"
    # because AppKit installs a tracking area per setToolTip_ call.
    # ------------------------------------------------------------------

    def _axis_label_rect(self, idx: int):
        bounds = self.bounds()
        cx = bounds.size.width / 2
        cy = bounds.size.height / 2
        radius = min(cx, cy) - 18
        theta = math.pi / 2 - (idx * math.pi / 3)
        lx = cx + (radius + 8) * math.cos(theta)
        ly = cy + (radius + 8) * math.sin(theta)
        size = 36
        return NSMakeRect(lx - size / 2, ly - size / 2, size, size)

    def _install_tooltip_zones(self):
        from tokenmon.pokemon.stats import IV_MAX

        for sv in self._tooltip_views:
            sv.removeFromSuperview()
        self._tooltip_views = []
        for i in range(6):
            rect = self._axis_label_rect(i)
            zone = NSView.alloc().initWithFrame_(rect)
            zone.setToolTip_(
                f"{self.AXIS_LABELS_CW[i]}  IV {self._ivs[i]}/{IV_MAX}"
            )
            self.addSubview_(zone)
            self._tooltip_views.append(zone)

    def _axis_points(self, cx: float, cy: float, radius: float) -> list[tuple[float, float]]:
        """Six points evenly spaced clockwise from the top of the chart."""
        out: list[tuple[float, float]] = []
        for i in range(6):
            theta = math.pi / 2 - (i * math.pi / 3)  # 90°, 30°, -30°, …
            out.append((cx + radius * math.cos(theta),
                        cy + radius * math.sin(theta)))
        return out

    def _polygon_path(self, points: list[tuple[float, float]]) -> NSBezierPath:
        path = NSBezierPath.bezierPath()
        first = points[0]
        path.moveToPoint_((first[0], first[1]))
        for pt in points[1:]:
            path.lineToPoint_((pt[0], pt[1]))
        path.closePath()
        return path

    def drawRect_(self, _rect):  # noqa: N802
        from tokenmon.pokemon.stats import IV_MAX

        bounds = self.bounds()
        cx = bounds.size.width / 2
        cy = bounds.size.height / 2
        # Leave room for the axis labels around the rim.
        radius = min(cx, cy) - 18

        # 1. Concentric hex grid — four rings at 25/50/75/100 % of the radius.
        grid_color = NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.20)
        for frac in (0.25, 0.5, 0.75, 1.0):
            ring_pts = self._axis_points(cx, cy, radius * frac)
            ring = self._polygon_path(ring_pts)
            grid_color.set()
            ring.setLineWidth_(1.0)
            ring.stroke()

        # 2. Six axis spokes from center to rim.
        outer = self._axis_points(cx, cy, radius)
        for ox, oy in outer:
            spoke = NSBezierPath.bezierPath()
            spoke.moveToPoint_((cx, cy))
            spoke.lineToPoint_((ox, oy))
            grid_color.set()
            spoke.setLineWidth_(1.0)
            spoke.stroke()

        # 3. IV polygon — fixed 0..IV_MAX scale.
        scale = float(IV_MAX)
        stat_pts: list[tuple[float, float]] = []
        for i, value in enumerate(self._ivs):
            frac = max(0.0, min(1.0, value / scale))
            theta = math.pi / 2 - (i * math.pi / 3)
            stat_pts.append((cx + radius * frac * math.cos(theta),
                             cy + radius * frac * math.sin(theta)))
        stat_path = self._polygon_path(stat_pts)

        if self._type_color is not None:
            r, g, b = self._type_color
        else:
            r, g, b = (0.36, 0.78, 0.20)  # fallback green
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.35).set()
        stat_path.fill()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.95).set()
        stat_path.setLineWidth_(1.5)
        stat_path.stroke()

        # 4. Vertex dots so low-stat axes are still visible.
        for px, py in stat_pts:
            dot = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(px - 2, py - 2, 4, 4)
            )
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
            dot.fill()

        # 5. Axis labels just outside each rim vertex.
        label_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(9),
            NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
        }
        for i, (ox, oy) in enumerate(outer):
            text = self.AXIS_LABELS_CW[i]
            astr = NSAttributedString.alloc().initWithString_attributes_(
                text, label_attrs,
            )
            size = astr.size()
            theta = math.pi / 2 - (i * math.pi / 3)
            # Push the label outwards along the axis by ~half its size + a gap.
            lx = cx + (radius + 8) * math.cos(theta) - size.width / 2
            ly = cy + (radius + 8) * math.sin(theta) - size.height / 2
            astr.drawAtPoint_((lx, ly))


def _type_badge_row(
    cx: float, y: float, types: tuple[str, ...],
    *,
    badge_w: int = TYPE_BADGE_WIDTH,
    badge_h: int = TYPE_BADGE_HEIGHT,
    gap: int = TYPE_BADGE_GAP,
) -> list[_TypeBadge]:
    """Build 1 or 2 ``_TypeBadge`` views centred horizontally on ``cx``.

    Returns a list of badges the caller should ``addSubview_`` on the
    parent view. They're not packed into a wrapper view so the parent
    keeps full control over the subview tree (and so we don't add a
    layer-backed wrapper that fights with surrounding pane layout).
    """
    if not types:
        return []
    n = len(types)
    total_w = n * badge_w + (n - 1) * gap
    start_x = cx - total_w / 2
    badges: list[_TypeBadge] = []
    for i, t in enumerate(types):
        x = start_x + i * (badge_w + gap)
        b = _TypeBadge.alloc().initWithFrame_typeName_(
            NSMakeRect(x, y, badge_w, badge_h), t,
        )
        badges.append(b)
    return badges


# --- Token-usage chart ----------------------------------------------------


def _nice_max(value: int) -> int:
    """Round ``value`` up to the nearest 1/2/5 × 10ⁿ — used as the y-axis
    ceiling for the token chart so the gridline labels stay legible
    (10K, 20K, 50K …) instead of arbitrary numbers like 17,342.

    ``_nice_max(0) == 1`` so an empty-day chart still has a valid scale.
    """
    if value <= 0:
        return 1
    import math
    exp = math.floor(math.log10(value))
    base = 10 ** exp
    for mul in (1, 2, 5, 10):
        cap = mul * base
        if cap >= value:
            return int(cap)
    return int(10 * base)


class _TokenChartView(NSView):
    """Bar chart of output tokens per fixed-size time bucket across today.

    Y axis = tokens (0..nice_max), X axis = local time of day (0..24h).
    A vertical accent-coloured "now" line marks the current time so the
    user can read past activity left of it and see the empty future on
    the right.

    State is set via ``setBuckets_nowMinute_`` so ``drawRect_`` stays
    pure — the controller refreshes both data and the now position
    every 30 s while the pane is visible.
    """

    PLOT_LEFT_PAD = 36   # room for y-axis labels
    PLOT_RIGHT_PAD = 8
    PLOT_TOP_PAD = 8
    PLOT_BOTTOM_PAD = 14  # room for x-axis labels

    def initWithFrame_buckets_bucketMinutes_nowMinute_(  # noqa: N802
        self, frame, buckets, bucket_minutes, now_minute,
    ):
        self = objc.super(_TokenChartView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._buckets: list[int] = list(buckets)
        self._bucket_minutes: int = int(bucket_minutes)
        self._now_minute: int = int(now_minute)
        return self

    def setBuckets_nowMinute_(self, buckets, now_minute):  # noqa: N802
        self._buckets = list(buckets)
        self._now_minute = int(now_minute)
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        # Card background.
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.06).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 8, 8,
        ).fill()
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.18).set()
        border = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 8, 8,
        )
        border.setLineWidth_(0.5)
        border.stroke()

        plot_x = self.PLOT_LEFT_PAD
        plot_y = self.PLOT_BOTTOM_PAD
        plot_w = bounds.size.width - self.PLOT_LEFT_PAD - self.PLOT_RIGHT_PAD
        plot_h = bounds.size.height - self.PLOT_TOP_PAD - self.PLOT_BOTTOM_PAD

        from tokenmon.ui_helpers import fmt_tokens

        total = sum(self._buckets)
        if total == 0:
            # Empty-state — show a single centred line where the bars would be.
            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(11),
                NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
            }
            astr = NSAttributedString.alloc().initWithString_attributes_(
                "Noch keine Tokens heute", attrs,
            )
            size = astr.size()
            astr.drawAtPoint_((
                (bounds.size.width - size.width) / 2,
                (bounds.size.height - size.height) / 2,
            ))
            self._draw_x_axis_labels(plot_x, plot_y, plot_w)
            return

        peak = max(self._buckets)
        y_max = _nice_max(peak)

        # Gridlines + y-axis labels at 0, mid, max.
        grid_color = NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.18)
        label_attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(9),
            NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
        }
        for frac, value in ((0.0, 0), (0.5, y_max // 2), (1.0, y_max)):
            gy = plot_y + plot_h * frac
            line = NSBezierPath.bezierPath()
            line.moveToPoint_((plot_x, gy))
            line.lineToPoint_((plot_x + plot_w, gy))
            grid_color.set()
            line.setLineWidth_(0.5)
            line.stroke()
            label = fmt_tokens(int(value))
            astr = NSAttributedString.alloc().initWithString_attributes_(
                label, label_attrs,
            )
            sz = astr.size()
            astr.drawAtPoint_((plot_x - sz.width - 4, gy - sz.height / 2))

        # Bars.
        n = len(self._buckets)
        bar_w = plot_w / n
        accent = NSColor.controlAccentColor().colorWithAlphaComponent_(0.85)
        accent.set()
        for i, value in enumerate(self._buckets):
            if value <= 0:
                continue
            h = plot_h * (value / y_max)
            x = plot_x + i * bar_w
            # Inset by 0.5 px so adjacent non-zero bars don't smear.
            rect = NSMakeRect(x + 0.5, plot_y, max(0.5, bar_w - 1.0), h)
            NSBezierPath.fillRect_(rect)

        # "Now" line — only if it falls inside today's window (0..1440).
        if 0 <= self._now_minute <= 1440:
            nx = plot_x + plot_w * (self._now_minute / 1440.0)
            now_path = NSBezierPath.bezierPath()
            now_path.moveToPoint_((nx, plot_y))
            now_path.lineToPoint_((nx, plot_y + plot_h))
            NSColor.systemOrangeColor().colorWithAlphaComponent_(0.9).set()
            now_path.setLineWidth_(1.0)
            now_path.stroke()

        self._draw_x_axis_labels(plot_x, plot_y, plot_w)

    def _draw_x_axis_labels(self, plot_x, plot_y, plot_w):
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(9),
            NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
        }
        for hour in (0, 6, 12, 18, 24):
            frac = hour / 24.0
            x = plot_x + plot_w * frac
            text = f"{hour:02d}"
            astr = NSAttributedString.alloc().initWithString_attributes_(
                text, attrs,
            )
            sz = astr.size()
            # Label hangs below the plot area inside PLOT_BOTTOM_PAD.
            astr.drawAtPoint_((x - sz.width / 2, plot_y - sz.height - 1))

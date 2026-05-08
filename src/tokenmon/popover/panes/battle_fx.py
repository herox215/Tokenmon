"""Per-type hit-effect overlays for the battle pane.

Each FX is a short-lived ``NSView`` mounted on top of the defender
sprite when an ``AttackEvent`` resolves. The view runs an internal
NSTimer that advances a frame counter; ``drawRect_`` paints type-
appropriate particles whose alpha fades out near the end of the
window. Once the duration elapses the view removes itself from its
superview — the runner doesn't need to track lifetimes.

Types with bespoke visuals: fire, water, electric, grass, ice,
psychic, ghost, dragon, dark, fighting. Everything else falls through
to ``_ImpactFX`` (white starburst), which still reads as "something
just landed" without committing to wrong-type imagery.
"""
from __future__ import annotations

import math
import random
from typing import Final

import objc
from AppKit import NSBezierPath, NSColor, NSView
from Foundation import NSMakeRect, NSMakePoint, NSTimer

# Total on-screen time per FX. The runner sequences attacker-shake →
# FX (this) → HP-drain back-to-back, so this should stay short or the
# turn drags.
FX_DURATION: Final = 0.30
FX_FPS: Final = 30


class _TypeFXBase(NSView):
    """Common timer plumbing — subclasses just override ``drawFrame_fade_``.

    ``frame`` index advances 30× per second; ``fade`` is 1.0 → 0.0
    over the back third of the window for a gentle exit.
    """

    def initWithFrame_seed_(self, frame, seed):  # noqa: N802
        self = objc.super(_TypeFXBase, self).initWithFrame_(frame)
        if self is None:
            return None
        self._frame_idx = 0
        self._max_frames = max(1, int(FX_DURATION * FX_FPS))
        self._rng = random.Random(int(seed))
        self.setWantsLayer_(True)
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FX_FPS, self, b"tickFx:", None, True,
        )
        return self

    def tickFx_(self, _timer):  # noqa: N802
        self._frame_idx += 1
        if self._frame_idx >= self._max_frames:
            try:
                self._timer.invalidate()
            except Exception:
                pass
            self._timer = None
            self.removeFromSuperview()
            return
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):  # noqa: N802
        # Fade from 1.0 → 0.0 over the last 33% of the window.
        cutoff = int(self._max_frames * 0.66)
        if self._frame_idx <= cutoff:
            fade = 1.0
        else:
            fade = max(0.0, 1.0 - (self._frame_idx - cutoff)
                       / (self._max_frames - cutoff))
        self.drawFrame_fade_(self._frame_idx, fade)

    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802 — override hook
        pass

    @objc.python_method
    def _bounds(self):
        return self.bounds()

    @objc.python_method
    def _center(self):
        b = self.bounds()
        return (b.size.width / 2.0, b.size.height / 2.0)


# ----------------------------------------------------------------------
# Bespoke per-type effects. Each one is intentionally simple — five-or-
# so primitives, no images, no particle systems beyond a Random walk.
# ----------------------------------------------------------------------


class _FireFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        for i in range(8):
            angle = (i / 8.0) * math.tau + frame * 0.18
            radius = 18 + frame * 1.8 + self._rng.uniform(-2, 2)
            x = cx + math.cos(angle) * radius
            y = cy + math.sin(angle) * radius * 0.9
            r_pix = 6 + self._rng.uniform(-1, 1)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                1.0, 0.55 - i * 0.04, 0.10, 0.85 * fade,
            ).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - r_pix, y - r_pix, r_pix * 2, r_pix * 2),
            ).fill()


class _WaterFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        for i in range(10):
            angle = (i / 10.0) * math.tau
            radius = 8 + frame * 2.5
            x = cx + math.cos(angle) * radius
            y = cy + math.sin(angle) * radius
            r_pix = 4
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.20, 0.55, 0.95, 0.80 * fade,
            ).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - r_pix, y - r_pix, r_pix * 2, r_pix * 2),
            ).fill()


class _ElectricFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            1.0, 0.95, 0.10, 0.95 * fade,
        ).set()
        for _ in range(5):
            path = NSBezierPath.bezierPath()
            x = cx + self._rng.uniform(-30, 30)
            y = cy + self._rng.uniform(-20, 20)
            path.moveToPoint_(NSMakePoint(x, y + 18))
            for step in range(4):
                x += self._rng.uniform(-10, 10)
                y -= 9
                path.lineToPoint_(NSMakePoint(x, y))
            path.setLineWidth_(2.0)
            path.stroke()


class _GrassFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        for i in range(8):
            angle = (i / 8.0) * math.tau + frame * 0.05
            radius = 10 + frame * 2
            x = cx + math.cos(angle) * radius
            y = cy + math.sin(angle) * radius
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.30 + i * 0.04, 0.75, 0.30, 0.85 * fade,
            ).set()
            # Leaf = ellipse rotated; AppKit's affine here would be
            # heavy, so just draw a tilted thin oval.
            path = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(x - 5, y - 2, 10, 4),
            )
            path.fill()


class _IceFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.80, 0.95, 1.0, 0.95 * fade,
        ).set()
        for i in range(6):
            angle = (i / 6.0) * math.tau + frame * 0.04
            radius = 6 + frame * 2.2
            x = cx + math.cos(angle) * radius
            y = cy + math.sin(angle) * radius
            path = NSBezierPath.bezierPath()
            for arm in range(6):
                a = (arm / 6.0) * math.tau
                path.moveToPoint_(NSMakePoint(x, y))
                path.lineToPoint_(NSMakePoint(
                    x + math.cos(a) * 5, y + math.sin(a) * 5,
                ))
            path.setLineWidth_(1.5)
            path.stroke()


class _PsychicFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            1.0, 0.45, 0.85, 0.55 * fade,
        ).set()
        for i in range(3):
            r = 8 + frame * 3 + i * 6
            path = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx - r, cy - r, r * 2, r * 2),
            )
            path.setLineWidth_(2.0)
            path.stroke()


class _GhostFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        for i in range(6):
            ox = self._rng.uniform(-25, 25)
            oy = self._rng.uniform(-25, 25) + frame * 0.6
            r_pix = 9 + self._rng.uniform(-2, 2)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.50, 0.20, 0.65, 0.55 * fade,
            ).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx + ox - r_pix, cy + oy - r_pix,
                           r_pix * 2, r_pix * 2),
            ).fill()


class _DragonFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.20, 0.55, 0.85, 0.85 * fade,
        ).set()
        path = NSBezierPath.bezierPath()
        for step in range(20):
            t = step / 20.0
            angle = t * math.tau * 1.5 + frame * 0.15
            radius = 6 + t * 28
            x = cx + math.cos(angle) * radius
            y = cy + math.sin(angle) * radius
            if step == 0:
                path.moveToPoint_(NSMakePoint(x, y))
            else:
                path.lineToPoint_(NSMakePoint(x, y))
        path.setLineWidth_(2.5)
        path.stroke()


class _DarkFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        for _ in range(8):
            ox = self._rng.uniform(-22, 22)
            oy = self._rng.uniform(-22, 22)
            r_pix = 10
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.10, 0.05, 0.20, 0.65 * fade,
            ).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx + ox - r_pix, cy + oy - r_pix,
                           r_pix * 2, r_pix * 2),
            ).fill()


class _FightingFX(_TypeFXBase):
    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        # Six radial impact spokes growing outward.
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.95, 0.30, 0.30, 0.95 * fade,
        ).set()
        path = NSBezierPath.bezierPath()
        outer = 12 + frame * 2.5
        inner = 4 + frame * 0.8
        for i in range(6):
            a = (i / 6.0) * math.tau
            path.moveToPoint_(NSMakePoint(
                cx + math.cos(a) * inner, cy + math.sin(a) * inner,
            ))
            path.lineToPoint_(NSMakePoint(
                cx + math.cos(a) * outer, cy + math.sin(a) * outer,
            ))
        path.setLineWidth_(3.0)
        path.stroke()


class _ImpactFX(_TypeFXBase):
    """Generic white starburst — fallback for types without bespoke FX."""

    @objc.python_method
    def drawFrame_fade_(self, frame, fade):  # noqa: N802
        cx, cy = self._center()
        NSColor.colorWithCalibratedWhite_alpha_(0.95, 0.85 * fade).set()
        path = NSBezierPath.bezierPath()
        outer = 8 + frame * 2.0
        for i in range(8):
            a = (i / 8.0) * math.tau
            path.moveToPoint_(NSMakePoint(cx, cy))
            path.lineToPoint_(NSMakePoint(
                cx + math.cos(a) * outer,
                cy + math.sin(a) * outer,
            ))
        path.setLineWidth_(2.0)
        path.stroke()


_FX_REGISTRY: dict[str, type] = {
    "fire": _FireFX,
    "water": _WaterFX,
    "electric": _ElectricFX,
    "grass": _GrassFX,
    "ice": _IceFX,
    "psychic": _PsychicFX,
    "ghost": _GhostFX,
    "dragon": _DragonFX,
    "dark": _DarkFX,
    "fighting": _FightingFX,
}


def make_type_fx(frame, move_type: str, *, seed: int = 0) -> _TypeFXBase:
    """Build (and auto-start) the FX overlay view for a given move type.
    Caller adds it as a subview; the view tears itself down when the
    animation window closes."""
    cls = _FX_REGISTRY.get((move_type or "").lower(), _ImpactFX)
    return cls.alloc().initWithFrame_seed_(frame, seed)

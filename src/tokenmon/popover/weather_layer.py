"""NSTimer-driven background-particle animator for the popover.

Owned by ``TokenmonPopover``. Created on popover-show when ``use_weather``
is on and the current weather snapshot maps to a non-trivial
``ParticleSpec``; torn down on popover-close.

Particle subviews live inside a dedicated host ``NSView`` that's added
to the popover's content container *before* any pane view, so the panes
naturally render above the particles in the z-stack.
"""
from __future__ import annotations

import logging
import math
import random

import objc
from AppKit import NSColor, NSTimer, NSView
from Foundation import NSMakeRect, NSMakeSize, NSObject

from tokenmon.weather import ParticleSpec, WeatherSnapshot

log = logging.getLogger("tokenmon.popover.weather_layer")

_TICK_INTERVAL = 1.0 / 30.0
_FLASH_TICK_DURATION = 4  # ~133 ms at 30 fps

# px / sec of horizontal drift per km/h of wind. At 30 km/h (proper
# breeze) we get ~75 px/sec, which crosses the popover content area in
# a few seconds — visible but not chaotic.
_WIND_DRIFT_PER_KMH = 2.5


def _wind_drift_x(wind_kmh: float, wind_dir_deg: float) -> float:
    """Convert meteorological wind (direction wind blows *from*) to a
    horizontal pixel velocity. Wind from the west (270°) blows toward
    the east → particles drift right (+x)."""
    rad = math.radians(wind_dir_deg)
    # Wind *direction-to* is rotated 180° from the meteorological "from"
    # angle. The east component of that vector is sin(rad + π) = -sin(rad).
    return -math.sin(rad) * wind_kmh * _WIND_DRIFT_PER_KMH


class _Particle:
    """Per-particle state. View ref kept so the animator can re-frame it
    on each tick without touching the AppKit subview list."""

    __slots__ = ("x", "y", "speed_y", "phase", "view")

    def __init__(self, x: float, y: float, speed_y: float, phase: float, view) -> None:
        self.x = x
        self.y = y
        self.speed_y = speed_y
        self.phase = phase
        self.view = view


def _color(rgba: tuple[float, float, float, float]):
    r, g, b, a = rgba
    return NSColor.colorWithRed_green_blue_alpha_(r, g, b, a)


class WeatherParticleAnimator(NSObject):
    """Drive a falling-particle background animation inside a host NSView."""

    def initWithHost_spec_snapshot_(self, host_view, spec, snap):  # noqa: N802
        self = objc.super(WeatherParticleAnimator, self).init()
        if self is None:
            return None
        self._host = host_view
        self._spec: ParticleSpec = spec
        self._snap: WeatherSnapshot = snap
        self._wind_dx: float = _wind_drift_x(snap.wind_kmh, snap.wind_dir_deg)
        self._particles: list[_Particle] = []
        self._timer: NSTimer | None = None
        self._flash_view: NSView | None = None
        self._flash_remaining: int = 0
        self._static_view: NSView | None = None
        return self

    # ---- lifecycle ----

    def start(self) -> None:
        if self._timer is not None or self._static_view is not None:
            return
        bounds = self._host.bounds()
        w = float(bounds.size.width)
        h = float(bounds.size.height)

        if self._spec.static_overlay:
            self._static_view = self._make_overlay(self._spec.color_rgba, alpha_mult=1.0)
            self._host.addSubview_(self._static_view)
            return

        for _ in range(self._spec.count):
            x = random.uniform(0, w)
            y = random.uniform(0, h)
            speed = random.uniform(self._spec.speed_min, self._spec.speed_max)
            phase = random.uniform(0.0, math.tau)
            view = self._make_particle_view(x, y)
            self._host.addSubview_(view)
            self._particles.append(_Particle(x, y, speed, phase, view))

        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _TICK_INTERVAL, self, b"tick:", None, True,
        )

    def stop(self) -> None:
        if self._timer is not None:
            try:
                self._timer.invalidate()
            except Exception:
                log.exception("weather timer invalidate failed")
            self._timer = None
        for p in self._particles:
            try:
                p.view.removeFromSuperview()
            except Exception:
                pass
        self._particles = []
        if self._flash_view is not None:
            try:
                self._flash_view.removeFromSuperview()
            except Exception:
                pass
            self._flash_view = None
        if self._static_view is not None:
            try:
                self._static_view.removeFromSuperview()
            except Exception:
                pass
            self._static_view = None

    # ---- per-tick step ----

    def tick_(self, _timer):  # noqa: N802
        if self._host is None:
            return
        bounds = self._host.bounds()
        w = float(bounds.size.width)
        h = float(bounds.size.height)
        dt = _TICK_INTERVAL
        spec = self._spec

        wind_dx = self._wind_dx
        for p in self._particles:
            p.y -= p.speed_y * dt
            p.x += wind_dx * dt
            x_visual = p.x
            if spec.drift_amplitude > 0:
                p.phase += dt * (math.tau / spec.drift_period)
                x_visual = p.x + math.sin(p.phase) * spec.drift_amplitude

            # Wrap when we leave the host bounds — direction depends on
            # the sign of speed_y (positive = falls, negative = rises).
            falls = spec.speed_min > 0
            if falls and p.y + spec.height < 0:
                p.x = random.uniform(0, w)
                p.y = h + random.uniform(0, 50)
                p.speed_y = random.uniform(spec.speed_min, spec.speed_max)
                x_visual = p.x
            elif not falls and p.y > h + 20:
                p.x = random.uniform(0, w)
                p.y = -spec.height - random.uniform(0, 30)
                p.speed_y = random.uniform(spec.speed_min, spec.speed_max)
                x_visual = p.x

            # Horizontal wrap so wind doesn't sweep every particle off
            # one side. Margin matches the soft-glow halo so the visual
            # pop-in stays at the edge instead of mid-screen.
            margin = 12.0
            if x_visual > w + margin:
                p.x -= w + margin * 2
            elif x_visual < -spec.width - margin:
                p.x += w + margin * 2

            try:
                p.view.setFrame_(NSMakeRect(x_visual, p.y, spec.width, spec.height))
            except Exception:
                pass

        # Thunder flash bookkeeping — rare lightning-style overlay.
        if spec.flash_interval is not None:
            if self._flash_remaining > 0:
                self._flash_remaining -= 1
                if self._flash_remaining == 0 and self._flash_view is not None:
                    try:
                        self._flash_view.removeFromSuperview()
                    except Exception:
                        pass
                    self._flash_view = None
            elif random.random() < dt / spec.flash_interval:
                self._flash_view = self._make_overlay(
                    (1.0, 1.0, 0.95, 0.55), alpha_mult=1.0,
                )
                self._host.addSubview_(self._flash_view)
                self._flash_remaining = _FLASH_TICK_DURATION

    # ---- view factories ----

    def _make_particle_view(self, x: float, y: float) -> NSView:
        spec = self._spec
        v = NSView.alloc().initWithFrame_(NSMakeRect(x, y, spec.width, spec.height))
        v.setWantsLayer_(True)
        layer = v.layer()
        layer.setBackgroundColor_(_color(spec.color_rgba).CGColor())
        # Snow + cloud read better as soft shapes — round the corners up
        # to a reasonable radius without paying for a real bezier path.
        if spec.kind == "snow":
            layer.setCornerRadius_(min(spec.width, spec.height) / 2)
        elif spec.kind == "cloud":
            layer.setCornerRadius_(spec.height / 2)
        if spec.border_rgba is not None:
            layer.setBorderColor_(_color(spec.border_rgba).CGColor())
            layer.setBorderWidth_(0.5)
        # Soft halo — cheap stand-in for a Gaussian blur. The particle's
        # own color leaks outward, giving the edges a fuzzy falloff that
        # reads as "blurry" against translucent pane backgrounds (used
        # in Tokendex / Box scroll views). masksToBounds defaults to
        # False on CALayer, so the shadow extends past the view frame.
        layer.setShadowColor_(_color(spec.color_rgba).CGColor())
        layer.setShadowOpacity_(0.85)
        layer.setShadowRadius_(4.0)
        layer.setShadowOffset_(NSMakeSize(0, 0))
        return v

    def _make_overlay(self, rgba, alpha_mult: float = 1.0) -> NSView:
        bounds = self._host.bounds()
        v = NSView.alloc().initWithFrame_(bounds)
        v.setWantsLayer_(True)
        r, g, b, a = rgba
        v.layer().setBackgroundColor_(
            NSColor.colorWithRed_green_blue_alpha_(r, g, b, a * alpha_mult).CGColor()
        )
        return v

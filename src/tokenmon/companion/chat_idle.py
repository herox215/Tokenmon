"""Ambient idle animation for the companion sprite while the chat
panel is open.

Two layers:

- ``IdleStateMachine`` — pure-Python state + math. Given a clock, an
  anchor, an x-range and a random source it produces the absolute
  ``(x, y)`` window origin at any point in time. No AppKit imports.
  Fully unit-testable: drive it with a fake clock and a seeded RNG.

- ``ChatIdleAnimator`` — thin AppKit shell that owns an NSTimer at
  20 Hz, calls ``state.tick()``, and applies the result via
  ``setFrameOrigin_``. The only side effects are timer + window
  mutation, and they live entirely in this class.

State plan (always running while ``start()``ed):

  BOB     baseline — gentle vertical sine, ±3 px, period 2.4 s
  HOP     one-shot, 0.6 s parabolic arc up to +18 px on y, x untouched
  SHAKE   one-shot, 0.3 s horizontal damped oscillation ±5 px, y untouched
  PACE    one-shot, 0.8 s linear x slide to a random spot inside x_range;
          ``anchor_x`` updates continuously so BOB centres on the new
          position both during and after PACE

Concurrency: BOB's y output is the baseline at all times. HOP overrides
y for its duration. SHAKE overrides x for its duration. PACE overrides
x AND mutates anchor_x for its duration. HOP+SHAKE and HOP+PACE cannot
overlap (the selector only fires when current one-shot is BOB).

Stop semantics: ``stop()`` ends any in-flight one-shot and snaps to the
current anchor so the hand-off to companion_drv's redock starts from a
known, predictable position (not from somewhere mid-HOP arc).
"""
from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

log = logging.getLogger("tokenmon.companion.chat_idle")

# 20 Hz matches the project's existing _WiggleHandler cadence and is
# fast enough that the ±3 px BOB sine looks smooth rather than steppy.
TICK_INTERVAL = 0.05

# BOB — slow breathing motion.
BOB_PERIOD_S = 2.4
BOB_AMPLITUDE_PX = 3.0

# HOP — single ballistic arc.
HOP_DURATION_S = 0.6
HOP_HEIGHT_PX = 18.0

# SHAKE — short damped horizontal oscillation. Damping schedule mirrors
# the existing _WiggleHandler so a SHAKE looks like a small head-shake
# rather than a panic-jitter.
SHAKE_DURATION_S = 0.3
SHAKE_AMPLITUDE_PX = 5.0
SHAKE_CYCLES = 3.0  # number of full ±swings inside the duration

# PACE — horizontal slide to a new x inside the chat panel's range.
PACE_DURATION_S = 0.8

# How often a one-shot action fires while BOB is the active baseline.
# Drawn uniformly from this range at the end of each one-shot (and once
# at start()). Lower bound is high enough that the sprite has time to
# settle visually; upper bound keeps the companion from feeling dead.
NEXT_ACTION_MIN_S = 4.0
NEXT_ACTION_MAX_S = 9.0


class Action(Enum):
    BOB = "bob"
    HOP = "hop"
    SHAKE = "shake"
    PACE = "pace"


# Actions the selector picks from when BOB is the current state.
_ONE_SHOT_CHOICES = (Action.HOP, Action.SHAKE, Action.PACE)


@dataclass
class IdleStateMachine:
    """Pure-Python state for the chat-idle animator.

    Construct with anchor + x_range, then call ``tick(now)`` every
    frame. Returns the absolute window origin to apply that frame.
    """
    anchor_x: float
    anchor_y: float
    x_range: tuple[float, float]
    rng: random.Random = field(default_factory=random.Random)

    # Internal state — initialised by ``reset(t0)`` so tests can drive
    # the machine from a deterministic starting point.
    _t0: float = 0.0
    _state: Action = Action.BOB
    _action_start_t: float = 0.0
    _action_end_t: float = 0.0
    _next_action_t: float = 0.0
    _pace_start_x: float = 0.0
    _pace_target_x: float = 0.0

    def reset(self, now: float) -> None:
        """Restart with BOB as the active state and the next one-shot
        scheduled NEXT_ACTION_MIN..MAX seconds out. Called from
        ``start()`` so the first tick after start picks up clean
        state."""
        self._t0 = now
        self._state = Action.BOB
        self._action_start_t = now
        self._action_end_t = now
        self._next_action_t = now + self._roll_next_action_delay()

    def _roll_next_action_delay(self) -> float:
        return self.rng.uniform(NEXT_ACTION_MIN_S, NEXT_ACTION_MAX_S)

    def _select_next_action(self) -> Action:
        return self.rng.choice(_ONE_SHOT_CHOICES)

    def tick(self, now: float) -> tuple[float, float]:
        """Advance the state machine to ``now`` and return the absolute
        window origin (x, y) to apply this frame.

        Order:
          1. If a one-shot is active and its end has passed, transition
             back to BOB and schedule the next action.
          2. If BOB is active and the next-action deadline has passed,
             start a new one-shot.
          3. Compute (x, y) from the active state + BOB baseline.
        """
        # 1. One-shot completion → BOB + schedule next.
        if self._state != Action.BOB and now >= self._action_end_t:
            if self._state == Action.PACE:
                # PACE finishes by snapping anchor to the exact target
                # so any rounding drift over the lerp doesn't leave us
                # half a pixel off the planned landing spot.
                self.anchor_x = self._pace_target_x
            self._state = Action.BOB
            self._next_action_t = now + self._roll_next_action_delay()

        # 2. Selector tick — only while BOB is the active state.
        if self._state == Action.BOB and now >= self._next_action_t:
            self._start_one_shot(now, self._select_next_action())

        # 3. Render.
        return self._render(now)

    def _start_one_shot(self, now: float, action: Action) -> None:
        self._action_start_t = now
        if action == Action.HOP:
            self._state = Action.HOP
            self._action_end_t = now + HOP_DURATION_S
        elif action == Action.SHAKE:
            self._state = Action.SHAKE
            self._action_end_t = now + SHAKE_DURATION_S
        elif action == Action.PACE:
            self._state = Action.PACE
            self._pace_start_x = self.anchor_x
            self._pace_target_x = self._pick_pace_target()
            self._action_end_t = now + PACE_DURATION_S

    def _pick_pace_target(self) -> float:
        """Pick a new x inside x_range that's far enough from the
        current anchor to actually look like movement (≥ 20 px). If
        x_range is too narrow for that, just pick anything in range —
        a tiny pace is better than no motion."""
        lo, hi = self.x_range
        lo = float(lo)
        hi = float(hi)
        if hi - lo < 1.0:
            return self.anchor_x
        for _ in range(6):
            candidate = self.rng.uniform(lo, hi)
            if abs(candidate - self.anchor_x) >= 20.0:
                return candidate
        # Fallback after a few rejected samples — happens when the
        # current anchor sits in the middle of a narrow range. Bias to
        # whichever side gives more travel.
        return lo if (self.anchor_x - lo) < (hi - self.anchor_x) else hi

    def _render(self, now: float) -> tuple[float, float]:
        t = now - self._t0
        # Y baseline — BOB sine, always-on.
        y = self.anchor_y + BOB_AMPLITUDE_PX * math.sin(
            2.0 * math.pi * t / BOB_PERIOD_S,
        )
        x = self.anchor_x

        if self._state == Action.HOP:
            # Y overrides during HOP. Half-sine from 0 → +H → 0.
            elapsed = now - self._action_start_t
            phase = math.pi * elapsed / HOP_DURATION_S
            y = self.anchor_y + HOP_HEIGHT_PX * math.sin(phase)
        elif self._state == Action.SHAKE:
            # X oscillation with linear damping toward zero so the last
            # swing is the smallest. At elapsed == SHAKE_DURATION the
            # offset is exactly 0 — important so BOB resumes centred.
            elapsed = now - self._action_start_t
            decay = max(0.0, 1.0 - elapsed / SHAKE_DURATION_S)
            phase = 2.0 * math.pi * SHAKE_CYCLES * elapsed / SHAKE_DURATION_S
            x = self.anchor_x + SHAKE_AMPLITUDE_PX * decay * math.sin(phase)
        elif self._state == Action.PACE:
            # Linear lerp from pace_start_x to pace_target_x, and
            # advance anchor_x continuously so BOB's y centring tracks
            # the slide rather than snapping at the end.
            elapsed = now - self._action_start_t
            progress = min(1.0, elapsed / PACE_DURATION_S)
            x = self._pace_start_x + (self._pace_target_x - self._pace_start_x) * progress
            self.anchor_x = x

        return x, y

    def stop_position(self) -> tuple[float, float]:
        """The position to snap to on stop — the current anchor. PACE
        may have moved anchor_x partway through a slide; we accept
        wherever we got to rather than reverting to the original
        anchor, which would look like a teleport."""
        return self.anchor_x, self.anchor_y


# ---------------------------------------------------------------------------
# AppKit shell
# ---------------------------------------------------------------------------

try:
    import objc
    from AppKit import NSObject, NSTimer
    _APPKIT_AVAILABLE = True
except Exception:  # pragma: no cover — only fires on non-macOS / stripped builds
    _APPKIT_AVAILABLE = False


if _APPKIT_AVAILABLE:

    class ChatIdleAnimator(NSObject):  # type: ignore[misc]
        """Drives ambient sprite motion while pinned to the chat panel.

        Lifecycle::

            anim = ChatIdleAnimator.alloc().initWithWindow_anchor_xRange_(
                sprite_window, (anchor_x, anchor_y), (x_lo, x_hi),
            )
            anim.start()
            ...
            anim.stop()  # snaps to anchor

        Safe to re-``start()`` after ``stop()`` (e.g. on chat reattach)
        — each start invalidates any prior timer and resets the state
        machine, so we don't leak NSTimers.
        """

        # initWithWindow_anchor_xRange_(window, anchor, x_range, rng=None)
        def initWithWindow_anchor_xRange_(self, window, anchor, x_range):  # noqa: N802
            self = objc.super(ChatIdleAnimator, self).init()
            if self is None:
                return None
            self._window = window
            self._state = IdleStateMachine(
                anchor_x=float(anchor[0]),
                anchor_y=float(anchor[1]),
                x_range=(float(x_range[0]), float(x_range[1])),
            )
            self._timer = None
            return self

        def anchor(self) -> tuple[float, float]:
            """Current (anchor_x, anchor_y). External callers (e.g.
            ``PokemonOverlay.wiggle``) read this when they need a
            stable origin instead of the live, possibly-mid-PACE
            window frame."""
            return self._state.anchor_x, self._state.anchor_y

        def start(self) -> None:
            self.stop()  # idempotent: drop any prior timer first
            self._state.reset(time.monotonic())
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                TICK_INTERVAL, self, b"tick:", None, True,
            )

        def stop(self) -> None:
            if self._timer is not None:
                try:
                    self._timer.invalidate()
                except Exception:
                    log.exception("chat-idle timer invalidate failed")
                self._timer = None
            # Snap the window back to the anchor so a subsequent
            # hand-off (e.g. companion_drv's redock callback) starts
            # from a known position, not from mid-arc.
            if self._window is not None:
                try:
                    x, y = self._state.stop_position()
                    self._window.setFrameOrigin_((x, y))
                except Exception:
                    log.exception("chat-idle snap-to-anchor failed")

        def tick_(self, _timer):  # noqa: N802
            if self._window is None:
                return
            try:
                x, y = self._state.tick(time.monotonic())
                self._window.setFrameOrigin_((x, y))
            except Exception:
                log.exception("chat-idle tick failed")
                # Fail safe: cancel the timer so we don't spam logs.
                self.stop()

else:  # pragma: no cover
    class ChatIdleAnimator:  # type: ignore[no-redef]
        """No-op fallback on non-macOS builds — keeps imports working
        in headless test environments. The pure ``IdleStateMachine``
        is the part tests exercise."""

        @classmethod
        def alloc(cls):
            return cls()

        def initWithWindow_anchor_xRange_(self, *_a, **_kw):  # noqa: N802
            return self

        def anchor(self):
            return (0.0, 0.0)

        def start(self):
            pass

        def stop(self):
            pass

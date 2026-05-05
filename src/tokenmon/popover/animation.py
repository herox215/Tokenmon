"""Animation step builders + NSTimer-driven step runners.

Both ``_CatchAnimationHandler`` and ``_PatHandler`` share the same
"flat list of (delay, action) tuples consumed one one-shot timer at a
time" pattern. They could be collapsed onto a shared base class but
keeping the code flat trades a tiny duplication for an easier read.

The handlers don't know what each action *does* — they only call back
into the popover (``popover._catch_step(action, payload)`` /
``popover._pat_step(action)``), keeping all view mutation in TokenmonPopover.
"""
from __future__ import annotations

import logging

import objc
from AppKit import NSTimer
from Foundation import NSObject

from tokenmon.ui_helpers import AFFECTION_MAX

log = logging.getLogger("tokenmon.popover.animation")


# --- Catch animation tunables --------------------------------------------

CATCH_BALL_SIZE = 40
CATCH_THROW_FRAMES = 3
CATCH_WOBBLE_DX = 14
CATCH_REST_DROP_PX = 24


def _build_catch_steps(caught: bool, shakes: int) -> list[tuple[float, str]]:
    """Construct the (delay, action) tape played by ``_CatchAnimationHandler``.

    ``shakes`` is 0..3. ``caught`` only affects the outcome cap (``click``
    vs ``burst``). Total runtime scales linearly with ``shakes``: a 0-shake
    break-out feels rapidly disappointing; a 3-shake catch feels suspenseful.
    """
    steps: list[tuple[float, str]] = [
        (0.00, "throw_start"),
        (0.10, "throw_arc_1"),
        (0.10, "throw_arc_2"),
        (0.10, "throw_arc_3"),
        (0.05, "absorb_flash"),
        (0.12, "flash_end"),
        (0.20, "ball_drop"),
    ]
    for k in range(int(shakes)):
        steps.extend([
            (0.55, f"shake_left_{k}"),
            (0.16, f"shake_right_{k}"),
            (0.16, f"shake_centre_{k}"),
        ])
    if caught:
        steps.extend([
            (0.55, "click"),
            (0.10, "caught_announce"),
            (0.25, "caught_sparkle_1"),
            (0.18, "caught_sparkle_2"),
            (0.18, "caught_sparkle_3"),
            (0.18, "caught_sparkle_4"),
            (1.20, "caught_hold"),
            (0.30, "done"),
        ])
    else:
        steps.extend([(0.55, "burst"), (0.25, "done")])
    return steps


class _CatchAnimationHandler(NSObject):
    """NSTimer target driving the catch animation step-by-step."""

    def initWithPopover_payload_(self, popover, payload):  # noqa: N802
        self = objc.super(_CatchAnimationHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._payload = payload
        self._steps = _build_catch_steps(
            bool(payload.get("caught", False)),
            int(payload.get("shakes", 0)),
        )
        self._idx = 0
        return self

    def start(self):
        self._scheduleNext()

    def _scheduleNext(self):
        if self._idx >= len(self._steps):
            return
        delay, _ = self._steps[self._idx]
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.001, delay), self, b"fire:", None, False,
        )

    def fire_(self, _timer):  # noqa: N802
        if self._idx >= len(self._steps):
            return
        _, action = self._steps[self._idx]
        self._idx += 1
        try:
            self._popover._catch_step(action, self._payload)
        except Exception:
            log.exception("catch step %s failed", action)
            try:
                self._popover._end_catch_animation(self._payload)
            except Exception:
                log.exception("catch animation teardown failed")
            return
        self._scheduleNext()


# --- Drop-claim animation ------------------------------------------------


def build_claim_steps(
    ordered: list[tuple[str, int]],
) -> list[tuple[float, str]]:
    """Stagger: each item drops over 3 frames; next item starts after a
    short delay; final hold then auto-claim.

    Pure — extracted from ``TokenmonPopover._build_claim_steps`` so the
    Items-pane controller can build its tape without instance state.
    """
    steps: list[tuple[float, str]] = []
    for i, _ in enumerate(ordered):
        steps.extend([
            (0.10, f"drop_{i}_1"),
            (0.06, f"drop_{i}_2"),
            (0.06, f"drop_{i}_3"),
        ])
    steps.append((0.80, "done"))
    return steps


# --- Pat animation -------------------------------------------------------

PAT_HOP_PX = 10
PAT_HEART_THRESHOLD = int(0.9 * AFFECTION_MAX)  # 90% of 255 = 229


def _build_pat_steps(with_hearts: bool) -> list[tuple[float, str]]:
    """Two-bounce sequence; hearts appear at each bounce apex when the
    affection threshold is met."""
    if with_hearts:
        return [
            (0.00, "hop_up"),
            (0.05, "heart_1"),
            (0.10, "hop_down"),
            (0.10, "hop_up"),
            (0.04, "heart_2"),
            (0.06, "heart_3"),
            (0.10, "hop_down"),
            (0.04, "heart_4"),
            (0.06, "heart_5"),
            (0.90, "done"),
        ]
    return [
        (0.00, "hop_up"),
        (0.14, "hop_down"),
        (0.10, "hop_up"),
        (0.14, "hop_down"),
        (0.20, "done"),
    ]


class _PatHandler(NSObject):
    """NSTimer-driven step runner — same pattern as _CatchAnimationHandler."""

    def initWithPopover_steps_(self, popover, steps):  # noqa: N802
        self = objc.super(_PatHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._steps = list(steps)
        self._idx = 0
        return self

    def start(self):
        self._scheduleNext()

    def _scheduleNext(self):
        if self._idx >= len(self._steps):
            return
        delay, _ = self._steps[self._idx]
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.001, delay), self, b"fire:", None, False,
        )

    def fire_(self, _timer):  # noqa: N802
        if self._idx >= len(self._steps):
            return
        _, action = self._steps[self._idx]
        self._idx += 1
        try:
            self._popover._pat_step(action)
        except Exception:
            log.exception("pat step %s failed", action)
            try:
                self._popover._end_pat()
            except Exception:
                log.exception("pat teardown failed")
            return
        self._scheduleNext()


# --- Reveal timer + claim-animation runner -------------------------------


class _RevealTimerHandler(NSObject):
    """Fires once after the catch-reveal hold to dismiss the encounter pane."""

    def initWithPopover_(self, popover):  # noqa: N802
        self = objc.super(_RevealTimerHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        return self

    def fire_(self, _timer):  # noqa: N802
        try:
            # Late import: PANE_POKEMON lives in popover.widgets to avoid a
            # circular import (animation -> _main).
            from tokenmon.popover.widgets import PANE_POKEMON
            self._popover._show_pane(PANE_POKEMON)
        except Exception:
            log.exception("reveal teardown failed")


class _ClaimAnimationHandler(NSObject):
    """NSTimer-driven step runner for the items-claim animation. Same
    pattern as _CatchAnimationHandler / _PatHandler.

    The popover knows how to interpret each (delay, action) step — this
    object just paces them."""

    def initWithPopover_steps_(self, popover, steps):  # noqa: N802
        self = objc.super(_ClaimAnimationHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._steps = list(steps)
        self._idx = 0
        return self

    def start(self):
        self._scheduleNext()

    def _scheduleNext(self):
        if self._idx >= len(self._steps):
            return
        delay, _ = self._steps[self._idx]
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.001, delay), self, b"fire:", None, False,
        )

    def fire_(self, _timer):  # noqa: N802
        if self._idx >= len(self._steps):
            return
        _, action = self._steps[self._idx]
        self._idx += 1
        try:
            self._popover._claim_step(action)
        except Exception:
            log.exception("claim step %s failed", action)
            try:
                self._popover._end_drop_claim_animation()
            except Exception:
                log.exception("claim teardown failed")
            return
        self._scheduleNext()

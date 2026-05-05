"""Catch-animation pseudo-pane: silhouette + Pokeball arc + wobble + flash.

Not a sidebar-selectable pane; the encounter row's "throw" action calls
``TokenmonPopover._begin_catch_animation``, which installs a
``CatchAnimationController`` as the active controller and replaces the
content view directly (bypasses ``_show_pane`` so the encounter sidebar
slot stays put for the duration).

A pure ``compute_ball_position`` helper drives the ball-x/y for shake
and rest frames — extracted so the geometry math has a unit-test target.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSColor,
    NSFont,
    NSImage,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import items, items_remote, pokemon
from tokenmon.overlay import _silhouette_image
from tokenmon.popover.animation import (
    CATCH_BALL_SIZE,
    CATCH_REST_DROP_PX,
    CATCH_THROW_FRAMES,
    CATCH_WOBBLE_DX,
    _CatchAnimationHandler,
)
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_ENCOUNTER,
    POPOVER_HEIGHT,
    _crisp_image_view,
    _label,
)

log = logging.getLogger("tokenmon.popover.panes.catch_animation")


def compute_ball_position(
    action: str, geom: dict,
) -> tuple[float, float] | None:
    """Pure geometry helper used by the step dispatcher to decide where
    the ball should sit for a given step.

    Returns (x, y) for ``shake_*`` and ``rest`` actions. Returns None for
    actions that have no fixed position (``throw_arc_*`` reads from the
    pre-computed arc table; ``absorb_flash`` jumps to the absorb point).
    Pure — no view mutation, no NSColor calls.
    """
    if action == "rest" or action == "ball_drop":
        return geom["rest_x"], geom["rest_y"]
    if action.startswith("shake_left"):
        return geom["rest_x"] - CATCH_WOBBLE_DX, geom["rest_y"] + 2
    if action.startswith("shake_right"):
        return geom["rest_x"] + CATCH_WOBBLE_DX, geom["rest_y"] + 2
    if action.startswith("shake_centre"):
        return geom["rest_x"], geom["rest_y"]
    if action == "absorb_flash":
        return geom["absorb_x"], geom["absorb_y"]
    return None


class CatchAnimationController(PaneController):
    """Owns the catch-animation pane, geometry and step dispatcher."""

    def __init__(self, popover, payload: dict) -> None:
        super().__init__(popover)
        self._payload = payload
        self._silhouette = None
        self._ball = None
        self._flash = None
        self._pane = None
        self._geom: dict | None = None
        self._header: NSTextField | None = None
        self._sparkles: list[NSTextField] = []

    # The controller is created inside ``begin`` rather than via _show_pane,
    # because the catch animation never goes through pane-routing (it
    # replaces the encounter pane content in-place).
    def begin(self) -> None:
        pop = self.popover
        view = self._build_view()
        if pop._current_pane_view is not None:
            pop._current_pane_view.removeFromSuperview()
        view.setFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        pop._content_container.addSubview_(view)
        pop._current_pane_view = view
        self._pane = view

        handler = (
            _CatchAnimationHandler.alloc()
            .initWithPopover_payload_(pop, self._payload)
        )
        self._handlers.append(handler)
        # Stash on popover so _show_pane can clear the strong ref later.
        pop._catch_anim_handler = handler
        handler.start()

    def _build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        header_y = POPOVER_HEIGHT - 40
        header = _label(
            NSMakeRect(16, header_y, CONTENT_WIDTH - 32, 22),
            "A wild Pokemon appeared!",
            font=NSFont.boldSystemFontOfSize_(15),
            align=NSTextAlignmentCenter,
        )
        view.addSubview_(header)
        self._header = header

        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = header_y - sprite_size - 8
        sil_iv = _crisp_image_view(
            NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size)
        )
        species_dex_id = int(self._payload["species_dex_id"])
        sp = pokemon.ensure_sprite(species_dex_id)
        if sp is not None and sp.exists():
            base = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if base is not None:
                sil = _silhouette_image(base, NSColor.whiteColor())
                sil_iv.setImage_(sil)
                sil_iv.setAnimates_(True)
        view.addSubview_(sil_iv)
        self.popover._animated_image_views.append(sil_iv)
        self._silhouette = sil_iv

        ball = _crisp_image_view(NSMakeRect(
            CONTENT_WIDTH + CATCH_BALL_SIZE,
            sprite_y + sprite_size,
            CATCH_BALL_SIZE, CATCH_BALL_SIZE,
        ))
        item = items.get(self._payload["item_key"])
        if item is not None:
            ball_img = items_remote.get_item_image(item)
            if ball_img is not None:
                ball.setImage_(ball_img)
        ball.setHidden_(True)
        view.addSubview_(ball)
        self._ball = ball

        sprite_centre_x = sprite_x + sprite_size // 2
        sprite_centre_y = sprite_y + sprite_size // 2
        rest_x = sprite_centre_x - CATCH_BALL_SIZE // 2
        rest_y = sprite_y + CATCH_REST_DROP_PX
        absorb_x = sprite_centre_x - CATCH_BALL_SIZE // 2
        absorb_y = sprite_centre_y - CATCH_BALL_SIZE // 2
        start_x = CONTENT_WIDTH + CATCH_BALL_SIZE
        start_y = sprite_y + sprite_size + 40
        arc_frames: list[tuple[int, int]] = []
        for i in range(1, CATCH_THROW_FRAMES + 1):
            t = i / float(CATCH_THROW_FRAMES)
            x = int(start_x + (absorb_x - start_x) * t)
            arc_y = start_y + (absorb_y - start_y) * t
            lift = -28 * (4 * t * (1 - t))
            arc_frames.append((x, int(arc_y + lift)))
        self._geom = {
            "rest_x": rest_x,
            "rest_y": rest_y,
            "absorb_x": absorb_x,
            "absorb_y": absorb_y,
            "arc_frames": arc_frames,
            "ball_size": CATCH_BALL_SIZE,
        }

        sparkle_specs: list[tuple[str, int, int, int]] = [
            ("✨", -22, CATCH_BALL_SIZE - 4, 22),
            ("⭐", CATCH_BALL_SIZE + 4, CATCH_BALL_SIZE - 12, 18),
            ("✨", -14, -16, 16),
            ("⭐", CATCH_BALL_SIZE - 6, -10, 20),
        ]
        sparkles: list[NSTextField] = []
        for ch, dx, dy, sz in sparkle_specs:
            sp_label = _label(
                NSMakeRect(rest_x + dx, rest_y + dy, sz + 8, sz + 8),
                ch,
                font=NSFont.systemFontOfSize_(sz),
                align=NSTextAlignmentCenter,
            )
            sp_label.setHidden_(True)
            view.addSubview_(sp_label)
            sparkles.append(sp_label)
        self._sparkles = sparkles
        return view

    # ---- step dispatcher ---------------------------------------------

    def step(self, action: str, payload: dict) -> None:
        """Mutate the per-frame view positions for one animation step."""
        # ``done`` is the only step that doesn't touch views — handle it
        # before the safety check so the catch flow still terminates even
        # if the user navigated away mid-animation.
        if action == "done":
            self.end(payload)
            return

        ball = self._ball
        sil = self._silhouette
        geom = self._geom or {}
        if ball is None or sil is None or not geom:
            return  # view torn down; remaining timer fires no-op out

        size = geom["ball_size"]

        if action == "throw_start":
            ball.setHidden_(False)
            return

        if action.startswith("throw_arc_"):
            idx = int(action.rsplit("_", 1)[-1]) - 1
            arc = geom["arc_frames"]
            if 0 <= idx < len(arc):
                x, y = arc[idx]
                ball.setFrame_(NSMakeRect(x, y, size, size))
            return

        if action == "absorb_flash":
            ball.setFrame_(NSMakeRect(
                geom["absorb_x"], geom["absorb_y"], size, size,
            ))
            sil.setHidden_(True)
            self._show_flash()
            return

        if action == "flash_end":
            self._hide_flash()
            return

        if action == "ball_drop":
            pos = compute_ball_position(action, geom)
            if pos is not None:
                ball.setFrame_(NSMakeRect(pos[0], pos[1], size, size))
            return

        if action.startswith("shake_"):
            pos = compute_ball_position(action, geom)
            if pos is not None:
                ball.setFrame_(NSMakeRect(pos[0], pos[1], size, size))
            return

        if action == "click":
            self._show_flash()
            return

        if action == "caught_announce":
            self._hide_flash()
            if self._header is not None:
                self._header.setStringValue_("Gotcha!")
                self._header.setTextColor_(
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        1.0, 0.85, 0.0, 1.0,
                    )
                )
            return

        if action.startswith("caught_sparkle_"):
            idx = int(action.rsplit("_", 1)[-1]) - 1
            if 0 <= idx < len(self._sparkles):
                self._sparkles[idx].setHidden_(False)
            return

        if action == "caught_hold":
            return

        if action == "burst":
            ball.setHidden_(True)
            sil.setHidden_(False)
            return

    def _show_flash(self) -> None:
        if self._pane is None or self._flash is not None:
            return
        bounds = self._pane.bounds()
        flash = NSView.alloc().initWithFrame_(bounds)
        flash.setWantsLayer_(True)
        flash.layer().setBackgroundColor_(NSColor.whiteColor().CGColor())
        self._pane.addSubview_(flash)
        self._flash = flash

    def _hide_flash(self) -> None:
        if self._flash is None:
            return
        try:
            self._flash.removeFromSuperview()
        except Exception:
            log.exception("flash teardown failed")
        self._flash = None

    def end(self, payload: dict) -> None:
        """Final step — drop animation refs and route to the outcome pane."""
        pop = self.popover
        pop._catch_anim_handler = None
        self._silhouette = None
        self._ball = None
        self._pane = None
        self._geom = None
        self._header = None
        self._sparkles = []
        self._hide_flash()

        if payload.get("caught"):
            # Hand off to the encounter pane's reveal flow.
            pop._begin_catch_reveal()
        else:
            pop._encounter_bag_open = True
            pop._show_pane(PANE_ENCOUNTER)

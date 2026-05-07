"""Menubar status-bar icon plumbing — image setting, tooltip, sprite animator,
and the one-shot button-wire handler that hooks the rumps status item up to
our popover.

These helpers are file-disjoint from the rest of TokenmonApp's logic. They
take ``app`` (a ``TokenmonApp`` instance) as the first argument and read or
mutate ``app._statusbar_button()`` / ``app._animator`` / ``app._popover`` /
``app._nsapp`` directly. State stays on ``TokenmonApp`` so PyObjC GC anchors
remain stable.
"""
from __future__ import annotations

import logging
from datetime import date

import objc
from AppKit import NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp
from Foundation import NSObject

from tokenmon import box, pokemon
from tokenmon.menubar_sprite import SpriteAnimator
from tokenmon.storage import query_xp_for_date, query_xp_for_pokemon

TZ = "Europe/Berlin"

log = logging.getLogger("tokenmon.menubar.icon")


class _ButtonWireHandler(NSObject):
    """One-shot NSTimer target that wires the status-bar button to our popover.
    Has to fire AFTER applicationDidFinishLaunching, since rumps installs its
    own NSMenu on the status item there. Scheduling a 0.1 s timer from
    __init__ guarantees we run after launch finishes."""

    def initWithApp_(self, app):  # noqa: N802
        self = objc.super(_ButtonWireHandler, self).init()
        if self is None:
            return None
        self._app = app
        return self

    def wire_(self, _timer):  # noqa: N802
        try:
            btn = statusbar_button(self._app)
            if btn is None or self._app._popover is None:
                return
            try:
                self._app._nsapp.nsstatusitem.setMenu_(None)
            except Exception:
                log.exception("setMenu_(None) failed")
            # Receive both left and right mouse-up events so the popover can
            # be shown on left-click and a fallback context menu on right.
            try:
                btn.cell().sendActionOn_(
                    NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp
                )
            except Exception:
                log.exception("sendActionOn_ failed")
            btn.setTarget_(self._app._popover)
            # IMPORTANT: the popover's buttonClicked_ must be decorated with
            # @objc.IBAction. Without that, pyobjc's auto-bridged selector
            # registers OK (respondsToSelector_ returns True) and performClick_
            # works, but real mouse events don't dispatch through it.
            btn.setAction_("buttonClicked:")
        except Exception:
            log.exception("button wiring failed")


def statusbar_button(app):
    try:
        return app._nsapp.nsstatusitem.button()
    except (AttributeError, Exception):
        return None


def set_menubar_image(app, img) -> None:
    btn = statusbar_button(app)
    if btn is not None:
        btn.setImage_(img)


def update_tooltip(app) -> None:
    btn = statusbar_button(app)
    if btn is None:
        return
    try:
        active = box.get_active_pokemon()
        if active is not None:
            xp = query_xp_for_pokemon(active.id)
        else:
            xp = query_xp_for_date(date.today(), TZ)
    except Exception:
        xp = 0
    rate = pokemon.growth_rate_of(app._line_base_id)
    level, into, needed = pokemon.level_from_xp(xp, rate)
    name = pokemon.display_name(
        active.nickname if active is not None else None,
        app._pokemon_dex_id,
    )
    if level >= pokemon.MAX_LEVEL:
        tooltip = f"{name} — Lv MAX • {xp:,} XP"
    else:
        tooltip = f"{name} — Lv {level} • {into:,}/{needed:,} XP"
    btn.setToolTip_(tooltip)


def stop_animator(app, *, clear_image: bool = True) -> None:
    if app._animator is not None:
        app._animator.stop()
        app._animator = None
    if clear_image:
        set_menubar_image(app, None)


def start_animator(app) -> None:
    # Don't blank the button while we're swapping animators — the new
    # animator's first frame paints synchronously when its init runs, and
    # any None state in between would cause the status-bar button to
    # collapse to its empty width and shift the open popover.
    stop_animator(app, clear_image=False)
    if not app._show_pokemon or app._pokemon_sprite is None or not app._pokemon_sprite.exists():
        set_menubar_image(app, None)
        return
    try:
        anim = SpriteAnimator.alloc().initWithGifPath_setter_(
            str(app._pokemon_sprite),
            lambda img: set_menubar_image(app, img),
        )
        app._animator = anim
    except Exception:
        log.exception("failed to start sprite animator")
        app._animator = None


def sync_menubar_icon(app) -> None:
    """Reconcile the title + image with the current state."""
    if app._show_pokemon and app._pokemon_sprite is not None and app._pokemon_sprite.exists():
        start_animator(app)
    else:
        stop_animator(app)

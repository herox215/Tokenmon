"""Companion-overlay driver — input + active-app observers, window docking,
and the proximity fade tick.

Companion mode follows the user's foreground app (NSWorkspace activate
notifications) and global input (every key/click/scroll, throttled). When
either fires we re-dock to the focused window's bottom-right and re-
evaluate orientation (front vs. back sprite). The 5 s ``activity_poll``
covers same-app drift (window dragged, resized, migrated to another
screen).

State stays on TokenmonApp:
  - ``app._companion_mode`` / ``app._overlay``
  - ``app._input_monitor`` / ``app._active_app_observer``  (PyObjC GC anchors)
  - ``app._chat_hotkey`` (Carbon RegisterEventHotKey ref for ⌘⇧Space)
  - ``app._last_dock_rect`` / ``app._last_dock_check_mono`` (drift detection
    + per-keystroke throttle)
"""
from __future__ import annotations

import logging

log = logging.getLogger("tokenmon.menubar.companion_drv")


def proximity_tick(app) -> None:
    """Fade the companion overlay when the cursor approaches so it
    doesn't sit in front of whatever the user is trying to click.
    Companion mode still accepts a double-click on the sprite to open
    the mock session chat."""
    if not app._companion_mode or not app._overlay.visible:
        if app._overlay._proximity_alpha < 1.0:
            app._overlay.set_proximity_alpha(1.0)
        return
    if app._overlay._window is None:
        return
    try:
        from AppKit import NSEvent
        from tokenmon.companion.proximity import proximity_alpha
        loc = NSEvent.mouseLocation()
        frame = app._overlay._window.frame()
        cx = float(frame.origin.x) + float(frame.size.width) / 2.0
        cy = float(frame.origin.y) + float(frame.size.height) / 2.0
        dx = float(loc.x) - cx
        dy = float(loc.y) - cy
        distance = (dx * dx + dy * dy) ** 0.5
        app._overlay.set_proximity_alpha(proximity_alpha(distance))
    except Exception:
        log.exception("proximity tick failed")


def install_input_monitor(app) -> None:
    if app._input_monitor is not None:
        return
    try:
        from tokenmon.companion.input_monitor import InputActivityMonitor
        # Each input event triggers two cheap checks:
        #  * _tick_orientation early-exits when sprite + side are
        #    already correct, so per-keystroke cost is a comparison.
        #  * _tick_dock with a 200 ms throttle so cmd-` and clicks on
        #    other windows reposition us almost immediately, but
        #    sustained typing doesn't keep hammering the window list.
        mon = InputActivityMonitor(on_input=lambda: on_input_event(app))
        mon.start()
        mon.mark_input_now()
        app._input_monitor = mon
    except Exception:
        log.exception("install input monitor failed")


def on_input_event(app) -> None:
    """Called from the global input monitor on every key/click/scroll.
    Bumps orientation immediately and (throttled) re-checks the dock
    target so we follow same-app window switches with ~200 ms latency
    instead of waiting up to 5 s for the periodic tick."""
    from tokenmon.menubar import ticks
    try:
        ticks.tick_orientation(app)
    except Exception:
        log.exception("orientation tick failed")
    try:
        ticks.tick_dock(app, throttle_s=0.2)
    except Exception:
        log.exception("dock tick failed")


def uninstall_input_monitor(app) -> None:
    mon = app._input_monitor
    if mon is None:
        return
    try:
        mon.stop()
    except Exception:
        log.exception("stop input monitor failed")
    app._input_monitor = None


def install_chat_hotkey(app) -> None:
    """Register ⌘⇧Space as a system-wide hotkey that toggles the
    companion chat. The hotkey lives on the app for as long as
    companion mode is on — `toggle_companion` unregisters it when the
    feature is switched off so we don't keep eating ⌘⇧Space when the
    chat panel is unreachable anyway."""
    if getattr(app, "_chat_hotkey", None) is not None:
        return
    try:
        from tokenmon.companion.hotkey import (
            GlobalHotKey,
            cmdKey,
            kVK_Space,
            shiftKey,
        )

        def _on_press() -> None:
            try:
                app._overlay.toggle_chat()
            except Exception:
                log.exception("hotkey toggle_chat failed")

        hk = GlobalHotKey(kVK_Space, cmdKey | shiftKey, on_press=_on_press)
        if hk.start():
            app._chat_hotkey = hk
    except Exception:
        log.exception("install chat hotkey failed")


def uninstall_chat_hotkey(app) -> None:
    hk = getattr(app, "_chat_hotkey", None)
    if hk is None:
        return
    try:
        hk.stop()
    except Exception:
        log.exception("stop chat hotkey failed")
    app._chat_hotkey = None


def install_active_app_observer(app) -> None:
    if app._active_app_observer is not None:
        return
    try:
        from tokenmon.companion.active_app import ActiveAppObserver
        obs = ActiveAppObserver.alloc().initWithCallback_(
            lambda bid: on_active_app_changed(app, bid),
        )
        obs.start()
        app._active_app_observer = obs
    except Exception:
        log.exception("install active-app observer failed")


def uninstall_active_app_observer(app) -> None:
    obs = app._active_app_observer
    if obs is None:
        return
    try:
        obs.stop()
    except Exception:
        log.exception("stop active-app observer failed")
    app._active_app_observer = None


def current_bundle_id_safe(app) -> str | None:
    try:
        from tokenmon.companion.active_app import current_bundle_id
        return current_bundle_id()
    except Exception:
        log.exception("current_bundle_id failed")
        return None


def on_active_app_changed(app, _bundle_id: str | None) -> None:
    """When the foreground app changes, immediately dock to the new
    app's bottom-left window edge (animated) and re-evaluate
    orientation. The 5-s ``_tick_dock`` then keeps the position in
    sync as the user drags the window around or switches between
    windows of the same app (which doesn't fire this notification)."""
    if not app._companion_mode:
        return
    from tokenmon.menubar import ticks
    try:
        dock_to_focused_window(app, force=True)
    except Exception:
        log.exception("dock to focused window failed")
    try:
        ticks.tick_orientation(app, force=True)
    except Exception:
        log.exception("orientation tick after app change failed")


def dock_to_focused_window(
    app, *, force: bool = False, slide_duration: float | None = None,
) -> None:
    """Slide the overlay to the bottom-RIGHT of the focused window —
    a fixed anchor that doesn't move between engaged and idle
    states. Engagement is communicated by sprite orientation
    (front/back) AND by horizontal mirror: from the right anchor
    the un-mirrored back sprite would face right (away from
    content), so we mirror it to face left toward the window.

    Multi-monitor: we explicitly do NOT clamp negative x — a screen
    left of the primary has negative x in both CG and AppKit. We DO
    verify the target sits on a connected screen, otherwise corner-
    fallback.

    Cross-screen moves use ``animate=False`` because NSWindow's
    animated setFrame can glitch when the target frame is on a
    different display than the current one.

    ``force=True`` re-issues the move even if the rect is unchanged.

    While the companion chat is open the sprite is pinned to the
    top-right of that panel (see ``PokemonOverlay._dock_sprite_to_chat``).
    Skip all docking in that mode so the periodic _tick_dock /
    on_input redocks don't yank the sprite back down. The pin is
    cleared by ``hide_chat``, which also fires the redock callback
    we install in _main.py for an immediate hand-off.
    """
    if getattr(app._overlay, "_sprite_pinned_to_chat", False):
        return
    try:
        from tokenmon.companion.window_geom import (
            focused_window_bounds, frontmost_pid, screen_containing_point,
        )
    except Exception:
        log.exception("window_geom import failed")
        return
    pid = frontmost_pid()
    if pid is None:
        if app._last_dock_rect is not None or force:
            app._overlay.move_to_corner(animate=True, slide_duration=slide_duration)
            app._last_dock_rect = None
        return
    rect = focused_window_bounds(pid)
    if rect is None:
        if app._last_dock_rect is not None or force:
            app._overlay.move_to_corner(animate=True, slide_duration=slide_duration)
            app._last_dock_rect = None
        return
    if not force and rect == app._last_dock_rect:
        return
    sprite_size = app._overlay._size
    # Bottom-right of the focused window with 4 px inset so the
    # sprite doesn't ride the macOS window-shadow gradient.
    target_x = rect.x + rect.width - sprite_size - 4
    target_y = rect.y
    # Verify the target sits on a real connected screen.
    anchor_x = target_x + sprite_size / 2
    anchor_y = target_y + 1
    target_screen = screen_containing_point(anchor_x, anchor_y)
    if target_screen is None:
        log.warning(
            "dock target (%s, %s) is off all screens; falling back to corner",
            target_x, target_y,
        )
        app._overlay.move_to_corner(animate=True, slide_duration=slide_duration)
        app._last_dock_rect = None
        return
    try:
        current_screen = app._overlay._window.screen() if app._overlay._window else None
    except Exception:
        current_screen = None
    animate = (current_screen is not None and current_screen == target_screen)
    # Cross-screen moves disable animation entirely (see docstring), so the
    # slide_duration override only kicks in for same-screen moves — which is
    # the common case anyway when handing off from a closing chat panel.
    app._overlay.move_to(
        target_x, target_y,
        animate=animate,
        slide_duration=slide_duration if animate else None,
    )
    app._last_dock_rect = rect

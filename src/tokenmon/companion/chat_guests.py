"""Cameo appearances of box Pokémon on the companion chat panel.

While the chat panel is open, occasionally a random Pokémon from the
user's box (excluding the active one) slides in on the left edge of
the panel, idles for a few seconds, and slides back out. Small
easter-egg cameos that make the panel feel like a party-member hangout
instead of a single sprite docked at a corner.

Two layers, mirroring ``companion/chat_idle.py``:

- ``GuestScheduler`` — pure-Python state machine. Given a clock and a
  seeded RNG it decides when to spawn the next guest and when to
  despawn an active one. No AppKit imports → unit-testable headlessly.

- ``ChatGuestDriver`` — AppKit shell. NSObject + 1 Hz NSTimer that
  asks the scheduler each tick, then opens / closes a borderless
  sprite window when ``"spawn"`` / ``"despawn"`` come back. The guest
  window reuses ``ChatIdleAnimator`` from ``chat_idle`` for its
  ambient BOB/HOP/SHAKE/PACE behaviour — guests animate identically to
  the active companion.

Lifecycle: ``start()`` after the chat sprite finishes its dock
animation, ``stop()`` when the chat panel hides or the sprite is
unpinned. Both are idempotent. ``stop()`` hard-removes any active
guest window so we never leak a ghost sprite over the desktop.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("tokenmon.companion.chat_guests")


# Spawn cooldown — drawn at ``reset()`` and after every despawn so two
# back-to-back cameos can't happen. Wide range keeps the cameos feeling
# like a surprise rather than a metronome.
COOLDOWN_MIN_S = 60.0
COOLDOWN_MAX_S = 180.0

# Per-guest on-screen lifespan. A handful of one-shots inside this
# window is enough to read as "a Pokémon hopped on and hopped off
# again" — much longer and the guest starts competing with the active
# for attention.
LIFESPAN_MIN_S = 6.0
LIFESPAN_MAX_S = 12.0

# Driver tick rate. Spawn / despawn decisions only need to be checked
# at human-perceptible cadence (the actual visual idle runs at 20 Hz
# inside ``ChatIdleAnimator``), so a slow timer keeps CPU near zero.
DRIVER_TICK_S = 1.0

# Slide animation parameters — matched to the chat-panel dock slide
# (``_dock_sprite_to_chat``) so the visual style stays consistent.
SLIDE_DURATION_S = 0.28
SLIDE_OFFSCREEN_OFFSET_PX = 60.0


# ---------------------------------------------------------------------------
# Pure state machine
# ---------------------------------------------------------------------------


@dataclass
class GuestScheduler:
    """Pure-Python spawn/despawn timing for chat-panel guests.

    Driver pattern:

        sched = GuestScheduler(rng=random.Random())
        sched.reset(now)
        ...
        while running:
            action = sched.tick(now)
            if action == "spawn":
                spawned = try_spawn()
                if spawned:
                    sched.note_spawn(now, lifespan=picked_lifespan)
                else:
                    # Spawn rejected (empty box, sprite fetch failed,
                    # …). Roll a new cooldown so we don't busy-loop on
                    # tick().
                    sched.note_spawn_failed(now)
            elif action == "despawn":
                begin_despawn()
                sched.note_despawn(now)

    The state machine itself does no I/O — picking a Pokémon, loading
    its sprite, and opening a window live in the driver. This keeps
    the spawn-cadence logic testable with a fake clock + seeded RNG.
    """

    rng: random.Random = field(default_factory=random.Random)
    cooldown_range_s: tuple[float, float] = (COOLDOWN_MIN_S, COOLDOWN_MAX_S)
    lifespan_range_s: tuple[float, float] = (LIFESPAN_MIN_S, LIFESPAN_MAX_S)

    # State — initialised by ``reset(t0)``. ``_next_spawn_t`` is the
    # earliest monotonic time the next guest may spawn.
    # ``_active_until_t`` is None when no guest is on screen, else the
    # monotonic time at which the active guest should slide out.
    _next_spawn_t: float = 0.0
    _active_until_t: Optional[float] = None

    def reset(self, now: float) -> None:
        """Restart the scheduler. The first guest can spawn no earlier
        than ``now + uniform(cooldown_range_s)`` — without this initial
        delay every chat-open would spawn a guest within seconds and
        feel scripted rather than spontaneous."""
        self._next_spawn_t = now + self._roll_cooldown()
        self._active_until_t = None

    def tick(self, now: float) -> Optional[str]:
        """Return ``"spawn"`` / ``"despawn"`` / ``None`` for this frame.

        Mutually exclusive: when a guest is active we only ever look
        at the despawn deadline; when no guest is active we only look
        at the spawn deadline. The driver is in charge of actually
        opening / closing windows and calling ``note_*``.
        """
        if self._active_until_t is not None:
            if now >= self._active_until_t:
                return "despawn"
            return None
        if now >= self._next_spawn_t:
            return "spawn"
        return None

    def pick_lifespan(self) -> float:
        """Roll a lifespan ahead of the spawn so the caller knows when
        the guest will be asked to leave. Public so the driver can
        log it / surface it without re-rolling."""
        lo, hi = self.lifespan_range_s
        return self.rng.uniform(lo, hi)

    def note_spawn(self, now: float, lifespan: float) -> None:
        """Driver confirms it opened a guest window. Marks the guest
        as active until ``now + lifespan``."""
        self._active_until_t = now + float(lifespan)

    def note_spawn_failed(self, now: float) -> None:
        """Driver couldn't open a guest (empty box, sprite fetch
        failed, …). We don't want ``tick()`` to keep returning
        ``"spawn"`` immediately on every following tick — roll a fresh
        cooldown so the next attempt is a normal interval away."""
        self._next_spawn_t = now + self._roll_cooldown()
        self._active_until_t = None

    def note_despawn(self, now: float) -> None:
        """Driver confirms the guest finished its slide-out and is
        gone. The cooldown starts here (not at spawn time) so two
        cameos can't appear back-to-back — there's always at least
        ``COOLDOWN_MIN_S`` of empty panel between guests."""
        self._active_until_t = None
        self._next_spawn_t = now + self._roll_cooldown()

    def is_guest_active(self) -> bool:
        return self._active_until_t is not None

    def _roll_cooldown(self) -> float:
        lo, hi = self.cooldown_range_s
        return self.rng.uniform(lo, hi)


# ---------------------------------------------------------------------------
# AppKit shell
# ---------------------------------------------------------------------------

try:
    import objc
    from AppKit import (
        NSBackingStoreBuffered,
        NSColor,
        NSFloatingWindowLevel,
        NSImage,
        NSImageScaleProportionallyUpOrDown,
        NSImageView,
        NSObject,
        NSPopUpMenuWindowLevel,
        NSTimer,
        NSWindow,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorIgnoresCycle,
        NSWindowCollectionBehaviorStationary,
        NSWindowCollectionBehaviorTransient,
        NSWindowStyleMaskBorderless,
    )
    from Foundation import NSMakeRect
    _APPKIT_AVAILABLE = True
except Exception:  # pragma: no cover — only fires on non-macOS / stripped builds
    _APPKIT_AVAILABLE = False


if _APPKIT_AVAILABLE:

    class ChatGuestDriver(NSObject):  # type: ignore[misc]
        """Spawns and despawns guest Pokémon next to the chat panel.

        Construct via PyObjC convention::

            drv = ChatGuestDriver.alloc().initWithOverlay_chatFrameProvider_(
                overlay, lambda: chat_window.frame() if visible else None,
            )
            drv.start()
            ...
            drv.stop()

        ``chat_frame_provider`` returns the live chat-panel frame each
        tick (or ``None`` when the panel is gone — the driver
        self-stops in that case as a safety net, even though
        ``PokemonOverlay`` also calls ``stop()`` from
        ``_stop_chat_idle_animator``).
        """

        # initWithOverlay_chatFrameProvider_(overlay, provider)
        def initWithOverlay_chatFrameProvider_(self, overlay, chat_frame_provider):  # noqa: N802
            self = objc.super(ChatGuestDriver, self).init()
            if self is None:
                return None
            self._overlay = overlay
            self._chat_frame_provider = chat_frame_provider
            self._scheduler = GuestScheduler()
            self._timer = None
            # Strong-refs that have to outlive the immediate method
            # call: the guest window itself, its idle animator, and
            # the in-flight slide handler. Without these, PyObjC GCs
            # the NSObject targets before their NSTimers tick.
            self._guest_window = None
            self._guest_idle_animator = None
            self._guest_slide_handler = None
            # Species id of the currently spawned guest, kept around
            # for logging / future routing of clicks. Cleared on
            # despawn.
            self._guest_species_id: Optional[int] = None
            return self

        # -- lifecycle ----------------------------------------------------

        def start(self) -> None:
            """Idempotent — drop any prior timer first."""
            self.stop()
            self._scheduler.reset(time.monotonic())
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                DRIVER_TICK_S, self, b"tick:", None, True,
            )

        def stop(self) -> None:
            """Tear down the timer and any active guest. Safe to call
            when nothing is running."""
            if self._timer is not None:
                try:
                    self._timer.invalidate()
                except Exception:
                    log.exception("guest driver timer invalidate failed")
                self._timer = None
            # Hard-remove an in-flight guest. We don't try to play the
            # slide-out animation here because ``stop()`` is the
            # synchronous hand-off path used when the chat panel is
            # disappearing — playing a 280 ms slide on a window whose
            # backdrop just vanished would look broken.
            self._teardown_active_guest()

        # -- timer callback ----------------------------------------------

        def tick_(self, _timer):  # noqa: N802
            now = time.monotonic()
            # Safety net: if the chat panel went away without us being
            # told, self-stop. ``PokemonOverlay`` does call
            # ``stop()`` from ``_stop_chat_idle_animator``, but a
            # crashy code path that skips that would otherwise leave
            # the timer firing forever.
            try:
                chat_frame = self._chat_frame_provider()
            except Exception:
                log.exception("chat_frame_provider raised")
                chat_frame = None
            if chat_frame is None:
                self.stop()
                return

            action = self._scheduler.tick(now)
            if action == "spawn":
                self._try_spawn(now, chat_frame)
            elif action == "despawn":
                self._try_despawn(now)

        # -- spawn / despawn ---------------------------------------------

        def _try_spawn(self, now: float, chat_frame) -> None:
            """Pick a random non-active box Pokémon, load its sprite,
            open a guest window, slide it in, and start its idle
            animator. On any failure, mark the spawn as failed so the
            scheduler rolls a fresh cooldown."""
            try:
                pick = _pick_guest_pokemon()
            except Exception:
                log.exception("guest pick failed")
                pick = None
            if pick is None:
                self._scheduler.note_spawn_failed(now)
                return

            dex_id, is_shiny = pick

            try:
                from tokenmon import pokemon as _pokemon
                sprite_path = _pokemon.ensure_sprite(dex_id, shiny=is_shiny)
            except Exception:
                log.exception("guest ensure_sprite failed for dex %s", dex_id)
                sprite_path = None
            if sprite_path is None:
                self._scheduler.note_spawn_failed(now)
                return

            try:
                self._open_guest_window(sprite_path, chat_frame)
            except Exception:
                log.exception("guest window open failed")
                self._teardown_active_guest()
                self._scheduler.note_spawn_failed(now)
                return

            self._guest_species_id = dex_id
            lifespan = self._scheduler.pick_lifespan()
            self._scheduler.note_spawn(now, lifespan=lifespan)
            log.info(
                "chat guest spawn dex=%s shiny=%s lifespan=%.1fs",
                dex_id, is_shiny, lifespan,
            )

        def _try_despawn(self, now: float) -> None:
            """Begin the slide-out. We mark the scheduler as despawned
            immediately (rather than waiting for the slide to
            finish) so the cooldown clock starts now — the visual
            slide is just a courtesy on the way out."""
            self._scheduler.note_despawn(now)
            self._slide_out_active_guest()
            log.info("chat guest despawn dex=%s", self._guest_species_id)

        # -- window plumbing ---------------------------------------------

        def _open_guest_window(self, sprite_path, chat_frame) -> None:
            """Create the borderless guest window, load the sprite,
            position it just off the left edge of the chat panel,
            slide it in to the target anchor, then arm the idle
            animator on completion."""
            from tokenmon.overlay import _guest_origin_for_chat
            from tokenmon.companion.chat_idle import ChatIdleAnimator

            size = float(self._overlay._size)  # match active companion size
            target_x, target_y = _guest_origin_for_chat(chat_frame, int(size))

            # Slide-in start frame: same y as target, but x offscreen
            # to the left and alpha=0. The slide handler ramps alpha
            # back to 1 over its duration so the guest fades in
            # while it glides into place.
            start_x = float(chat_frame.origin.x) - SLIDE_OFFSCREEN_OFFSET_PX
            start_rect = NSMakeRect(start_x, target_y, size, size)
            end_rect = NSMakeRect(target_x, target_y, size, size)

            win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                start_rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False,
            )
            win.setOpaque_(False)
            win.setBackgroundColor_(NSColor.clearColor())
            win.setHasShadow_(False)
            win.setIgnoresMouseEvents_(True)
            win.setMovable_(False)
            # Match the active companion's elevated level while the
            # chat panel is open so the guest isn't clipped by the
            # panel when BOB / HOP dips it close to the panel edge.
            win.setLevel_(NSPopUpMenuWindowLevel)
            win.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorIgnoresCycle
                | NSWindowCollectionBehaviorTransient
            )
            win.setReleasedWhenClosed_(False)
            win.setAlphaValue_(0.0)

            # Reuse the active companion's image view — its drawRect_
            # disables NSImageInterpolation per-frame, which is what
            # actually keeps animated GIFs crisp at scale-up.
            # Layer-level magnification filters alone aren't enough
            # for GIFs because each frame goes through NSImageView's
            # draw pipeline (default bilinear interpolation) before
            # ever reaching the layer. We pass the overlay ref to
            # satisfy the init signature; the double-click handler
            # never fires here because the guest window has
            # setIgnoresMouseEvents_(True), so events pass through.
            from tokenmon.overlay import _CompanionImageView
            img_view = _CompanionImageView.alloc().initWithFrame_overlay_(
                NSMakeRect(0, 0, size, size), self._overlay,
            )
            img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            img_view.setAnimates_(True)
            img_view.setWantsLayer_(True)
            if img_view.layer() is not None:
                img_view.layer().setMagnificationFilter_("nearest")
                img_view.layer().setMinificationFilter_("nearest")
            win.setContentView_(img_view)

            # Load the sprite — animated GIF when possible, static
            # fallback otherwise. Same path as
            # ``PokemonOverlay.update_sprite``.
            try:
                from tokenmon.sprite_speed import load_animated_image
                img = load_animated_image(sprite_path, speed=1.0)
            except Exception:
                log.exception("guest load_animated_image failed; static fallback")
                img = None
            if img is None:
                try:
                    img = NSImage.alloc().initWithContentsOfFile_(str(sprite_path))
                except Exception:
                    log.exception("guest static NSImage load failed")
                    img = None
            if img is not None:
                img_view.setImage_(img)

            win.orderFront_(None)
            self._guest_window = win

            # Slide-in handler — reuse the same _ChatSlideHandler that
            # docks the active companion. on_complete arms the idle
            # animator at the final anchor.
            from tokenmon.overlay import _ChatSlideHandler

            x_lo, x_hi = _guest_x_range_for_chat(chat_frame, int(size))

            def _on_slide_in_complete():
                if self._guest_window is None:
                    return
                try:
                    anim = ChatIdleAnimator.alloc().initWithWindow_anchor_xRange_(
                        self._guest_window, (target_x, target_y), (x_lo, x_hi),
                    )
                    self._guest_idle_animator = anim
                    anim.start()
                except Exception:
                    log.exception("guest idle animator start failed")

            handler = _ChatSlideHandler.alloc().initWithWindow_startFrame_endFrame_startAlpha_endAlpha_duration_onComplete_(
                win, start_rect, end_rect, 0.0, 1.0, SLIDE_DURATION_S,
                _on_slide_in_complete,
            )
            self._guest_slide_handler = handler
            handler.start()

        def _slide_out_active_guest(self) -> None:
            """Stop the idle animator, then slide the guest back off
            to the left and fade alpha to 0. On completion the window
            and all strong-refs are dropped."""
            win = self._guest_window
            if win is None:
                return

            anim = self._guest_idle_animator
            if anim is not None:
                try:
                    anim.stop()
                except Exception:
                    log.exception("guest idle animator stop failed")
                self._guest_idle_animator = None

            prior_slide = self._guest_slide_handler
            if prior_slide is not None:
                try:
                    prior_slide.cancel()
                except Exception:
                    pass
                self._guest_slide_handler = None

            try:
                start_frame = win.frame()
                current_alpha = float(win.alphaValue())
            except Exception:
                log.exception("guest slide-out frame read failed")
                self._teardown_active_guest()
                return
            end_x = float(start_frame.origin.x) - SLIDE_OFFSCREEN_OFFSET_PX
            end_frame = NSMakeRect(
                end_x, float(start_frame.origin.y),
                float(start_frame.size.width), float(start_frame.size.height),
            )

            def _on_slide_out_complete():
                self._teardown_active_guest()

            try:
                from tokenmon.overlay import _ChatSlideHandler
                handler = _ChatSlideHandler.alloc().initWithWindow_startFrame_endFrame_startAlpha_endAlpha_duration_onComplete_(
                    win, start_frame, end_frame, current_alpha, 0.0,
                    SLIDE_DURATION_S, _on_slide_out_complete,
                )
                self._guest_slide_handler = handler
                handler.start()
            except Exception:
                log.exception("guest slide-out start failed")
                self._teardown_active_guest()

        def _teardown_active_guest(self) -> None:
            """Synchronously drop the guest window and all strong-refs.
            Called both from ``stop()`` (hard close, no animation) and
            from the slide-out completion callback."""
            anim = self._guest_idle_animator
            if anim is not None:
                try:
                    anim.stop()
                except Exception:
                    pass
                self._guest_idle_animator = None

            handler = self._guest_slide_handler
            if handler is not None:
                try:
                    handler.cancel()
                except Exception:
                    pass
                self._guest_slide_handler = None

            win = self._guest_window
            if win is not None:
                try:
                    win.orderOut_(None)
                    win.close()
                except Exception:
                    log.exception("guest window close failed")
                self._guest_window = None

            self._guest_species_id = None

else:  # pragma: no cover — non-macOS test environments
    class ChatGuestDriver:  # type: ignore[no-redef]
        """No-op fallback so imports still resolve on headless CI."""

        @classmethod
        def alloc(cls):
            return cls()

        def initWithOverlay_chatFrameProvider_(self, *_a, **_kw):  # noqa: N802
            return self

        def start(self):
            pass

        def stop(self):
            pass


# ---------------------------------------------------------------------------
# Helpers (kept module-level so the AppKit driver and tests can share)
# ---------------------------------------------------------------------------


def _pick_guest_pokemon() -> Optional[tuple[int, bool]]:
    """Pick a random non-active box Pokémon. Returns ``(dex_id, is_shiny)``
    or ``None`` when the box is empty / only contains the active.

    Defined at module level (not on the driver) so it stays import-
    light: the AppKit path imports this lazily inside a try/except so
    a temporary DB or storage hiccup doesn't tear down the timer.
    """
    from tokenmon import box

    try:
        active_id = box.get_active_pokemon_id()
    except Exception:
        log.exception("get_active_pokemon_id failed; guest pick skipped")
        return None

    try:
        all_pokemon = box.list_pokemon()
    except Exception:
        log.exception("list_pokemon failed; guest pick skipped")
        return None

    candidates = [p for p in all_pokemon if p.id != active_id]
    if not candidates:
        return None

    pick = random.choice(candidates)
    return (int(pick.species_dex_id), bool(pick.is_shiny))


def _guest_x_range_for_chat(chat_frame, sprite_size: int) -> tuple[float, float]:
    """PACE x-range for the guest sprite — the left half of the chat
    panel, with insets matched to ``_dock_sprite_to_chat``. Keeps the
    guest from pacing across the centre of the panel into the active
    companion's territory."""
    chat_x = float(chat_frame.origin.x)
    chat_w = float(chat_frame.size.width)
    centre = chat_x + chat_w / 2.0
    x_lo = chat_x + 8.0
    x_hi = centre - float(sprite_size) / 2.0 - 8.0
    if x_hi < x_lo:
        # Narrow chat panel — degenerate range. Returning equal
        # bounds makes the chat-idle PACE selector a no-op, which is
        # fine: BOB / HOP / SHAKE still animate.
        x_hi = x_lo
    return x_lo, x_hi

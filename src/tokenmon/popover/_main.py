"""Custom NSPopover that replaces the rumps dropdown menu.

Layout: a 60-pixel sidebar on the left with four icon buttons (Today /
Pokedex / Box / Usage) and a swappable content pane on the right. The
popover anchors to the menubar status item with NSRectEdgeMinY so it
"rolls down" from the icon, and uses NSPopoverBehaviorTransient so
clicking outside dismisses it automatically.
"""

from __future__ import annotations

import logging
import os
import subprocess

import objc
import rumps
from AppKit import (
    NSBezelStyleRegularSquare,
    NSButton,
    NSEvent,
    NSEventMaskLeftMouseDown,
    NSEventMaskOtherMouseDown,
    NSEventMaskRightMouseDown,
    NSEventTypeRightMouseDown,
    NSEventTypeRightMouseUp,
    NSFont,
    NSImage,
    NSImageView,
    NSMenu,
    NSMenuItem,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSRectEdgeMinY,
    NSView,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject

from tokenmon import box, config, items
from tokenmon.storage import (
    get_pending_encounter,
    get_pending_trainer,
)

log = logging.getLogger("tokenmon.popover")

TZ = "Europe/Berlin"

# Layout constants + widgets are now in popover.widgets — re-imported here
# so the rest of this module (and external callers via the package) keep
# their existing references working.
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_BATTLE,
    PANE_BATTLE_REWARD,
    PANE_BOX,
    PANE_ENCOUNTER,
    PANE_ITEMS,
    PANE_POKEMON,
    PANE_TOKENDEX,
    PANE_USAGE,
    POPOVER_HEIGHT,
    POPOVER_WIDTH,
    SIDEBAR_WIDTH,
    _SidebarView,
    _new_vc,
)

# While an encounter or trainer flow is pending, sidebar navigation is
# locked: the only base-pane slot that stays clickable is Usage
# (Tokenverbrauch). The active encounter/trainer slot also stays clickable
# so the user can return to the fight from Usage. Lock state is derived
# from storage (get_pending_encounter / get_pending_trainer) rather than
# the current pane — peeking at Usage mid-fight must not unlock the rest.


# Re-exports for popover/__init__.py — public test surface for the pure
# helpers (formatters + step builders). Keep the names stable.
from tokenmon.ui_helpers import (
    fmt_affection as _fmt_affection,
    fmt_tokens as _fmt_tokens,
    fmt_usd as _fmt_usd,
)


class _RightClickHandler(NSObject):
    """Bridge for the right-click fallback menu's Quit item."""

    def quit_(self, _sender):  # noqa: N802
        rumps.quit_application(None)


# Re-exports for popover/__init__.py — public test surface.
from tokenmon.popover.animation import (
    _build_catch_steps,
    _build_pat_steps,
)


class TokenmonPopover(NSObject):
    """Holds the NSPopover, builds panes, owns sidebar selection state."""

    def initWithApp_(self, app):  # noqa: N802
        self = objc.super(TokenmonPopover, self).init()
        if self is None:
            return None
        self._app = app
        self._popover = NSPopover.alloc().init()
        self._popover.setBehavior_(NSPopoverBehaviorTransient)
        self._popover.setAnimates_(True)
        self._popover.setDelegate_(self)
        self._popover.setContentSize_(NSMakeSize(POPOVER_WIDTH, POPOVER_HEIGHT))

        self._root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_HEIGHT))

        # Sidebar starts with just the four base panes; rebuild_sidebar() is
        # called every time the popover opens to add/remove the encounter slot.
        self._sidebar = _SidebarView.alloc().initWithFrame_paneIds_selected_(
            NSMakeRect(0, 0, SIDEBAR_WIDTH, POPOVER_HEIGHT),
            [PANE_POKEMON, PANE_TOKENDEX, PANE_BOX, PANE_ITEMS, PANE_USAGE],
            PANE_POKEMON,
        )
        self._sidebar_buttons: list[NSButton] = []
        self._sidebar_pane_ids: list[int] = []
        # Must be set before the first _rebuild_sidebar() — it inspects
        # _current_pane to decide which slots are locked.
        self._current_pane: int = PANE_POKEMON
        self._rebuild_sidebar()
        self._root.addSubview_(self._sidebar)

        self._content_container = NSView.alloc().initWithFrame_(
            NSMakeRect(SIDEBAR_WIDTH, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )
        self._root.addSubview_(self._content_container)

        # Weather background layer — sits inside the content container
        # *before* any pane view, so panes naturally render above it in
        # the z-stack. Started/stopped by popoverWillShow_/DidClose_.
        self._weather_bg_view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )
        self._content_container.addSubview_(self._weather_bg_view)
        self._weather_animator = None

        self._current_pane_view: NSView | None = None
        # Animated NSImageViews collected during build_view; popoverWillShow_/
        # DidClose_ start/stop their GIF playback to save CPU when the
        # popover is closed.
        self._animated_image_views: list[NSImageView] = []

        # Pane-specific state read by closures and pane controllers across
        # in-pane re-renders. Controllers own their own handler lifetimes
        # (PaneController._handlers); the fields here only carry the
        # *intent* between renders (e.g. "show box detail #7").
        self._box_selected_id: int | None = None
        # When set, the box detail view is in "switch attack" mode for
        # this slot (0..3) — replaces the regular detail with the swap
        # picker. Cleared when the user picks a move or hits Back.
        self._box_swap_slot: int | None = None
        self._pokedex_selected_dex: int | None = None
        self._stats_mode: str = "stats"
        self._items_pocket: str = "balls"
        self._editing_nickname: bool = False
        self._encounter_bag_open: bool = False
        self._pending_reveal_pokemon: dict | None = None

        # Animation handlers — kept here so manual pane navigation can
        # break the timer chain by clearing the strong ref. NSTimer keeps
        # its own ref to the target, so clearing these doesn't fire the
        # timer; the handler's per-step short-circuit on missing views
        # turns remaining ticks into no-ops.
        self._reveal_timer = None
        self._reveal_timer_handler = None
        self._catch_anim_handler = None

        # Drop-claim animation state — the controller is recreated each
        # pane render, so this state lives on the popover to survive the
        # in-pane re-render that flips items.list ↔ items.claim.
        self._claim_active: bool = False
        self._claim_handler = None
        self._claim_payload: dict[str, int] = {}
        self._claim_views: list = []

        # Active pane controller — populated by _show_pane.
        self._current_controller = None

        self._vc = _new_vc(self._root)
        self._popover.setContentViewController_(self._vc)

        self._right_click_handler = _RightClickHandler.alloc().init()
        self._global_monitor = None  # NSEvent monitor; set while popover open

        return self

    # ---- navigation lock ----

    def _is_navigation_locked(self) -> bool:
        """True while an encounter or trainer flow is pending. While locked,
        sidebar slots other than Usage and the active encounter/trainer slot
        are disabled, so the user cannot swap their active Pokemon (via Box)
        or resolve a Pokemon (Pokemon pane) mid-fight."""
        try:
            if get_pending_encounter() is not None:
                return True
        except Exception:
            log.exception("get_pending_encounter failed")
        try:
            if get_pending_trainer() is not None:
                return True
        except Exception:
            log.exception("get_pending_trainer failed")
        return False

    def _resolve_locked_click(self, idx: int) -> int | None:
        """Return the pane id to navigate to for a sidebar click while locked,
        or None if the click should be ignored. Usage is always allowed.
        The unified encounter slot routes to PANE_BATTLE when a session is
        live (so peeking at Usage and coming back doesn't reset the fight)
        and to PANE_ENCOUNTER preview otherwise."""
        if idx == PANE_USAGE:
            return PANE_USAGE
        if idx == PANE_ENCOUNTER:
            if getattr(self, "_battle_session", None) is not None:
                return PANE_BATTLE
            return PANE_ENCOUNTER
        return None

    # ---- sidebar ----

    def _rebuild_sidebar(self) -> None:
        """Rebuild the sidebar buttons based on current pending-encounter state.

        Called once per popover open (and after a successful catch/run-away
        when the encounter slot needs to disappear). Idempotent — wipes any
        existing buttons and rebuilds from the current slot list.
        """
        # Wipe existing buttons.
        for btn in self._sidebar_buttons:
            btn.removeFromSuperview()
        self._sidebar_buttons = []

        # Determine slot list — pending entities take the top slots.
        # Trainer takes priority over wild encounter when both happen
        # to be pending (the spawn loop already prevents that, but be
        # defensive).
        try:
            pending_enc = get_pending_encounter()
        except Exception:
            log.exception("get_pending_encounter failed")
            pending_enc = None
        try:
            pending_trainer = get_pending_trainer()
        except Exception:
            log.exception("get_pending_trainer failed")
            pending_trainer = None

        items: list[tuple[int, str]] = []
        if pending_trainer is not None:
            items.append((PANE_ENCOUNTER, "⚔️"))
        elif pending_enc is not None:
            items.append((PANE_ENCOUNTER, "⚡"))
        items += [
            (PANE_POKEMON, "🥚"),
            (PANE_TOKENDEX, "📖"),
            (PANE_BOX, "📦"),
            (PANE_ITEMS, "🎒"),
            (PANE_USAGE, "$"),
        ]
        self._sidebar_pane_ids = [pane_id for pane_id, _ in items]
        self._sidebar.setPaneIds_(self._sidebar_pane_ids)

        locked = self._is_navigation_locked()
        slot_h = _SidebarView.SLOT_HEIGHT
        for slot_idx, (pane_id, fallback) in enumerate(items):
            y = POPOVER_HEIGHT - (slot_idx + 1) * slot_h
            btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(8, y + 8, SIDEBAR_WIDTH - 16, slot_h - 16)
            )
            btn.setTitle_(fallback)
            btn.setBezelStyle_(NSBezelStyleRegularSquare)
            btn.setBordered_(False)
            btn.setFont_(NSFont.systemFontOfSize_(20))
            # tag() is a 32-bit signed int — PANE_ENCOUNTER = -1 is fine.
            btn.setTag_(pane_id)
            btn.setTarget_(self)
            btn.setAction_(b"sidebarClicked:")
            # Disable + visually fade slots that the lock disallows so the
            # user can see they're inert. setEnabled_(False) alone barely
            # reads on a borderless emoji button — the alpha makes it obvious.
            if locked and self._resolve_locked_click(pane_id) is None:
                btn.setEnabled_(False)
                btn.setAlphaValue_(0.25)
            self._sidebar.addSubview_(btn)
            self._sidebar_buttons.append(btn)

    def _refresh_sidebar_pokemon_icon(self) -> None:
        sprite = self._app._pokemon_sprite
        if sprite is None or not sprite.exists():
            return
        # Find the Today slot in the current sidebar layout (its index varies
        # depending on whether the encounter slot is showing).
        try:
            slot_idx = self._sidebar_pane_ids.index(PANE_POKEMON)
        except ValueError:
            return
        if slot_idx >= len(self._sidebar_buttons):
            return
        img = NSImage.alloc().initWithContentsOfFile_(str(sprite))
        if img is None:
            return
        img.setSize_(NSMakeSize(36, 36))
        btn = self._sidebar_buttons[slot_idx]
        btn.setImage_(img)
        btn.setTitle_("")
        # Nearest-neighbor scaling for the small sidebar sprite too.
        btn.setWantsLayer_(True)
        if btn.layer() is not None:
            btn.layer().setMagnificationFilter_("nearest")
            btn.layer().setMinificationFilter_("nearest")

    def sidebarClicked_(self, sender):  # noqa: N802
        idx = int(sender.tag())
        if idx == self._current_pane and self._current_pane_view is not None:
            return
        # Lock: while an encounter/trainer flow is pending, only Usage and
        # the active encounter/trainer slot are clickable. Trainer slot
        # routes to PANE_BATTLE when a session is in progress so peeking at
        # Usage doesn't drop the player back to the preview screen (which
        # would reset _battle_session via the "Fight" button on resume).
        if self._is_navigation_locked():
            target = self._resolve_locked_click(idx)
            if target is None:
                return
            idx = target
            if idx == self._current_pane and self._current_pane_view is not None:
                return
        self._show_pane(idx)

    # ---- panes ----

    def _show_pane(self, idx: int) -> None:
        # Inter-pane state intent — each pane decides whether the survivor
        # state from a previous render is still meaningful.
        self._animated_image_views = []
        if idx != PANE_ENCOUNTER:
            self._encounter_bag_open = False
        if idx != PANE_BOX or self._box_selected_id is None:
            self._editing_nickname = False
            self._box_swap_slot = None
        # Reveal timer + animation handlers lose their popover anchor;
        # NSTimer's own retain keeps remaining ticks alive, but their
        # step methods short-circuit when the controller's views are gone.
        if self._reveal_timer is not None:
            try:
                self._reveal_timer.invalidate()
            except Exception:
                pass
            self._reveal_timer = None
        self._reveal_timer_handler = None
        self._pending_reveal_pokemon = None
        self._catch_anim_handler = None

        # Tear down the previous controller before building the next.
        if self._current_controller is not None:
            try:
                self._current_controller.teardown()
            except Exception:
                log.exception("controller teardown failed")
            self._current_controller = None

        view = self._build_controller_view(idx)
        if self._current_pane_view is not None:
            self._current_pane_view.removeFromSuperview()
        view.setFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        self._content_container.addSubview_(view)
        self._current_pane_view = view
        self._current_pane = idx
        # Rebuild sidebar in case the encounter just resolved (slot disappears)
        # or a new one just spawned (slot appears).
        self._rebuild_sidebar()
        self._refresh_sidebar_pokemon_icon()
        self._sidebar.setSelected_(idx)

    def _build_controller_view(self, idx: int) -> NSView:
        """Instantiate the matching controller, build its view, and stash
        the controller on ``self._current_controller``. Returns a fallback
        empty view on import or build failure so the sidebar always lands
        somewhere clickable."""
        # Lazy imports so the controllers stay decoupled at module import
        # time (avoids cycles between _main and panes/*).
        from tokenmon.popover.panes.battle import BattleController
        from tokenmon.popover.panes.battle_reward import BattleRewardController
        from tokenmon.popover.panes.box import BoxController
        from tokenmon.popover.panes.encounter_preview import (
            EncounterPreviewController,
        )
        from tokenmon.popover.panes.items import ItemsController
        from tokenmon.popover.panes.pokemon import PokemonController
        from tokenmon.popover.panes.tokendex import TokendexController
        from tokenmon.popover.panes.usage import UsageController

        registry = {
            PANE_ENCOUNTER: EncounterPreviewController,
            PANE_BATTLE: BattleController,
            PANE_BATTLE_REWARD: BattleRewardController,
            PANE_POKEMON: PokemonController,
            PANE_TOKENDEX: TokendexController,
            PANE_BOX: BoxController,
            PANE_ITEMS: ItemsController,
            PANE_USAGE: UsageController,
        }
        ctrl_cls = registry.get(idx)
        if ctrl_cls is None:
            return NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
            )
        try:
            ctrl = ctrl_cls(self)
            view = ctrl.build_view()
            self._current_controller = ctrl
            return view
        except Exception:
            log.exception("failed to build pane %s", idx)
            return NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
            )

    # =========================================================================
    # Pane: Encounter — delegates to EncounterController (popover/panes/encounter.py)
    # =========================================================================

    def _begin_catch_reveal(self) -> None:
        """Hand off from the catch animation to the encounter pane's reveal
        view. We're called from CatchAnimationController.end(), so the
        current controller is the catch one — we swap it for a fresh
        EncounterController whose ``begin_catch_reveal`` paints the reveal
        view directly into the content container (bypassing _show_pane so
        the encounter sidebar slot stays put for the 2.5 s hold).
        """
        from tokenmon.popover.panes.encounter import EncounterController
        if self._current_controller is not None:
            try:
                self._current_controller.teardown()
            except Exception:
                log.exception("controller teardown failed during catch reveal")
        ctrl = EncounterController(self)
        self._current_controller = ctrl
        ctrl.begin_catch_reveal()

    # =========================================================================
    # Pane: Catch animation — delegates to CatchAnimationController
    # (popover/panes/catch_animation.py)
    # =========================================================================

    def _begin_catch_animation(
        self,
        *,
        item_key: str,
        encounter_id: int,
        species_dex_id: int,
        caught: bool,
        shakes: int,
        hint: str | None,
    ) -> None:
        """Spin up a CatchAnimationController and let it take over the
        content area. Bypasses _show_pane so the encounter sidebar slot
        stays put for the duration of the animation."""
        from tokenmon.popover.panes.catch_animation import (
            CatchAnimationController,
        )
        payload = {
            "item_key": item_key,
            "encounter_id": int(encounter_id),
            "species_dex_id": int(species_dex_id),
            "caught": bool(caught),
            "shakes": int(shakes),
            "hint": hint,
        }
        ctrl = CatchAnimationController(self, payload)
        self._current_controller = ctrl
        ctrl.begin()

    def _catch_step(self, action: str, payload: dict) -> None:
        ctrl = self._current_controller
        if ctrl is not None and hasattr(ctrl, "step"):
            ctrl.step(action, payload)

    def _end_catch_animation(self, payload: dict) -> None:
        ctrl = self._current_controller
        if ctrl is not None and hasattr(ctrl, "end"):
            ctrl.end(payload)

    # =========================================================================
    # Pane: Pokemon — delegates to PokemonController (popover/panes/pokemon.py)
    # =========================================================================

    def _pat_step(self, action: str) -> None:
        ctrl = self._current_controller
        if ctrl is not None and hasattr(ctrl, "pat_step"):
            ctrl.pat_step(action)

    def _end_pat(self) -> None:
        ctrl = self._current_controller
        if ctrl is not None and hasattr(ctrl, "end_pat"):
            ctrl.end_pat()

    # =========================================================================
    # Pane: Items — delegates to ItemsController (popover/panes/items.py)
    # =========================================================================

    def _claim_step(self, action: str) -> None:
        ctrl = self._current_controller
        if ctrl is not None and hasattr(ctrl, "claim_step"):
            ctrl.claim_step(action)

    def _end_drop_claim_animation(self) -> None:
        ctrl = self._current_controller
        if ctrl is not None and hasattr(ctrl, "end_drop_claim_animation"):
            ctrl.end_drop_claim_animation()

    # ---- toggle / button actions (Usage pane) ----

    def toggleMenubarPokemon_(self, _sender):  # noqa: N802
        self._app.toggle_menubar_pokemon(None)

    def toggleCompanion_(self, _sender):  # noqa: N802
        self._app.toggle_companion(None)

    def toggleWeather_(self, _sender):  # noqa: N802
        self._app.toggle_weather(None)

    def restartProxy_(self, _sender):  # noqa: N802
        self._app.restart_proxy(None)

    def quitApp_(self, _sender):  # noqa: N802
        rumps.quit_application(None)

    # ---- click action ----

    @objc.IBAction
    def buttonClicked_(self, sender):  # noqa: N802
        try:
            from AppKit import NSApp
            event = NSApp.currentEvent()
        except Exception:
            event = None
        if event is not None and event.type() in (NSEventTypeRightMouseDown, NSEventTypeRightMouseUp):
            self.show_right_click_menu(sender)
        else:
            self.show_from_button(sender)

    # ---- show / hide ----

    def show_from_button(self, button) -> None:
        if self._popover.isShown():
            self._popover.close()
            return
        base_panes = (PANE_POKEMON, PANE_TOKENDEX, PANE_BOX, PANE_USAGE)
        try:
            pending_trainer = get_pending_trainer()
        except Exception:
            log.exception("get_pending_trainer failed")
            pending_trainer = None
        try:
            pending_enc = get_pending_encounter()
        except Exception:
            log.exception("get_pending_encounter failed")
            pending_enc = None
        if (pending_trainer is not None or pending_enc is not None) and (
            self._current_pane in base_panes
        ):
            self._current_pane = PANE_ENCOUNTER
        self._refresh_sidebar_pokemon_icon()
        self._show_pane(self._current_pane)
        # Activate so the popover gets keyboard focus and macOS-managed
        # transient dismiss has a chance.
        try:
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            log.exception("activateIgnoringOtherApps failed")
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, NSRectEdgeMinY,
        )
        self._install_global_monitor()

    def _install_global_monitor(self) -> None:
        """Install an NSEvent monitor for clicks anywhere outside our app.
        Belt-and-suspenders alongside NSPopoverBehaviorTransient — global
        monitors fire even when an LSUIElement-ish app hasn't yet "really"
        activated, which is exactly the case where transient dismiss fails."""
        if self._global_monitor is not None:
            return
        mask = (NSEventMaskLeftMouseDown
                | NSEventMaskRightMouseDown
                | NSEventMaskOtherMouseDown)
        try:
            self._global_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, self._on_global_click,
            )
        except Exception:
            log.exception("global monitor install failed")
            self._global_monitor = None

    def _uninstall_global_monitor(self) -> None:
        if self._global_monitor is None:
            return
        try:
            NSEvent.removeMonitor_(self._global_monitor)
        except Exception:
            log.exception("global monitor remove failed")
        self._global_monitor = None

    def _on_global_click(self, _event):
        # Any mouse-down anywhere outside our app while the popover is shown
        # → close the popover. The monitor doesn't fire for events inside our
        # own windows, so clicks inside the popover itself are safe.
        if self._popover is not None and self._popover.isShown():
            self._popover.close()

    def show_right_click_menu(self, button) -> None:
        """Fallback: small NSMenu with Quit, shown on right-click."""
        menu = NSMenu.alloc().init()
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit", b"quit:", "",
        )
        item.setTarget_(self._right_click_handler)
        menu.addItem_(item)
        event = button.window().currentEvent() if button.window() is not None else None
        if event is not None:
            NSMenu.popUpContextMenuWithEvent_forView_(menu, event, button)

    # ---- NSPopoverDelegate ----

    def popoverWillShow_(self, _notification):  # noqa: N802
        for iv in self._animated_image_views:
            iv.setAnimates_(True)
        self._start_weather_layer()

    def popoverDidClose_(self, _notification):  # noqa: N802
        self._uninstall_global_monitor()
        for iv in self._animated_image_views:
            iv.setAnimates_(False)
        self._stop_weather_layer()
        # Tear down the active controller so any timers it owns (e.g. the
        # Usage pane's 30-s chart refresh) stop firing while the popover
        # is hidden. The controller is rebuilt next time _show_pane runs.
        if self._current_controller is not None:
            try:
                self._current_controller.teardown()
            except Exception:
                log.exception("controller teardown on popover close failed")
            self._current_controller = None

    # ---- weather background ----

    def _start_weather_layer(self) -> None:
        """If use_weather is on and the current weather has a particle
        spec, build & start the animator inside the background view.
        Failures fall through silently — the popover still works."""
        try:
            from tokenmon import config, weather
            if not config.get("use_weather"):
                return
            snap = weather.get_weather()
            if snap is None:
                return
            spec = weather.particles_for(snap)
            if spec is None:
                return
            from tokenmon.popover.weather_layer import WeatherParticleAnimator
            anim = WeatherParticleAnimator.alloc().initWithHost_spec_snapshot_(
                self._weather_bg_view, spec, snap,
            )
            anim.start()
            self._weather_animator = anim
        except Exception:
            log.exception("weather layer start failed")

    def _stop_weather_layer(self) -> None:
        if self._weather_animator is None:
            return
        try:
            self._weather_animator.stop()
        except Exception:
            log.exception("weather layer stop failed")
        self._weather_animator = None

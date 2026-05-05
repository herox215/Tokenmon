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
from pathlib import Path

import objc
import rumps
from AppKit import (
    NSBezelStyleRegularSquare,
    NSBezierPath,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSEvent,
    NSEventMaskLeftMouseDown,
    NSEventMaskOtherMouseDown,
    NSEventMaskRightMouseDown,
    NSEventTypeRightMouseDown,
    NSEventTypeRightMouseUp,
    NSFont,
    NSFontAttributeName,
    NSGraphicsContext,
    NSImage,
    NSImageInterpolationNone,
    NSImageLeft,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMenu,
    NSMenuItem,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSRectEdgeMinY,
    NSScrollView,
    NSTextAlignmentCenter,
    NSTextField,
    NSTimer,
    NSView,
    NSViewController,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject

from tokenmon import config, encounter, items, items_remote, pokemon
from tokenmon.overlay import _silhouette_image
from tokenmon.pricing import cost_for
from tokenmon.storage import (
    get_pending_encounter,
    list_pokemon,
    query_item_counts,
    query_pokemon_xp,
    query_today,
    query_today_by_model,
    query_xp_for_date,
    query_xp_for_pokemon,
)
from tokenmon.tokendex import _XPBarView

log = logging.getLogger("tokenmon.popover")

POPOVER_WIDTH = 480
POPOVER_HEIGHT = 500
SIDEBAR_WIDTH = 60
CONTENT_WIDTH = POPOVER_WIDTH - SIDEBAR_WIDTH
TZ = "Europe/Berlin"

# Conditional 5th slot — sentinel value so it can never collide with the
# 0..3 indices used for the four base panes.
PANE_ENCOUNTER = -1
PANE_POKEMON = 0
PANE_TOKENDEX = 1
PANE_BOX = 2
PANE_ITEMS = 3
PANE_USAGE = 4

ROW_HEIGHT = 100  # mirrors tokendex.ROW_HEIGHT


# Pretty per-action menu titles for the bag's right-click-style flyout. The
# ``{name}`` placeholder gets formatted with the Item.display_name. Items in
# Item.actions that aren't in this dict simply get omitted from the menu.
ACTION_TITLES: dict[str, str] = {
    "throw": "Throw at wild Pokemon",
    "use": "Use {name}",
    "evolve": "Use on a Pokemon",
}


class _CrispImageView(NSImageView):
    """NSImageView subclass that disables interpolation when drawing the image.
    Without this, animated GIF sprites get bilinear-blurred when scaled up
    from their native ~96×96 to e.g. 144×144 in the popover."""

    def drawRect_(self, rect):  # noqa: N802
        ctx = NSGraphicsContext.currentContext()
        if ctx is not None:
            ctx.setImageInterpolation_(NSImageInterpolationNone)
        objc.super(_CrispImageView, self).drawRect_(rect)


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


def _label(frame, text, *, font=None, color=None, align=None, multiline=False) -> NSTextField:
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


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000:.1f}K"
    if n < 1_000_000_000:
        return f"{n/1_000_000:.2f}M"
    return f"{n/1_000_000_000:.2f}B"


def _fmt_usd(amount: float) -> str:
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 1:
        return f"${amount:.3f}"
    return f"${amount:.2f}"


class _ContentVC(NSViewController):
    """Trivial NSViewController subclass — NSPopover requires one."""

    def loadView(self):  # noqa: N802
        if hasattr(self, "_root_view") and self._root_view is not None:
            self.setView_(self._root_view)
        else:
            self.setView_(NSView.alloc().initWithFrame_(NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_HEIGHT)))


def _new_vc(root_view: NSView) -> NSViewController:
    vc = _ContentVC.alloc().init()
    vc._root_view = root_view
    vc.view()  # force loadView
    return vc


class _SidebarView(NSView):
    """Background-tinted sidebar that highlights the selected slot.

    The sidebar has a *variable* number of slots: the four base panes are
    always present (Today / Pokedex / Box / Usage), and a 5th encounter slot
    is prepended at the top whenever a wild encounter is pending. We track
    the slot order as a list of ``pane_id``s so the selection-pill geometry
    automatically adapts to whichever set of slots is currently visible.
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
        # Subtle sidebar background tint.
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.03).set()
        NSBezierPath.fillRect_(bounds)
        # Right edge separator.
        NSColor.separatorColor().set()
        NSBezierPath.fillRect_(NSMakeRect(bounds.size.width - 1, 0, 1, bounds.size.height))
        # Selected-slot pill — only drawn if the selected pane is actually a slot.
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


class _RightClickHandler(NSObject):
    """Bridge for the right-click fallback menu's Quit item."""

    def quit_(self, _sender):  # noqa: N802
        rumps.quit_application(None)


# =============================================================================
# Box pane click handlers
# =============================================================================


class _BoxItemHandler(NSObject):
    """Per-item click target for grid Pokemon buttons. Stores the Pokemon id
    and on click pushes the popover into detail-view mode for that id."""

    def initWithPopover_pokemonId_(self, popover, pokemon_id):  # noqa: N802
        self = objc.super(_BoxItemHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._pokemon_id = int(pokemon_id)
        return self

    def itemClicked_(self, _sender):  # noqa: N802
        self._popover._box_selected_id = self._pokemon_id
        self._popover._show_pane(PANE_BOX)


class _BoxBackHandler(NSObject):
    """"← Back" button handler — clears the selected id and re-renders the grid."""

    def initWithPopover_(self, popover):  # noqa: N802
        self = objc.super(_BoxBackHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        return self

    def backClicked_(self, _sender):  # noqa: N802
        self._popover._box_selected_id = None
        self._popover._show_pane(PANE_BOX)


class _PokedexEntryHandler(NSObject):
    """Click target for a caught Pokedex row — opens the species detail pane."""

    def initWithPopover_dexId_(self, popover, dex_id):  # noqa: N802
        self = objc.super(_PokedexEntryHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._dex_id = int(dex_id)
        return self

    def entryClicked_(self, _sender):  # noqa: N802
        self._popover._pokedex_selected_dex = self._dex_id
        self._popover._show_pane(PANE_TOKENDEX)


class _PokedexBackHandler(NSObject):
    """← Back from Pokedex detail to the species list."""

    def initWithPopover_(self, popover):  # noqa: N802
        self = objc.super(_PokedexBackHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        return self

    def backClicked_(self, _sender):  # noqa: N802
        self._popover._pokedex_selected_dex = None
        self._popover._show_pane(PANE_TOKENDEX)


class _SetActiveHandler(NSObject):
    """Box-detail "Set as active" button — pins the active Pokemon and re-renders."""

    def initWithPopover_pokemonId_(self, popover, pokemon_id):  # noqa: N802
        self = objc.super(_SetActiveHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._pokemon_id = int(pokemon_id)
        return self

    def setActiveClicked_(self, _sender):  # noqa: N802
        try:
            from tokenmon import box
            box.set_active_pokemon(self._pokemon_id)
        except Exception:
            log.exception("set_active_pokemon failed")
        # Order matters: update the menubar app's internal _pokemon_sprite
        # FIRST (so the sidebar icon refresh picks up the new sprite), then
        # the sidebar icon, then the box detail re-render. We deliberately
        # skip app.refresh(None) — it rebuilds the menu and resets the
        # status-bar animator, which causes a brief image-blank that shifts
        # the popover's anchor by a few pixels. The next 5s activity_poll
        # picks up tooltip and title changes naturally.
        try:
            app = self._popover._app
            if hasattr(app, "_refresh_pokemon_state"):
                app._refresh_pokemon_state()
        except Exception:
            log.exception("menubar refresh after set_active failed")
        try:
            self._popover._refresh_sidebar_pokemon_icon()
        except Exception:
            log.exception("sidebar icon refresh failed")
        # Re-render the detail view so the button label flips.
        self._popover._show_pane(PANE_BOX)


# =============================================================================
# Encounter pane click handlers
# =============================================================================


class _BagOpenHandler(NSObject):
    """[🎒 Bag] click target — flips the popover into bag-open mode."""

    def initWithPopover_(self, popover):  # noqa: N802
        self = objc.super(_BagOpenHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        return self

    def openClicked_(self, _sender):  # noqa: N802
        self._popover._encounter_bag_open = True
        self._popover._show_pane(PANE_ENCOUNTER)


class _BagBackHandler(NSObject):
    """[← Back] click target inside the bag-open view."""

    def initWithPopover_(self, popover):  # noqa: N802
        self = objc.super(_BagBackHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        return self

    def backClicked_(self, _sender):  # noqa: N802
        self._popover._encounter_bag_open = False
        self._popover._show_pane(PANE_ENCOUNTER)


class _ItemRowHandler(NSObject):
    """Per-row click target inside the bag-open inventory list.

    Click on the row → opens a native NSMenu anchored to the row's NSButton,
    with one item per ``Item.actions`` entry. Selecting a menu item dispatches
    based on the action key — currently only ``throw`` is implemented.
    """

    def initWithPopover_encounterId_itemKey_(self, popover, encounter_id, item_key):  # noqa: N802
        self = objc.super(_ItemRowHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._encounter_id = int(encounter_id)
        self._item_key = str(item_key)
        return self

    def _title_for_action(self, action: str) -> str:
        item = items.get(self._item_key)
        name = item.display_name if item is not None else self._item_key
        template = ACTION_TITLES.get(action, action)
        try:
            return template.format(name=name)
        except (KeyError, IndexError):
            return template

    def itemRowClicked_(self, sender):  # noqa: N802
        item = items.get(self._item_key)
        if item is None or not item.actions:
            return
        # Single-action items (currently every throwable item): skip the
        # context menu and execute the action directly. The menu was nice in
        # theory but a one-item popup just adds an extra click for no gain.
        if len(item.actions) == 1:
            self._dispatch_action(item.actions[0])
            return
        menu = NSMenu.alloc().initWithTitle_("")
        for action in item.actions:
            title = self._title_for_action(action)
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, b"actionSelected:", "",
            )
            mi.setTarget_(self)
            mi.setRepresentedObject_(action)
            menu.addItem_(mi)
        # Use the button-anchored variant — popUpContextMenuWithEvent_ wants
        # a right-mouse-down event we don't have during a left-click action.
        try:
            bounds = sender.bounds()
            location = (0.0, float(bounds.size.height))
            menu.popUpMenuPositioningItem_atLocation_inView_(
                None, location, sender,
            )
        except Exception:
            log.exception("item-row context menu failed")

    def _dispatch_action(self, action: str) -> None:
        """Same dispatch path used by both the menu (multi-action items) and
        the direct-execute shortcut for single-action items."""
        if action == "throw":
            # Snapshot the encounter's species before resolving the throw —
            # use_item will mutate it to resolved on a catch and the silhouette
            # we want to show during the animation needs the dex_id from the
            # *unresolved* row.
            try:
                pending = get_pending_encounter()
            except Exception:
                log.exception("get_pending_encounter failed")
                return
            if pending is None or pending.id != self._encounter_id:
                return
            species_dex_id = int(pending.species_dex_id)
            try:
                result = encounter.use_item(self._encounter_id, self._item_key)
            except Exception:
                log.exception("use_item(throw) failed")
                return
            # Drop bag-open state so the post-animation outcome lands cleanly:
            # success → reveal pane (sidebar refreshes), failure → we re-set
            # bag-open inside _end_catch_animation before re-rendering.
            self._popover._encounter_bag_open = False
            self._popover._begin_catch_animation(
                item_key=self._item_key,
                encounter_id=self._encounter_id,
                species_dex_id=species_dex_id,
                caught=bool(result.get("caught")),
                shakes=int(result.get("shakes", 0)),
                hint=result.get("hint"),
            )

    def actionSelected_(self, sender):  # noqa: N802
        try:
            action = str(sender.representedObject())
        except Exception:
            return
        self._dispatch_action(action)


class _RunAwayHandler(NSObject):
    """Run-away button — resolves the encounter as 'ran' and falls back to Today."""

    def initWithPopover_encounterId_(self, popover, encounter_id):  # noqa: N802
        self = objc.super(_RunAwayHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._encounter_id = int(encounter_id)
        return self

    def runAwayClicked_(self, _sender):  # noqa: N802
        try:
            encounter.run_away(self._encounter_id)
        except Exception:
            log.exception("run_away failed")
        self._popover._show_pane(PANE_POKEMON)


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
            self._popover._show_pane(PANE_POKEMON)
        except Exception:
            log.exception("reveal teardown failed")


class _DebugSpawnHandler(NSObject):
    """Usage-pane debug button — force-spawns an encounter or shows '(already pending)'."""

    def initWithPopover_(self, popover):  # noqa: N802
        self = objc.super(_DebugSpawnHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        return self

    def spawnClicked_(self, _sender):  # noqa: N802
        try:
            spawned = encounter.maybe_spawn(force=True)
        except Exception:
            log.exception("maybe_spawn(force=True) failed")
            return
        if spawned is None:
            self._popover._flash_already_pending()
            return
        self._popover._show_pane(PANE_ENCOUNTER)


# =============================================================================
# Catch animation — GBA-style throw → absorb → wobble → outcome
# =============================================================================


# Animation tunables. Wobble is implemented as a horizontal frame translation
# (no CALayer transforms required). Total runtime ranges from ~1.5s (0 shakes,
# instant break-out) to ~3.7s (3 shakes + click + reveal handoff).
CATCH_BALL_SIZE = 40
CATCH_THROW_FRAMES = 3
CATCH_WOBBLE_DX = 14
CATCH_REST_DROP_PX = 24


def _build_catch_steps(caught: bool, shakes: int) -> list[tuple[float, str]]:
    """Construct the (delay, action) tape played by _CatchAnimationHandler.

    ``shakes`` is 0..3. ``caught`` only affects the outcome cap (`click` vs
    `burst`). The total runtime scales linearly with ``shakes`` — that's the
    whole point: a 0-shake break-out feels rapidly disappointing, a 3-shake
    catch feels suspenseful.
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
    """NSTimer target that drives the catch animation step-by-step.

    Mirrors :class:`tokenmon.overlay._EvolutionHandler`: a flat list of
    ``(delay, action)`` tuples is consumed one one-shot timer at a time, with
    the popover responsible for actually mutating views per action.
    """

    def initWithPopover_payload_(self, popover, payload):  # noqa: N802
        self = objc.super(_CatchAnimationHandler, self).init()
        if self is None:
            return None
        self._popover = popover
        self._payload = payload  # {caught, shakes, hint, item_key, encounter_id}
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
        self._rebuild_sidebar()
        self._root.addSubview_(self._sidebar)

        self._content_container = NSView.alloc().initWithFrame_(
            NSMakeRect(SIDEBAR_WIDTH, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )
        self._root.addSubview_(self._content_container)

        self._current_pane: int = PANE_POKEMON
        self._current_pane_view: NSView | None = None
        self._animated_image_views: list[NSImageView] = []

        # Box pane state — None means show grid, an int id means show detail.
        self._box_selected_id: int | None = None
        self._pokedex_selected_dex: int | None = None
        self._pokedex_handlers: list = []
        self._pokedex_back_handler: _PokedexBackHandler | None = None
        # Strong references to handlers so they aren't garbage-collected
        # while the buttons that target them are alive.
        self._box_handlers: list[NSObject] = []
        self._box_back_handler: _BoxBackHandler | None = None
        self._set_active_handler: _SetActiveHandler | None = None

        # Encounter-pane handler refs.
        self._encounter_bag_open: bool = False
        self._bag_open_handler: _BagOpenHandler | None = None
        self._bag_back_handler: _BagBackHandler | None = None
        self._item_row_handlers: list[_ItemRowHandler] = []
        self._run_away_handler: _RunAwayHandler | None = None
        self._reveal_timer_handler: _RevealTimerHandler | None = None
        self._reveal_timer = None
        self._pending_reveal_pokemon: dict | None = None
        # Catch-animation state. The handler holds its own step index; we just
        # need a strong ref so the NSTimer target survives, plus pointers to
        # the views we mutate per step.
        self._catch_anim_handler: _CatchAnimationHandler | None = None
        self._catch_anim_silhouette: NSImageView | None = None
        self._catch_anim_ball: NSImageView | None = None
        self._catch_anim_flash: NSView | None = None
        self._catch_anim_pane: NSView | None = None
        self._catch_anim_geom: dict | None = None
        self._catch_anim_header: NSTextField | None = None
        self._catch_anim_sparkles: list[NSTextField] = []
        self._debug_spawn_handler: _DebugSpawnHandler | None = None
        self._already_pending_label = None
        self._already_pending_timer = None

        self._vc = _new_vc(self._root)
        self._popover.setContentViewController_(self._vc)

        self._right_click_handler = _RightClickHandler.alloc().init()
        self._global_monitor = None  # NSEvent monitor; set while popover open

        return self

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

        # Determine slot list — encounter slot at top when one is pending.
        try:
            pending = get_pending_encounter()
        except Exception:
            log.exception("get_pending_encounter failed")
            pending = None

        items: list[tuple[int, str]] = []
        if pending is not None:
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
        self._show_pane(idx)

    # ---- panes ----

    def _show_pane(self, idx: int) -> None:
        # Reset per-pane handler refs — new pane gets fresh lists.
        self._animated_image_views = []
        self._box_handlers = []
        self._box_back_handler = None
        self._set_active_handler = None
        self._bag_open_handler = None
        self._bag_back_handler = None
        self._item_row_handlers = []
        self._run_away_handler = None
        self._pokedex_handlers = []
        self._pokedex_back_handler = None
        # Bag-open is part of the *encounter* state machine — leaving the
        # encounter pane drops it; staying inside it (e.g. after a failed
        # throw) preserves the user's bag-open intent.
        if idx != PANE_ENCOUNTER:
            self._encounter_bag_open = False
        # Cancel any pending reveal timer if we're transitioning panes manually.
        if self._reveal_timer is not None:
            try:
                self._reveal_timer.invalidate()
            except Exception:
                pass
            self._reveal_timer = None
        self._reveal_timer_handler = None
        # _pending_reveal_pokemon is set by _begin_catch_reveal and consumed
        # only on the very next pane render — clear it here so e.g. switching
        # to Box and back doesn't accidentally re-show the reveal.
        self._pending_reveal_pokemon = None
        # Drop any in-flight catch-animation refs. The handler's pending
        # NSTimers will still fire, but _catch_step short-circuits when the
        # views are gone, so they become no-ops.
        self._catch_anim_handler = None
        self._catch_anim_silhouette = None
        self._catch_anim_ball = None
        self._catch_anim_pane = None
        self._catch_anim_geom = None
        self._catch_anim_header = None
        self._catch_anim_sparkles = []
        if self._catch_anim_flash is not None:
            try:
                self._catch_anim_flash.removeFromSuperview()
            except Exception:
                pass
            self._catch_anim_flash = None
        try:
            if idx == PANE_ENCOUNTER:
                view = self._build_pane_encounter()
            elif idx == PANE_POKEMON:
                view = self._build_pane_pokemon()
            elif idx == PANE_TOKENDEX:
                view = self._build_pane_tokendex()
            elif idx == PANE_BOX:
                view = self._build_pane_box()
            elif idx == PANE_ITEMS:
                view = self._build_pane_items()
            elif idx == PANE_USAGE:
                view = self._build_pane_usage()
            else:
                view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        except Exception:
            log.exception("failed to build pane %s", idx)
            view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
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

    # =========================================================================
    # Pane: Encounter (silhouette + ball selector + hints + catch reveal)
    # =========================================================================

    def _build_pane_encounter(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # If somehow we render this pane with no pending encounter, show a
        # graceful fallback rather than crashing.
        try:
            enc = get_pending_encounter()
        except Exception:
            log.exception("get_pending_encounter failed")
            enc = None

        if enc is None:
            view.addSubview_(_label(
                NSMakeRect(16, POPOVER_HEIGHT // 2 - 10, CONTENT_WIDTH - 32, 20),
                "Kein wildes Pokemon mehr — schon resolved.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        # --- Header ---
        header_y = POPOVER_HEIGHT - 40
        view.addSubview_(_label(
            NSMakeRect(16, header_y, CONTENT_WIDTH - 32, 22),
            "A wild Pokemon appeared!",
            font=NSFont.boldSystemFontOfSize_(15),
            align=NSTextAlignmentCenter,
        ))

        # --- Silhouette sprite ---
        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = header_y - sprite_size - 8
        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(enc.species_dex_id)
        if sp is not None and sp.exists():
            base = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if base is not None:
                sil = _silhouette_image(base, NSColor.whiteColor())
                iv.setImage_(sil)
                iv.setAnimates_(True)
        view.addSubview_(iv)
        self._animated_image_views.append(iv)

        # --- Level + Type stub ---
        info_y = sprite_y - 24
        view.addSubview_(_label(
            NSMakeRect(0, info_y, CONTENT_WIDTH, 18),
            f"Lv {enc.level}     Type: ???",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        # --- Last hint (italic, only when not in default-mode AND present).
        # Hide the hint in the *default* view so the layout stays tight, but
        # surface it in the bag-open view where the user is mid-throw and
        # needs the feedback.
        hint_y = info_y - 22
        if enc.last_hint and self._encounter_bag_open:
            hint_label = _label(
                NSMakeRect(16, hint_y, CONTENT_WIDTH - 32, 16),
                enc.last_hint,
                font=NSFont.systemFontOfSize_(11),
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            )
            try:
                from Foundation import NSAttributedString
                italic = NSFont.fontWithName_size_("HelveticaNeue-Italic", 11)
                if italic is None:
                    italic = NSFont.systemFontOfSize_(11)
                attrs = {NSFontAttributeName: italic}
                hint_label.setAttributedStringValue_(
                    NSAttributedString.alloc().initWithString_attributes_(
                        enc.last_hint, attrs
                    )
                )
                hint_label.setTextColor_(NSColor.secondaryLabelColor())
                hint_label.setAlignment_(NSTextAlignmentCenter)
            except Exception:
                pass
            view.addSubview_(hint_label)
        else:
            hint_y = info_y  # no gap when no hint shown

        # --- Separator above either action area or inventory.
        sep1_y = hint_y - 8
        sep1 = NSView.alloc().initWithFrame_(NSMakeRect(16, sep1_y, CONTENT_WIDTH - 32, 1))
        sep1.setWantsLayer_(True)
        sep1.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
        view.addSubview_(sep1)

        if self._encounter_bag_open:
            self._build_encounter_bag_open(view, enc, sep1_y)
        else:
            self._build_encounter_actions(view, enc, sep1_y)

        return view

    # --- Encounter pane: default action row (Bag / Run away) ---

    def _build_encounter_actions(self, view: NSView, enc, top_y: int) -> None:
        """Bottom action bar for the default (non-bag-open) encounter pane.
        Two equal-width buttons: [🎒 Bag]  [Run away]."""
        margin = 16
        gap = 12
        btn_y = top_y - 16 - 32
        btn_w = (CONTENT_WIDTH - 2 * margin - gap) // 2
        btn_h = 32

        bag_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin, btn_y, btn_w, btn_h)
        )
        bag_btn.setTitle_("🎒 Bag")
        bag_btn.setBezelStyle_(1)
        self._bag_open_handler = _BagOpenHandler.alloc().initWithPopover_(self)
        bag_btn.setTarget_(self._bag_open_handler)
        bag_btn.setAction_(b"openClicked:")
        view.addSubview_(bag_btn)

        run_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + btn_w + gap, btn_y, btn_w, btn_h)
        )
        run_btn.setTitle_("Run away")
        run_btn.setBezelStyle_(1)
        self._run_away_handler = _RunAwayHandler.alloc().initWithPopover_encounterId_(
            self, enc.id,
        )
        run_btn.setTarget_(self._run_away_handler)
        run_btn.setAction_(b"runAwayClicked:")
        view.addSubview_(run_btn)

    # --- Encounter pane: bag-open inventory list ---

    def _build_encounter_bag_open(self, view: NSView, enc, top_y: int) -> None:
        """Inventory list rendered below the silhouette/info block. Each item
        in the registry gets a row showing emoji + display name + count.
        Rows for items with non-empty Item.actions and a positive count are
        clickable and pop a native NSMenu of actions on click."""
        # "Inventory" header.
        inv_header_y = top_y - 8 - 18
        view.addSubview_(_label(
            NSMakeRect(16, inv_header_y, CONTENT_WIDTH - 32, 18),
            "Inventory",
            font=NSFont.boldSystemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
        ))

        try:
            counts = query_item_counts()
        except Exception:
            log.exception("query_item_counts failed")
            counts = {}

        row_h = 26
        rows_top = inv_header_y - 4
        registry_keys = list(items.ITEMS.keys())
        for i, key in enumerate(registry_keys):
            item = items.ITEMS[key]
            count = int(counts.get(key, 0) or 0)
            has_action = bool(item.actions)
            enabled = has_action and count > 0

            y = rows_top - (i + 1) * row_h
            btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(16, y, CONTENT_WIDTH - 32, row_h - 2)
            )
            chevron = "  ›" if enabled else ""
            sprite = items_remote.get_item_image(item)
            if sprite is not None:
                sprite.setSize_(NSMakeSize(20, 20))
                btn.setImage_(sprite)
                btn.setImagePosition_(NSImageLeft)
                btn.setTitle_(f"  {item.display_name}     × {count}{chevron}")
            else:
                btn.setTitle_(
                    f"{item.emoji}  {item.display_name}     × {count}{chevron}"
                )
            btn.setBezelStyle_(1)
            btn.setAlignment_(0)  # left
            btn.setEnabled_(enabled)
            if not enabled:
                # Greyed-out look for disabled rows. setEnabled_(False) already
                # gives macOS-standard styling but the row title stays at
                # default colour — we set an attributed title for the faint
                # tertiary look the spec asks for.
                try:
                    from Foundation import NSAttributedString
                    attrs = {
                        NSFontAttributeName: NSFont.systemFontOfSize_(13),
                    }
                    title = btn.title()
                    btn.setAttributedTitle_(
                        NSAttributedString.alloc().initWithString_attributes_(
                            title, attrs,
                        )
                    )
                except Exception:
                    pass
            else:
                handler = _ItemRowHandler.alloc().initWithPopover_encounterId_itemKey_(
                    self, enc.id, key,
                )
                self._item_row_handlers.append(handler)
                btn.setTarget_(handler)
                btn.setAction_(b"itemRowClicked:")
            view.addSubview_(btn)

        # --- Bottom action bar: [← Back]  [Run away] ---
        margin = 16
        gap = 12
        btn_y = 16
        btn_w = (CONTENT_WIDTH - 2 * margin - gap) // 2
        btn_h = 32

        back_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin, btn_y, btn_w, btn_h)
        )
        back_btn.setTitle_("← Back")
        back_btn.setBezelStyle_(1)
        self._bag_back_handler = _BagBackHandler.alloc().initWithPopover_(self)
        back_btn.setTarget_(self._bag_back_handler)
        back_btn.setAction_(b"backClicked:")
        view.addSubview_(back_btn)

        run_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + btn_w + gap, btn_y, btn_w, btn_h)
        )
        run_btn.setTitle_("Run away")
        run_btn.setBezelStyle_(1)
        self._run_away_handler = _RunAwayHandler.alloc().initWithPopover_encounterId_(
            self, enc.id,
        )
        run_btn.setTarget_(self._run_away_handler)
        run_btn.setAction_(b"runAwayClicked:")
        view.addSubview_(run_btn)

    # --- Encounter pane: catch reveal animation ---

    def _begin_catch_reveal(self) -> None:
        """Trigger the cross-fade reveal: fetch the just-resolved encounter
        (which has ``resolved='caught'`` and ``pokemon_id`` set), stash it,
        rebuild the encounter pane (which now switches into reveal mode), and
        schedule a 2.5s timer to dismiss to the Today pane.
        """
        # The catch already mutated state — get_pending_encounter() now returns
        # None. Pull the most recent caught encounter from the DB to find which
        # species was just caught.
        from tokenmon.storage import _connect
        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT species_dex_id, pokemon_id "
                    "FROM encounters "
                    "WHERE resolved = 'caught' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
        except Exception:
            log.exception("query last-caught encounter failed")
            row = None

        if row is None:
            # Defensive — shouldn't happen, but if it does just bail to Today.
            self._show_pane(PANE_POKEMON)
            return

        self._pending_reveal_pokemon = {
            "species_dex_id": int(row[0]),
            "pokemon_id": int(row[1]) if row[1] is not None else None,
        }
        # Rebuild pane in reveal mode. Keep _current_pane = PANE_ENCOUNTER but
        # replace the view; we don't go through _show_pane() because that would
        # rebuild the sidebar (which would drop the encounter slot since the
        # encounter is now resolved) — we want the slot to stick around for the
        # 2.5s reveal hold.
        view = self._build_pane_encounter_reveal(self._pending_reveal_pokemon)
        if self._current_pane_view is not None:
            self._current_pane_view.removeFromSuperview()
        view.setFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        self._content_container.addSubview_(view)
        self._current_pane_view = view

        # Schedule the dismiss timer.
        self._reveal_timer_handler = _RevealTimerHandler.alloc().initWithPopover_(self)
        self._reveal_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                2.5, self._reveal_timer_handler, b"fire:", None, False,
            )
        )

    def _build_pane_encounter_reveal(self, payload: dict) -> NSView:
        """Reveal layout: real animated sprite + 'caught!' banner. Used both
        when the reveal first kicks off and during the 2.5s hold."""
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        species_dex_id = int(payload["species_dex_id"])
        species_name = pokemon.name_of(species_dex_id)

        # Banner.
        banner_y = POPOVER_HEIGHT - 50
        view.addSubview_(_label(
            NSMakeRect(16, banner_y, CONTENT_WIDTH - 32, 24),
            "Pokemon was caught!",
            font=NSFont.boldSystemFontOfSize_(16),
            align=NSTextAlignmentCenter,
        ))
        view.addSubview_(_label(
            NSMakeRect(16, banner_y - 22, CONTENT_WIDTH - 32, 18),
            f"{species_name} added to your Box.",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        # Real (non-silhouette) sprite at the same position the silhouette had.
        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = banner_y - 32 - sprite_size - 8
        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(species_dex_id)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
                iv.setAnimates_(True)
        view.addSubview_(iv)
        self._animated_image_views.append(iv)

        # Name label below sprite.
        name_y = sprite_y - 28
        view.addSubview_(_label(
            NSMakeRect(0, name_y, CONTENT_WIDTH, 22),
            f"#{species_dex_id:03d}  {species_name}",
            font=NSFont.boldSystemFontOfSize_(15),
            align=NSTextAlignmentCenter,
        ))

        return view

    # =========================================================================
    # Pane: Catch animation (between throw and outcome — silhouette + wobble)
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
        """Replace the encounter pane content with the animation pane and
        start the timer-driven step sequence.

        We deliberately don't call ``_show_pane`` — that would rebuild the
        sidebar (dropping the encounter slot now that the encounter resolved
        in the DB) and reset ``_encounter_bag_open``. We want the slot and
        the bag-open state to persist for the duration of the animation, just
        like the existing reveal flow at popover.py:_begin_catch_reveal.
        """
        payload = {
            "item_key": item_key,
            "encounter_id": int(encounter_id),
            "species_dex_id": int(species_dex_id),
            "caught": bool(caught),
            "shakes": int(shakes),
            "hint": hint,
        }
        view = self._build_pane_catch_animation(payload)
        if self._current_pane_view is not None:
            self._current_pane_view.removeFromSuperview()
        view.setFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        self._content_container.addSubview_(view)
        self._current_pane_view = view
        self._catch_anim_pane = view

        self._catch_anim_handler = (
            _CatchAnimationHandler.alloc().initWithPopover_payload_(self, payload)
        )
        self._catch_anim_handler.start()

    def _build_pane_catch_animation(self, payload: dict) -> NSView:
        """Build the pane the catch animation runs on. Same layout as the
        encounter default pane (header + silhouette in the same spot) so the
        transition is seamless when the animation pane replaces it.
        """
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # --- Header (same look as the encounter-default pane). Kept as a ref
        # so the catch-success step can swap it to "Gotcha!" in yellow. ---
        header_y = POPOVER_HEIGHT - 40
        header = _label(
            NSMakeRect(16, header_y, CONTENT_WIDTH - 32, 22),
            "A wild Pokemon appeared!",
            font=NSFont.boldSystemFontOfSize_(15),
            align=NSTextAlignmentCenter,
        )
        view.addSubview_(header)
        self._catch_anim_header = header

        # --- Silhouette sprite (same position as the default pane) ---
        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = header_y - sprite_size - 8
        sil_iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        species_dex_id = int(payload["species_dex_id"])
        sp = pokemon.ensure_sprite(species_dex_id)
        if sp is not None and sp.exists():
            base = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if base is not None:
                sil = _silhouette_image(base, NSColor.whiteColor())
                sil_iv.setImage_(sil)
                sil_iv.setAnimates_(True)
        view.addSubview_(sil_iv)
        self._animated_image_views.append(sil_iv)
        self._catch_anim_silhouette = sil_iv

        # --- Ball sprite — starts off-pane top-right, hidden until throw_start ---
        ball = _crisp_image_view(NSMakeRect(
            CONTENT_WIDTH + CATCH_BALL_SIZE,  # off-pane to the right
            sprite_y + sprite_size,
            CATCH_BALL_SIZE, CATCH_BALL_SIZE,
        ))
        item = items.get(payload["item_key"])
        if item is not None:
            ball_img = items_remote.get_item_image(item)
            if ball_img is not None:
                ball.setImage_(ball_img)
        ball.setHidden_(True)
        view.addSubview_(ball)
        self._catch_anim_ball = ball

        # Stash the geometry the step handler needs so it doesn't re-derive
        # positions every frame.
        sprite_centre_x = sprite_x + sprite_size // 2
        sprite_centre_y = sprite_y + sprite_size // 2
        rest_x = sprite_centre_x - CATCH_BALL_SIZE // 2
        rest_y = sprite_y + CATCH_REST_DROP_PX  # near the bottom of the sprite area
        absorb_x = sprite_centre_x - CATCH_BALL_SIZE // 2
        absorb_y = sprite_centre_y - CATCH_BALL_SIZE // 2
        # Three frames along an arc from off-pane-right + above to absorb point.
        start_x = CONTENT_WIDTH + CATCH_BALL_SIZE
        start_y = sprite_y + sprite_size + 40
        arc_frames: list[tuple[int, int]] = []
        for i in range(1, CATCH_THROW_FRAMES + 1):
            t = i / float(CATCH_THROW_FRAMES)
            x = int(start_x + (absorb_x - start_x) * t)
            # Simple parabolic arc: peak roughly between start and absorb.
            arc_y = start_y + (absorb_y - start_y) * t
            lift = -28 * (4 * t * (1 - t))  # max −28 px at t=0.5
            arc_frames.append((x, int(arc_y + lift)))
        self._catch_anim_geom = {
            "rest_x": rest_x,
            "rest_y": rest_y,
            "absorb_x": absorb_x,
            "absorb_y": absorb_y,
            "arc_frames": arc_frames,
            "ball_size": CATCH_BALL_SIZE,
        }

        # --- Sparkles, hidden until the caught-success steps reveal them.
        # Four glyphs scattered around the ball at rest, each at slightly
        # different size so the burst feels less rigid.
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
        self._catch_anim_sparkles = sparkles

        return view

    def _catch_step(self, action: str, payload: dict) -> None:
        """Execute one step of the catch animation. Called from
        :class:`_CatchAnimationHandler` on the main thread."""
        ball = self._catch_anim_ball
        sil = self._catch_anim_silhouette
        geom = self._catch_anim_geom or {}
        if ball is None or sil is None or not geom:
            # View was torn down (user navigated away). Just bail — the timer
            # will fire its remaining steps as no-ops.
            return

        size = geom["ball_size"]

        if action == "throw_start":
            # Reveal the ball at the off-pane start. Arc frames will move it
            # toward the silhouette over the next few steps.
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
            ball.setFrame_(NSMakeRect(geom["absorb_x"], geom["absorb_y"], size, size))
            sil.setHidden_(True)
            self._show_catch_flash()
            return

        if action == "flash_end":
            self._hide_catch_flash()
            return

        if action == "ball_drop":
            ball.setFrame_(NSMakeRect(geom["rest_x"], geom["rest_y"], size, size))
            return

        if action.startswith("shake_left_"):
            ball.setFrame_(NSMakeRect(
                geom["rest_x"] - CATCH_WOBBLE_DX, geom["rest_y"] + 2, size, size,
            ))
            return

        if action.startswith("shake_right_"):
            ball.setFrame_(NSMakeRect(
                geom["rest_x"] + CATCH_WOBBLE_DX, geom["rest_y"] + 2, size, size,
            ))
            return

        if action.startswith("shake_centre_"):
            ball.setFrame_(NSMakeRect(geom["rest_x"], geom["rest_y"], size, size))
            return

        if action == "click":
            # Brief flash to mark the catch confirmation; flash_end is folded
            # into the next step (caught_announce) which doesn't need it gone.
            self._show_catch_flash()
            return

        if action == "caught_announce":
            self._hide_catch_flash()
            if self._catch_anim_header is not None:
                self._catch_anim_header.setStringValue_("Gotcha!")
                self._catch_anim_header.setTextColor_(
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        1.0, 0.85, 0.0, 1.0,
                    )
                )
            return

        if action.startswith("caught_sparkle_"):
            idx = int(action.rsplit("_", 1)[-1]) - 1
            if 0 <= idx < len(self._catch_anim_sparkles):
                self._catch_anim_sparkles[idx].setHidden_(False)
            return

        if action == "caught_hold":
            return  # ball + sparkles + banner already on-screen; just hold

        if action == "burst":
            # Pokémon escapes — hide the ball, bring the silhouette back.
            ball.setHidden_(True)
            sil.setHidden_(False)
            return

        if action == "done":
            self._end_catch_animation(payload)
            return

    def _show_catch_flash(self) -> None:
        if self._catch_anim_pane is None or self._catch_anim_flash is not None:
            return
        bounds = self._catch_anim_pane.bounds()
        flash = NSView.alloc().initWithFrame_(bounds)
        flash.setWantsLayer_(True)
        flash.layer().setBackgroundColor_(NSColor.whiteColor().CGColor())
        self._catch_anim_pane.addSubview_(flash)
        self._catch_anim_flash = flash

    def _hide_catch_flash(self) -> None:
        if self._catch_anim_flash is None:
            return
        try:
            self._catch_anim_flash.removeFromSuperview()
        except Exception:
            log.exception("flash teardown failed")
        self._catch_anim_flash = None

    def _end_catch_animation(self, payload: dict) -> None:
        """Final step — drop animation refs and route to the outcome pane."""
        self._catch_anim_handler = None
        self._catch_anim_silhouette = None
        self._catch_anim_ball = None
        self._catch_anim_pane = None
        self._catch_anim_geom = None
        self._catch_anim_header = None
        self._catch_anim_sparkles = []
        self._hide_catch_flash()

        if payload.get("caught"):
            # Hand off to the existing reveal flow, which schedules its own
            # 2.5s timer and dismisses to the Today pane afterwards.
            self._begin_catch_reveal()
        else:
            # Failure — return to the bag-open pane so the user can throw
            # again. The new hint is already persisted on the encounter row
            # by encounter._resolve_throw, so _build_pane_encounter will pick
            # it up.
            self._encounter_bag_open = True
            self._show_pane(PANE_ENCOUNTER)

    # =========================================================================
    # Pane: Today (active Pokemon detail — defaults to today's catch but the
    # user can pin any owned Pokemon as active via the Box detail view).
    # =========================================================================

    def _build_pane_pokemon(self) -> NSView:
        # Lazy import to avoid a circular import (box → storage → ...).
        from tokenmon import box

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # Make sure today's row exists so a fresh install isn't blank, then
        # ask box.get_active_pokemon() — which honours config['active_pokemon_id']
        # and falls back to today's catch.
        try:
            box.ensure_today_pokemon()
            row = box.get_active_pokemon()
        except Exception:
            log.exception("get_active_pokemon failed")
            row = None

        if row is None:
            view.addSubview_(_label(
                NSMakeRect(16, POPOVER_HEIGHT // 2 - 10, CONTENT_WIDTH - 32, 20),
                "Konnte aktives Pokemon nicht laden.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        # The row's species_dex_id IS the current form (evolution mutates the
        # row in place via box.maybe_evolve), so no derive-on-render needed.
        species = row.species_dex_id
        try:
            xp = query_xp_for_pokemon(row.id)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(species)
        level, into, needed = pokemon.level_from_xp(xp, rate)

        # "Active: …" header, top of pane.
        header_y = POPOVER_HEIGHT - 28
        view.addSubview_(_label(
            NSMakeRect(0, header_y, CONTENT_WIDTH, 20),
            f"Active: {pokemon.name_of(species)}",
            font=NSFont.boldSystemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = header_y - sprite_size - 12

        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(species)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self._animated_image_views.append(iv)

        name = pokemon.name_of(species)
        name_y = sprite_y - 32
        view.addSubview_(_label(
            NSMakeRect(0, name_y, CONTENT_WIDTH, 26),
            f"#{species:03d}  {name}",
            font=NSFont.boldSystemFontOfSize_(18),
            align=NSTextAlignmentCenter,
        ))

        lvl_y = name_y - 28
        lvl_text = "Lv MAX" if level >= pokemon.MAX_LEVEL else f"Lv {level}"
        view.addSubview_(_label(
            NSMakeRect(0, lvl_y, CONTENT_WIDTH, 22),
            lvl_text,
            font=NSFont.boldSystemFontOfSize_(14),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        bar_w = 260
        bar_x = (CONTENT_WIDTH - bar_w) // 2
        bar_y = lvl_y - 14
        progress = into / needed if needed > 0 else (1.0 if level >= pokemon.MAX_LEVEL else 0.0)
        bar = _XPBarView.alloc().initWithFrame_progress_(
            NSMakeRect(bar_x, bar_y, bar_w, 8), progress,
        )
        view.addSubview_(bar)

        xp_y = bar_y - 20
        xp_text = "MAX" if level >= pokemon.MAX_LEVEL else f"{into:,} / {needed:,} XP"
        view.addSubview_(_label(
            NSMakeRect(0, xp_y, CONTENT_WIDTH, 14),
            xp_text,
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        nature_y = xp_y - 24
        view.addSubview_(_label(
            NSMakeRect(0, nature_y, CONTENT_WIDTH, 16),
            f"{row.nature} nature",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.labelColor(),
            align=NSTextAlignmentCenter,
        ))

        char_y = nature_y - 18
        view.addSubview_(_label(
            NSMakeRect(0, char_y, CONTENT_WIDTH, 16),
            f"“{row.characteristic}.”",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        return view

    # =========================================================================
    # Pane: Pokedex (species-counts from box)
    # =========================================================================

    def _build_pane_tokendex(self) -> NSView:
        # Detail-view branch when the user clicked a caught entry.
        if self._pokedex_selected_dex is not None:
            return self._build_pane_pokedex_detail(self._pokedex_selected_dex)
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # Pull caught species from the box and seen species from encounters.
        from tokenmon.storage import list_distinct_encounter_species
        try:
            box_rows = list_pokemon()
        except Exception:
            log.exception("list_pokemon failed")
            box_rows = []
        try:
            seen_set = list_distinct_encounter_species()
        except Exception:
            log.exception("list_distinct_encounter_species failed")
            seen_set = set()
        caught_set: set[int] = {p.species_dex_id for p in box_rows}

        # Walk the canonical 151 Gen-1 ids in dex order. ALL_NAMES covers them.
        all_ids = sorted(pokemon.ALL_NAMES.keys())

        # Header
        caught_n = sum(1 for i in all_ids if i in caught_set)
        seen_n = sum(1 for i in all_ids if i in seen_set and i not in caught_set)
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, CONTENT_WIDTH - 32, 22),
            f"Pokedex   ·   {caught_n} caught   ·   {seen_n} seen",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        scroll_h = POPOVER_HEIGHT - 44
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, scroll_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)

        row_h = 44
        row_width = CONTENT_WIDTH - 16
        content_h = max(row_h * len(all_ids), scroll_h)
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, row_width, content_h))

        for i, dex_id in enumerate(all_ids):
            y = content_h - (i + 1) * row_h
            if dex_id in caught_set:
                state = "caught"
            elif dex_id in seen_set:
                state = "seen"
            else:
                state = "unknown"
            content.addSubview_(self._build_pokedex_row(
                NSMakeRect(0, y, row_width, row_h - 4), dex_id, state,
            ))

        scroll.setDocumentView_(content)
        # Scroll to the top (#001) by default.
        scroll.contentView().scrollToPoint_((0, max(0, content_h - scroll_h)))
        view.addSubview_(scroll)

        return view

    def _build_pokedex_row(self, frame, dex_id: int, state: str) -> NSView:
        """One Pokedex entry: dex#, sprite (or silhouette), name (or ???).

        state ∈ {"caught", "seen", "unknown"}. Caught shows the full coloured
        animated sprite + the species name; clicking opens the species
        detail pane. Seen shows a white silhouette + "Seen". Unknown shows
        a faint "?" + "???"."""
        # When caught, wrap the row contents in an NSButton so the whole row
        # is clickable. Otherwise a plain NSView (no interaction).
        height = frame.size.height
        width = frame.size.width
        sprite_size = 28

        if state == "caught":
            row = NSButton.alloc().initWithFrame_(frame)
            row.setTitle_("")
            row.setBordered_(False)
            row.setBezelStyle_(NSBezelStyleRegularSquare)
            handler = _PokedexEntryHandler.alloc().initWithPopover_dexId_(self, dex_id)
            self._pokedex_handlers.append(handler)
            row.setTarget_(handler)
            row.setAction_(b"entryClicked:")
        else:
            row = NSView.alloc().initWithFrame_(frame)

        sprite_x = 16
        sprite_y = (height - sprite_size) / 2

        if state == "caught":
            iv = _crisp_image_view(
                NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size)
            )
            sp = pokemon.ensure_sprite(dex_id)
            if sp is not None and sp.exists():
                img = NSImage.alloc().initWithContentsOfFile_(str(sp))
                if img is not None:
                    iv.setImage_(img)
            row.addSubview_(iv)
            self._animated_image_views.append(iv)
        elif state == "seen":
            # Silhouette: load sprite, render through _silhouette_image with
            # a neutral grey so unknown-but-encountered species look distinct
            # from the still-totally-unknown ones.
            from tokenmon.overlay import _silhouette_image
            iv = _crisp_image_view(
                NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size)
            )
            sp = pokemon.ensure_sprite(dex_id)
            if sp is not None and sp.exists():
                img = NSImage.alloc().initWithContentsOfFile_(str(sp))
                if img is not None:
                    sil = _silhouette_image(
                        img, NSColor.colorWithCalibratedWhite_alpha_(0.55, 1.0)
                    )
                    iv.setImage_(sil)
                    iv.setAnimates_(False)
            row.addSubview_(iv)
        else:
            # Unknown: a faint "?" mark in place of any sprite.
            row.addSubview_(_label(
                NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size),
                "?",
                font=NSFont.boldSystemFontOfSize_(18),
                color=NSColor.tertiaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))

        text_x = sprite_x + sprite_size + 12
        text_w = width - text_x - 16

        if state == "caught":
            label_text = f"#{dex_id:03d}  {pokemon.name_of(dex_id)}"
            color = NSColor.labelColor()
        elif state == "seen":
            label_text = f"#{dex_id:03d}  Seen"
            color = NSColor.secondaryLabelColor()
        else:
            label_text = f"#{dex_id:03d}  ???"
            color = NSColor.tertiaryLabelColor()

        row.addSubview_(_label(
            NSMakeRect(text_x, (height - 18) / 2, text_w, 18),
            label_text,
            font=NSFont.systemFontOfSize_(13),
            color=color,
        ))

        return row

    # =========================================================================
    # Pane: Box (grid of caught Pokemon + per-id detail view)
    # =========================================================================

    def _build_pane_pokedex_detail(self, dex_id: int) -> NSView:
        """Detail view for a caught species: big animated sprite + name +
        genus + flavour text from PokeAPI."""
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # Back button.
        self._pokedex_back_handler = _PokedexBackHandler.alloc().initWithPopover_(self)
        back = NSButton.alloc().initWithFrame_(
            NSMakeRect(8, POPOVER_HEIGHT - 32, 80, 24)
        )
        back.setTitle_("← Back")
        back.setBezelStyle_(1)  # NSBezelStyleRounded
        back.setTarget_(self._pokedex_back_handler)
        back.setAction_(b"backClicked:")
        view.addSubview_(back)

        # Big animated sprite, top-centred.
        sprite_size = 128
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = POPOVER_HEIGHT - 36 - sprite_size
        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(dex_id)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self._animated_image_views.append(iv)

        # Name (#NNN  Name) under the sprite.
        name = pokemon.name_of(dex_id)
        name_y = sprite_y - 30
        view.addSubview_(_label(
            NSMakeRect(0, name_y, CONTENT_WIDTH, 24),
            f"#{dex_id:03d}  {name}",
            font=NSFont.boldSystemFontOfSize_(16),
            align=NSTextAlignmentCenter,
        ))

        # Fetch species info (cached lazily). On miss, show a friendly fallback.
        try:
            from tokenmon.pokedex_remote import get_species_info
            info = get_species_info(dex_id)
        except Exception:
            log.exception("get_species_info failed")
            info = None

        genus = (info or {}).get("genus") or ""
        description = (info or {}).get("description") or ""

        # Genus line.
        genus_y = name_y - 22
        view.addSubview_(_label(
            NSMakeRect(0, genus_y, CONTENT_WIDTH, 18),
            genus or "—",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        # Description block — multi-line wrapped text field.
        desc_y_top = genus_y - 12
        desc_h = desc_y_top - 16
        desc_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, 16, CONTENT_WIDTH - 40, desc_h)
        )
        desc_field.setStringValue_(description or "(No description available — try again later.)")
        desc_field.setBezeled_(False)
        desc_field.setDrawsBackground_(False)
        desc_field.setEditable_(False)
        desc_field.setSelectable_(True)
        desc_field.setFont_(NSFont.systemFontOfSize_(12))
        desc_field.setTextColor_(NSColor.labelColor())
        desc_field.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
        try:
            desc_field.cell().setWraps_(True)
        except Exception:
            pass
        view.addSubview_(desc_field)

        return view

    def _build_pane_box(self) -> NSView:
        if self._box_selected_id is None:
            return self._build_pane_box_grid()
        return self._build_pane_box_detail(self._box_selected_id)

    def _build_pane_box_grid(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        try:
            rows = list_pokemon()
        except Exception:
            log.exception("list_pokemon failed")
            rows = []

        # Header.
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, CONTENT_WIDTH - 32, 22),
            f"Box ({len(rows)} caught)",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        scroll_h = POPOVER_HEIGHT - 44
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, scroll_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)

        cols = 8
        cell = 40
        gap = 6
        margin = 12
        # Grid metrics.
        n = len(rows)
        rows_count = max(1, (n + cols - 1) // cols)
        grid_w = cols * cell + (cols - 1) * gap
        # Doc view: tall enough for all items.
        content_h = max(rows_count * cell + (rows_count - 1) * gap + margin * 2, scroll_h)
        doc_w = CONTENT_WIDTH - 16  # account for scrollbar gutter
        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, doc_w, content_h))

        if not rows:
            content.addSubview_(_label(
                NSMakeRect(16, content_h / 2 - 10, doc_w - 32, 20),
                "No Pokemon caught yet.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
        else:
            grid_x0 = max(margin, (doc_w - grid_w) // 2)
            for i, p in enumerate(rows):
                col = i % cols
                row_idx = i // cols
                x = grid_x0 + col * (cell + gap)
                # Top-aligned: row 0 is at the top of the doc view.
                y = content_h - margin - (row_idx + 1) * cell - row_idx * gap

                btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, cell, cell))
                btn.setTitle_("")
                btn.setBordered_(False)
                btn.setBezelStyle_(NSBezelStyleRegularSquare)
                btn.setWantsLayer_(True)
                if btn.layer() is not None:
                    btn.layer().setMagnificationFilter_("nearest")
                    btn.layer().setMinificationFilter_("nearest")
                # species_dex_id reflects the CURRENT form (evolution mutates
                # the row in place), so just read it directly.
                sp = pokemon.ensure_sprite(p.species_dex_id)
                if sp is not None and sp.exists():
                    img = NSImage.alloc().initWithContentsOfFile_(str(sp))
                    if img is not None:
                        img.setSize_(NSMakeSize(36, 36))
                        btn.setImage_(img)
                # Wire click → set _box_selected_id, re-render.
                handler = _BoxItemHandler.alloc().initWithPopover_pokemonId_(self, p.id)
                self._box_handlers.append(handler)
                btn.setTarget_(handler)
                btn.setAction_(b"itemClicked:")
                content.addSubview_(btn)

        scroll.setDocumentView_(content)
        # Scroll to top — newest entries (sorted desc by caught_date) sit at top.
        scroll.contentView().scrollToPoint_((0, max(0, content_h - scroll_h)))
        view.addSubview_(scroll)

        return view

    def _build_pane_box_detail(self, pokemon_id: int) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # Look up the row.
        from tokenmon.storage import get_pokemon_by_id
        try:
            p = get_pokemon_by_id(pokemon_id)
        except Exception:
            log.exception("get_pokemon_by_id failed")
            p = None

        # ← Back button (top-left).
        self._box_back_handler = _BoxBackHandler.alloc().initWithPopover_(self)
        back = NSButton.alloc().initWithFrame_(
            NSMakeRect(8, POPOVER_HEIGHT - 32, 80, 24)
        )
        back.setTitle_("← Back")
        back.setBezelStyle_(1)  # NSBezelStyleRounded
        back.setTarget_(self._box_back_handler)
        back.setAction_(b"backClicked:")
        view.addSubview_(back)

        if p is None:
            view.addSubview_(_label(
                NSMakeRect(16, POPOVER_HEIGHT // 2 - 10, CONTENT_WIDTH - 32, 20),
                "Pokemon nicht gefunden.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        # Row's species_dex_id is the current form (evolution mutates in place).
        species = p.species_dex_id
        try:
            xp = query_xp_for_pokemon(p.id)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(species)
        level, into, needed = pokemon.level_from_xp(xp, rate)

        # 2-column layout: left = big sprite, right = labels.
        sprite_size = 128
        sprite_x = 16
        sprite_y = POPOVER_HEIGHT - 56 - sprite_size
        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(species)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self._animated_image_views.append(iv)

        # Right column.
        col_x = sprite_x + sprite_size + 16
        col_w = CONTENT_WIDTH - col_x - 16
        y_cursor = POPOVER_HEIGHT - 60

        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 22, col_w, 22),
            f"#{species:03d}  {pokemon.name_of(species)}",
            font=NSFont.boldSystemFontOfSize_(15),
        ))
        y_cursor -= 26

        lvl_text = "Lv MAX" if level >= pokemon.MAX_LEVEL else f"Lv {level}"
        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 18, col_w, 18),
            lvl_text,
            font=NSFont.boldSystemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 22

        # XP bar
        progress = into / needed if needed > 0 else (1.0 if level >= pokemon.MAX_LEVEL else 0.0)
        bar = _XPBarView.alloc().initWithFrame_progress_(
            NSMakeRect(col_x, y_cursor - 8, col_w, 8), progress,
        )
        view.addSubview_(bar)
        y_cursor -= 14

        xp_text = "MAX" if level >= pokemon.MAX_LEVEL else f"{into:,} / {needed:,} XP"
        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 14, col_w, 14),
            xp_text,
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
        ))
        y_cursor -= 22

        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 16, col_w, 16),
            f"Nature: {p.nature}",
            font=NSFont.systemFontOfSize_(12),
        ))
        y_cursor -= 18

        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 16, col_w, 16),
            f"“{p.characteristic}.”",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 18

        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 16, col_w, 16),
            f"Caught: {p.caught_date.isoformat()}",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
        ))

        # "Set as active" / "✓ Active" button at the bottom.
        from tokenmon import box
        try:
            active_id = box.get_active_pokemon_id()
        except Exception:
            log.exception("get_active_pokemon_id failed")
            active_id = None
        is_active = active_id == p.id

        btn_w = 160
        btn_x = (CONTENT_WIDTH - btn_w) // 2
        active_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(btn_x, 16, btn_w, 28)
        )
        if is_active:
            active_btn.setTitle_("✓ Active")
            active_btn.setEnabled_(False)
        else:
            active_btn.setTitle_("Set as active")
            self._set_active_handler = (
                _SetActiveHandler.alloc().initWithPopover_pokemonId_(self, p.id)
            )
            active_btn.setTarget_(self._set_active_handler)
            active_btn.setAction_(b"setActiveClicked:")
        active_btn.setBezelStyle_(1)  # NSBezelStyleRounded
        view.addSubview_(active_btn)

        return view

    # =========================================================================
    # Pane: Usage
    # =========================================================================

    # =========================================================================
    # Pane: Items (PokeBall inventory + earn rates)
    # =========================================================================

    def _build_pane_items(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        # Header.
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, CONTENT_WIDTH - 32, 22),
            "Items",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        try:
            counts = query_item_counts()
        except Exception:
            log.exception("query_item_counts failed")
            counts = {}

        # Per-item rows — driven by the items registry, in registry (insertion)
        # order.
        y_cursor = POPOVER_HEIGHT - 50
        for key, item in items.ITEMS.items():
            count = int(counts.get(key, 0) or 0)
            row_h = 56

            # Sprite (or emoji fallback) on the left.
            sprite = items_remote.get_item_image(item)
            if sprite is not None:
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(16, y_cursor - row_h + 14, 36, 30)
                )
                iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                iv.setImage_(sprite)
                view.addSubview_(iv)
            else:
                view.addSubview_(_label(
                    NSMakeRect(16, y_cursor - row_h + 14, 36, 30),
                    item.emoji,
                    font=NSFont.systemFontOfSize_(24),
                    align=NSTextAlignmentCenter,
                ))

            # Name + count on the same row.
            text_x = 60
            text_w = CONTENT_WIDTH - text_x - 20
            count_text = f"× {count}"
            count_color = (
                NSColor.tertiaryLabelColor() if count == 0
                else NSColor.labelColor()
            )
            name_field = _label(
                NSMakeRect(text_x, y_cursor - 22, text_w - 50, 18),
                item.display_name,
                font=NSFont.boldSystemFontOfSize_(13),
            )
            view.addSubview_(name_field)
            count_field = _label(
                NSMakeRect(CONTENT_WIDTH - 80, y_cursor - 22, 64, 18),
                count_text,
                font=NSFont.boldSystemFontOfSize_(13),
                color=count_color,
                align=2,  # NSTextAlignmentRight
            )
            view.addSubview_(count_field)

            # Description below.
            desc_field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(text_x, y_cursor - row_h + 4, text_w, 32)
            )
            desc_field.setStringValue_(item.description)
            desc_field.setBezeled_(False)
            desc_field.setDrawsBackground_(False)
            desc_field.setEditable_(False)
            desc_field.setSelectable_(False)
            desc_field.setFont_(NSFont.systemFontOfSize_(11))
            desc_field.setTextColor_(NSColor.secondaryLabelColor())
            desc_field.setLineBreakMode_(0)  # word-wrapping
            try:
                desc_field.cell().setWraps_(True)
            except Exception:
                pass
            view.addSubview_(desc_field)

            y_cursor -= row_h + 4

        # Footer hint about earn rates.
        footer_y = 16
        rates_lines = "  ·  ".join(
            f"{it.emoji} 1 / {it.threshold:,}" for it in items.ITEMS.values()
        )
        view.addSubview_(_label(
            NSMakeRect(16, footer_y, CONTENT_WIDTH - 32, 14),
            f"Earned per output token:  {rates_lines}",
            font=NSFont.systemFontOfSize_(10),
            color=NSColor.tertiaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        return view

    def _build_pane_usage(self) -> NSView:
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))

        try:
            totals = query_today(TZ)
            by_model = query_today_by_model(TZ)
        except Exception:
            log.exception("usage query failed")
            from tokenmon.storage import Totals
            totals, by_model = Totals(), {}

        margin_x = 16
        y_cursor = POPOVER_HEIGHT - 30

        # Header
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 20),
            f"Heute: {_fmt_tokens(totals.output_tokens)} output tokens · {totals.request_count} requests",
            font=NSFont.boldSystemFontOfSize_(14),
        ))
        y_cursor -= 22
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 16),
            f"Output {_fmt_tokens(totals.output_tokens)}   ·   Input {_fmt_tokens(totals.input_tokens)}",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 26

        # Per-model
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 18),
            "Pro Modell",
            font=NSFont.boldSystemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 18

        total_cost = 0.0
        priced_tokens = 0
        all_tokens = 0
        max_rows = 6
        models_shown = 0
        for model, t in by_model.items():
            cost, has_price = cost_for(
                model,
                input_tokens=t.input_tokens,
                output_tokens=t.output_tokens,
                cache_read_tokens=t.cache_read_tokens,
                cache_creation_tokens=t.cache_creation_tokens,
            )
            total_cost += cost
            tokens = (
                t.input_tokens + t.output_tokens
                + t.cache_read_tokens + t.cache_creation_tokens
            )
            all_tokens += tokens
            if has_price:
                priced_tokens += tokens
            if models_shown < max_rows:
                cost_str = _fmt_usd(cost) if has_price else "?"
                model_short = model if len(model) <= 36 else model[:33] + "…"
                view.addSubview_(_label(
                    NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 16),
                    f"  {model_short}    {_fmt_tokens(t.output_tokens)} out  {cost_str}",
                    font=NSFont.systemFontOfSize_(11),
                    color=NSColor.labelColor(),
                ))
                y_cursor -= 16
                models_shown += 1
        if len(by_model) > max_rows:
            view.addSubview_(_label(
                NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 14),
                f"  … +{len(by_model) - max_rows} weitere",
                font=NSFont.systemFontOfSize_(10),
                color=NSColor.tertiaryLabelColor(),
            ))
            y_cursor -= 14
        y_cursor -= 6

        # Cost summary
        coverage_suffix = ""
        if all_tokens > 0 and priced_tokens < all_tokens:
            coverage = priced_tokens / all_tokens
            coverage_suffix = f"   ({coverage:.0%} Preisabdeckung)"
        view.addSubview_(_label(
            NSMakeRect(margin_x, y_cursor, CONTENT_WIDTH - 32, 16),
            f"Geschätzte Kosten: {_fmt_usd(total_cost)}{coverage_suffix}",
            font=NSFont.boldSystemFontOfSize_(12),
        ))
        y_cursor -= 24

        # Footer toolbar — toggles + buttons.
        # Toggle: show pokemon in menubar
        sw_pokemon = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, y_cursor - 16, CONTENT_WIDTH - 32, 18)
        )
        sw_pokemon.setButtonType_(NSButtonTypeSwitch)
        sw_pokemon.setTitle_("Pokemon im Menubar anzeigen")
        sw_pokemon.setState_(1 if self._app._show_pokemon else 0)
        sw_pokemon.setTarget_(self)
        sw_pokemon.setAction_(b"toggleMenubarPokemon:")
        view.addSubview_(sw_pokemon)
        y_cursor -= 22

        sw_overlay = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, y_cursor - 16, CONTENT_WIDTH - 32, 18)
        )
        sw_overlay.setButtonType_(NSButtonTypeSwitch)
        sw_overlay.setTitle_("Pokemon als Desktop-Overlay anzeigen")
        sw_overlay.setState_(1 if self._app._show_overlay else 0)
        sw_overlay.setTarget_(self)
        sw_overlay.setAction_(b"toggleOverlay:")
        view.addSubview_(sw_overlay)
        y_cursor -= 26

        # Debug spawn-encounter button (sits just above the Restart/Quit row).
        spawn_btn_y = 44
        spawn_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, spawn_btn_y, 220, 24)
        )
        spawn_btn.setTitle_("🐛 Spawn encounter (debug)")
        spawn_btn.setBezelStyle_(1)
        self._debug_spawn_handler = _DebugSpawnHandler.alloc().initWithPopover_(self)
        spawn_btn.setTarget_(self._debug_spawn_handler)
        spawn_btn.setAction_(b"spawnClicked:")
        view.addSubview_(spawn_btn)

        # Inline label slot for "(already pending)" — created lazily in
        # _flash_already_pending() and torn down by its NSTimer.
        self._already_pending_label_frame = NSMakeRect(
            margin_x + 226, spawn_btn_y + 4, CONTENT_WIDTH - margin_x - 226 - 16, 16,
        )
        self._already_pending_parent = view

        # Buttons row: Restart Proxy + Quit, anchored to bottom.
        btn_y = 12
        restart = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin_x, btn_y, 160, 24)
        )
        restart.setTitle_("Proxy neustarten")
        restart.setBezelStyle_(1)  # NSBezelStyleRounded
        restart.setTarget_(self)
        restart.setAction_(b"restartProxy:")
        view.addSubview_(restart)

        quit_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(CONTENT_WIDTH - margin_x - 100, btn_y, 100, 24)
        )
        quit_btn.setTitle_("Beenden")
        quit_btn.setBezelStyle_(1)
        quit_btn.setTarget_(self)
        quit_btn.setAction_(b"quitApp:")
        view.addSubview_(quit_btn)

        return view

    def _flash_already_pending(self) -> None:
        """Show '(already pending)' next to the debug spawn button briefly."""
        parent = getattr(self, "_already_pending_parent", None)
        frame = getattr(self, "_already_pending_label_frame", None)
        if parent is None or frame is None:
            return
        # Tear down any in-flight one first.
        if self._already_pending_timer is not None:
            try:
                self._already_pending_timer.invalidate()
            except Exception:
                pass
            self._already_pending_timer = None
        if self._already_pending_label is not None:
            self._already_pending_label.removeFromSuperview()
            self._already_pending_label = None

        lbl = _label(
            frame,
            "(already pending)",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
        )
        parent.addSubview_(lbl)
        self._already_pending_label = lbl

        # Reuse _RevealTimerHandler? — no, that switches panes. Inline handler.
        class _Hider(NSObject):
            def initWithPopover_(self_, popover):  # noqa: N802
                self_ = objc.super(_Hider, self_).init()
                if self_ is None:
                    return None
                self_._popover = popover
                return self_

            def fire_(self_, _t):  # noqa: N802
                pop = self_._popover
                if pop._already_pending_label is not None:
                    pop._already_pending_label.removeFromSuperview()
                    pop._already_pending_label = None
                pop._already_pending_timer = None

        self._already_pending_hider = _Hider.alloc().initWithPopover_(self)
        self._already_pending_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.5, self._already_pending_hider, b"fire:", None, False,
            )
        )

    # ---- toggle / button actions (Usage pane) ----

    def toggleMenubarPokemon_(self, _sender):  # noqa: N802
        self._app.toggle_menubar_pokemon(None)

    def toggleOverlay_(self, _sender):  # noqa: N802
        self._app.toggle_overlay(None)

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
        # Auto-select the encounter pane whenever one is pending and the user
        # is currently on a base pane — covers both "first pop after spawn"
        # and "user closed popover, encounter spawned, opens again".
        try:
            pending = get_pending_encounter()
        except Exception:
            log.exception("get_pending_encounter failed")
            pending = None
        if pending is not None and self._current_pane in (
            PANE_POKEMON, PANE_TOKENDEX, PANE_BOX, PANE_USAGE,
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
            "Beenden", b"quit:", "",
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

    def popoverDidClose_(self, _notification):  # noqa: N802
        self._uninstall_global_monitor()
        for iv in self._animated_image_views:
            iv.setAnimates_(False)

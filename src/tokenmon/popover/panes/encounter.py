"""Encounter pane: silhouette + bag-open inventory + post-catch reveal.

State on ``popover``: ``_encounter_bag_open``, ``_pending_reveal_pokemon``,
``_reveal_timer*``. The pane builder reads them to decide which of the
three sub-views to render. The catch-reveal hand-off comes through
``begin_catch_reveal`` (called by the catch-animation controller via the
popover delegate).
"""
from __future__ import annotations

import logging

import objc
from AppKit import (
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSImage,
    NSImageLeft,
    NSMenu,
    NSMenuItem,
    NSTextAlignmentCenter,
    NSTimer,
    NSView,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject

from tokenmon import config, encounter, items, items_remote, pokemon, weather
from tokenmon.overlay import _silhouette_image
from tokenmon.popover._actions import title_for_action
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.animation import _RevealTimerHandler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_ENCOUNTER,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    TYPE_BADGE_HEIGHT,
    _crisp_image_view,
    _label,
    _type_badge_row,
)
from tokenmon.storage import get_pending_encounter, query_item_counts

log = logging.getLogger("tokenmon.popover.panes.encounter")


class _ItemRowHandler(NSObject):
    """Per-row click target inside the bag-open inventory list.

    Click on the row → opens a native NSMenu anchored to the row's NSButton,
    with one item per ``Item.actions`` entry. Selecting a menu item dispatches
    based on the action key — currently only ``throw`` is implemented.
    """

    def initWithController_encounterId_itemKey_(  # noqa: N802
        self, ctrl, encounter_id, item_key,
    ):
        self = objc.super(_ItemRowHandler, self).init()
        if self is None:
            return None
        self._ctrl = ctrl
        self._encounter_id = int(encounter_id)
        self._item_key = str(item_key)
        return self

    def itemRowClicked_(self, sender):  # noqa: N802
        item = items.get(self._item_key)
        if item is None or not item.actions:
            return
        if len(item.actions) == 1:
            self._dispatch_action(item.actions[0])
            return
        menu = NSMenu.alloc().initWithTitle_("")
        for action in item.actions:
            title = title_for_action(self._item_key, action)
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, b"actionSelected:", "",
            )
            mi.setTarget_(self)
            mi.setRepresentedObject_(action)
            menu.addItem_(mi)
        try:
            bounds = sender.bounds()
            location = (0.0, float(bounds.size.height))
            menu.popUpMenuPositioningItem_atLocation_inView_(
                None, location, sender,
            )
        except Exception:
            log.exception("item-row context menu failed")

    @objc.python_method
    def _dispatch_action(self, action: str) -> None:
        if action == "throw":
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
            popover = self._ctrl.popover
            popover._encounter_bag_open = False
            popover._begin_catch_animation(
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


class EncounterController(PaneController):
    """Renders the encounter pane in one of three modes:

    1. *Default*: silhouette + Bag/Run buttons (when ``_pending_reveal_pokemon``
       is None and ``_encounter_bag_open`` is False).
    2. *Bag-open*: silhouette + inventory list + Back/Run buttons.
    3. *Reveal*: real sprite + caught banner + type badges (when
       ``_pending_reveal_pokemon`` is set).
    """

    def build_view(self) -> NSView:
        pop = self.popover
        if pop._pending_reveal_pokemon is not None:
            return self._build_reveal(pop._pending_reveal_pokemon)
        return self._build_main()

    # ---- main view (default + bag-open) -------------------------------

    def _build_main(self) -> NSView:
        pop = self.popover
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

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
        pop._animated_image_views.append(iv)

        # --- Level + Type stub ---
        info_y = sprite_y - 24
        view.addSubview_(_label(
            NSMakeRect(0, info_y, CONTENT_WIDTH, 18),
            f"Lv {enc.level}     Type: ???",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        # --- Weather flavor (only when use_weather is on and a fetch
        # succeeded) — small subtitle below the Lv/Type stub. Failures
        # silently drop the line; spawning still works without bias.
        weather_y = info_y - 16
        if config.get("use_weather"):
            try:
                snap = weather.get_weather()
            except Exception:
                log.exception("weather.get_weather failed in encounter pane")
                snap = None
            if snap is not None:
                view.addSubview_(_label(
                    NSMakeRect(0, weather_y, CONTENT_WIDTH, 14),
                    weather.emoji_label(snap),
                    font=NSFont.systemFontOfSize_(11),
                    color=NSColor.secondaryLabelColor(),
                    align=NSTextAlignmentCenter,
                ))
                info_y = weather_y  # downstream layout starts below the line

        # --- Hint (only in bag-open mode) ---
        hint_y = info_y - 22
        if enc.last_hint and pop._encounter_bag_open:
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
            hint_y = info_y

        sep1_y = hint_y - 8
        sep1 = NSView.alloc().initWithFrame_(NSMakeRect(16, sep1_y, CONTENT_WIDTH - 32, 1))
        sep1.setWantsLayer_(True)
        sep1.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
        view.addSubview_(sep1)

        if pop._encounter_bag_open:
            self._build_bag_open(view, enc, sep1_y)
        else:
            self._build_default_actions(view, enc, sep1_y)

        return view

    def _build_default_actions(self, view: NSView, enc, top_y: int) -> None:
        """Bottom action bar: [🎒 Bag]  [Run away]."""
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
        def _open_bag(_s):
            self.popover._encounter_bag_open = True
            self.popover._show_pane(PANE_ENCOUNTER)
        bag_handler = make_handler(_open_bag)
        self._handlers.append(bag_handler)
        bag_btn.setTarget_(bag_handler)
        bag_btn.setAction_(b"fire:")
        view.addSubview_(bag_btn)

        run_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + btn_w + gap, btn_y, btn_w, btn_h)
        )
        run_btn.setTitle_("Run away")
        run_btn.setBezelStyle_(1)
        def _run_away(_s, eid=enc.id):
            try:
                encounter.run_away(eid)
            except Exception:
                log.exception("run_away failed")
            self.popover._show_pane(PANE_POKEMON)
        run_handler = make_handler(_run_away)
        self._handlers.append(run_handler)
        run_btn.setTarget_(run_handler)
        run_btn.setAction_(b"fire:")
        view.addSubview_(run_btn)

    def _build_bag_open(self, view: NSView, enc, top_y: int) -> None:
        """Inventory list with click-to-throw rows + back/run row."""
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
            btn.setAlignment_(0)
            btn.setEnabled_(enabled)
            if not enabled:
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
                handler = (
                    _ItemRowHandler.alloc()
                    .initWithController_encounterId_itemKey_(self, enc.id, key)
                )
                self._handlers.append(handler)
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
        def _bag_back(_s):
            self.popover._encounter_bag_open = False
            self.popover._show_pane(PANE_ENCOUNTER)
        back_handler = make_handler(_bag_back)
        self._handlers.append(back_handler)
        back_btn.setTarget_(back_handler)
        back_btn.setAction_(b"fire:")
        view.addSubview_(back_btn)

        run_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(margin + btn_w + gap, btn_y, btn_w, btn_h)
        )
        run_btn.setTitle_("Run away")
        run_btn.setBezelStyle_(1)
        def _run_away(_s, eid=enc.id):
            try:
                encounter.run_away(eid)
            except Exception:
                log.exception("run_away failed")
            self.popover._show_pane(PANE_POKEMON)
        run_handler = make_handler(_run_away)
        self._handlers.append(run_handler)
        run_btn.setTarget_(run_handler)
        run_btn.setAction_(b"fire:")
        view.addSubview_(run_btn)

    # ---- reveal view (post-catch hold) --------------------------------

    def _build_reveal(self, payload: dict) -> NSView:
        """Reveal layout: real animated sprite + 'caught!' banner."""
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        species_dex_id = int(payload["species_dex_id"])
        species_name = pokemon.name_of(species_dex_id)
        is_shiny = bool(payload.get("is_shiny", False))
        gender = payload.get("gender")

        banner_y = POPOVER_HEIGHT - 50
        banner_text = "Shiny Pokemon was caught!" if is_shiny else "Pokemon was caught!"
        view.addSubview_(_label(
            NSMakeRect(16, banner_y, CONTENT_WIDTH - 32, 24),
            banner_text,
            font=NSFont.boldSystemFontOfSize_(16),
            color=(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    1.0, 0.85, 0.0, 1.0,
                )
                if is_shiny else NSColor.labelColor()
            ),
            align=NSTextAlignmentCenter,
        ))
        view.addSubview_(_label(
            NSMakeRect(16, banner_y - 22, CONTENT_WIDTH - 32, 18),
            f"{species_name} added to your Box.",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = banner_y - 32 - sprite_size - 8
        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(species_dex_id, shiny=is_shiny)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
                iv.setAnimates_(True)
        view.addSubview_(iv)
        self.popover._animated_image_views.append(iv)

        sym = pokemon.gender_symbol(gender)
        name_decoration = (
            ("✨ " if is_shiny else "")
            + f"#{species_dex_id:03d}  {species_name}"
            + (f"  {sym}" if sym else "")
        )
        name_y = sprite_y - 28
        view.addSubview_(_label(
            NSMakeRect(0, name_y, CONTENT_WIDTH, 22),
            name_decoration,
            font=NSFont.boldSystemFontOfSize_(15),
            align=NSTextAlignmentCenter,
        ))

        types = pokemon.types_of(species_dex_id)
        for badge in _type_badge_row(
            CONTENT_WIDTH / 2, name_y - TYPE_BADGE_HEIGHT - 4, types,
        ):
            view.addSubview_(badge)
        return view

    # ---- catch-reveal trigger -----------------------------------------

    def begin_catch_reveal(self) -> None:
        """Called by the catch-animation controller when the catch resolves
        successfully. Loads the latest caught row, flips this pane into
        reveal mode without going through ``_show_pane`` (we want the
        encounter-slot to stay in the sidebar for the 2.5s hold), and
        schedules the dismiss timer.
        """
        pop = self.popover
        from tokenmon.storage import _connect
        try:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT species_dex_id, pokemon_id, gender, is_shiny "
                    "FROM encounters "
                    "WHERE resolved = 'caught' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
        except Exception:
            log.exception("query last-caught encounter failed")
            row = None

        if row is None:
            pop._show_pane(PANE_POKEMON)
            return

        pop._pending_reveal_pokemon = {
            "species_dex_id": int(row[0]),
            "pokemon_id": int(row[1]) if row[1] is not None else None,
            "gender": row[2],
            "is_shiny": bool(row[3]) if row[3] is not None else False,
        }
        view = self._build_reveal(pop._pending_reveal_pokemon)
        if pop._current_pane_view is not None:
            pop._current_pane_view.removeFromSuperview()
        view.setFrame_(NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT))
        pop._content_container.addSubview_(view)
        pop._current_pane_view = view

        pop._reveal_timer_handler = (
            _RevealTimerHandler.alloc().initWithPopover_(pop)
        )
        pop._reveal_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                2.5, pop._reveal_timer_handler, b"fire:", None, False,
            )
        )

"""Items pane: inventory list + drop-claim animation.

Owns the per-row click handler (NSObject subclass for the Multi-selector
NSMenu pattern) and the drop-claim animation state machine. Claim state
lives on ``popover`` so it survives the in-pane re-render that flips
between the regular list and the claim view.
"""
from __future__ import annotations

import logging

import objc
import rumps
from AppKit import (
    NSBezelStyleRegularSquare,
    NSButton,
    NSColor,
    NSFont,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMenu,
    NSMenuItem,
    NSScrollView,
    NSSegmentedControl,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRect, NSObject

from tokenmon import box, items, items_remote, pokemon
from tokenmon.items import POCKETS, items_in_pocket
from tokenmon.popover._actions import title_for_action
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.animation import _ClaimAnimationHandler, build_claim_steps
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_ITEMS,
    POPOVER_HEIGHT,
    _label,
)
from tokenmon.storage import (
    claim_pending_drops,
    get_money,
    query_item_counts,
    query_pending_drops,
)

log = logging.getLogger("tokenmon.popover.panes.items")


class _ItemsPaneRowHandler(NSObject):
    """Click on an Items-pane row → native NSMenu of the item's
    applicable actions, anchored to the row. Selecting an action
    executes it (currently only ``use`` is implemented).
    """

    def initWithController_itemKey_(self, ctrl, item_key):  # noqa: N802
        self = objc.super(_ItemsPaneRowHandler, self).init()
        if self is None:
            return None
        self._ctrl = ctrl
        self._item_key = str(item_key)
        return self

    def rowClicked_(self, sender):  # noqa: N802
        item = items.get(self._item_key)
        if item is None or not item.actions:
            return
        applicable = [a for a in item.actions if a == "use"]
        if not applicable:
            return
        menu = NSMenu.alloc().initWithTitle_("")
        for action in applicable:
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
            log.exception("items-pane context menu failed")

    def actionSelected_(self, sender):  # noqa: N802
        try:
            action = str(sender.representedObject())
        except Exception:
            return
        if action == "use":
            self._dispatch_use()

    @objc.python_method
    def _dispatch_use(self) -> None:
        try:
            active = box.get_active_pokemon()
        except Exception:
            log.exception("get_active_pokemon failed in items-pane use")
            return
        if active is None:
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle="No active Pokémon",
                    message="Set a Pokémon as active first.",
                )
            except Exception:
                pass
            return

        # Healing items branch: potion → use_potion. Potions return
        # ``(old_hp, new_hp, hp_max)`` on success and trigger a brief
        # animated HP fill on the active-Pokémon pane.
        from tokenmon.items import POTION_HEAL_AMOUNTS
        item = items.get(self._item_key)
        item_name = item.display_name if item is not None else self._item_key
        if self._item_key in POTION_HEAL_AMOUNTS:
            try:
                result = box.use_potion(active.id, self._item_key)
            except Exception:
                log.exception("use_potion failed")
                return
            popover = self._ctrl.popover
            if result is None:
                try:
                    rumps.notification(
                        title="Tokenmon",
                        subtitle=f"{item_name} had no effect",
                        message=(
                            f"{pokemon.display_name(active.nickname, active.species_dex_id)} "
                            f"is already at full HP."
                        ),
                    )
                except Exception:
                    pass
                popover._show_pane(PANE_ITEMS)
                return
            old_hp, new_hp, hp_max = result
            # Hand-off to the active-Pokémon pane for the fill animation.
            popover._hp_anim_from_hp = old_hp
            popover._hp_anim_to_hp = new_hp
            popover._hp_anim_max = hp_max
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle=f"Used {item_name}!",
                    message=f"+{new_hp - old_hp} HP ({new_hp}/{hp_max}).",
                )
            except Exception:
                pass
            from tokenmon.popover.widgets import PANE_POKEMON
            popover._show_pane(PANE_POKEMON)
            return

        try:
            evolved = box.use_stone(active.id, self._item_key)
        except Exception:
            log.exception("use_stone failed")
            return
        popover = self._ctrl.popover
        if evolved is None:
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle=f"{item_name} had no effect",
                    message=(
                        f"{pokemon.display_name(active.nickname, active.species_dex_id)} "
                        f"can't be evolved with {item_name}."
                    ),
                )
            except Exception:
                pass
            popover._show_pane(PANE_ITEMS)
            return
        try:
            app = popover._app
            if hasattr(app, "_refresh_pokemon_state"):
                app._refresh_pokemon_state()
        except Exception:
            log.exception("menubar refresh after use_stone failed")
        try:
            popover._refresh_sidebar_pokemon_icon()
        except Exception:
            log.exception("sidebar icon refresh failed")
        try:
            rumps.notification(
                title="Tokenmon",
                subtitle="Evolution!",
                message=(
                    f"{pokemon.display_name(active.nickname, active.species_dex_id)} "
                    f"evolved into {pokemon.name_of(evolved)}!"
                ),
            )
        except Exception:
            pass
        popover._show_pane(PANE_ITEMS)


class ItemsController(PaneController):
    """Inventory list + drop-claim animation. Claim state survives
    re-renders by living on ``popover``; ItemsController instances are
    recreated each pane render and read from ``popover._claim_*``."""

    def build_view(self) -> NSView:
        if self.popover._claim_active:
            return self._build_claim()
        return self._build_list()

    # ---- regular list view --------------------------------------------

    def _build_list(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, 180, 22),
            "Items",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        # Player money — small, right-aligned, sits in the header gap
        # between the "Items" title and the 🎁 button (x=310..406).
        # Placed at x=196, w=110 so its right edge stops at 306, leaving
        # a 4 px gap before the gift button.
        try:
            money_amount = int(get_money())
        except Exception:
            log.exception("get_money failed")
            money_amount = 0
        view.addSubview_(_label(
            NSMakeRect(196, POPOVER_HEIGHT - 32, 110, 18),
            f"$ {money_amount:,}",
            font=NSFont.systemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
            align=2,  # NSTextAlignmentRight
        ))

        # "Found N items" gift button — top-right.
        try:
            pending = query_pending_drops()
        except Exception:
            log.exception("query_pending_drops failed")
            pending = {}
        pending_total = sum(pending.values())
        if pending_total > 0:
            claim_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(CONTENT_WIDTH - 110, POPOVER_HEIGHT - 36, 96, 28)
            )
            claim_btn.setTitle_(f"🎁  ×{pending_total}")
            claim_btn.setBezelStyle_(1)
            def _claim_drops(_s):
                try:
                    fresh_pending = query_pending_drops()
                except Exception:
                    log.exception("query_pending_drops failed")
                    return
                if not fresh_pending:
                    return
                self.begin_drop_claim_animation(fresh_pending)
            claim_handler = make_handler(_claim_drops)
            self._handlers.append(claim_handler)
            claim_btn.setTarget_(claim_handler)
            claim_btn.setAction_(b"fire:")
            view.addSubview_(claim_btn)

        # Pocket tabs — segmented control just below the title. Picks a
        # subset of ITEMS to render in the rows below; the user can switch
        # tabs even when a pocket is empty.
        pocket_keys = [pk for pk, _ in POCKETS]
        current_pocket = getattr(self.popover, "_items_pocket", "balls")
        if current_pocket not in pocket_keys:
            current_pocket = "balls"
        seg_y = POPOVER_HEIGHT - 68
        seg = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(16, seg_y, CONTENT_WIDTH - 32, 24)
        )
        seg.setSegmentCount_(len(POCKETS))
        for i, (_, label) in enumerate(POCKETS):
            seg.setLabel_forSegment_(label, i)
        try:
            seg.setSelectedSegment_(pocket_keys.index(current_pocket))
        except ValueError:
            seg.setSelectedSegment_(0)
        def _pocket_changed(sender):
            try:
                new_idx = int(sender.selectedSegment())
            except Exception:
                return
            if 0 <= new_idx < len(pocket_keys):
                self.popover._items_pocket = pocket_keys[new_idx]
                self.popover._show_pane(PANE_ITEMS)
        pocket_handler = make_handler(_pocket_changed)
        self._handlers.append(pocket_handler)
        seg.setTarget_(pocket_handler)
        seg.setAction_(b"fire:")
        view.addSubview_(seg)

        try:
            counts = query_item_counts()
        except Exception:
            log.exception("query_item_counts failed")
            counts = {}

        # Filter to the active pocket, then keep only items the player owns.
        pocket_items = items_in_pocket(current_pocket)
        owned_items = [
            (key, item) for key, item in pocket_items
            if int(counts.get(key, 0) or 0) > 0
        ]

        # Header (title) eats POPOVER_HEIGHT-32..-10; the segmented control
        # eats POPOVER_HEIGHT-68..-44. Leave a small gap above the scroll.
        scroll_h = POPOVER_HEIGHT - 80

        if not owned_items:
            pocket_label = next(
                (label for pk, label in POCKETS if pk == current_pocket),
                current_pocket,
            )
            empty_y = (POPOVER_HEIGHT - 80) // 2 + 8
            view.addSubview_(_label(
                NSMakeRect(16, empty_y, CONTENT_WIDTH - 32, 20),
                f"Keine {pocket_label} im Beutel.",
                font=NSFont.systemFontOfSize_(12),
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        # Scrollable rows — Header + tab strip occupy the top 80 px of the
        # pane, everything below scrolls. Mirrors the Tokendex/Box pattern
        # so the weather background stays visible behind the rows.
        row_h = 56
        row_gap = 4
        content_h = max(len(owned_items) * (row_h + row_gap), scroll_h)
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, scroll_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)
        scroll.setDrawsBackground_(False)
        scroll.contentView().setDrawsBackground_(False)
        content = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, content_h)
        )

        y_cursor = content_h - 4
        for key, item in owned_items:
            count = int(counts.get(key, 0) or 0)

            sprite = items_remote.get_item_image(item)
            if sprite is not None:
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(16, y_cursor - row_h + 14, 36, 30)
                )
                iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                iv.setImage_(sprite)
                content.addSubview_(iv)
            else:
                content.addSubview_(_label(
                    NSMakeRect(16, y_cursor - row_h + 14, 36, 30),
                    item.emoji,
                    font=NSFont.systemFontOfSize_(24),
                    align=NSTextAlignmentCenter,
                ))

            text_x = 60
            text_w = CONTENT_WIDTH - text_x - 20
            count_text = f"× {count}"
            count_color = (
                NSColor.tertiaryLabelColor() if count == 0
                else NSColor.labelColor()
            )
            content.addSubview_(_label(
                NSMakeRect(text_x, y_cursor - 22, text_w - 50, 18),
                item.display_name,
                font=NSFont.boldSystemFontOfSize_(13),
            ))
            content.addSubview_(_label(
                NSMakeRect(CONTENT_WIDTH - 80, y_cursor - 22, 64, 18),
                count_text,
                font=NSFont.boldSystemFontOfSize_(13),
                color=count_color,
                align=2,
            ))

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
            desc_field.setLineBreakMode_(0)
            try:
                desc_field.cell().setWraps_(True)
            except Exception:
                pass
            content.addSubview_(desc_field)

            if any(a == "use" for a in item.actions) and count > 0:
                row_btn = NSButton.alloc().initWithFrame_(
                    NSMakeRect(0, y_cursor - row_h, CONTENT_WIDTH, row_h)
                )
                row_btn.setTitle_("")
                row_btn.setBordered_(False)
                row_btn.setBezelStyle_(NSBezelStyleRegularSquare)
                row_btn.setTransparent_(True)
                row_handler = (
                    _ItemsPaneRowHandler.alloc()
                    .initWithController_itemKey_(self, key)
                )
                self._handlers.append(row_handler)
                row_btn.setTarget_(row_handler)
                row_btn.setAction_(b"rowClicked:")
                content.addSubview_(row_btn)

            y_cursor -= row_h + row_gap

        scroll.setDocumentView_(content)
        view.addSubview_(scroll)
        return view

    # ---- claim animation ----------------------------------------------

    def begin_drop_claim_animation(self, pending: dict[str, int]) -> None:
        """Snapshot pending drops, flip the pane into claim mode, build the
        animation view, kick off the step sequence."""
        pop = self.popover
        if pop._claim_active or not pending:
            return
        ordered = [
            (k, pending[k]) for k in items.ITEMS if k in pending
        ]
        pop._claim_payload = dict(ordered)
        pop._claim_active = True
        pop._show_pane(PANE_ITEMS)  # re-render → claim builder takes over
        steps = build_claim_steps(ordered)
        pop._claim_handler = (
            _ClaimAnimationHandler.alloc()
            .initWithPopover_steps_(pop, steps)
        )
        pop._claim_handler.start()

    def _build_claim(self) -> NSView:
        """Render the claim animation view: header + a row per pending item.
        The sprites get repositioned per ``_claim_step`` action to produce
        the top-down drop motion."""
        pop = self.popover
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 36, CONTENT_WIDTH - 32, 24),
            "🎁  You found:",
            font=NSFont.boldSystemFontOfSize_(15),
            align=NSTextAlignmentCenter,
        ))

        ordered = list(pop._claim_payload.items())
        if not ordered:
            return view

        n = len(ordered)
        row_h = 56
        gap = 4
        total_h = n * row_h + (n - 1) * gap
        top_y = (POPOVER_HEIGHT - 60 - total_h) // 2 + total_h
        sprite_size = 40
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2 - 60

        pop._claim_views = []
        for i, (key, count) in enumerate(ordered):
            slot_y = top_y - (i + 1) * row_h - i * gap

            iv = NSImageView.alloc().initWithFrame_(
                NSMakeRect(sprite_x, POPOVER_HEIGHT + 10, sprite_size, sprite_size)
            )
            iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            iv.setWantsLayer_(True)
            if iv.layer() is not None:
                iv.layer().setMagnificationFilter_("nearest")
                iv.layer().setMinificationFilter_("nearest")
            item = items.get(key)
            if item is not None:
                sprite = items_remote.get_item_image(item)
                if sprite is not None:
                    iv.setImage_(sprite)
            view.addSubview_(iv)

            label = _label(
                NSMakeRect(sprite_x + sprite_size + 16, slot_y, 220, sprite_size),
                f"+{count}  {item.display_name if item else key}",
                font=NSFont.boldSystemFontOfSize_(14),
                align=2,
            )
            label.setHidden_(True)
            view.addSubview_(label)

            pop._claim_views.append({
                "sprite": iv,
                "label": label,
                "slot_y": slot_y,
                "sprite_x": sprite_x,
                "sprite_size": sprite_size,
                "drop_height": POPOVER_HEIGHT - slot_y,
            })
        return view

    def claim_step(self, action: str) -> None:
        if action == "done":
            self.end_drop_claim_animation()
            return
        if action.startswith("drop_"):
            try:
                _, idx_str, frame_str = action.split("_")
                idx = int(idx_str)
                frame = int(frame_str)
            except ValueError:
                return
            views = self.popover._claim_views
            if idx >= len(views):
                return
            slot = views[idx]
            sprite = slot["sprite"]
            slot_y = slot["slot_y"]
            drop_h = slot["drop_height"]
            x = slot["sprite_x"]
            size = slot["sprite_size"]
            if frame == 1:
                y = slot_y + drop_h * 0.6
            elif frame == 2:
                y = slot_y + 8
            else:
                y = slot_y
                slot["label"].setHidden_(False)
            sprite.setFrame_(NSMakeRect(x, y, size, size))

    def end_drop_claim_animation(self) -> None:
        try:
            claimed = claim_pending_drops()
        except Exception:
            log.exception("claim_pending_drops failed at animation end")
            claimed = {}
        pop = self.popover
        pop._claim_active = False
        pop._claim_handler = None
        pop._claim_payload = {}
        pop._claim_views = []
        pop._show_pane(PANE_ITEMS)
        log.info("claim_drops: transferred %s", claimed)

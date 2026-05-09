"""Shop pane: spend earned money on consumables.

Sells the 5 items with ``shop_price`` set in ``items.ITEMS`` (Poké/Great/
Ultra Ball + Potion + Ether). Master Ball and stones are intentionally
absent — they're found-only.

Layout mirrors ``ItemsController._build_list`` so the two panes feel like
sibling tabs: 56px rows, sprite on the left, name + count + Buy button on
the right. Buying re-renders the pane to refresh the money label and
per-row counts.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSButton,
    NSColor,
    NSFont,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSScrollView,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRect

import rumps

from tokenmon import items, items_remote
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_SHOP,
    POPOVER_HEIGHT,
    _label,
)
from tokenmon.storage import (
    add_money,
    add_to_inventory,
    get_money,
    query_item_counts,
)

log = logging.getLogger("tokenmon.popover.panes.shop")


class ShopController(PaneController):
    """Renders the Shop list and handles buy clicks."""

    def build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        # Header — title left, money label right (matches Items pane).
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT - 32, 180, 22),
            "Shop",
            font=NSFont.boldSystemFontOfSize_(15),
        ))

        try:
            money_amount = int(get_money())
        except Exception:
            log.exception("get_money failed")
            money_amount = 0
        view.addSubview_(_label(
            NSMakeRect(CONTENT_WIDTH - 130, POPOVER_HEIGHT - 32, 114, 18),
            f"$ {money_amount:,}",
            font=NSFont.boldSystemFontOfSize_(13),
            color=NSColor.labelColor(),
            align=2,  # NSTextAlignmentRight
        ))

        try:
            counts = query_item_counts()
        except Exception:
            log.exception("query_item_counts failed")
            counts = {}

        scroll_h = POPOVER_HEIGHT - 56
        row_h = 56
        row_gap = 4
        shop = items.shop_items()
        content_h = max(len(shop) * (row_h + row_gap), scroll_h)

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
        for key, item in shop:
            count = int(counts.get(key, 0) or 0)
            price = int(item.shop_price or 0)
            self._build_row(
                content, key, item,
                y=y_cursor, row_h=row_h,
                count=count, price=price,
                money=money_amount,
            )
            y_cursor -= row_h + row_gap

        scroll.setDocumentView_(content)
        view.addSubview_(scroll)
        return view

    # ---- per-row builder ----------------------------------------------

    def _build_row(
        self, content: NSView, key: str, item: items.Item,
        *, y: float, row_h: int, count: int, price: int, money: int,
    ) -> None:
        """Render one shop row inside ``content`` with its top edge at ``y``."""
        # Sprite (left).
        sprite = items_remote.get_item_image(item)
        if sprite is not None:
            iv = NSImageView.alloc().initWithFrame_(
                NSMakeRect(16, y - row_h + 14, 36, 30)
            )
            iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            iv.setImage_(sprite)
            content.addSubview_(iv)
        else:
            content.addSubview_(_label(
                NSMakeRect(16, y - row_h + 14, 36, 30),
                item.emoji,
                font=NSFont.systemFontOfSize_(24),
                align=NSTextAlignmentCenter,
            ))

        # Name + description (middle).
        text_x = 60
        # Right side reserves: Buy button (~92) + count label (~52) + gap.
        right_reserve = 156
        text_w = CONTENT_WIDTH - text_x - right_reserve
        content.addSubview_(_label(
            NSMakeRect(text_x, y - 22, text_w, 18),
            item.display_name,
            font=NSFont.boldSystemFontOfSize_(13),
        ))
        desc = NSTextField.alloc().initWithFrame_(
            NSMakeRect(text_x, y - row_h + 4, text_w, 32)
        )
        desc.setStringValue_(item.description)
        desc.setBezeled_(False)
        desc.setDrawsBackground_(False)
        desc.setEditable_(False)
        desc.setSelectable_(False)
        desc.setFont_(NSFont.systemFontOfSize_(11))
        desc.setTextColor_(NSColor.secondaryLabelColor())
        desc.setLineBreakMode_(0)
        try:
            desc.cell().setWraps_(True)
        except Exception:
            pass
        content.addSubview_(desc)

        # Right cell — count owned (top) + Buy button (bottom).
        count_x = CONTENT_WIDTH - right_reserve + 4
        count_color = (
            NSColor.tertiaryLabelColor() if count == 0
            else NSColor.secondaryLabelColor()
        )
        content.addSubview_(_label(
            NSMakeRect(count_x, y - 22, right_reserve - 12, 16),
            f"× {count}",
            font=NSFont.systemFontOfSize_(11),
            color=count_color,
            align=2,
        ))

        cap = int(item.cap)
        broke = money < price
        full = count >= cap
        btn_w = 92
        btn_x = CONTENT_WIDTH - btn_w - 16
        btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(btn_x, y - row_h + 8, btn_w, 26)
        )
        btn.setTitle_(f"Buy ${price:,}")
        btn.setBezelStyle_(1)  # NSBezelStyleRounded
        if broke or full:
            btn.setEnabled_(False)
            btn.setAlphaValue_(0.4)
            if full:
                btn.setToolTip_("Bag full")
            else:
                btn.setToolTip_("Not enough money")

        item_key = key

        def _buy(_sender):
            self._handle_buy(item_key)

        handler = make_handler(_buy)
        self._handlers.append(handler)
        btn.setTarget_(handler)
        btn.setAction_(b"fire:")
        content.addSubview_(btn)

    # ---- buy ----------------------------------------------------------

    def _handle_buy(self, item_key: str) -> None:
        item = items.get(item_key)
        if item is None or item.shop_price is None:
            return
        price = int(item.shop_price)
        cap = int(item.cap)
        try:
            cur_money = int(get_money())
        except Exception:
            log.exception("get_money failed during buy")
            return
        try:
            cur_count = int(query_item_counts([item_key]).get(item_key, 0) or 0)
        except Exception:
            log.exception("query_item_counts failed during buy")
            return
        if cur_money < price or cur_count >= cap:
            # Defensive — buttons should already be disabled in this state.
            return
        try:
            add_money(-price)
            add_to_inventory(item_key, 1)
        except Exception:
            log.exception("buy failed: %s", item_key)
            return
        try:
            rumps.notification(
                title="Tokenmon",
                subtitle=f"Bought {item.display_name}",
                message=f"-${price:,}  ·  ×{cur_count + 1} now",
            )
        except Exception:
            pass
        # Re-render so money label + count refresh.
        self.popover._show_pane(PANE_SHOP)

"""Box pane: caught-Pokemon grid + per-id detail view (incl. nickname edit).

The ``_NicknameInlineHandler`` stays an NSObject subclass — it implements
the ``control:textView:doCommandBySelector:`` delegate protocol so
``Esc`` can cancel the edit, which a callback-based handler can't
provide.
"""
from __future__ import annotations

import logging

import objc
from AppKit import (
    NSBezelStyleRegularSquare,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSScrollView,
    NSSegmentedControl,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRect, NSMakeSize, NSObject

from tokenmon import box, pokemon
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_BOX,
    POPOVER_HEIGHT,
    _CardView,
    _crisp_image_view,
    _label,
    _SeparatorView,
    _StatsRadarView,
    _type_badge_row,
)
from tokenmon.storage import (
    get_pokemon_by_id,
    list_pokemon,
    query_xp_for_pokemon,
)
from tokenmon.tokendex import _XPBarView
from tokenmon.ui_helpers import fmt_affection as _fmt_affection

log = logging.getLogger("tokenmon.popover.panes.box")


class _NicknameInlineHandler(NSObject):
    """Inline nickname editor handlers: ✏️ enters edit mode, ✓ / Enter
    saves, ✗ / Esc cancels. State lives on the controller (which is
    re-instantiated on each pane render) so the label↔field swap survives
    re-renders. Empty/whitespace input collapses to NULL → original name.
    """

    def initWithController_pokemonId_(self, ctrl, pokemon_id):  # noqa: N802
        self = objc.super(_NicknameInlineHandler, self).init()
        if self is None:
            return None
        self._ctrl = ctrl
        self._pokemon_id = int(pokemon_id)
        self._field = None  # populated when the field is built
        return self

    def beginEdit_(self, _sender):  # noqa: N802 — pencil click
        self._ctrl.popover._editing_nickname = True
        self._ctrl.popover._show_pane(PANE_BOX)

    @objc.python_method
    def _commit(self, value):
        from tokenmon.storage import update_pokemon_nickname
        try:
            update_pokemon_nickname(self._pokemon_id, value)
        except Exception:
            log.exception("update_pokemon_nickname failed")
        try:
            app = self._ctrl.popover._app
            if hasattr(app, "_update_tooltip"):
                app._update_tooltip()
        except Exception:
            log.exception("tooltip refresh after nickname change failed")
        self._ctrl.popover._editing_nickname = False
        self._ctrl.popover._show_pane(PANE_BOX)

    def saveField_(self, sender):  # noqa: N802 — NSTextField action (Enter)
        self._commit(sender.stringValue().strip() or None)

    def saveButton_(self, _sender):  # noqa: N802 — ✓ click
        if self._field is None:
            return
        self._commit(self._field.stringValue().strip() or None)

    def cancelButton_(self, _sender):  # noqa: N802 — ✗ click
        self._ctrl.popover._editing_nickname = False
        self._ctrl.popover._show_pane(PANE_BOX)

    # NSTextField delegate: catch Esc to cancel without saving.
    def control_textView_doCommandBySelector_(  # noqa: N802
        self, _control, _text_view, command,
    ):
        sel = str(command) if command is not None else ""
        if sel in ("cancelOperation:", "cancel:"):
            self._ctrl.popover._editing_nickname = False
            self._ctrl.popover._show_pane(PANE_BOX)
            return True
        return False


class BoxController(PaneController):
    """Grid-or-detail view of caught Pokemon with nickname inline-edit."""

    def __init__(self, popover) -> None:
        super().__init__(popover)
        self._selected_id: int | None = popover._box_selected_id
        self._editing_nickname: bool = popover._editing_nickname
        self._stats_mode: str = popover._stats_mode
        self._nick_handler: _NicknameInlineHandler | None = None

    def build_view(self) -> NSView:
        if self._selected_id is None:
            return self._build_grid()
        return self._build_detail(self._selected_id)

    # ---- grid view -----------------------------------------------------

    def _build_grid(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        try:
            rows = list_pokemon()
        except Exception:
            log.exception("list_pokemon failed")
            rows = []

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
        # Transparent so the popover's weather layer shows through.
        scroll.setDrawsBackground_(False)
        scroll.contentView().setDrawsBackground_(False)

        cols = 8
        cell = 40
        gap = 6
        margin = 12
        n = len(rows)
        rows_count = max(1, (n + cols - 1) // cols)
        grid_w = cols * cell + (cols - 1) * gap
        content_h = max(rows_count * cell + (rows_count - 1) * gap + margin * 2, scroll_h)
        doc_w = CONTENT_WIDTH - 16
        content = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, doc_w, content_h)
        )

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
                y = content_h - margin - (row_idx + 1) * cell - row_idx * gap

                btn = NSButton.alloc().initWithFrame_(
                    NSMakeRect(x, y, cell, cell)
                )
                btn.setTitle_("")
                btn.setBordered_(False)
                btn.setBezelStyle_(NSBezelStyleRegularSquare)
                btn.setWantsLayer_(True)
                if btn.layer() is not None:
                    btn.layer().setMagnificationFilter_("nearest")
                    btn.layer().setMinificationFilter_("nearest")
                sp = pokemon.ensure_sprite(p.species_dex_id, shiny=p.is_shiny)
                if sp is not None and sp.exists():
                    img = NSImage.alloc().initWithContentsOfFile_(str(sp))
                    if img is not None:
                        img.setSize_(NSMakeSize(36, 36))
                        btn.setImage_(img)
                def _open(_s, pid=p.id):
                    self.popover._box_selected_id = pid
                    self.popover._show_pane(PANE_BOX)
                handler = make_handler(_open)
                self._handlers.append(handler)
                btn.setTarget_(handler)
                btn.setAction_(b"fire:")
                content.addSubview_(btn)

        scroll.setDocumentView_(content)
        scroll.contentView().scrollToPoint_((0, max(0, content_h - scroll_h)))
        view.addSubview_(scroll)
        return view

    # ---- detail view ---------------------------------------------------

    def _build_detail(self, pokemon_id: int) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        try:
            p = get_pokemon_by_id(pokemon_id)
        except Exception:
            log.exception("get_pokemon_by_id failed")
            p = None

        # ← Back button (top-left).
        def _back(_s):
            self.popover._box_selected_id = None
            self.popover._show_pane(PANE_BOX)
        back_handler = make_handler(_back)
        self._handlers.append(back_handler)
        back = NSButton.alloc().initWithFrame_(
            NSMakeRect(8, POPOVER_HEIGHT - 32, 80, 24)
        )
        back.setTitle_("← Back")
        back.setBezelStyle_(1)
        back.setTarget_(back_handler)
        back.setAction_(b"fire:")
        view.addSubview_(back)

        if p is None:
            view.addSubview_(_label(
                NSMakeRect(16, POPOVER_HEIGHT // 2 - 10, CONTENT_WIDTH - 32, 20),
                "Pokemon nicht gefunden.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        species = p.species_dex_id
        try:
            xp = query_xp_for_pokemon(p.id)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(species)
        level, into, needed = pokemon.level_from_xp(xp, rate)

        # Card backdrops.
        card1_rect = NSMakeRect(8, 252, CONTENT_WIDTH - 16, 208)
        card2_rect = NSMakeRect(8, 52, CONTENT_WIDTH - 16, 188)
        view.addSubview_(_CardView.alloc().initWithFrame_(card1_rect))
        view.addSubview_(_CardView.alloc().initWithFrame_(card2_rect))
        view.addSubview_(_SeparatorView.alloc().initWithFrame_(
            NSMakeRect(152, 264, 1, 184)
        ))

        # Sprite.
        sprite_size = 128
        sprite_x = 16
        sprite_y = POPOVER_HEIGHT - 56 - sprite_size
        iv = _crisp_image_view(NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size))
        sp = pokemon.ensure_sprite(species, shiny=p.is_shiny)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self.popover._animated_image_views.append(iv)

        col_x = sprite_x + sprite_size + 16
        col_w = CONTENT_WIDTH - col_x - 16
        y_cursor = POPOVER_HEIGHT - 60

        sym = pokemon.gender_symbol(p.gender)
        species_name = pokemon.name_of(species)
        display_name = (p.nickname or species_name).strip() or species_name
        title_text = (
            ("✨ " if p.is_shiny else "")
            + display_name
            + (f"  {sym}" if sym else "")
        )

        # Inline nickname editor handler.
        self._nick_handler = (
            _NicknameInlineHandler.alloc()
            .initWithController_pokemonId_(self, p.id)
        )
        self._handlers.append(self._nick_handler)

        if self._editing_nickname:
            field_w = col_w - 56
            text_field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(col_x, y_cursor - 24, field_w, 24)
            )
            text_field.setStringValue_(p.nickname or "")
            text_field.setPlaceholderString_(species_name)
            text_field.setFont_(NSFont.boldSystemFontOfSize_(14))
            text_field.setTarget_(self._nick_handler)
            text_field.setAction_(b"saveField:")
            text_field.setDelegate_(self._nick_handler)
            view.addSubview_(text_field)
            self._nick_handler._field = text_field
            text_field.performSelector_withObject_afterDelay_(
                b"selectText:", None, 0.0,
            )

            save_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(col_x + col_w - 52, y_cursor - 24, 24, 24)
            )
            save_btn.setTitle_("✓")
            save_btn.setBordered_(False)
            save_btn.setToolTip_("Speichern (Enter)")
            save_btn.setTarget_(self._nick_handler)
            save_btn.setAction_(b"saveButton:")
            view.addSubview_(save_btn)

            cancel_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(col_x + col_w - 26, y_cursor - 24, 24, 24)
            )
            cancel_btn.setTitle_("✗")
            cancel_btn.setBordered_(False)
            cancel_btn.setToolTip_("Abbrechen (Esc)")
            cancel_btn.setTarget_(self._nick_handler)
            cancel_btn.setAction_(b"cancelButton:")
            view.addSubview_(cancel_btn)
        else:
            view.addSubview_(_label(
                NSMakeRect(col_x, y_cursor - 22, col_w - 28, 22),
                title_text,
                font=NSFont.boldSystemFontOfSize_(15),
            ))
            edit_btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(col_x + col_w - 26, y_cursor - 24, 26, 24)
            )
            edit_btn.setTitle_("✏️")
            edit_btn.setBordered_(False)
            edit_btn.setToolTip_("Spitznamen bearbeiten")
            edit_btn.setTarget_(self._nick_handler)
            edit_btn.setAction_(b"beginEdit:")
            view.addSubview_(edit_btn)
        y_cursor -= 24

        subtitle_text = (
            f"#{species:03d}  {species_name}  ·  {p.caught_date.isoformat()}"
        )
        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 14, col_w, 14),
            subtitle_text,
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.tertiaryLabelColor(),
        ))
        y_cursor -= 18

        types = pokemon.types_of(species)
        badge_w_small = 52
        badge_h_small = 16
        badge_y = y_cursor - badge_h_small - 2
        col_cx = col_x + col_w / 2
        for badge in _type_badge_row(
            col_cx, badge_y, types, badge_w=badge_w_small, badge_h=badge_h_small,
        ):
            view.addSubview_(badge)
        y_cursor = badge_y - 4

        lvl_text = "Lv MAX" if level >= pokemon.MAX_LEVEL else f"Lv {level}"
        view.addSubview_(_label(
            NSMakeRect(col_x, y_cursor - 18, col_w, 18),
            lvl_text,
            font=NSFont.boldSystemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
        ))
        y_cursor -= 22

        progress = into / needed if needed > 0 else (
            1.0 if level >= pokemon.MAX_LEVEL else 0.0
        )
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
            f"Zuneigung  {_fmt_affection(p.affection)}",
            font=NSFont.systemFontOfSize_(12),
        ))
        y_cursor -= 20

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

        # Stats / IVs split.
        from tokenmon.pokemon import (
            IV_MAX, STAT_LABELS, STAT_ORDER, TYPE_COLORS, final_stats,
            nature_multipliers,
        )

        seg_top = 210
        seg_w = 160
        seg_h = 22
        def _stats_mode_changed(sender):
            idx = int(sender.selectedSegment())
            self.popover._stats_mode = "ivs" if idx == 1 else "stats"
            self.popover._show_pane(PANE_BOX)
        seg_handler = make_handler(_stats_mode_changed)
        self._handlers.append(seg_handler)
        seg = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect((CONTENT_WIDTH - seg_w) // 2, seg_top, seg_w, seg_h)
        )
        seg.setSegmentCount_(2)
        seg.setLabel_forSegment_("Stats", 0)
        seg.setLabel_forSegment_("IVs", 1)
        seg.setSelectedSegment_(1 if self._stats_mode == "ivs" else 0)
        seg.setTarget_(seg_handler)
        seg.setAction_(b"fire:")
        view.addSubview_(seg)

        content_top = seg_top - 8

        if self._stats_mode == "ivs":
            radar_size = 140
            radar_x = 24
            radar_y = 62
            primary_type = types[0] if types else "normal"
            type_color = TYPE_COLORS.get(primary_type)
            radar = _StatsRadarView.alloc().initWithFrame_ivs_typeColor_(
                NSMakeRect(radar_x, radar_y, radar_size, radar_size),
                p.ivs, type_color,
            )
            view.addSubview_(radar)

            iv_x = radar_x + radar_size + 16
            iv_w = CONTENT_WIDTH - iv_x - 16
            row_h = 22
            iv_top = radar_y + radar_size - 4
            for i, key in enumerate(STAT_ORDER):
                row_y = iv_top - (i + 1) * row_h
                view.addSubview_(_label(
                    NSMakeRect(iv_x, row_y, 50, row_h),
                    STAT_LABELS[key],
                    font=NSFont.systemFontOfSize_(11),
                    color=NSColor.secondaryLabelColor(),
                ))
                view.addSubview_(_label(
                    NSMakeRect(iv_x + 52, row_y, iv_w - 56, row_h),
                    f"{p.ivs[i]} / {IV_MAX}",
                    font=NSFont.boldSystemFontOfSize_(12),
                ))
        else:
            try:
                stats = final_stats(species, p.ivs, level, p.nature)
            except Exception:
                log.exception("final_stats failed")
                stats = (1, 1, 1, 1, 1, 1)
            mults = nature_multipliers(p.nature)

            col_w_stat = (CONTENT_WIDTH - 32) // 2
            grid_x = 24
            row_h = 28
            grid_h_total = 3 * row_h
            content_area_bottom = 52
            grid_top = content_top - (
                content_top - content_area_bottom - grid_h_total
            ) // 2
            for i, key in enumerate(STAT_ORDER):
                col = i % 2
                row = i // 2
                x = grid_x + col * col_w_stat
                y = grid_top - row_h - row * row_h
                view.addSubview_(_label(
                    NSMakeRect(x, y, 56, row_h),
                    STAT_LABELS[key],
                    font=NSFont.systemFontOfSize_(12),
                    color=NSColor.secondaryLabelColor(),
                ))
                mult = mults[key]
                if mult > 1.0:
                    color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        0.86, 0.40, 0.20, 1.0,
                    )
                elif mult < 1.0:
                    color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        0.30, 0.55, 0.85, 1.0,
                    )
                else:
                    color = NSColor.labelColor()
                view.addSubview_(_label(
                    NSMakeRect(x + 60, y, col_w_stat - 64, row_h),
                    f"{stats[i]}",
                    font=NSFont.boldSystemFontOfSize_(14),
                    color=color,
                ))

        # "Set as active" / "✓ Active" button at bottom.
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
            def _set_active(_s, pid=p.id):
                try:
                    box.set_active_pokemon(pid)
                except Exception:
                    log.exception("set_active_pokemon failed")
                try:
                    app = self.popover._app
                    if hasattr(app, "_refresh_pokemon_state"):
                        app._refresh_pokemon_state()
                except Exception:
                    log.exception("menubar refresh after set_active failed")
                try:
                    self.popover._refresh_sidebar_pokemon_icon()
                except Exception:
                    log.exception("sidebar icon refresh failed")
                self.popover._show_pane(PANE_BOX)
            active_handler = make_handler(_set_active)
            self._handlers.append(active_handler)
            active_btn.setTarget_(active_handler)
            active_btn.setAction_(b"fire:")
        active_btn.setBezelStyle_(1)
        view.addSubview_(active_btn)

        return view

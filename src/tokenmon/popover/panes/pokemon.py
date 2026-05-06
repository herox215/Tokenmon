"""Pokemon pane: active sprite + level/XP/affection/nature card + pat-animation.

Pat-state (sprite + heart NSTextFields) lives on the controller. The
``_PatClickCatcher`` (in ``widgets.py``) calls ``target._begin_pat()`` on
mouseDown — we set its target to this controller so the click drives the
controller's own pat machinery, not popover-global state.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSColor,
    NSFont,
    NSImage,
    NSImageView,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import box, pokemon
from tokenmon.popover.animation import (
    PAT_HEART_THRESHOLD,
    PAT_HOP_PX,
    _build_pat_steps,
    _PatHandler,
)
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    POPOVER_HEIGHT,
    TYPE_BADGE_HEIGHT,
    _PatClickCatcher,
    _crisp_image_view,
    _label,
    _type_badge_row,
)
from tokenmon.storage import query_xp_for_pokemon
from tokenmon.tokendex import _XPBarView
from tokenmon.ui_helpers import fmt_affection as _fmt_affection

log = logging.getLogger("tokenmon.popover.panes.pokemon")


class PokemonController(PaneController):
    """Active-Pokemon pane: identity card + pat interaction."""

    def __init__(self, popover) -> None:
        super().__init__(popover)
        # Pat-interaction state is per-controller — re-rendered each pane build.
        self._pat_active: bool = False
        self._pat_handler: _PatHandler | None = None
        self._pat_catcher: _PatClickCatcher | None = None
        self._pat_sprite: NSImageView | None = None
        self._pat_sprite_rest_y: int = 0
        self._pat_hearts: list[NSTextField] = []

    def build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        try:
            box.ensure_today_pokemon()
            row = box.get_active_pokemon()
        except Exception:
            log.exception("get_active_pokemon failed")
            row = None

        if row is None:
            view.addSubview_(_label(
                NSMakeRect(16, POPOVER_HEIGHT // 2 - 10, CONTENT_WIDTH - 32, 20),
                "Could not load active Pokémon.",
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        species = row.species_dex_id
        try:
            xp = query_xp_for_pokemon(row.id)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(species)
        level, into, needed = pokemon.level_from_xp(xp, rate)

        header_y = POPOVER_HEIGHT - 28
        view.addSubview_(_label(
            NSMakeRect(0, header_y, CONTENT_WIDTH, 20),
            f"Active: {pokemon.display_name(row.nickname, species)}",
            font=NSFont.boldSystemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = header_y - sprite_size - 12

        iv = _crisp_image_view(
            NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size)
        )
        sp = pokemon.ensure_sprite(species, shiny=row.is_shiny)
        if sp is not None and sp.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(sp))
            if img is not None:
                iv.setImage_(img)
        view.addSubview_(iv)
        self.popover._animated_image_views.append(iv)

        # --- Pat interaction wiring ---
        self._pat_sprite = iv
        self._pat_sprite_rest_y = sprite_y
        self._pat_active = False
        self._pat_handler = None

        heart_offsets: list[tuple[int, int, int]] = [
            (-26, sprite_size - 24, 24),
            (sprite_size // 2 - 12, sprite_size + 6, 22),
            (sprite_size + 6, sprite_size - 30, 26),
            (-18, int(sprite_size * 0.45), 20),
            (sprite_size + 4, int(sprite_size * 0.55), 22),
        ]
        hearts: list[NSTextField] = []
        for dx, dy, sz in heart_offsets:
            hx = sprite_x + dx
            hy = sprite_y + dy
            ht = _label(
                NSMakeRect(hx, hy, sz + 8, sz + 8),
                "❤️",
                font=NSFont.systemFontOfSize_(sz),
                align=NSTextAlignmentCenter,
            )
            ht.setHidden_(True)
            view.addSubview_(ht)
            hearts.append(ht)
        self._pat_hearts = hearts

        # _PatClickCatcher's mouseDown_ calls ``target._begin_pat()`` — we
        # are the target, so the click drives the controller's own state.
        catcher = _PatClickCatcher.alloc().initWithFrame_target_(
            NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size), self,
        )
        view.addSubview_(catcher)
        self._pat_catcher = catcher

        species_name = pokemon.name_of(species)
        display_name = pokemon.display_name(row.nickname, species)
        sym = pokemon.gender_symbol(row.gender)
        if row.nickname and row.nickname.strip():
            name_decoration = (
                ("✨ " if row.is_shiny else "")
                + display_name
                + (f"  {sym}" if sym else "")
            )
        else:
            name_decoration = (
                ("✨ " if row.is_shiny else "")
                + f"#{species:03d}  {species_name}"
                + (f"  {sym}" if sym else "")
            )
        name_y = sprite_y - 32
        view.addSubview_(_label(
            NSMakeRect(0, name_y, CONTENT_WIDTH, 26),
            name_decoration,
            font=NSFont.boldSystemFontOfSize_(18),
            align=NSTextAlignmentCenter,
        ))

        if row.nickname and row.nickname.strip():
            subtitle_y = name_y - 14
            view.addSubview_(_label(
                NSMakeRect(0, subtitle_y, CONTENT_WIDTH, 14),
                f"#{species:03d}  {species_name}",
                font=NSFont.systemFontOfSize_(11),
                color=NSColor.tertiaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            badge_anchor_y = subtitle_y
        else:
            badge_anchor_y = name_y

        types = pokemon.types_of(species)
        badge_y = badge_anchor_y - TYPE_BADGE_HEIGHT - 6
        for badge in _type_badge_row(CONTENT_WIDTH / 2, badge_y, types):
            view.addSubview_(badge)

        lvl_y = badge_y - 22
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
        progress = into / needed if needed > 0 else (
            1.0 if level >= pokemon.MAX_LEVEL else 0.0
        )
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

        affection_y = xp_y - 22
        view.addSubview_(_label(
            NSMakeRect(0, affection_y, CONTENT_WIDTH, 16),
            f"Affection   {_fmt_affection(row.affection)}",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.labelColor(),
            align=NSTextAlignmentCenter,
        ))

        nature_y = affection_y - 22
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

    # ---- pat interaction state machine -------------------------------

    def _begin_pat(self) -> None:
        """Called by ``_PatClickCatcher.mouseDown_`` — we are its target.
        Hearts appear when current affection clears the 90% threshold.
        Re-queries affection so growth that happened since the pane was
        rendered counts.
        """
        if self._pat_active or self._pat_sprite is None:
            return
        try:
            row = box.get_active_pokemon()
        except Exception:
            log.exception("get_active_pokemon failed in _begin_pat")
            return
        with_hearts = (
            row is not None and int(row.affection) >= PAT_HEART_THRESHOLD
        )
        steps = _build_pat_steps(with_hearts)
        self._pat_active = True
        self._pat_handler = (
            _PatHandler.alloc().initWithPopover_steps_(self.popover, steps)
        )
        self._pat_handler.start()

    def pat_step(self, action: str) -> None:
        """Forwarded by ``TokenmonPopover._pat_step`` from the timer handler."""
        sprite = self._pat_sprite
        if sprite is None:
            return
        rest_y = self._pat_sprite_rest_y
        if action == "hop_up":
            f = sprite.frame()
            sprite.setFrame_(NSMakeRect(
                f.origin.x, rest_y + PAT_HOP_PX, f.size.width, f.size.height,
            ))
            return
        if action == "hop_down":
            f = sprite.frame()
            sprite.setFrame_(NSMakeRect(
                f.origin.x, rest_y, f.size.width, f.size.height,
            ))
            return
        if action.startswith("heart_"):
            idx = int(action.rsplit("_", 1)[-1]) - 1
            if 0 <= idx < len(self._pat_hearts):
                self._pat_hearts[idx].setHidden_(False)
            return
        if action == "done":
            self.end_pat()
            return

    def end_pat(self) -> None:
        self._pat_active = False
        self._pat_handler = None
        for ht in self._pat_hearts:
            try:
                ht.setHidden_(True)
            except Exception:
                pass
        if self._pat_sprite is not None:
            f = self._pat_sprite.frame()
            self._pat_sprite.setFrame_(NSMakeRect(
                f.origin.x, self._pat_sprite_rest_y,
                f.size.width, f.size.height,
            ))

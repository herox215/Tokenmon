"""Encounter pane: post-catch reveal only.

After Phase 4/5, the pre-fight preview moved to ``encounter_preview`` and
the bag/throw flow moved into the battle pane. This module survives only
to host ``EncounterController.begin_catch_reveal`` — the imperative
hand-off from the catch animation. Module rename to ``catch_reveal.py``
was deliberately skipped (see plan Phase 6) — renaming Python files
churns imports for no behavioural gain.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSColor,
    NSFont,
    NSImage,
    NSTextAlignmentCenter,
    NSTimer,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import pokemon
from tokenmon.popover.animation import _RevealTimerHandler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    TYPE_BADGE_HEIGHT,
    _crisp_image_view,
    _label,
    _type_badge_row,
)

log = logging.getLogger("tokenmon.popover.panes.encounter")


class EncounterController(PaneController):
    """Reveal-only controller: shown imperatively after a successful catch
    via ``begin_catch_reveal``. The pre-fight preview lives in
    EncounterPreviewController; the bag-open inventory lives on the battle
    pane (Phase 5)."""

    def build_view(self) -> NSView:
        pop = self.popover
        if pop._pending_reveal_pokemon is not None:
            return self._build_reveal(pop._pending_reveal_pokemon)
        # Empty fallback for stray sidebar clicks mid-reveal — the unified
        # preview owns the real "what's pending" screen.
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )
        view.addSubview_(_label(
            NSMakeRect(16, POPOVER_HEIGHT // 2 - 10, CONTENT_WIDTH - 32, 20),
            "",
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))
        return view

    def _build_reveal(self, payload: dict) -> NSView:
        """Real animated sprite + 'caught!' banner."""
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

    def begin_catch_reveal(self) -> None:
        """Called by the catch-animation controller when the catch resolves
        successfully. Loads the latest caught row, flips this pane into
        reveal mode without going through ``_show_pane`` (we want the
        encounter-slot to stay in the sidebar for the 2.5 s hold), and
        schedules the dismiss timer."""
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

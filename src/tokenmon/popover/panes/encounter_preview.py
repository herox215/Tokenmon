"""Unified encounter-preview pane.

One slot, two flavors: when a trainer is pending, render the trainer
preview (sprite, title, difficulty, ball-row); otherwise when a wild
encounter is pending, render the silhouette + level. Trainer takes
priority — matches show_from_button precedence.

Run-button semantics differ by kind:
- trainer Run → mark_trainer_resolved(status='ran'), back to Pokemon pane.
- wild Run    → encounter.run_away(eid), back to Pokemon pane.

No money penalty on either since both are pre-fight.
"""
from __future__ import annotations

import logging

from AppKit import (
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextAlignmentCenter,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import config, encounter, pokemon, weather
from tokenmon.overlay import _silhouette_image
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_BATTLE,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    _crisp_image_view,
    _label,
)
from tokenmon.storage import (
    get_pending_encounter,
    get_pending_trainer,
    list_trainer_pokemon,
    mark_trainer_resolved,
)

log = logging.getLogger("tokenmon.popover.panes.encounter_preview")


_DIFFICULTY_COLORS = {
    "easy": (0.36, 0.78, 0.20, 1.0),
    "medium": (1.00, 0.65, 0.10, 1.0),
    "hard": (0.95, 0.30, 0.30, 1.0),
}


class EncounterPreviewController(PaneController):
    """Pre-battle preview for both trainers and wild Pokemon."""

    def build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        try:
            trainer = get_pending_trainer()
        except Exception:
            log.exception("get_pending_trainer failed")
            trainer = None

        if trainer is not None:
            self._kind = "trainer"
            self._render_trainer(view, trainer)
            return view

        try:
            enc = get_pending_encounter()
        except Exception:
            log.exception("get_pending_encounter failed")
            enc = None

        if enc is not None:
            self._kind = "wild"
            self._render_wild(view, enc)
            return view

        self._kind = None
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT // 2, CONTENT_WIDTH - 40, 22),
            "No encounter waiting.",
            font=NSFont.systemFontOfSize_(13),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))
        return view

    # ---- trainer branch ------------------------------------------------

    def _render_trainer(self, view: NSView, trainer) -> None:
        try:
            team = list_trainer_pokemon(trainer.id)
        except Exception:
            log.exception("list_trainer_pokemon failed")
            team = []

        # Belt-and-suspenders prefetch: maybe_spawn already kicked one
        # off, but if the user opens the preview minutes later the
        # in-memory cache may have been GC'd.
        try:
            from tokenmon import trainer as trainer_mod
            trainer_mod._kick_battle_asset_prefetch(trainer.id)
        except Exception:
            log.exception("preview-pane prefetch kick failed")

        sprite_path = None
        try:
            from tokenmon import trainers_remote
            sprite_path = trainers_remote.ensure_trainer_sprite(trainer.title)
        except Exception:
            log.exception("trainer sprite lookup failed")

        avatar_y = POPOVER_HEIGHT - 110
        if sprite_path is not None:
            sprite_size = 96
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(
                (CONTENT_WIDTH - sprite_size) // 2, avatar_y,
                sprite_size, sprite_size,
            ))
            iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            iv.setWantsLayer_(True)
            layer = iv.layer()
            if layer is not None:
                layer.setMagnificationFilter_("nearest")
                layer.setMinificationFilter_("nearest")
            img = NSImage.alloc().initWithContentsOfFile_(str(sprite_path))
            if img is not None:
                iv.setImage_(img)
                view.addSubview_(iv)
            else:
                sprite_path = None
        if sprite_path is None:
            try:
                from tokenmon import trainers_remote
                avatar = trainers_remote.emoji_for(trainer.title)
            except Exception:
                avatar = "👤"
            view.addSubview_(_label(
                NSMakeRect(20, POPOVER_HEIGHT - 90, CONTENT_WIDTH - 40, 60),
                avatar,
                font=NSFont.systemFontOfSize_(48),
                align=NSTextAlignmentCenter,
            ))
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 120, CONTENT_WIDTH - 40, 22),
            f"{trainer.title} {trainer.name}",
            font=NSFont.boldSystemFontOfSize_(16),
            align=NSTextAlignmentCenter,
        ))

        diff = trainer.difficulty.lower()
        rgba = _DIFFICULTY_COLORS.get(diff, (0.5, 0.5, 0.5, 1.0))
        diff_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgba)
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 150, CONTENT_WIDTH - 40, 20),
            f"Difficulty: {diff.upper()}",
            font=NSFont.boldSystemFontOfSize_(12),
            color=diff_color,
            align=NSTextAlignmentCenter,
        ))

        balls = "  ".join("🔴" for _ in team) or "(empty team)"
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 200, CONTENT_WIDTH - 40, 30),
            balls,
            font=NSFont.systemFontOfSize_(22),
            align=NSTextAlignmentCenter,
        ))
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 230, CONTENT_WIDTH - 40, 18),
            f"Team size: {len(team)}",
            font=NSFont.systemFontOfSize_(11),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        self._add_action_buttons(view, trainer_id=trainer.id, encounter_id=None)

    # ---- wild branch ---------------------------------------------------

    def _render_wild(self, view: NSView, enc) -> None:
        header_y = POPOVER_HEIGHT - 40
        view.addSubview_(_label(
            NSMakeRect(16, header_y, CONTENT_WIDTH - 32, 22),
            "A wild Pokemon appeared!",
            font=NSFont.boldSystemFontOfSize_(15),
            align=NSTextAlignmentCenter,
        ))

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
        self.popover._animated_image_views.append(iv)

        info_y = sprite_y - 24
        view.addSubview_(_label(
            NSMakeRect(0, info_y, CONTENT_WIDTH, 18),
            f"Lv {enc.level}     Type: ???",
            font=NSFont.systemFontOfSize_(12),
            color=NSColor.secondaryLabelColor(),
            align=NSTextAlignmentCenter,
        ))

        if config.get("use_weather"):
            try:
                snap = weather.get_weather()
            except Exception:
                log.exception("weather.get_weather failed in encounter preview")
                snap = None
            if snap is not None:
                view.addSubview_(_label(
                    NSMakeRect(0, info_y - 16, CONTENT_WIDTH, 14),
                    weather.emoji_label(snap),
                    font=NSFont.systemFontOfSize_(11),
                    color=NSColor.secondaryLabelColor(),
                    align=NSTextAlignmentCenter,
                ))

        self._add_action_buttons(view, trainer_id=None, encounter_id=enc.id)

    # ---- shared button bar ---------------------------------------------

    def _add_action_buttons(
        self, view: NSView, *, trainer_id: int | None, encounter_id: int | None,
    ) -> None:
        btn_w = 120
        gap = 16
        total = btn_w * 2 + gap
        x_left = (CONTENT_WIDTH - total) // 2

        fight_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(x_left, 60, btn_w, 32)
        )
        fight_btn.setTitle_("Fight")
        fight_btn.setBezelStyle_(1)

        def _start_fight(_s):
            try:
                self.popover._battle_session = None
                self.popover._show_pane(PANE_BATTLE)
            except Exception:
                log.exception("transition to battle pane failed")

        fight_handler = make_handler(_start_fight)
        self._handlers.append(fight_handler)
        fight_btn.setTarget_(fight_handler)
        fight_btn.setAction_(b"fire:")
        view.addSubview_(fight_btn)

        run_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(x_left + btn_w + gap, 60, btn_w, 32)
        )
        run_btn.setTitle_("Run")
        run_btn.setBezelStyle_(1)

        def _run(_s):
            if trainer_id is not None:
                try:
                    mark_trainer_resolved(trainer_id, status="ran")
                except Exception:
                    log.exception("trainer run failed")
            elif encounter_id is not None:
                try:
                    encounter.run_away(encounter_id)
                except Exception:
                    log.exception("wild run failed")
            self.popover._show_pane(PANE_POKEMON)

        run_handler = make_handler(_run)
        self._handlers.append(run_handler)
        run_btn.setTarget_(run_handler)
        run_btn.setAction_(b"fire:")
        view.addSubview_(run_btn)

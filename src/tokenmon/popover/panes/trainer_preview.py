"""Trainer-battle preview pane.

Shown when a trainer is pending. Displays the trainer's name + title +
difficulty + a row of Pokéballs representing their team size (without
spoiling species). Two buttons: Fight (transitions to BattleController)
and Run (forfeits before the battle even starts — counts as
``resolved="ran"``, no rewards, can spawn another later).
"""
from __future__ import annotations

import logging

from AppKit import (
    NSButton,
    NSColor,
    NSFont,
    NSTextAlignmentCenter,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon.popover._handlers import make_handler
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_BATTLE,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    _label,
)
from tokenmon.storage import (
    get_pending_trainer,
    list_trainer_pokemon,
    mark_trainer_resolved,
)

log = logging.getLogger("tokenmon.popover.panes.trainer_preview")


_DIFFICULTY_COLORS = {
    "easy": (0.36, 0.78, 0.20, 1.0),    # green
    "medium": (1.00, 0.65, 0.10, 1.0),  # orange
    "hard": (0.95, 0.30, 0.30, 1.0),    # red
}


class TrainerPreviewController(PaneController):
    """Pre-battle preview. Shows the trainer + team-size + difficulty
    badge and a Fight / Run choice."""

    def build_view(self) -> NSView:
        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, CONTENT_WIDTH, POPOVER_HEIGHT)
        )

        try:
            trainer = get_pending_trainer()
        except Exception:
            log.exception("get_pending_trainer failed")
            trainer = None

        if trainer is None:
            view.addSubview_(_label(
                NSMakeRect(20, POPOVER_HEIGHT // 2, CONTENT_WIDTH - 40, 22),
                "No trainer waiting.",
                font=NSFont.systemFontOfSize_(13),
                color=NSColor.secondaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            return view

        try:
            team = list_trainer_pokemon(trainer.id)
        except Exception:
            log.exception("list_trainer_pokemon failed")
            team = []

        # Header — emoji + title + name
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 80, CONTENT_WIDTH - 40, 36),
            "👤",
            font=NSFont.systemFontOfSize_(28),
            align=NSTextAlignmentCenter,
        ))
        view.addSubview_(_label(
            NSMakeRect(20, POPOVER_HEIGHT - 120, CONTENT_WIDTH - 40, 22),
            f"{trainer.title} {trainer.name}",
            font=NSFont.boldSystemFontOfSize_(16),
            align=NSTextAlignmentCenter,
        ))

        # Difficulty badge
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

        # Pokéball row — one per team-mon. Cute, doesn't spoil species.
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

        # Buttons
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
                # Reset any prior battle session — fresh battle state.
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
            try:
                mark_trainer_resolved(trainer.id, status="ran")
                self.popover._show_pane(PANE_POKEMON)
            except Exception:
                log.exception("trainer run failed")

        run_handler = make_handler(_run)
        self._handlers.append(run_handler)
        run_btn.setTarget_(run_handler)
        run_btn.setAction_(b"fire:")
        view.addSubview_(run_btn)

        return view

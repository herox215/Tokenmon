"""Pokemon pane: active sprite + level/XP/affection/nature card + pat-animation.

Pat-state (sprite + heart NSTextFields) lives on the controller. The
``_PatClickCatcher`` (in ``widgets.py``) calls ``target._begin_pat()`` on
mouseDown — we set its target to this controller so the click drives the
controller's own pat machinery, not popover-global state.
"""
from __future__ import annotations

import logging

import objc
from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSImageView,
    NSTextAlignmentCenter,
    NSTextField,
    NSTimer,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import box, pokemon
from tokenmon.popover._handlers import make_handler
from tokenmon.popover.animation import (
    PAT_HEART_THRESHOLD,
    PAT_HOP_PX,
    _build_pat_steps,
    _PatHandler,
)
from tokenmon.popover.panes.base import PaneController
from tokenmon.popover.widgets import (
    CONTENT_WIDTH,
    PANE_BOX,
    PANE_POKEMON,
    POPOVER_HEIGHT,
    TYPE_BADGE_HEIGHT,
    _PatClickCatcher,
    _crisp_image_view,
    _label,
    _type_badge_row,
)
from tokenmon.storage import query_xp_for_pokemon
from tokenmon.tokendex import _XPBarView

log = logging.getLogger("tokenmon.popover.panes.pokemon")


HP_ANIM_FRAMES = 24    # 24 × 0.04 s = ~1.0 s fill duration
HP_ANIM_INTERVAL = 0.04


class _AnimatedHPBar(NSView):
    """HP bar that tweens from ``start_hp`` to ``end_hp`` (vs ``hp_max``)
    over ~1 s on first display. Used by the active-Pokémon pane to
    visualise potion heals — the user clicks "Use Potion" in the items
    pane and is bounced here with the bar already fill-animating.

    Colour follows the same green/orange/red threshold rules as
    ``battle._HPBar`` so the visualisation is consistent across panes.
    """

    def initWithFrame_from_to_max_(  # noqa: N802
        self, frame, start_hp, end_hp, hp_max,
    ):
        self = objc.super(_AnimatedHPBar, self).initWithFrame_(frame)
        if self is None:
            return None
        self._start = max(0, int(start_hp))
        self._end = max(0, int(end_hp))
        self._max = max(1, int(hp_max))
        self._current = self._start
        self._frame = 0
        self._timer = None
        return self

    def viewDidMoveToWindow(self):  # noqa: N802
        try:
            objc.super(_AnimatedHPBar, self).viewDidMoveToWindow()
        except Exception:
            pass
        if self._timer is None and self.window() is not None:
            self._timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    HP_ANIM_INTERVAL, self, b"_step:", None, True,
                )
            )

    def _step_(self, _t):  # noqa: N802
        self._frame += 1
        if self._frame >= HP_ANIM_FRAMES:
            self._current = self._end
            if self._timer is not None:
                try:
                    self._timer.invalidate()
                except Exception:
                    pass
                self._timer = None
            self.setNeedsDisplay_(True)
            return
        t = self._frame / HP_ANIM_FRAMES
        eased = 1.0 - (1.0 - t) ** 3
        self._current = self._start + (self._end - self._start) * eased
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):  # noqa: N802
        bounds = self.bounds()
        # Track
        NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.18).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, 4, 4,
        ).fill()
        if self._current <= 0:
            return
        frac = max(0.0, min(1.0, self._current / self._max))
        if frac > 0.5:
            r, g, b = 0.36, 0.78, 0.20
        elif frac > 0.2:
            r, g, b = 1.00, 0.65, 0.10
        else:
            r, g, b = 0.95, 0.30, 0.30
        fill_w = bounds.size.width * frac
        fill = NSMakeRect(0, 0, fill_w, bounds.size.height)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            fill, 4, 4,
        ).fill()


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
        detail_handler = make_handler(lambda _s, pid=row.id: self._open_box_detail(pid))
        self._handlers.append(detail_handler)
        detail_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(CONTENT_WIDTH - 86, POPOVER_HEIGHT - 34, 72, 24)
        )
        detail_btn.setTitle_("Details")
        detail_btn.setBezelStyle_(1)
        detail_btn.setTarget_(detail_handler)
        detail_btn.setAction_(b"fire:")
        view.addSubview_(detail_btn)

        sprite_size = 144
        sprite_x = (CONTENT_WIDTH - sprite_size) // 2
        sprite_y = header_y - sprite_size - 12

        iv = _crisp_image_view(
            NSMakeRect(sprite_x, sprite_y, sprite_size, sprite_size)
        )
        sp = pokemon.ensure_sprite(species, shiny=row.is_shiny)
        if sp is not None and sp.exists():
            # GIF playback slows below 80% HP — same speed-curve drives
            # both this sprite and the desktop companion's.
            try:
                from tokenmon.pokemon.stats import final_stats
                from tokenmon.sprite_speed import (
                    hp_playback_speed, load_animated_image,
                )
                hp_max = final_stats(
                    species, row.ivs, max(1, level), row.nature,
                )[0]
                speed = hp_playback_speed(row.hp_current, hp_max)
                img = load_animated_image(sp, speed=speed)
            except Exception:
                log.exception("HP-aware sprite load failed; falling back")
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

        # HP block — shows the persisted current HP vs the level's max.
        # Reuses the battle pane's coloured-by-threshold ``_HPBar`` so
        # the visualisation matches what the user sees in combat.
        # When the popover sets ``_hp_anim_from_hp`` (e.g. just-used
        # potion), the bar animates from that value to the actual
        # current HP over ~1 s instead of snapping.
        try:
            from tokenmon.pokemon.stats import final_stats
            from tokenmon.popover.panes.battle import _HPBar
            hp_max = final_stats(
                species, row.ivs, max(1, level), row.nature,
            )[0]
            hp_cur = (
                int(row.hp_current) if row.hp_current is not None
                else hp_max
            )
            hp_cur = max(0, min(hp_cur, hp_max))
            hp_y = lvl_y - 14
            anim_from = getattr(self.popover, "_hp_anim_from_hp", None)
            anim_to = getattr(self.popover, "_hp_anim_to_hp", None)
            if anim_from is not None and anim_to is not None:
                # Single-shot animation — clear the hint after consuming.
                hp_bar = _AnimatedHPBar.alloc().initWithFrame_from_to_max_(
                    NSMakeRect(bar_x, hp_y, bar_w, 8),
                    int(anim_from), int(anim_to), hp_max,
                )
                self.popover._hp_anim_from_hp = None
                self.popover._hp_anim_to_hp = None
                self.popover._hp_anim_max = None
                hp_label_value = anim_to
            else:
                hp_bar = _HPBar.alloc().initWithFrame_current_max_(
                    NSMakeRect(bar_x, hp_y, bar_w, 8), hp_cur, hp_max,
                )
                hp_label_value = hp_cur
            view.addSubview_(hp_bar)
            view.addSubview_(_label(
                NSMakeRect(0, hp_y - 16, CONTENT_WIDTH, 14),
                f"{hp_label_value} / {hp_max} HP",
                font=NSFont.systemFontOfSize_(11),
                color=NSColor.tertiaryLabelColor(),
                align=NSTextAlignmentCenter,
            ))
            after_hp_y = hp_y - 16 - 12  # text + small gap before XP
        except Exception:
            log.exception("HP block render failed")
            after_hp_y = lvl_y - 14

        bar_y = after_hp_y - 8
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

        cursor_y = xp_y - 22

        # Move grid: shows the four learned moves with hover tooltips
        # for power/accuracy/PP. Backfills missing moves from the
        # learnset so a Pokémon caught before this feature still shows
        # something sensible.
        try:
            self._backfill_initial_moves(row.id, row.species_dex_id, level)
            from tokenmon.popover.panes._move_grid import build_move_grid
            grid_h = build_move_grid(
                view, pokemon_id=row.id, top_y=cursor_y - 4,
            )
        except Exception:
            log.exception("move grid build failed")
            grid_h = 0
        cursor_y -= grid_h

        nature_y = cursor_y - 22
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

    def _open_box_detail(self, pokemon_id: int) -> None:
        self.popover._box_selected_id = int(pokemon_id)
        self.popover._box_return_pane = PANE_POKEMON
        self.popover._box_swap_slot = None
        self.popover._editing_nickname = False
        self.popover._show_pane(PANE_BOX)

    def _backfill_initial_moves(
        self, pokemon_id: int, species_dex_id: int, level: int,
    ) -> None:
        """If this Pokémon has no rows in ``pokemon_moves`` yet (caught
        before the trainer-battle feature, or via legacy code path),
        seed it with the latest 4 level-up moves the species would
        know at its current level. PokeAPI lookups + cache reads run
        synchronously here, but the caller is already inside a pane
        build; cold-cache cost is bounded to ~4 fetches × ~200 ms.
        """
        try:
            from tokenmon import learnsets_remote, moves_remote
            from tokenmon.storage import (
                get_pokemon_moves,
                set_pokemon_move,
                unlock_move,
            )
        except Exception:
            log.exception("move backfill imports failed")
            return
        try:
            existing = get_pokemon_moves(pokemon_id)
            if existing:
                return
            keys = learnsets_remote.initial_moves(
                species_dex_id, max(1, level),
            )
            for slot, key in enumerate(keys[:4]):
                md = moves_remote.get_move_data(key)
                max_pp = md.pp if md is not None else 35
                set_pokemon_move(pokemon_id, slot, key, max_pp=max_pp)
                try:
                    unlock_move(pokemon_id, key, max(1, level))
                except Exception:
                    log.exception("unlock_move failed for %s", key)
        except Exception:
            log.exception("move backfill failed for #%d", pokemon_id)

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

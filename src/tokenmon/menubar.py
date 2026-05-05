"""macOS menubar app — shows today's total tokens with a 🥚 icon.

Click opens a dropdown with per-model breakdown and estimated USD cost.
Refreshes every 30 seconds from SQLite. Pings the proxy /healthz every 10s
and shows a warning state if the proxy is down.
"""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import date
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import objc
import rumps
from AppKit import (
    NSColor,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseUp,
    NSEventTypeRightMouseDown,
    NSEventTypeRightMouseUp,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextField,
    NSTimer,
    NSView,
)
from Foundation import NSMakeRect, NSObject

from tokenmon import box, config, items, items_remote, pokemon, tokendex
from tokenmon.menubar_sprite import SpriteAnimator
from tokenmon.overlay import PokemonOverlay
from tokenmon.popover import TokenmonPopover
from tokenmon.pricing import cost_for
from tokenmon.proxy import HOST, PORT
from tokenmon.storage import (
    Totals,
    init_db,
    query_today,
    query_today_by_model,
    query_xp_for_date,
    query_xp_for_pokemon,
)

REFRESH_INTERVAL_SEC = 30
HEALTH_INTERVAL_SEC = 10
ACTIVITY_POLL_INTERVAL_SEC = 5
TZ = "Europe/Berlin"
EGG = "🥚"
EGG_DOWN = "⚠️"

log = logging.getLogger("tokenmon.menubar")


class _ButtonWireHandler(NSObject):
    """One-shot NSTimer target that wires the status-bar button to our popover.
    Has to fire AFTER applicationDidFinishLaunching, since rumps installs its
    own NSMenu on the status item there. Scheduling a 0.1 s timer from
    __init__ guarantees we run after launch finishes."""

    def initWithApp_(self, app):  # noqa: N802
        self = objc.super(_ButtonWireHandler, self).init()
        if self is None:
            return None
        self._app = app
        return self

    def wire_(self, _timer):  # noqa: N802
        try:
            btn = self._app._statusbar_button()
            if btn is None or self._app._popover is None:
                return
            try:
                self._app._nsapp.nsstatusitem.setMenu_(None)
            except Exception:
                log.exception("setMenu_(None) failed")
            # Receive both left and right mouse-up events so the popover can
            # be shown on left-click and a fallback context menu on right.
            try:
                btn.cell().sendActionOn_(
                    NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp
                )
            except Exception:
                log.exception("sendActionOn_ failed")
            btn.setTarget_(self._app._popover)
            # IMPORTANT: the popover's buttonClicked_ must be decorated with
            # @objc.IBAction. Without that, pyobjc's auto-bridged selector
            # registers OK (respondsToSelector_ returns True) and performClick_
            # works, but real mouse events don't dispatch through it.
            btn.setAction_("buttonClicked:")
        except Exception:
            log.exception("button wiring failed")


def _active_provider_endpoints() -> list[tuple[str, str]]:
    """Return [(provider_name, health_url), ...] for everything that
    proxy_providers config currently lists."""
    from tokenmon.providers import load as load_provider
    out: list[tuple[str, str]] = []
    for name in (config.get("proxy_providers") or ["anthropic"]):
        try:
            strategy = load_provider(name)
        except ValueError:
            log.warning("unknown provider in config: %s", name)
            continue
        out.append((name, f"http://{HOST}:{strategy.default_port}/healthz"))
    return out


def _fmt_tokens(n: int) -> str:
    """1234 -> '1.2K', 1_234_567 -> '1.2M'."""
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


def _ping(url: str, timeout: float = 1.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (URLError, TimeoutError, OSError):
        return False


def _proxy_health() -> tuple[bool, list[str]]:
    """Returns (all_up, down_providers). all_up = True even when the list of
    configured providers is empty (nothing to fail)."""
    down: list[str] = []
    for name, url in _active_provider_endpoints():
        if not _ping(url):
            down.append(name)
    return (len(down) == 0), down


from tokenmon.tokendex import _XPBarView  # ObjC class — defined once, imported here


def _label(frame, text, *, font=None, color=None) -> NSTextField:
    f = NSTextField.alloc().initWithFrame_(frame)
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setFont_(font or NSFont.menuFontOfSize_(13))
    f.setTextColor_(color or NSColor.labelColor())
    return f


def _make_pokemon_view(
    sprite: Path | None,
    name_label: str,
    level: int,
    xp_into: int,
    xp_needed: int,
) -> NSView:
    """Custom NSView for an NSMenuItem: animated sprite + name + level + XP bar.
    Uses pyobjc directly because rumps' MenuItem.icon is a static NSImage."""
    width, height = 260, 96
    container = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))

    sprite_size = 72
    img_view = NSImageView.alloc().initWithFrame_(
        NSMakeRect(12, (height - sprite_size) / 2, sprite_size, sprite_size)
    )
    if sprite is not None and sprite.exists():
        img = NSImage.alloc().initWithContentsOfFile_(str(sprite))
        if img is not None:
            img_view.setImage_(img)
    img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    img_view.setAnimates_(True)
    container.addSubview_(img_view)

    text_x = sprite_size + 24
    text_w = width - text_x - 8

    name_y = height - 30
    container.addSubview_(_label(
        NSMakeRect(text_x, name_y, text_w, 18),
        name_label,
        font=NSFont.boldSystemFontOfSize_(13),
    ))

    level_y = name_y - 20
    container.addSubview_(_label(
        NSMakeRect(text_x, level_y, text_w, 16),
        f"Lv {level}" if level < pokemon.MAX_LEVEL else "Lv MAX",
        font=NSFont.menuFontOfSize_(12),
    ))

    bar_y = level_y - 14
    bar_w = text_w - 4
    progress = xp_into / xp_needed if xp_needed > 0 else (1.0 if level >= pokemon.MAX_LEVEL else 0.0)
    bar = _XPBarView.alloc().initWithFrame_progress_(
        NSMakeRect(text_x, bar_y, bar_w, 8), progress,
    )
    container.addSubview_(bar)

    xp_y = bar_y - 16
    xp_text = "MAX" if level >= pokemon.MAX_LEVEL else f"{xp_into:,} / {xp_needed:,} XP"
    container.addSubview_(_label(
        NSMakeRect(text_x, xp_y, text_w, 14),
        xp_text,
        font=NSFont.menuFontOfSize_(10),
        color=NSColor.secondaryLabelColor(),
    ))

    return container


def _restart_proxies_via_launchctl() -> tuple[bool, str]:
    """Restart every configured provider's proxy via launchctl. Returns
    (all_ok, message)."""
    import os as _os
    from tokenmon.launchd import proxy_label
    failures: list[str] = []
    for name in (config.get("proxy_providers") or ["anthropic"]):
        label = proxy_label(name)
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{_os.getuid()}/{label}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                failures.append(f"{name}: {result.stderr.strip() or f'exit {result.returncode}'}")
        except (subprocess.SubprocessError, OSError) as exc:
            failures.append(f"{name}: {exc}")
    if not failures:
        return True, "Proxies neugestartet"
    return False, "; ".join(failures)


class TokenmonApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(name="Tokenmon", title=f"{EGG} 0", quit_button=None)
        self._proxy_up = True
        # Backfill any historical days that don't have a Pokemon entry yet,
        # then ensure today's entry exists. ensure_today_pokemon is the
        # source of truth for "today's species" — its species_dex_id drives
        # the menubar icon, the overlay, and level-up detection.
        try:
            init_db()
            box.migrate_legacy_days()
            today_row = box.ensure_today_pokemon()
            self._line_base_id = today_row.species_dex_id
        except Exception:
            log.exception("box init failed; falling back to legacy pick")
            self._line_base_id = pokemon.pick_for_today()
        self._pokemon_picked_for: date = date.today()
        # Current displayed dex_id (could be evolved form). Recomputed each refresh.
        self._pokemon_dex_id: int = self._line_base_id
        self._pokemon_sprite: Path | None = pokemon.ensure_sprite(self._pokemon_dex_id)
        self._show_pokemon = bool(config.get("show_pokemon_in_menubar"))
        self._animator: SpriteAnimator | None = None
        self._overlay = PokemonOverlay(
            size=int(config.get("overlay_size") or 128),
            corner=str(config.get("overlay_corner") or "bottom-right"),
        )
        self._show_overlay = bool(config.get("show_overlay"))
        # Level-up detection state. The overlay never appears outside of
        # level-up events, so we only need a "last seen level" to detect changes.
        self._last_known_level: int = self._compute_current_level()
        # Wild-encounter spawn tracking — for every new requests row, we roll
        # encounter.maybe_spawn(). _last_seen_request_id holds the highest id
        # already considered, so we don't double-roll.
        self._last_seen_request_id: int = self._query_max_request_id()
        # Popover replaces the rumps dropdown. Wired after launch via a
        # short-lived NSTimer (see _ButtonWireHandler).
        self._popover = TokenmonPopover.alloc().initWithApp_(self)
        self._button_wire_handler = _ButtonWireHandler.alloc().initWithApp_(self)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self._button_wire_handler, b"wire:", None, False,
        )
        self._sync_menubar_icon()
        self.refresh(None)

    def _refresh_pokemon_state(self) -> None:
        """The menubar icon mirrors the user's ACTIVE Pokemon. When the
        active's trained XP crosses an evolution threshold, box.maybe_evolve
        mutates its species_dex_id in the DB — the row literally becomes the
        evolved Pokemon, matching real Pokemon-game semantics."""
        try:
            active = box.get_active_pokemon()
        except Exception:
            log.exception("get_active_pokemon failed")
            active = None
        if active is None:
            return

        # User switched active to a different row.
        if active.species_dex_id != self._line_base_id:
            self._line_base_id = active.species_dex_id
            self._pokemon_dex_id = active.species_dex_id
            self._pokemon_sprite = pokemon.ensure_sprite(active.species_dex_id)
            self._last_known_level = self._compute_current_level()
            self._sync_menubar_icon()
            self._sync_overlay()
            return

        # Try to evolve the active Pokemon if its XP says it's due.
        try:
            new_id = box.maybe_evolve(active.id)
        except Exception:
            log.exception("maybe_evolve failed")
            new_id = None
        if new_id is None:
            return

        old_id = self._pokemon_dex_id
        self._pokemon_dex_id = new_id
        self._line_base_id = new_id
        self._pokemon_sprite = pokemon.ensure_sprite(new_id)
        self._sync_menubar_icon()
        self._sync_overlay()

        # Evolution always implies a forward step — fire notification + animation.
        base = pokemon.line_of(new_id)
        chain = pokemon.evolution_chain(base)
        if old_id in chain and new_id in chain and chain.index(new_id) > chain.index(old_id):
            # Real in-line evolution. Fire notification + animation, and sync the
            # level cache so the level-up animation doesn't also fire.
            self._last_known_level = self._compute_current_level()
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle="Entwicklung!",
                    message=f"{pokemon.name_of(old_id)} entwickelt sich zu {pokemon.name_of(new_id)}!",
                )
            except Exception:
                log.exception("evolution notification failed")
            if self._show_overlay:
                try:
                    self._overlay.show_evolution(old_id, new_id)
                except Exception:
                    log.exception("evolution animation failed")

    def _sync_overlay(self) -> None:
        """Refresh the overlay's sprite (when the displayed Pokemon changes).
        Does NOT decide visibility — the overlay only appears on level-up events."""
        if self._overlay.visible:
            self._overlay.update_sprite(self._pokemon_sprite)

    def _query_max_request_id(self) -> int:
        try:
            import sqlite3
            from tokenmon.storage import DB_PATH
            with sqlite3.connect(DB_PATH, timeout=2.0) as conn:
                row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()
                return int(row[0] or 0)
        except Exception:
            log.exception("max request id query failed")
            return 0

    def _query_new_request_count(self, since: int) -> int:
        try:
            import sqlite3
            from tokenmon.storage import DB_PATH
            with sqlite3.connect(DB_PATH, timeout=2.0) as conn:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(MAX(id), ?) FROM requests WHERE id > ?",
                    (since, since),
                ).fetchone()
                return int(row[0] or 0), int(row[1] or since)
        except Exception:
            return 0, since

    def _maybe_roll_encounters(self) -> None:
        """For every new request that landed since the last poll, give
        encounter.maybe_spawn() a chance to spawn. The spawn logic itself
        guards on cooldown + pending status + 3% probability, so calling it
        once per new request honours the per-call probability semantics."""
        n_new, max_id = self._query_new_request_count(self._last_seen_request_id)
        if n_new <= 0:
            return
        self._last_seen_request_id = max_id
        try:
            from tokenmon import encounter
        except Exception:
            log.exception("encounter import failed")
            return
        for _ in range(n_new):
            try:
                spawned = encounter.maybe_spawn()
            except Exception:
                log.exception("maybe_spawn failed")
                spawned = None
            if spawned is not None:
                try:
                    rumps.notification(
                        title="Tokenmon",
                        subtitle="A wild Pokemon appeared!",
                        message="Click the menubar to investigate.",
                    )
                except Exception:
                    log.exception("encounter notification failed")
                break  # only one pending encounter at a time

    def _compute_current_level(self) -> int:
        try:
            active = box.get_active_pokemon()
            if active is not None:
                xp = query_xp_for_pokemon(active.id)
            else:
                xp = query_xp_for_date(date.today(), TZ)
        except Exception:
            return 1
        rate = pokemon.growth_rate_of(self._line_base_id)
        level, _, _ = pokemon.level_from_xp(xp, rate)
        return level

    def _check_level_up(self, now: float) -> None:
        """Detect a level increase since the last poll and fire visuals/notification."""
        new_level = self._compute_current_level()
        if new_level > self._last_known_level:
            self._last_known_level = new_level
            try:
                rumps.notification(
                    title="Tokenmon",
                    subtitle="Level up!",
                    message=f"{pokemon.name_of(self._pokemon_dex_id)} ist aufgestiegen!",
                )
            except Exception:
                log.exception("level-up notification failed")
            # Pop the overlay for the duration of the level-up banner. The
            # overlay hides itself again when the banner timer expires.
            # Suppress the level-up animation while an evolution animation is
            # already running (level-up and evolution often coincide).
            if self._show_overlay and not self._overlay.evolution_running:
                try:
                    self._overlay.update_sprite(self._pokemon_sprite)
                    self._overlay.show_level_up()
                except Exception:
                    log.exception("overlay level-up animation failed")
        elif new_level < self._last_known_level:
            # Defensive: data shrunk (manual DB edit?). Track silently.
            self._last_known_level = new_level

    def _statusbar_button(self):
        try:
            return self._nsapp.nsstatusitem.button()
        except (AttributeError, Exception):
            return None

    def _set_menubar_image(self, img) -> None:
        btn = self._statusbar_button()
        if btn is not None:
            btn.setImage_(img)

    def _update_tooltip(self) -> None:
        btn = self._statusbar_button()
        if btn is None:
            return
        try:
            active = box.get_active_pokemon()
            if active is not None:
                xp = query_xp_for_pokemon(active.id)
            else:
                xp = query_xp_for_date(date.today(), TZ)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(self._line_base_id)
        level, into, needed = pokemon.level_from_xp(xp, rate)
        name = pokemon.name_of(self._pokemon_dex_id)
        if level >= pokemon.MAX_LEVEL:
            tooltip = f"{name} — Lv MAX • {xp:,} XP"
        else:
            tooltip = f"{name} — Lv {level} • {into:,}/{needed:,} XP"
        btn.setToolTip_(tooltip)

    def _stop_animator(self, *, clear_image: bool = True) -> None:
        if self._animator is not None:
            self._animator.stop()
            self._animator = None
        if clear_image:
            self._set_menubar_image(None)

    def _start_animator(self) -> None:
        # Don't blank the button while we're swapping animators — the new
        # animator's first frame paints synchronously when its init runs, and
        # any None state in between would cause the status-bar button to
        # collapse to its empty width and shift the open popover.
        self._stop_animator(clear_image=False)
        if not self._show_pokemon or self._pokemon_sprite is None or not self._pokemon_sprite.exists():
            self._set_menubar_image(None)
            return
        try:
            anim = SpriteAnimator.alloc().initWithGifPath_setter_(
                str(self._pokemon_sprite), self._set_menubar_image
            )
            self._animator = anim
        except Exception:
            log.exception("failed to start sprite animator")
            self._animator = None

    def _sync_menubar_icon(self) -> None:
        """Reconcile the title + image with the current state."""
        if self._show_pokemon and self._pokemon_sprite is not None and self._pokemon_sprite.exists():
            self._start_animator()
        else:
            self._stop_animator()

    def _maybe_repick_for_new_day(self) -> None:
        today = date.today()
        if today != self._pokemon_picked_for:
            # Day rolled over — make sure today's daily-catch row exists, but
            # the menubar icon follows the user's ACTIVE Pokemon (which may
            # be a different species they pinned previously).
            try:
                box.ensure_today_pokemon()
                active = box.get_active_pokemon()
                self._line_base_id = (
                    active.species_dex_id if active is not None
                    else pokemon.pick_for_today(today)
                )
            except Exception:
                log.exception("day-rollover state refresh failed")
                self._line_base_id = pokemon.pick_for_today(today)
            self._pokemon_picked_for = today
            self._pokemon_dex_id = self._line_base_id
            self._pokemon_sprite = pokemon.ensure_sprite(self._pokemon_dex_id)
            self._last_known_level = self._compute_current_level()
            self._sync_menubar_icon()

    def _pokemon_menu_item(self) -> rumps.MenuItem:
        dex_id = self._pokemon_dex_id
        label = f"#{dex_id:03d}  {pokemon.name_of(dex_id)}"
        try:
            xp = query_xp_for_date(date.today(), TZ)
        except Exception:
            log.exception("failed to compute today's xp")
            xp = 0
        rate = pokemon.growth_rate_of(self._line_base_id)
        level, into, needed = pokemon.level_from_xp(xp, rate)
        item = rumps.MenuItem(label)
        view = _make_pokemon_view(self._pokemon_sprite, label, level, into, needed)
        item._menuitem.setView_(view)
        return item

    def _build_menu(self, totals: Totals, by_model: dict[str, Totals], proxy_up: bool) -> list:
        items: list = []
        items.append(self._pokemon_menu_item())
        items.append(rumps.MenuItem("📖 Tokendex öffnen", callback=self.open_tokendex))
        toggle = rumps.MenuItem("Pokemon im Menubar anzeigen", callback=self.toggle_menubar_pokemon)
        toggle.state = 1 if self._show_pokemon else 0
        items.append(toggle)
        overlay_toggle = rumps.MenuItem(
            "Pokemon als Desktop-Overlay anzeigen", callback=self.toggle_overlay
        )
        overlay_toggle.state = 1 if self._show_overlay else 0
        items.append(overlay_toggle)
        items.append(None)
        if not proxy_up:
            items.append(rumps.MenuItem("⚠️  Proxy offline — Calls werden NICHT getrackt!"))
            items.append(rumps.MenuItem("Proxy neustarten", callback=self.restart_proxy))
            items.append(None)
        # XP / "active" tokens count only the model's output: it's the only
        # token category comparable across providers (cached input doesn't
        # exist in OpenRouter, while it dominates Anthropic-via-Claude-Code).
        active = totals.output_tokens
        items.extend([
            rumps.MenuItem(f"Heute: {_fmt_tokens(active)} tokens (output)"),
            rumps.MenuItem(f"  Output:   {_fmt_tokens(totals.output_tokens)}"),
            rumps.MenuItem(f"  Input:    {_fmt_tokens(totals.input_tokens)}"),
            rumps.MenuItem(f"  Requests: {totals.request_count}"),
            None,
        ])
        total_cost = 0.0
        priced_tokens = 0
        all_tokens = 0
        if by_model:
            items.append(rumps.MenuItem("Pro Modell:"))
            for model, t in by_model.items():
                cost, has_price = cost_for(
                    model,
                    input_tokens=t.input_tokens,
                    output_tokens=t.output_tokens,
                    cache_read_tokens=t.cache_read_tokens,
                    cache_creation_tokens=t.cache_creation_tokens,
                )
                total_cost += cost
                model_tokens = (
                    t.input_tokens + t.output_tokens
                    + t.cache_read_tokens + t.cache_creation_tokens
                )
                all_tokens += model_tokens
                if has_price:
                    priced_tokens += model_tokens
                visible_tokens = t.output_tokens
                cost_str = _fmt_usd(cost) if has_price else "?"
                items.append(
                    rumps.MenuItem(
                        f"  {model}: {_fmt_tokens(visible_tokens)} out ({cost_str})"
                    )
                )
            items.append(None)
            cost_line = f"Geschätzte Kosten: {_fmt_usd(total_cost)}"
            if all_tokens > 0 and priced_tokens < all_tokens:
                coverage = priced_tokens / all_tokens
                cost_line += f"  ({coverage:.0%} Preisabdeckung)"
            items.append(rumps.MenuItem(cost_line))
            items.append(None)
        items.append(rumps.MenuItem("Aktualisieren", callback=self.refresh))
        items.append(rumps.MenuItem("Beenden", callback=rumps.quit_application))
        return items

    @rumps.timer(REFRESH_INTERVAL_SEC)
    def auto_refresh(self, _sender) -> None:
        self.refresh(None)

    @rumps.timer(HEALTH_INTERVAL_SEC)
    def health_check(self, _sender) -> None:
        up, _down = _proxy_health()
        if up != self._proxy_up:
            self._proxy_up = up
            self.refresh(None)

    @rumps.timer(ACTIVITY_POLL_INTERVAL_SEC)
    def activity_poll(self, _sender) -> None:
        now = time.monotonic()
        prev_level = self._last_known_level
        self._check_level_up(now)
        self._maybe_roll_encounters()
        if self._last_known_level != prev_level:
            # Refresh now so the menubar title picks up the new level immediately
            # (otherwise we'd wait up to 30s for the next refresh).
            self.refresh(None)

    def restart_proxy(self, _sender) -> None:
        ok, msg = _restart_proxies_via_launchctl()
        rumps.notification(
            title="Tokenmon",
            subtitle="Proxy-Restart" if ok else "Proxy-Restart fehlgeschlagen",
            message=msg,
        )

    def toggle_menubar_pokemon(self, _sender) -> None:
        self._show_pokemon = not self._show_pokemon
        config.set_("show_pokemon_in_menubar", self._show_pokemon)
        self._sync_menubar_icon()
        self.refresh(None)

    def toggle_overlay(self, _sender) -> None:
        self._show_overlay = not self._show_overlay
        config.set_("show_overlay", self._show_overlay)
        if not self._show_overlay and self._overlay.visible:
            self._overlay.hide()
        self.refresh(None)

    def open_tokendex(self, _sender) -> None:
        try:
            tokendex.show()
        except Exception:
            log.exception("failed to open tokendex")

    def refresh(self, _sender) -> None:
        self._maybe_repick_for_new_day()
        self._refresh_pokemon_state()
        try:
            totals = query_today(TZ)
            by_model = query_today_by_model(TZ)
        except Exception:
            log.exception("failed to query usage")
            self.title = f"{EGG} ?"
            return
        active = totals.output_tokens
        # When the sprite is showing, the status item shows just the sprite (no
        # text — that's surfaced via the tooltip and the popover). When the
        # sprite is off, we fall back to the egg emoji + total tokens (or
        # ⚠️ + tokens when the proxy is offline).
        sprite_active = self._show_pokemon and self._animator is not None
        if sprite_active and self._proxy_up:
            self.title = ""
        else:
            icon = EGG if self._proxy_up else EGG_DOWN
            self.title = f"{icon} {_fmt_tokens(active)}"
        self._update_tooltip()
        # Idempotently kill any rumps-installed menu — popover handles clicks.
        try:
            if self._nsapp is not None:
                self._nsapp.nsstatusitem.setMenu_(None)
        except Exception:
            pass


def main() -> None:
    init_db()
    items_remote.prefetch_in_background(list(items.ITEMS.values()))
    TokenmonApp().run()


if __name__ == "__main__":
    main()

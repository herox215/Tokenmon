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

import rumps
from AppKit import (
    NSColor,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSTextField,
    NSView,
)
from Foundation import NSMakeRect

from tokenmon import config, pokemon, tokendex
from tokenmon.menubar_sprite import SpriteAnimator
from tokenmon.overlay import PokemonOverlay
from tokenmon.pricing import cost_for
from tokenmon.proxy import HOST, PORT
from tokenmon.storage import (
    Totals,
    init_db,
    query_pokemon_xp,
    query_today,
    query_today_by_model,
)

REFRESH_INTERVAL_SEC = 30
HEALTH_INTERVAL_SEC = 10
ACTIVITY_POLL_INTERVAL_SEC = 5
LEVEL_UP_TITLE_DISPLAY_SEC = 5.0
TZ = "Europe/Berlin"
EGG = "🥚"
EGG_DOWN = "⚠️"

log = logging.getLogger("tokenmon.menubar")


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
        self._line_base_id: int = pokemon.pick_for_today()
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
        self._level_up_title_until: float = 0.0
        self._level_up_title: str | None = None
        self._sync_menubar_icon()
        self.menu = self._build_menu(Totals(), {}, proxy_up=True)
        self.refresh(None)

    def _refresh_pokemon_state(self) -> None:
        """Recompute current evolution stage based on line XP and reload sprite
        if the displayed Pokemon has changed. When the change is an evolution
        within the same line, fire the evolution animation."""
        try:
            xp = query_pokemon_xp(self._line_base_id, TZ)
        except Exception:
            log.exception("failed to query line xp")
            xp = 0
        new_id = pokemon.current_stage_of(self._line_base_id, xp)
        if new_id == self._pokemon_dex_id:
            return
        old_id = self._pokemon_dex_id
        self._pokemon_dex_id = new_id
        self._pokemon_sprite = pokemon.ensure_sprite(new_id)
        self._sync_menubar_icon()
        self._sync_overlay()

        chain = pokemon.evolution_chain(self._line_base_id)
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

    def _compute_current_level(self) -> int:
        try:
            xp = query_pokemon_xp(self._line_base_id, TZ)
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
            # Title flash on the menubar (only meaningful when sprite mode is OFF
            # since otherwise the title is just "Lv N").
            self._level_up_title = "🌟 Level up!"
            self._level_up_title_until = now + LEVEL_UP_TITLE_DISPLAY_SEC
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
            xp = query_pokemon_xp(self._line_base_id, TZ)
        except Exception:
            xp = 0
        rate = pokemon.growth_rate_of(self._line_base_id)
        level, into, needed = pokemon.level_from_xp(xp, rate)
        name = pokemon.name_of(self._pokemon_dex_id)
        if level >= pokemon.MAX_LEVEL:
            tooltip = f"{name} — Lv MAX • {xp:,} XP gesamt"
        else:
            tooltip = f"{name} — Lv {level} • {into:,}/{needed:,} XP"
        btn.setToolTip_(tooltip)

    def _stop_animator(self) -> None:
        if self._animator is not None:
            self._animator.stop()
            self._animator = None
        self._set_menubar_image(None)

    def _start_animator(self) -> None:
        self._stop_animator()
        if not self._show_pokemon or self._pokemon_sprite is None or not self._pokemon_sprite.exists():
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
            self._line_base_id = pokemon.pick_for_today(today)
            self._pokemon_picked_for = today
            # Compute the correct stage for the new line up-front, otherwise
            # _refresh_pokemon_state would treat catching-up to existing XP as a
            # fresh evolution and fire the animation falsely.
            try:
                xp = query_pokemon_xp(self._line_base_id, TZ)
            except Exception:
                xp = 0
            self._pokemon_dex_id = pokemon.current_stage_of(self._line_base_id, xp)
            self._pokemon_sprite = pokemon.ensure_sprite(self._pokemon_dex_id)
            self._last_known_level = self._compute_current_level()
            self._sync_menubar_icon()

    def _pokemon_menu_item(self) -> rumps.MenuItem:
        dex_id = self._pokemon_dex_id
        label = f"#{dex_id:03d}  {pokemon.name_of(dex_id)}"
        try:
            xp = query_pokemon_xp(self._line_base_id, TZ)
        except Exception:
            log.exception("failed to compute line xp")
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
        active = totals.input_tokens + totals.output_tokens
        items.extend([
            rumps.MenuItem(f"Heute: {_fmt_tokens(active)} tokens"),
            rumps.MenuItem(f"  Input:    {_fmt_tokens(totals.input_tokens)}"),
            rumps.MenuItem(f"  Output:   {_fmt_tokens(totals.output_tokens)}"),
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
                visible_tokens = t.input_tokens + t.output_tokens
                cost_str = _fmt_usd(cost) if has_price else "?"
                items.append(
                    rumps.MenuItem(
                        f"  {model}: {_fmt_tokens(visible_tokens)} ({cost_str})"
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
        active = totals.input_tokens + totals.output_tokens
        # Level-up title flash takes priority for a few seconds after a level up.
        now = time.monotonic()
        if self._level_up_title is not None and now < self._level_up_title_until:
            self.title = self._level_up_title
            self._update_tooltip()
            self.menu.clear()
            for item in self._build_menu(totals, by_model, proxy_up=self._proxy_up):
                if item is None:
                    self.menu.add(rumps.separator)
                else:
                    self.menu.add(item)
            return
        if self._level_up_title is not None and now >= self._level_up_title_until:
            self._level_up_title = None
        # When the sprite is showing, the status item shows just the sprite (no
        # level text — that's surfaced via the tooltip and the dropdown). When
        # the sprite is off, we fall back to the egg emoji + total tokens (or
        # ⚠️ + tokens when the proxy is offline).
        sprite_active = self._show_pokemon and self._animator is not None
        if sprite_active and self._proxy_up:
            self.title = ""
        else:
            icon = EGG if self._proxy_up else EGG_DOWN
            self.title = f"{icon} {_fmt_tokens(active)}"
        self._update_tooltip()
        self.menu.clear()
        for item in self._build_menu(totals, by_model, proxy_up=self._proxy_up):
            if item is None:
                self.menu.add(rumps.separator)
            else:
                self.menu.add(item)


def main() -> None:
    init_db()
    TokenmonApp().run()


if __name__ == "__main__":
    main()

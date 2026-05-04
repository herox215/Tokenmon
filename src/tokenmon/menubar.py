"""macOS menubar app — shows today's total tokens with a 🥚 icon.

Click opens a dropdown with per-model breakdown and estimated USD cost.
Refreshes every 30 seconds from SQLite. Pings the proxy /healthz every 10s
and shows a warning state if the proxy is down.
"""

from __future__ import annotations

import logging
import subprocess
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

from tokenmon import pokemon, tokendex
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
HEALTH_URL = f"http://{HOST}:{PORT}/healthz"
PROXY_LAUNCHD_LABEL = "com.tokenmon.proxy"
TZ = "Europe/Berlin"
EGG = "🥚"
EGG_DOWN = "⚠️"

log = logging.getLogger("tokenmon.menubar")


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


def _proxy_healthy(timeout: float = 1.0) -> bool:
    try:
        with urlopen(HEALTH_URL, timeout=timeout) as r:
            return r.status == 200
    except (URLError, TimeoutError, OSError):
        return False


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


def _restart_proxy_via_launchctl() -> tuple[bool, str]:
    """Returns (ok, message)."""
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{__import__('os').getuid()}/{PROXY_LAUNCHD_LABEL}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return True, "Proxy neugestartet"
        return False, result.stderr.strip() or f"exit {result.returncode}"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)


class TokenmonApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(name="Tokenmon", title=f"{EGG} 0", quit_button=None)
        self._proxy_up = True
        self._pokemon_dex_id: int = pokemon.pick_for_today()
        self._pokemon_picked_for: date = date.today()
        self._pokemon_sprite: Path | None = pokemon.ensure_sprite(self._pokemon_dex_id)
        self.menu = self._build_menu(Totals(), {}, proxy_up=True)
        self.refresh(None)

    def _maybe_repick_for_new_day(self) -> None:
        today = date.today()
        if today != self._pokemon_picked_for:
            self._pokemon_dex_id = pokemon.pick_for_today(today)
            self._pokemon_picked_for = today
            self._pokemon_sprite = pokemon.ensure_sprite(self._pokemon_dex_id)

    def _pokemon_menu_item(self) -> rumps.MenuItem:
        dex_id = self._pokemon_dex_id
        label = f"#{dex_id:03d}  {pokemon.name_of(dex_id)}"
        try:
            xp = query_pokemon_xp(dex_id, TZ)
        except Exception:
            log.exception("failed to compute pokemon xp")
            xp = 0
        rate = pokemon.growth_rate_of(dex_id)
        level, into, needed = pokemon.level_from_xp(xp, rate)
        item = rumps.MenuItem(label)
        view = _make_pokemon_view(self._pokemon_sprite, label, level, into, needed)
        item._menuitem.setView_(view)
        return item

    def _build_menu(self, totals: Totals, by_model: dict[str, Totals], proxy_up: bool) -> list:
        items: list = []
        items.append(self._pokemon_menu_item())
        items.append(rumps.MenuItem("📖 Tokendex öffnen", callback=self.open_tokendex))
        items.append(rumps.MenuItem("🎲 Pokemon neu würfeln (debug)", callback=self.reroll_pokemon))
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
        if by_model:
            items.append(rumps.MenuItem("Pro Modell:"))
            for model, t in by_model.items():
                cost = cost_for(
                    model,
                    input_tokens=t.input_tokens,
                    output_tokens=t.output_tokens,
                    cache_read_tokens=t.cache_read_tokens,
                    cache_creation_tokens=t.cache_creation_tokens,
                )
                total_cost += cost
                items.append(
                    rumps.MenuItem(
                        f"  {model}: {_fmt_tokens(t.input_tokens + t.output_tokens)} ({_fmt_usd(cost)})"
                    )
                )
            items.append(None)
            items.append(rumps.MenuItem(f"Geschätzte Kosten: {_fmt_usd(total_cost)}"))
            items.append(None)
        items.append(rumps.MenuItem("Aktualisieren", callback=self.refresh))
        items.append(rumps.MenuItem("Beenden", callback=rumps.quit_application))
        return items

    @rumps.timer(REFRESH_INTERVAL_SEC)
    def auto_refresh(self, _sender) -> None:
        self.refresh(None)

    @rumps.timer(HEALTH_INTERVAL_SEC)
    def health_check(self, _sender) -> None:
        up = _proxy_healthy()
        if up != self._proxy_up:
            self._proxy_up = up
            self.refresh(None)

    def restart_proxy(self, _sender) -> None:
        ok, msg = _restart_proxy_via_launchctl()
        rumps.notification(
            title="Tokenmon",
            subtitle="Proxy-Restart" if ok else "Proxy-Restart fehlgeschlagen",
            message=msg,
        )

    def reroll_pokemon(self, _sender) -> None:
        self._pokemon_dex_id = pokemon.pick_random()
        self._pokemon_sprite = pokemon.ensure_sprite(self._pokemon_dex_id)
        self.refresh(None)

    def open_tokendex(self, _sender) -> None:
        try:
            tokendex.show()
        except Exception:
            log.exception("failed to open tokendex")

    def refresh(self, _sender) -> None:
        self._maybe_repick_for_new_day()
        try:
            totals = query_today(TZ)
            by_model = query_today_by_model(TZ)
        except Exception:
            log.exception("failed to query usage")
            self.title = f"{EGG} ?"
            return
        icon = EGG if self._proxy_up else EGG_DOWN
        active = totals.input_tokens + totals.output_tokens
        self.title = f"{icon} {_fmt_tokens(active)}"
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

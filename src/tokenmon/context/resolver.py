"""Picks the right context provider for the active app.

The resolver caches the most recent successful snapshot for half a
second — opening the chat twice in quick succession (which the user
does often) shouldn't re-scrape the active window each time."""
from __future__ import annotations

import logging
import sys
import time
from typing import Iterable

from .providers.base import ContextProvider
from .snapshot import ContextSnapshot

log = logging.getLogger("tokenmon.context.resolver")


class ContextResolver:
    def __init__(
        self,
        providers: Iterable[ContextProvider],
        fallback: ContextProvider | None = None,
        *,
        cache_ttl_s: float = 0.5,
    ) -> None:
        self._providers = list(providers)
        self._fallback = fallback
        self._cache_ttl = float(cache_ttl_s)
        self._cache_key: tuple[str, int] | None = None
        self._cache_at: float = 0.0
        self._cache_value: ContextSnapshot | None = None

    def resolve(self, app_id: str, pid: int) -> ContextSnapshot | None:
        if not app_id:
            return None
        key = (app_id, int(pid))
        now = time.monotonic()
        if self._cache_key == key and (now - self._cache_at) < self._cache_ttl:
            return self._cache_value
        snap = self._resolve_uncached(app_id, int(pid))
        self._cache_key = key
        self._cache_at = now
        self._cache_value = snap
        return snap

    def invalidate(self) -> None:
        self._cache_key = None
        self._cache_value = None

    def _resolve_uncached(self, app_id: str, pid: int) -> ContextSnapshot | None:
        for p in self._providers:
            try:
                if not p.supports(app_id):
                    continue
                snap = p.snapshot(app_id, pid)
                if snap is not None:
                    return snap
            except Exception:
                log.exception("provider %s raised", getattr(p, "name", p))
        if self._fallback is not None:
            try:
                return self._fallback.snapshot(app_id, pid)
            except Exception:
                log.exception("fallback provider raised")
        return None


def build_default_resolver() -> ContextResolver:
    """Default resolver wired up for the current platform. Currently
    Screen Recording + Vision OCR is the only path — universal across
    apps for the cost of one system permission. App-specific providers
    (AppleScript, kitty remote control, …) live in the codebase but
    aren't wired in by default; the OCR provider covers all of them
    with simpler UX. Linux later will branch on ``sys.platform`` and
    import its own xdg-portal-based provider."""
    if sys.platform == "darwin":
        from .providers.macos_screenshot import ScreenshotOCRProvider

        return ContextResolver(
            providers=[ScreenshotOCRProvider()],
            fallback=None,
        )
    return ContextResolver(providers=[], fallback=None)

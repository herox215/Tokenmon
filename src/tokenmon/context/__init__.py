"""Cross-platform window-context capture for the companion chat.

The companion chat can receive a snapshot of whatever the user was
looking at when they opened it (current Safari tab, terminal scrollback,
…). Providers are platform-specific; the snapshot dataclass and the
resolver that picks the right provider are not.

Usage:

    from tokenmon.context import build_default_resolver
    resolver = build_default_resolver()
    snap = resolver.resolve(bundle_id, pid)
    if snap is not None:
        prompt_block = snap.for_prompt()
"""
from .snapshot import ContextSnapshot
from .resolver import ContextResolver, build_default_resolver

__all__ = ["ContextSnapshot", "ContextResolver", "build_default_resolver"]

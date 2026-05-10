"""Terminal-emulator-agnostic context provider for macOS.

Different terminals expose their scrollback in completely different
ways (or not at all). We dispatch by bundle id to a per-terminal
strategy, then fall through to a *universal* strategy that pulls the
working directory out of the process tree — that one works for any
terminal, including Alacritty/Ghostty which have no IPC at all.

Layered approach:
    1. Per-terminal strategy   — best fidelity (full scrollback)
    2. Process-tree cwd        — universal, always at least the cwd
    3. (future) screenshot OCR — universal sichtbarer-Inhalt-Fallback
"""
from __future__ import annotations

import logging
import os
import re
from typing import Protocol

from ..snapshot import ContextSnapshot
from .base import run_subprocess

log = logging.getLogger("tokenmon.context.providers.macos_terminal")


_BUNDLE_TO_NAME = {
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm2",
    "net.kovidgoyal.kitty": "Kitty",
    "com.github.wez.wezterm": "WezTerm",
    "org.alacritty": "Alacritty",
    "io.alacritty": "Alacritty",
    "com.mitchellh.ghostty": "Ghostty",
}


class _Strategy(Protocol):
    name: str

    def matches(self, app_id: str) -> bool: ...

    def scrape(self, pid: int) -> tuple[str | None, str | None]:
        """Return ``(scrollback_text, cwd_hint)``. Either may be None."""
        ...


class _AppleTerminalStrategy:
    name = "appscript:Terminal"
    _SCRIPT = (
        'tell application "Terminal" to '
        "return (contents of selected tab of front window) as text"
    )

    def matches(self, app_id: str) -> bool:
        return app_id == "com.apple.Terminal"

    def scrape(self, pid: int) -> tuple[str | None, str | None]:
        result = run_subprocess(["osascript", "-e", self._SCRIPT], timeout=1.5)
        if result is None or result[0] != 0:
            return None, None
        return (result[1].rstrip("\n") or None), None


class _ITerm2Strategy:
    name = "appscript:iTerm2"
    _SCRIPT = (
        'tell application "iTerm" to tell current session of current window '
        "to return contents"
    )

    def matches(self, app_id: str) -> bool:
        return app_id == "com.googlecode.iterm2"

    def scrape(self, pid: int) -> tuple[str | None, str | None]:
        result = run_subprocess(["osascript", "-e", self._SCRIPT], timeout=1.5)
        if result is None or result[0] != 0:
            return None, None
        return (result[1].rstrip("\n") or None), None


class _KittyStrategy:
    """Kitty has no AppleScript bridge but ships a remote-control
    protocol over a Unix socket. Requires the user to set:

        # in ~/.config/kitty/kitty.conf
        allow_remote_control yes
        listen_on unix:/tmp/kitty-${USER}

    Without that, ``kitty @ get-text`` exits non-zero and we fall
    through to the cwd-only strategy.
    """

    name = "kitty-remote"

    # Try a few socket conventions in order. The unix:@kitty form is
    # an abstract socket on Linux; on macOS only filesystem sockets
    # exist, but kitty also exposes a system-default discovery via
    # KITTY_LISTEN_ON if the launching shell exports it.
    _SOCKET_CANDIDATES = [
        # Honour the env var when set (kitty exports this in shells it
        # spawns when listen_on is configured).
        os.environ.get("KITTY_LISTEN_ON"),
        f"unix:/tmp/kitty-{os.environ.get('USER', '')}",
        # No --to flag — kitty's default lookup.
        None,
    ]

    def matches(self, app_id: str) -> bool:
        return app_id == "net.kovidgoyal.kitty"

    def scrape(self, pid: int) -> tuple[str | None, str | None]:
        for socket in self._SOCKET_CANDIDATES:
            args = ["kitty", "@"]
            if socket:
                args += ["--to", socket]
            args += ["get-text", "--extent", "all"]
            result = run_subprocess(args, timeout=1.5)
            if result is None:
                continue
            rc, stdout, stderr = result
            if rc == 0 and stdout:
                return stdout.rstrip("\n"), None
            log.debug("kitty @ via %r failed rc=%s: %s", socket, rc, stderr.strip())
        return None, None


class _WezTermStrategy:
    name = "wezterm-cli"

    def matches(self, app_id: str) -> bool:
        return app_id == "com.github.wez.wezterm"

    def scrape(self, pid: int) -> tuple[str | None, str | None]:
        result = run_subprocess(
            ["wezterm", "cli", "get-text"], timeout=1.5,
        )
        if result is None or result[0] != 0:
            return None, None
        return (result[1] or None), None


# --- Universal cwd fallback -------------------------------------------------


_PROMPT_CWD_RE = re.compile(r":([~/][^\s%$#]*)\s*[%$#]\s*$")


def _guess_cwd_from_prompt(text: str) -> str | None:
    """Last-ditch parse of a ``user@host:/path %`` line in scraped
    scrollback. Heuristic — only used when the more reliable
    process-tree lookup fails."""
    for line in reversed(text.splitlines()[-12:]):
        m = _PROMPT_CWD_RE.search(line)
        if m:
            return m.group(1)
    return None


def _children(pid: int, *, timeout: float = 0.4) -> list[int]:
    result = run_subprocess(["pgrep", "-P", str(pid)], timeout=timeout)
    if result is None or result[0] not in (0, 1):
        return []
    return [int(p) for p in result[1].split() if p.strip().isdigit()]


def cwd_from_process_tree(terminal_pid: int) -> str | None:
    """Walk one or two levels of children of the terminal process and
    read the working directory of the leaf via ``lsof``.

    Handles tmux/screen by descending one extra level when a child
    itself has children. The youngest grandchild (highest PID) is
    typically the active shell — a stable heuristic in practice.

    Cross-platform note: ``pgrep`` and ``lsof`` are equally available
    on Linux, so this exact function moves to a Linux provider
    unchanged."""
    try:
        children = _children(terminal_pid)
        if not children:
            return None
        leaves: list[int] = []
        for c in children:
            grand = _children(c, timeout=0.2)
            leaves.extend(grand or [c])
        target = max(leaves)
        result = run_subprocess(
            ["lsof", "-a", "-p", str(target), "-d", "cwd", "-Fn"],
            timeout=0.6,
        )
        if result is None or result[0] != 0:
            return None
        for line in result[1].splitlines():
            if line.startswith("n"):
                return line[1:]
    except Exception:
        log.exception("cwd_from_process_tree failed")
    return None


# --- Aggregating provider ---------------------------------------------------


class TerminalProvider:
    """One provider for *all* terminals. Picks the right scrollback
    strategy by bundle id, then unconditionally adds the universal cwd
    fallback so that even Alacritty/Ghostty give the chat at least the
    working directory."""

    name = "macos_terminal"

    def __init__(self) -> None:
        self._strategies: list[_Strategy] = [
            _AppleTerminalStrategy(),
            _ITerm2Strategy(),
            _KittyStrategy(),
            _WezTermStrategy(),
        ]

    def supports(self, app_id: str) -> bool:
        return app_id in _BUNDLE_TO_NAME

    def snapshot(self, app_id: str, pid: int) -> ContextSnapshot | None:
        text: str | None = None
        cwd: str | None = None
        source = "terminal:cwd-only"

        for strat in self._strategies:
            if strat.matches(app_id):
                text, cwd_hint = strat.scrape(pid)
                if cwd_hint:
                    cwd = cwd_hint
                if text:
                    source = strat.name
                break

        if cwd is None:
            cwd = cwd_from_process_tree(pid)
        if cwd is None and text:
            cwd = _guess_cwd_from_prompt(text)

        if text is None and cwd is None:
            return None

        return ContextSnapshot(
            app_name=_BUNDLE_TO_NAME.get(app_id, "Terminal"),
            app_id=app_id,
            kind="terminal",
            text=text,
            cwd=cwd,
            source=source,
        )

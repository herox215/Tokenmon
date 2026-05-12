"""Background terminal session — singleton owned by the menubar process.

The session wraps the user's shell in a persistent ``tmux`` session, so
the actual terminal state (running processes, cwd, scrollback) survives
Tokenmon restarts. Only one local PTY client lives at a time. Closing
the chat panel hides the UI but keeps the PTY alive; quitting the
menubar app reaps the PTY via the ``atexit`` hook installed in
``tokenmon.menubar._main`` — the tmux server itself outlives all of it.

Public surface:

    from tokenmon import claude_session
    session = claude_session.get_session()   # spawn-or-reuse
    claude_session.shutdown()                # at app quit
"""
from __future__ import annotations

import logging
import threading

from .session import ClaudeSession, SessionUnavailable

log = logging.getLogger("tokenmon.claude_session")

__all__ = ["ClaudeSession", "SessionUnavailable", "get_session", "shutdown"]

_session: ClaudeSession | None = None
_lock = threading.Lock()


def get_session() -> ClaudeSession:
    """Return the live session, lazily spawning it on first call.

    First start always lands in ``~`` — a plain terminal environment,
    no peeking at the user's frontmost window or process tree. With
    tmux on PATH subsequent restarts reattach to the same named
    session so scrollback / running processes / cwd persist regardless
    of where the user happened to be when they first opened the chat.

    If the previous local PTY died (e.g. the user typed ``exit`` inside
    the shell) we transparently spawn a fresh one on the next call —
    the chat panel should always reattach to a working terminal, not a
    corpse.

    Raises ``SessionUnavailable`` if no shell can be spawned at all
    (highly unusual — see ``_resolve_shell``).
    """
    global _session
    with _lock:
        if _session is None or not _session.is_alive():
            log.info("spawning companion terminal (cwd=~)")
            _session = ClaudeSession()
            _session.start()
        return _session


def shutdown() -> None:
    """Tear down the local PTY if any. Safe to call multiple times.

    Called from ``atexit`` so a normal menubar quit doesn't leave a
    detached tmux client zombie. The tmux *server* and the named session
    keep running in the background — that's the whole point of using
    tmux as the persistence layer.
    """
    global _session
    with _lock:
        sess = _session
        _session = None
    if sess is None:
        return
    try:
        sess.close()
    except Exception:
        log.exception("claude_session.shutdown() failed")

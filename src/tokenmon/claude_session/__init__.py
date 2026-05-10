"""Background `claude` interactive session — singleton owned by the menubar
process.

Public surface:

    from tokenmon import claude_session
    session = claude_session.get_session()   # spawn-or-reuse
    claude_session.shutdown()                # at app quit

Only one session lives at a time. Closing the chat panel hides the UI but
keeps the PTY alive; quitting the menubar app reaps it via the ``atexit``
hook installed in ``tokenmon.menubar._main``.
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

    If the previous session's ``claude`` process has exited (e.g. the user
    typed ``/quit`` inside the terminal) we transparently spawn a fresh one
    on the next call — the chat panel should always reattach to a working
    session, not a corpse. We also re-read the
    ``companion_skip_permissions`` config flag at spawn time so toggling
    it from the menu takes effect on the next ``/quit`` + reopen.

    Raises ``SessionUnavailable`` if ``claude`` cannot be spawned (not on
    PATH, missing dependencies, etc.). Callers should surface the error
    text rather than retry in a loop.
    """
    global _session
    with _lock:
        if _session is None or not _session.is_alive():
            # Imported lazily so unit tests that monkeypatch shutil.which
            # don't have to also stub out the config layer.
            from tokenmon import config as _config
            from .cwd_resolver import resolve as resolve_cwd

            skip = bool(_config.get("companion_skip_permissions"))
            cwd, cwd_source = resolve_cwd()
            log.info("spawning claude in %s (%s)", cwd, cwd_source)
            _session = ClaudeSession(
                skip_permissions=skip,
                cwd=cwd,
                cwd_source=cwd_source,
            )
            _session.start()
        return _session


def shutdown() -> None:
    """Tear down the session if any. Safe to call multiple times.

    Called from ``atexit`` so a normal menubar quit doesn't leave a
    detached ``claude`` zombie. We don't wait long: SIGTERM, then SIGKILL
    after a short grace period inside ``ClaudeSession.close``.
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

"""PTY-backed wrapper around a persistent shell terminal.

We spawn the user's shell inside a named ``tmux`` session via a real
pseudo-terminal (``ptyprocess``) so it behaves exactly like a regular
terminal. The tmux wrapper is what makes the terminal survive Tokenmon
restarts: ``tmux new-session -A -s <name>`` attaches to the named
session if it already exists (kept alive by the tmux server, which is
independent of our process) and creates it otherwise. Quitting the
menubar app reaps only the local PTY client; the tmux server and its
shell history live on for the next launch.

When ``tmux`` isn't on PATH we fall back to running ``$SHELL`` directly
— same UX in the chat panel, but no cross-restart persistence.

Reading bytes off the master fd happens on a daemon thread; the
WKWebView host registers a listener and forwards every chunk to xterm.js.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

import ptyprocess

log = logging.getLogger("tokenmon.claude_session.session")

# Default terminal dimensions before xterm.js's FitAddon reports the real
# size on the first frame. We pick something larger than the panel so the
# very first paint isn't visibly truncated; the FitAddon will replace it
# within ~1 frame.
DEFAULT_ROWS = 30
DEFAULT_COLS = 100

# Read budget per loop tick. Bigger reads improve throughput on bursty
# output (claude streams responses in tight chunks) without inflating
# tail-latency on quiet input.
READ_CHUNK = 4096

# How long to wait for the PTY child to exit on its own after SIGTERM
# before escalating to SIGKILL. A tmux client / login shell unwinds
# quickly; 2 seconds is comfortable.
TERMINATE_GRACE_S = 2.0

# Name of the persistent tmux session backing the companion chat. We use
# a fixed name so every restart attaches to the same scrollback / cwd /
# running processes via ``tmux new-session -A``.
TMUX_SESSION_NAME = "tokenmon-companion"


class SessionUnavailable(RuntimeError):
    """Raised when neither a tmux-wrapped shell nor a plain shell can be
    spawned. We use this rather than ``FileNotFoundError`` so callers
    can format a user-facing message instead of leaking OS-level paths."""


def _resolve_shell() -> str:
    """Pick the user's interactive shell. ``$SHELL`` wins; otherwise
    fall back to ``/bin/zsh`` (macOS default since Catalina) then
    ``/bin/bash``."""
    candidate = os.environ.get("SHELL") or ""
    if candidate and shutil.which(candidate):
        return candidate
    for fallback in ("/bin/zsh", "/bin/bash", "/bin/sh"):
        if shutil.which(fallback):
            return fallback
    raise SessionUnavailable("no usable shell on PATH")


def _default_shell_command() -> list[str]:
    """Build the default argv for the persistent companion terminal.

    Preference order:

    1. ``tmux new-session -A -s <name> <shell> -l`` — ``-A`` attaches
       to the named session if it already exists, so Tokenmon restarts
       reuse the same scrollback / running processes / cwd. We pass
       the login shell explicitly rather than relying on tmux's
       ``default-command`` so the user's ``.zprofile`` always runs.
    2. ``screen -DR <name>`` — same persistence idea via GNU screen
       when tmux isn't on PATH. ``-DR`` detaches any other client and
       reattaches, creating the session if needed.
    3. Bare login shell. No restart persistence, but the chat panel
       still works.
    """
    shell = _resolve_shell()
    tmux = shutil.which("tmux")
    if tmux is not None:
        return [tmux, "new-session", "-A", "-s", TMUX_SESSION_NAME, shell, "-l"]
    screen = shutil.which("screen")
    if screen is not None:
        return [screen, "-DR", TMUX_SESSION_NAME]
    return [shell, "-l"]


Listener = Callable[[bytes], None]


class ClaudeSession:
    """One long-lived PTY-backed terminal session.

    By default this wraps the user's shell in a persistent tmux session
    (see ``TMUX_SESSION_NAME``), so closing the chat panel / restarting
    Tokenmon both reattach to the same scrollback. Tests can inject a
    custom ``command`` (typically ``/bin/cat``) to exercise the PTY
    plumbing without depending on tmux.

    Thread-safety: ``write`` / ``resize`` / ``close`` are safe to call
    from any thread. Listeners are invoked on the reader thread — they
    must marshal back to the AppKit main thread themselves (see
    ``terminal_view.TerminalWebView``).
    """

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        cwd: Path | None = None,
        cwd_source: str = "default",
        env: dict[str, str] | None = None,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
    ) -> None:
        # Default: persistent tmux-wrapped shell. The ``-A`` flag turns
        # ``new-session`` into attach-or-create so we hit the same
        # session on every restart, preserving the tab's running
        # processes, cwd and scrollback. Falls back to ``$SHELL``
        # directly when tmux is missing — same UX, no persistence.
        if command is None:
            command = _default_shell_command()
        self._command = list(command)
        # Both ``cwd`` and ``cwd_source`` are exposed publicly via
        # @property — the chat panel renders the source string in its
        # debug label so users can see which resolver stage picked
        # this directory.
        self._cwd = cwd or Path.home()
        self._cwd_source = cwd_source
        # Full UTF-8 + a recognisable TERM so claude renders colours and
        # the fancy box-drawing characters its TUI relies on. We also
        # tell node not to fall back to ASCII when stdout isn't a TTY
        # (it always will be here, but defence against future changes).
        base_env = dict(os.environ)
        base_env.setdefault("TERM", "xterm-256color")
        base_env.setdefault("LANG", "en_US.UTF-8")
        base_env.setdefault("LC_ALL", "en_US.UTF-8")
        base_env.setdefault("COLORTERM", "truecolor")
        if env:
            base_env.update(env)
        self._env = base_env
        self._dimensions = (int(rows), int(cols))

        self._proc: ptyprocess.PtyProcess | None = None
        self._reader: threading.Thread | None = None
        self._listeners: list[Listener] = []
        self._listeners_lock = threading.RLock()
        self._closed = False

    # --- public state --------------------------------------------------

    @property
    def cwd(self) -> Path:
        """Working directory the ``claude`` child was spawned in."""
        return self._cwd

    @property
    def cwd_source(self) -> str:
        """Short human-readable string describing how ``cwd`` was chosen.

        Set by ``get_session`` from ``cwd_resolver.resolve()``; rendered
        in the chat panel's debug label so users can verify the
        right directory was picked.
        """
        return self._cwd_source

    # --- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Spawn the child process and the reader thread. Idempotent."""
        if self._proc is not None:
            return
        try:
            self._proc = ptyprocess.PtyProcess.spawn(
                self._command,
                cwd=str(self._cwd),
                env=self._env,
                dimensions=self._dimensions,
            )
        except FileNotFoundError as exc:
            # The PATH lookup happened in __init__, but a race (claude
            # uninstalled between init and start) is possible. Map it to
            # the same clean exception type the chat panel knows how to
            # display.
            raise SessionUnavailable(str(exc)) from exc
        log.info(
            "spawned %s pid=%s cwd=%s",
            self._command[0], self._proc.pid, self._cwd,
        )
        self._reader = threading.Thread(
            target=self._read_loop,
            name="tokenmon.claude_session.reader",
            daemon=True,
        )
        self._reader.start()

    def is_alive(self) -> bool:
        if self._proc is None or self._closed:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    def close(self, *, grace: float = TERMINATE_GRACE_S) -> None:
        """Terminate the process group. Idempotent."""
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        # Kill the whole process group: ``claude`` shells out to sub-agent
        # tools and we don't want orphans. ptyprocess already calls
        # ``setsid()`` in the child so pid == pgid.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception:
            log.exception("SIGTERM to claude pgid=%s failed", proc.pid)

        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                if not proc.isalive():
                    break
            except Exception:
                break
            time.sleep(0.05)
        else:
            # Still kicking — escalate.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                log.exception("SIGKILL to claude pgid=%s failed", proc.pid)

        # Reader thread is daemon and will exit on the next read failure;
        # don't join it (we may be inside an atexit hook with a short
        # budget).

    # --- io ------------------------------------------------------------

    def write(self, data: bytes) -> None:
        """Forward a chunk of user input to the PTY's stdin.

        No-op if the session has been closed or hasn't started yet —
        keystrokes mid-shutdown shouldn't crash the UI.
        """
        if self._proc is None or self._closed:
            return
        try:
            self._proc.write(data)
        except (OSError, ptyprocess.PtyProcessError):
            # The child died between our last isalive() check and this
            # write. Mark closed so future writes also no-op rather than
            # raising on every keystroke.
            log.info("write to dead claude PTY; marking session closed")
            self._closed = True

    def resize(self, rows: int, cols: int) -> None:
        """Apply new TTY dimensions (rows, cols) — sent via TIOCSWINSZ.

        Called whenever xterm.js's FitAddon reports a new size. ``claude``
        re-renders its bottom input box on receiving SIGWINCH, so this
        keeps the visible width matching the actual terminal width.
        """
        if rows <= 0 or cols <= 0:
            return
        self._dimensions = (int(rows), int(cols))
        if self._proc is None or self._closed:
            return
        try:
            self._proc.setwinsize(int(rows), int(cols))
        except Exception:
            log.exception("setwinsize(%s, %s) failed", rows, cols)

    # --- listeners -----------------------------------------------------

    def add_listener(self, cb: Listener) -> None:
        with self._listeners_lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: Listener) -> None:
        with self._listeners_lock:
            try:
                self._listeners.remove(cb)
            except ValueError:
                pass

    # --- internals -----------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None
        # ptyprocess.read is a blocking read on the master fd; EOF is
        # signalled by an exception (PtyProcessError on Linux/macOS).
        while not self._closed:
            try:
                chunk = proc.read(READ_CHUNK)
            except (EOFError, ptyprocess.PtyProcessError):
                log.info("claude PTY hit EOF — reader thread exiting")
                break
            except OSError as exc:
                # EIO when the slave side is closed; treat as EOF.
                log.info("claude PTY read OSError (%s) — reader exiting", exc)
                break
            except Exception:
                log.exception("unexpected error reading claude PTY")
                break
            if not chunk:
                # Should not happen in blocking mode but guard anyway.
                continue
            with self._listeners_lock:
                listeners = list(self._listeners)
            for cb in listeners:
                try:
                    cb(chunk)
                except Exception:
                    log.exception("claude_session listener raised")
        # Mark closed so write()/resize() become no-ops once the child
        # is gone — without this the next keystroke would try to write
        # to a dead fd and trip an OSError.
        self._closed = True

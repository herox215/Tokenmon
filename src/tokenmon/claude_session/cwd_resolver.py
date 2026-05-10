"""Decide where to spawn ``claude`` based on what the user is doing.

Three-stage cascade, first hit wins:

1. **Per-app AppleScript** — Finder is the only one with a clean,
   stable scripting API for "the path I'm currently looking at". Most
   terminals expose tab/session info but not cwd directly; we let
   stage 2 handle them.

2. **Process-tree + ``lsof``** — walk descendants of the frontmost
   app's PID and read their cwd via ``lsof -d cwd``. Any app with a
   live shell child gets covered for free: Terminal.app, iTerm2,
   Kitty, WezTerm, Alacritty, Ghostty, Warp, plus VS Code / Cursor /
   JetBrains / Zed when their integrated terminal is open.

3. **Finder fallback** — if there's no shell descendant but Finder
   has a window open, use that.

4. **Last resort** — ``~``.

Every step returns ``(Path, source) | None``. The caller of
``resolve()`` always gets a usable Path plus a short human-readable
source string suitable for the chat panel's debug label.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("tokenmon.claude_session.cwd_resolver")

# Names matched against ``ps -o comm`` for the descendant scan. We
# recognise common Unix shells plus a few power-user choices. Order
# doesn't matter; matching is by basename only.
_SHELL_NAMES = {
    "zsh", "bash", "fish", "dash", "sh", "ksh", "tcsh", "csh",
    "nu", "xonsh", "elvish", "ion", "pwsh",
}

# Apps whose cwd we'd rather skip even if lsof returns one — they
# always run from system roots and the value is uninformative.
_SKIP_BUNDLES = {
    "com.apple.systemuiserver",
    "com.apple.dock",
    "com.apple.controlcenter",
    "com.apple.notificationcenterui",
    "com.tokenmon.menubar",  # ourselves
}


def resolve() -> tuple[Path, str]:
    """Return ``(cwd, source)`` — always succeeds, even if every stage
    falls over (last resort: ``~``).

    ``source`` is a short string suitable for the chat-panel debug
    label, e.g. ``"Finder front window"`` or ``"lsof child zsh (PID 4231)"``.
    """
    pid = _frontmost_pid()
    bundle = _frontmost_bundle_id() or ""

    if bundle in _SKIP_BUNDLES:
        # Don't snoop our own process tree.
        pid = 0

    # Stage 1: scripted apps with a first-class location concept.
    if bundle == "com.apple.finder":
        result = _try_finder()
        if result is not None:
            return result

    # Stage 2: process tree.
    if pid > 0:
        result = _try_process_tree(pid, bundle=bundle)
        if result is not None:
            return result

    # Stage 3: Finder fallback (any window open is a hint).
    result = _try_finder()
    if result is not None:
        return result

    # Stage 4: home.
    return Path.home(), "fallback (~)"


# --- Stage 1: AppleScript handlers ---------------------------------


_FINDER_SCRIPT = (
    'tell application "Finder"\n'
    '  if (count of windows) > 0 then\n'
    '    return POSIX path of (target of front window as alias)\n'
    '  end if\n'
    '  return ""\n'
    'end tell'
)


def _try_finder() -> tuple[Path, str] | None:
    """POSIX path of Finder's front window, if any."""
    out = _osascript(_FINDER_SCRIPT)
    if not out:
        return None
    p = Path(out).expanduser()
    if p.is_dir():
        return p, "Finder front window"
    return None


def _osascript(script: str, *, timeout: float = 3.0) -> str | None:
    """Run an AppleScript and return stripped stdout, or None on failure.

    Apple Events permission is asked-once per target app; the dialog
    appears the first time we hit Finder/Terminal/iTerm/etc. and the
    answer sticks. Timeouts must be short — a hung osascript shouldn't
    delay the chat panel for more than a beat.
    """
    if shutil.which("osascript") is None:
        return None
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("osascript timed out (%.1fs)", timeout)
        return None
    except Exception:
        log.exception("osascript invocation failed")
        return None
    if proc.returncode != 0:
        # Common cause: user denied Apple Events permission for the
        # target app. We log once at info — louder noise here would
        # show up every chat-open.
        log.info("osascript rc=%s stderr=%r", proc.returncode, proc.stderr.strip()[:200])
        return None
    return (proc.stdout or "").strip() or None


# --- Stage 2: process tree + lsof ----------------------------------


def _try_process_tree(pid: int, *, bundle: str = "") -> tuple[Path, str] | None:
    """Walk descendants of ``pid`` and pick the most informative cwd.

    Heuristic: prefer shells (zsh/bash/etc.) over arbitrary processes.
    Among shells, prefer the most recently spawned (highest PID) — for
    tabbed terminals that's typically the user's current tab.
    """
    descendants = _list_descendants(pid)
    candidates: list[tuple[bool, int, str, Path]] = []
    for cpid in descendants:
        name = _process_name(cpid)
        if not name:
            continue
        cwd = _process_cwd(cpid)
        if cwd is None:
            continue
        is_shell = name in _SHELL_NAMES
        # ``/`` is the default cwd for daemons spawned by launchd —
        # always uninformative; skip unless the process is *literally*
        # the user's frontmost shell.
        if cwd == Path("/") and not is_shell:
            continue
        candidates.append((is_shell, cpid, name, cwd))

    if candidates:
        # Sort: shells first, then by PID descending (most recent).
        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        is_shell, cpid, name, cwd = candidates[0]
        kind = name if is_shell else f"{name} (non-shell)"
        return cwd, f"lsof child {kind} (PID {cpid})"

    # Last try: the frontmost app itself. Useful for terminals that
    # don't fork (rare) or apps whose cwd is meaningfully their
    # workspace root.
    cwd = _process_cwd(pid)
    if cwd is not None and cwd != Path("/") and cwd != Path.home():
        label = bundle or f"PID {pid}"
        return cwd, f"lsof {label}"
    return None


def _list_descendants(pid: int) -> list[int]:
    """Return all descendant PIDs of ``pid`` (BFS via repeated ``pgrep -P``).

    macOS' ``pgrep -P`` only lists direct children; we iterate to walk
    the full tree. Capped at 2 levels of recursion in practice — chat
    panel can't afford to fan out into hundreds of subprocesses.
    """
    if shutil.which("pgrep") is None:
        return []
    out: list[int] = []
    frontier = [int(pid)]
    seen = {int(pid)}
    # Depth limit: 4 should cover even nested tmux/zellij/screen setups
    # while keeping the worst-case cost bounded.
    for _depth in range(4):
        if not frontier:
            break
        try:
            proc = subprocess.run(
                ["pgrep", "-P", ",".join(str(p) for p in frontier)],
                capture_output=True, text=True, timeout=2,
            )
        except Exception:
            break
        children = []
        for line in proc.stdout.split():
            try:
                cpid = int(line)
            except ValueError:
                continue
            if cpid in seen:
                continue
            seen.add(cpid)
            children.append(cpid)
            out.append(cpid)
        frontier = children
    return out


def _process_name(pid: int) -> str:
    """``ps -o comm=`` basename for ``pid``, or ``""``."""
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return ""
    name = (proc.stdout or "").strip()
    if not name:
        return ""
    # Strip absolute path; ps -o comm gives the full argv[0] path on
    # macOS for some procs.
    return name.rsplit("/", 1)[-1]


def _process_cwd(pid: int) -> Path | None:
    """Read the cwd of ``pid`` via ``lsof -F``. None if dead/inaccessible.

    ``-Fn`` makes lsof emit machine-readable lines — each prefixed with
    a single character indicating the field. ``n<path>`` is the name
    field for the cwd file descriptor.
    """
    if shutil.which("lsof") is None:
        return None
    try:
        proc = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return None
    for line in proc.stdout.splitlines():
        if not line.startswith("n"):
            continue
        path = line[1:].strip()
        if not path:
            continue
        try:
            p = Path(path)
        except Exception:
            continue
        # Only return existing dirs — lsof can lag if the proc just
        # ``cd``'d out, but stale paths are no help for spawning claude.
        if p.is_dir():
            return p
    return None


# --- Stage 0: helpers ---------------------------------------------


def _frontmost_pid() -> int:
    try:
        from tokenmon.companion.window_geom import frontmost_pid
        pid = frontmost_pid()
        return int(pid) if pid else 0
    except Exception:
        log.exception("frontmost_pid lookup failed")
        return 0


def _frontmost_bundle_id() -> str | None:
    try:
        from tokenmon.companion.active_app import current_bundle_id
        return current_bundle_id()
    except Exception:
        log.exception("current_bundle_id lookup failed")
        return None

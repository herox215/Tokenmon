"""Shared process-tree helpers (pgrep / ps wrappers).

Used by:

  - ``claude_session.cwd_resolver`` — picks a sensible spawn cwd by
    walking the frontmost app's descendant shells.
  - ``menubar.ticks.tick_claude_badge`` — decides whether the companion
    badge should be shown by checking if a ``claude`` (or future
    ``opencode``) process is running under the PTY.

All functions are bounded by short subprocess timeouts and swallow
errors — these are called every few seconds on the main thread and
must never block the AppKit run loop.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path  # noqa: F401 — re-exported for callers if needed

log = logging.getLogger("tokenmon.proc_tree")


# Maximum BFS depth when walking descendants. ``pgrep -P`` only lists
# direct children; we iterate to walk the full tree. Depth 4 covers
# nested tmux/zellij/screen → shell → tool layouts.
DEFAULT_MAX_DEPTH = 4


def list_descendants(pid: int, *, max_depth: int = DEFAULT_MAX_DEPTH) -> list[int]:
    """Return all descendant PIDs of ``pid`` (BFS via repeated ``pgrep -P``).

    macOS' ``pgrep -P`` only lists direct children; we iterate to walk
    the full tree. Depth-bounded so a pathological process tree can't
    blow up the tick budget.
    """
    if shutil.which("pgrep") is None:
        return []
    out: list[int] = []
    frontier = [int(pid)]
    seen = {int(pid)}
    for _depth in range(max_depth):
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


def process_name(pid: int) -> str:
    """``ps -o comm=`` basename for ``pid``, or ``""``.

    ``ps -o comm`` gives argv[0] which on macOS is sometimes a full
    path; strip to the basename so callers can do a plain membership
    test against a name set.
    """
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
    return name.rsplit("/", 1)[-1]


def process_argv(pid: int) -> str:
    """Full command-line of ``pid`` via ``ps -o args=``.

    Used for the "agent installed as a node shebang" case — `comm` reads
    as ``node`` but the full argv contains ``…/claude/cli.js``. Empty
    string on failure / dead process.
    """
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return ""
    return (proc.stdout or "").strip()


# Default match set for ``descendant_matches``: claude only for now.
# When opencode support lands, edit these to
# ``frozenset({"claude", "opencode"})`` + ``("claude", "opencode")``.
DEFAULT_AGENT_NAMES: frozenset[str] = frozenset({"claude"})
DEFAULT_AGENT_ARGV_SUBSTRINGS: tuple[str, ...] = ("claude",)


def descendant_matches(
    pid: int,
    *,
    names: frozenset[str] = DEFAULT_AGENT_NAMES,
    argv_substrings: tuple[str, ...] = DEFAULT_AGENT_ARGV_SUBSTRINGS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> int | None:
    """Return the first descendant PID of ``pid`` that matches, or ``None``.

    A descendant ``d`` matches if either:

      - ``process_name(d)`` is in ``names``, OR
      - any of ``argv_substrings`` appears in ``process_argv(d)``.

    The argv check catches the npm-shebang case where ``comm`` reads as
    ``node`` but the full argv contains ``…/claude/cli.js``.

    Short-circuits at the first hit so callers don't pay for a full
    descendant enumeration when one match is enough.
    """
    for cpid in list_descendants(pid, max_depth=max_depth):
        name = process_name(cpid)
        if name and name in names:
            return cpid
        argv = process_argv(cpid)
        if argv and any(sub in argv for sub in argv_substrings):
            return cpid
    return None

"""Thin subprocess harness that asks Claude Code to reply to a chat message.

We intentionally shell out to the ``claude`` CLI rather than calling the
Anthropic API directly: the user already has Claude Code authenticated
locally (OAuth or API key), and using ``claude -p`` means the companion
inherits whatever model / login / settings the user has configured for the
rest of their workflow.

The companion runs ``claude`` in *print mode* (``-p``), which sends the
prompt, waits for the full response, and exits — perfect for a one-shot
chat reply.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("tokenmon.companion.llm")

# Hard cap so the chat panel can't hang forever if claude stalls. The tail
# of the response is lost on timeout but the user sees a clear "(timed
# out)" line in the transcript instead of a frozen UI.
DEFAULT_TIMEOUT_S = 60.0


def claude_available() -> bool:
    """True iff the ``claude`` CLI is on PATH. Used by the chat panel to
    decide whether to even try sending the message — if not, we surface a
    short hint instead of failing on every keystroke."""
    return shutil.which("claude") is not None


def ask_claude(
    user_message: str,
    *,
    system_prompt: str,
    skip_permissions: bool = False,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[bool, str]:
    """Send ``user_message`` to Claude Code and return ``(ok, text)``.

    ``ok=True`` means we got a non-empty reply on stdout; ``text`` is the
    raw response stripped of leading/trailing whitespace.

    ``ok=False`` means something went wrong (claude not installed, non-zero
    exit, timeout, empty stdout). ``text`` is then a short error message
    suitable for showing directly in the chat transcript — no stack trace,
    no shell noise.

    Why ``--print`` + ``--system-prompt`` rather than appending to the
    default system prompt: we want a clean persona ("you are a Bold
    Charmander…") without Claude Code's default coding-assistant preamble
    leaking through. The default preamble is helpful in the terminal but
    actively breaks character here.

    ``skip_permissions=True`` adds ``--dangerously-skip-permissions`` so
    the companion can use Bash / Edit / Read tools without prompting the
    user mid-chat. It's gated on a config flag the user has to flip
    explicitly — we never enable it by default.
    """
    if not claude_available():
        return (
            False,
            "(claude CLI not found on PATH — install Claude Code to chat)",
        )

    # ``-p`` short for ``--print``: non-interactive, exits when done.
    args: list[str] = [
        "claude",
        "-p",
        "--system-prompt", system_prompt,
    ]
    if skip_permissions:
        args.append("--dangerously-skip-permissions")
    # Pass the user message as the trailing positional ``prompt`` argument.
    # We deliberately don't pipe it via stdin: stdin would force claude
    # into stream mode and complicate output parsing.
    args.append(user_message)

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            # Force UTF-8 explicitly: under a LaunchAgent the parent's
            # locale is empty, ``text=True`` alone falls back to ASCII,
            # and any non-ASCII byte from claude's output (the em-dash
            # it likes to insert, accented quotes, …) crashes the
            # worker with UnicodeDecodeError. ``errors="replace"`` is
            # belt-and-suspenders for the rare case claude emits a raw
            # byte sequence that isn't valid UTF-8.
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            # Don't inherit the parent process's stdin — claude would
            # block waiting for EOF if it's a TTY.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        log.warning("claude -p timed out after %.1fs", timeout)
        return (False, f"(no reply within {int(timeout)}s — try again)")
    except FileNotFoundError:
        # Race against ``claude_available()`` if the user uninstalled
        # claude between the check and the call.
        return (False, "(claude CLI not found on PATH)")
    except Exception:
        log.exception("claude subprocess failed")
        return (False, "(claude subprocess failed — see logs)")

    if result.returncode != 0:
        # Surface the first line of stderr for quick diagnosis (auth
        # errors, missing model access). Truncated to avoid blowing up
        # the chat transcript width.
        err = (result.stderr or "").strip().splitlines()
        head = err[0] if err else f"exit {result.returncode}"
        return (False, f"(claude error: {head[:160]})")

    text = (result.stdout or "").strip()
    if not text:
        return (False, "(empty reply)")
    return (True, text)

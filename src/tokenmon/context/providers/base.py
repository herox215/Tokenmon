"""Provider protocol — every platform adapter implements this."""
from __future__ import annotations

import logging
import subprocess
from typing import Protocol

from ..snapshot import ContextSnapshot

log = logging.getLogger("tokenmon.context.providers")


class ContextProvider(Protocol):
    name: str

    def supports(self, app_id: str) -> bool: ...

    def snapshot(self, app_id: str, pid: int) -> ContextSnapshot | None: ...


def run_subprocess(
    args: list[str],
    *,
    timeout: float = 1.5,
    input_text: str | None = None,
) -> tuple[int, str, str] | None:
    """Run an external command with a hard timeout. Returns
    ``(returncode, stdout, stderr)`` or ``None`` if the command timed
    out or could not be launched. Used by AppleScript / kitty / wezterm
    strategies — keeping it in one helper means the chat UI never blocks
    on a hung scriptable app for more than ``timeout`` seconds."""
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
    except subprocess.TimeoutExpired:
        log.warning("subprocess timed out: %s", args[0])
        return None
    except FileNotFoundError:
        log.debug("subprocess not found on PATH: %s", args[0])
        return None
    except Exception:
        log.exception("subprocess launch failed: %s", args)
        return None
    return r.returncode, r.stdout, r.stderr

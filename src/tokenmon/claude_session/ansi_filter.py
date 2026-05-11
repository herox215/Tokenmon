"""Streaming filter that drops ANSI background-colour and reverse-video
SGR parameters from a PTY byte stream.

Why: Claude Code's TUI (and a handful of other CLIs) renders tool-call
callouts, menu rows, and recap headers with bright background colours
*or* reverse-video. On a dark companion-chat panel both appear as
solid coral / white blocks that wash out the actual text. Stripping
just those parameters keeps foreground colours (syntax highlighting
in ``git diff``, ``less``, shell prompts) while flattening the visual
noise.

What gets dropped:

  - ``\\e[7m``                     — reverse-video / inverse (swaps fg+bg)
  - ``\\e[27m``                    — reset reverse-video
  - ``\\e[40m`` … ``\\e[47m``        — basic background colours
  - ``\\e[100m`` … ``\\e[107m``      — bright background colours
  - ``\\e[49m``                    — reset to default background
  - ``\\e[48;5;N m``               — 256-colour background
  - ``\\e[48;2;R;G;B m``           — 24-bit truecolour background

Other SGR parameters pass through unchanged. Compound sequences like
``\\e[1;31;48;5;234m`` (bold + red foreground + grey background) are
rewritten to ``\\e[1;31m``.

PTY reads can split an escape sequence across two chunks; the filter
buffers a trailing partial sequence and re-tries it on the next feed.
"""
from __future__ import annotations

import re

# Full SGR sequence: ESC '[' <digits-and-semicolons> 'm'.
_SGR_RE = re.compile(rb"\x1b\[([0-9;]*)m")

# A trailing CSI that hasn't reached its final byte yet — defer it so
# we don't accidentally split params or miss the terminator.
_PARTIAL_CSI_RE = re.compile(rb"\x1b(?:\[[0-9;]*)?$")


class AnsiBgStripper:
    """Stateful filter — call ``feed(chunk)`` for every PTY read."""

    def __init__(self) -> None:
        self._tail = b""

    def feed(self, data: bytes) -> bytes:
        """Return ``data`` with background-colour SGR params removed.

        A partial escape at the tail end is held back and prepended to
        the next call so split sequences round-trip correctly.
        """
        if self._tail:
            data = self._tail + data
            self._tail = b""
        m = _PARTIAL_CSI_RE.search(data)
        if m is not None:
            self._tail = data[m.start():]
            data = data[: m.start()]
        return _SGR_RE.sub(_filter_sgr, data)


def _filter_sgr(match: "re.Match[bytes]") -> bytes:
    raw = match.group(1)
    # ``\e[m`` is shorthand for ``\e[0m`` (reset everything) — keep it
    # untouched; dropping it would leave previous attributes stuck on.
    if not raw:
        return match.group(0)
    params = raw.split(b";")
    out: list[bytes] = []
    i = 0
    while i < len(params):
        p = params[i]
        try:
            num = int(p) if p else 0
        except ValueError:
            out.append(p)
            i += 1
            continue
        if 40 <= num <= 47 or 100 <= num <= 107 or num == 49:
            # Plain background-colour parameter — drop.
            i += 1
            continue
        if num == 7 or num == 27:
            # Reverse-video (or its reset) — drop. xterm.js renders
            # these by swapping fg/bg, which produces the same blocky
            # highlight we're trying to avoid.
            i += 1
            continue
        if num == 48:
            # Extended background: 48;5;N or 48;2;R;G;B.
            if i + 1 < len(params):
                try:
                    sub = int(params[i + 1]) if params[i + 1] else 0
                except ValueError:
                    sub = 0
                if sub == 5:
                    i += 3
                    continue
                if sub == 2:
                    i += 5
                    continue
            # Malformed — skip the 48 and its mode byte to stay in sync.
            i += 2
            continue
        out.append(p)
        i += 1
    if not out:
        # Every param was a background-colour code; emit nothing.
        return b""
    return b"\x1b[" + b";".join(out) + b"m"

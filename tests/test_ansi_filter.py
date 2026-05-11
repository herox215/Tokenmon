"""Tests for the streaming ANSI background-colour stripper."""
from __future__ import annotations

import pytest

from tokenmon.claude_session.ansi_filter import AnsiBgStripper


def _strip(*chunks: bytes) -> bytes:
    f = AnsiBgStripper()
    return b"".join(f.feed(c) for c in chunks)


def test_passthrough_plain_text():
    assert _strip(b"hello world") == b"hello world"


def test_drops_basic_bg_codes():
    # \e[41m sets red bg, \e[0m resets; foreground 'hi' between.
    inp = b"\x1b[41mhi\x1b[0m"
    assert _strip(inp) == b"hi\x1b[0m"


def test_drops_bright_bg_codes():
    inp = b"\x1b[101mwarn\x1b[0m"
    assert _strip(inp) == b"warn\x1b[0m"


def test_drops_256_color_bg():
    # 256-color bg uses 48;5;N
    inp = b"\x1b[48;5;234mdim\x1b[0m"
    assert _strip(inp) == b"dim\x1b[0m"


def test_drops_truecolor_bg():
    # 24-bit RGB bg uses 48;2;R;G;B
    inp = b"\x1b[48;2;200;100;100mhot\x1b[0m"
    assert _strip(inp) == b"hot\x1b[0m"


def test_keeps_foreground_color():
    # \e[31m = red fg; should pass through unchanged.
    inp = b"\x1b[31merror\x1b[0m"
    assert _strip(inp) == b"\x1b[31merror\x1b[0m"


def test_compound_sequence_keeps_fg_drops_bg():
    # bold + red fg + bg256(234) → bold + red fg.
    inp = b"\x1b[1;31;48;5;234mtitle\x1b[0m"
    assert _strip(inp) == b"\x1b[1;31mtitle\x1b[0m"


def test_compound_keeps_fg_drops_bg_truecolor():
    inp = b"\x1b[3;36;48;2;30;30;30;7mmix\x1b[0m"
    # italic(3) + cyan-fg(36) survive; 48;2;30;30;30 (bg truecolor) and
    # 7 (reverse) both drop.
    assert _strip(inp) == b"\x1b[3;36mmix\x1b[0m"


def test_bare_csi_m_kept_as_reset():
    # \e[m == \e[0m (reset). Should pass through untouched.
    inp = b"\x1b[mreset"
    assert _strip(inp) == b"\x1b[mreset"


def test_all_bg_sgr_dropped_entirely():
    # If every param in a SGR is a bg code, emit nothing.
    inp = b"a\x1b[41;48;5;200mb"
    assert _strip(inp) == b"ab"


def test_default_bg_reset_dropped():
    # \e[49m resets only the bg; we never set a non-default bg via the
    # palette so dropping it is fine.
    inp = b"\x1b[49mtail"
    assert _strip(inp) == b"tail"


def test_non_sgr_csi_passes_through():
    # Cursor moves, clear-line, etc. are CSI sequences that don't end
    # in 'm'. They must not be touched.
    inp = b"\x1b[2J\x1b[Hclear"
    assert _strip(inp) == b"\x1b[2J\x1b[Hclear"


def test_split_escape_across_chunks():
    # PTY reads can split an escape sequence. The filter must hold the
    # tail across calls and apply it correctly.
    out = _strip(b"\x1b[1;31;", b"48;5;234mhi\x1b[0m")
    assert out == b"\x1b[1;31mhi\x1b[0m"


def test_split_at_escape_start():
    out = _strip(b"\x1b", b"[41mboom\x1b[0m")
    assert out == b"boom\x1b[0m"


def test_split_inside_truecolor_bg():
    out = _strip(b"\x1b[48;2;10;20;", b"30mrgb\x1b[0m")
    assert out == b"rgb\x1b[0m"


def test_unknown_param_passes_through():
    # ``\e[99m`` is not a defined SGR — keep it so we don't accidentally
    # eat future codes the spec adds.
    inp = b"\x1b[99mtext\x1b[0m"
    assert _strip(inp) == b"\x1b[99mtext\x1b[0m"


def test_drops_reverse_video():
    # \e[7m turns on reverse-video; with our fg=light-grey + dark CSS
    # bg this would render as a light block. Strip it.
    inp = b"\x1b[7mHEADER\x1b[0m"
    assert _strip(inp) == b"HEADER\x1b[0m"


def test_drops_reverse_video_reset():
    inp = b"\x1b[27mtail"
    assert _strip(inp) == b"tail"


def test_compound_drops_reverse_keeps_bold_fg():
    inp = b"\x1b[1;7;31mtitle\x1b[0m"
    assert _strip(inp) == b"\x1b[1;31mtitle\x1b[0m"


def test_compound_drops_bg_and_reverse():
    inp = b"\x1b[7;48;5;208mmix\x1b[0m"
    assert _strip(inp) == b"mix\x1b[0m"


def test_empty_chunk_is_safe():
    f = AnsiBgStripper()
    assert f.feed(b"") == b""
    assert f.feed(b"plain") == b"plain"

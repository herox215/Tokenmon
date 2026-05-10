"""Smoke tests for ClaudeSession.

We deliberately spawn ``/bin/cat`` instead of the real ``claude`` CLI so
the suite has no dependency on Claude Code being installed — every CI box
has cat. The PTY code path is identical: ``cat`` echoes back exactly what
we write, which is the cleanest possible roundtrip for verifying the
read/write/listener plumbing.
"""
from __future__ import annotations

import os
import time

import pytest

from tokenmon import claude_session
from tokenmon.claude_session.session import ClaudeSession, SessionUnavailable


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="ClaudeSession uses ptyprocess, POSIX-only.",
)


def _wait(predicate, timeout: float = 2.0) -> bool:
    """Spin briefly waiting for ``predicate()`` to become truthy.

    The reader thread runs async, so we can't synchronously assert on
    output right after writing — we have to give the kernel a tick to
    ferry bytes through the PTY pair.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_roundtrip_via_cat():
    """Write through the PTY, expect the listener to see the echo."""
    sess = ClaudeSession(command=["/bin/cat"])
    sess.start()
    received = bytearray()
    sess.add_listener(received.extend)
    try:
        sess.write(b"hello tokenmon\n")
        assert _wait(lambda: b"hello tokenmon" in bytes(received)), (
            f"never saw echo, got: {bytes(received)!r}"
        )
    finally:
        sess.close()
    # close() drives is_alive() False without us having to poll the PID.
    assert not sess.is_alive()


def test_close_is_idempotent():
    sess = ClaudeSession(command=["/bin/cat"])
    sess.start()
    sess.close()
    sess.close()  # second call must not raise
    assert not sess.is_alive()


def test_write_after_close_is_noop():
    sess = ClaudeSession(command=["/bin/cat"])
    sess.start()
    sess.close()
    # No exception, no error log noise — keystrokes after teardown are
    # a normal race we deliberately swallow.
    sess.write(b"ignored")


def test_resize_does_not_raise():
    sess = ClaudeSession(command=["/bin/cat"])
    sess.start()
    try:
        sess.resize(40, 120)
        sess.resize(0, 0)  # invalid dims must be ignored, not raise
    finally:
        sess.close()


def test_remove_listener():
    sess = ClaudeSession(command=["/bin/cat"])
    sess.start()
    received = bytearray()
    sess.add_listener(received.extend)
    sess.remove_listener(received.extend)
    sess.write(b"silent\n")
    # Give the reader a chance — listener should NOT see anything.
    time.sleep(0.1)
    assert b"silent" not in bytes(received)
    sess.close()


def test_unavailable_command_raises():
    """A non-existent default ``claude`` should surface as SessionUnavailable.

    We can't easily simulate ``claude`` missing from PATH from inside a
    test (it would corrupt the user's PATH), so instead we exercise the
    same exception path by passing a definitely-missing command and
    triggering the FileNotFoundError → SessionUnavailable mapping in
    ``start``.
    """
    sess = ClaudeSession(command=["/usr/bin/definitely-not-a-real-binary-xyz"])
    with pytest.raises(SessionUnavailable):
        sess.start()


def test_get_session_is_singleton(monkeypatch):
    """``claude_session.get_session()`` returns the same instance across calls.

    Patch the spawn target to ``/bin/cat`` so this works on machines
    without claude installed. We also clean up the module-level slot at
    the end so we don't leak a session into other tests.
    """
    monkeypatch.setattr(
        claude_session.session, "shutil",
        type("shim", (), {"which": staticmethod(lambda _: "/bin/cat")}),
    )
    # Force a fresh slot — previous tests may have left one.
    claude_session.shutdown()
    try:
        s1 = claude_session.get_session()
        s2 = claude_session.get_session()
        assert s1 is s2
        assert s1.is_alive()
    finally:
        claude_session.shutdown()

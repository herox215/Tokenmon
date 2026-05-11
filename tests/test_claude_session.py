"""Smoke tests for ClaudeSession.

We deliberately spawn ``/bin/cat`` instead of the real default
tmux-wrapped shell so the suite has no dependency on tmux/zsh being
installed — every POSIX box has cat. The PTY code path is identical:
``cat`` echoes back exactly what we write, which is the cleanest
possible roundtrip for verifying the read/write/listener plumbing.
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
    """A non-existent command surfaces as ``SessionUnavailable``.

    The bare ``ClaudeSession`` constructor accepts any argv — we
    exercise the FileNotFoundError → SessionUnavailable mapping inside
    ``start`` by passing a path that definitely doesn't exist.
    """
    sess = ClaudeSession(command=["/usr/bin/definitely-not-a-real-binary-xyz"])
    with pytest.raises(SessionUnavailable):
        sess.start()


def test_default_shell_command_prefers_tmux(monkeypatch):
    """When tmux is on PATH, the default argv is ``tmux new-session -A -s ...``.

    Patches ``shutil.which`` to claim tmux exists at ``/usr/local/bin/tmux``
    and ``$SHELL`` exists at ``/bin/zsh`` so we can assert on the shape of
    the returned argv without spawning anything.
    """
    from tokenmon.claude_session import session as session_mod

    monkeypatch.setenv("SHELL", "/bin/zsh")

    def fake_which(name):
        return {"/bin/zsh": "/bin/zsh", "tmux": "/usr/local/bin/tmux"}.get(name)

    monkeypatch.setattr(session_mod.shutil, "which", fake_which)
    argv = session_mod._default_shell_command()
    assert argv[0] == "/usr/local/bin/tmux"
    assert "new-session" in argv
    assert "-A" in argv
    assert session_mod.TMUX_SESSION_NAME in argv
    # Shell + login flag are appended so the user's .zprofile runs.
    assert argv[-2:] == ["/bin/zsh", "-l"]


def test_default_shell_command_falls_back_to_screen(monkeypatch):
    """When tmux is missing but screen exists, we use ``screen -DR <name>``."""
    from tokenmon.claude_session import session as session_mod

    monkeypatch.setenv("SHELL", "/bin/zsh")

    def fake_which(name):
        if name == "tmux":
            return None
        return {
            "/bin/zsh": "/bin/zsh",
            "screen": "/usr/bin/screen",
        }.get(name)

    monkeypatch.setattr(session_mod.shutil, "which", fake_which)
    argv = session_mod._default_shell_command()
    assert argv == ["/usr/bin/screen", "-DR", session_mod.TMUX_SESSION_NAME]


def test_default_shell_command_falls_back_to_plain_shell(monkeypatch):
    """No tmux, no screen → bare login shell, no persistence."""
    from tokenmon.claude_session import session as session_mod

    monkeypatch.setenv("SHELL", "/bin/zsh")

    def fake_which(name):
        if name in ("tmux", "screen"):
            return None
        return {"/bin/zsh": "/bin/zsh"}.get(name)

    monkeypatch.setattr(session_mod.shutil, "which", fake_which)
    argv = session_mod._default_shell_command()
    assert argv == ["/bin/zsh", "-l"]


def test_screen_server_pid_parses_real_screen_ls(monkeypatch):
    """``_screen_server_pid`` parses ``screen -ls`` output and picks the
    server PID for our session name."""
    from tokenmon.claude_session import session as session_mod

    class _Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.returncode = 1  # screen -ls exits 1 even on success

    fake_stdout = (
        "There is a screen on:\n"
        "\t90616.tokenmon-companion\t(Attached)\n"
        "1 Socket in /var/folders/zn/.../T/.screen.\n"
    )

    def fake_run(_argv, **_kw):
        return _Result(fake_stdout)

    monkeypatch.setattr(session_mod.shutil, "which", lambda _: "/usr/bin/screen")
    monkeypatch.setattr(session_mod.subprocess, "run", fake_run)
    assert session_mod._screen_server_pid("tokenmon-companion") == 90616
    # Different name → no match.
    assert session_mod._screen_server_pid("other-session") is None


def test_screen_server_pid_returns_none_without_screen(monkeypatch):
    from tokenmon.claude_session import session as session_mod
    monkeypatch.setattr(session_mod.shutil, "which", lambda _: None)
    assert session_mod._screen_server_pid("tokenmon-companion") is None


def test_agent_root_pid_for_bare_shell():
    """No multiplexer → walk our own PTY child."""
    sess = ClaudeSession(command=["/bin/cat"])
    sess.start()
    try:
        assert sess.agent_root_pid() == sess.pid
    finally:
        sess.close()


def test_agent_root_pid_for_screen_command(monkeypatch):
    """Command starts with /usr/bin/screen → resolve via ``screen -ls``.

    Doesn't actually spawn screen — we monkeypatch ``_screen_server_pid``
    to return a fixture PID and exercise the dispatch in
    ``agent_root_pid`` itself.
    """
    from tokenmon.claude_session import session as session_mod
    sess = ClaudeSession(command=["/usr/bin/screen", "-DR", "tokenmon-companion"])
    sess.start()
    try:
        monkeypatch.setattr(
            session_mod, "_screen_server_pid",
            lambda name: 90616 if name == "tokenmon-companion" else None,
        )
        assert sess.agent_root_pid() == 90616
    finally:
        sess.close()


def test_pid_property_lifecycle():
    """``session.pid`` is the live PTY child PID, ``None`` before/after."""
    sess = ClaudeSession(command=["/bin/cat"])
    assert sess.pid is None
    sess.start()
    try:
        pid = sess.pid
        assert isinstance(pid, int) and pid > 0
        assert sess.is_alive()
    finally:
        sess.close()
    assert sess.pid is None


def test_recent_output_bytes_tracks_chunks():
    """Bytes echoed back via cat show up in ``recent_output_bytes``."""
    sess = ClaudeSession(command=["/bin/cat"])
    sess.start()
    try:
        # Empty before any output.
        assert sess.recent_output_bytes(3.0) == 0
        sess.write(b"abcdefghij\n")
        assert _wait(lambda: sess.recent_output_bytes(3.0) >= 10), (
            f"never observed recent bytes, got {sess.recent_output_bytes(3.0)}"
        )
        # 0 window collapses to 0 regardless of stored history.
        assert sess.recent_output_bytes(0.0) == 0
    finally:
        sess.close()


def test_recent_output_bytes_expires_old_samples():
    """Bytes older than the requested window aren't counted."""
    sess = ClaudeSession(command=["/bin/cat"])
    sess.start()
    try:
        sess.write(b"ping\n")
        assert _wait(lambda: sess.recent_output_bytes(3.0) >= 5)
        # 0.001 s window — old samples shouldn't count.
        assert sess.recent_output_bytes(0.001) == 0
    finally:
        sess.close()


def test_get_session_is_singleton(monkeypatch):
    """``claude_session.get_session()`` returns the same instance across calls.

    Force the spawn target to ``/bin/cat`` so this works without tmux or
    a specific user shell. We clean up the module-level slot at the end
    so we don't leak a session into other tests.
    """
    monkeypatch.setattr(
        claude_session.session,
        "_default_shell_command",
        lambda: ["/bin/cat"],
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

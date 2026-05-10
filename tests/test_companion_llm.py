"""Tests for the Claude Code subprocess harness used by the companion chat.

We monkeypatch ``subprocess.run`` so the suite stays offline and doesn't
require ``claude`` to be installed in CI. Each test asserts on the exact
argument list the harness would have executed — enough to catch flag
regressions without spawning a real subprocess.
"""
from __future__ import annotations

import subprocess

import pytest

from tokenmon.companion import llm


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def claude_on_path(monkeypatch):
    """Pretend the ``claude`` CLI is installed without actually requiring
    it. Tests that need to assert "claude missing" override this."""
    monkeypatch.setattr(llm, "claude_available", lambda: True)


def test_returns_error_when_claude_missing(monkeypatch):
    monkeypatch.setattr(llm, "claude_available", lambda: False)
    ok, text = llm.ask_claude("hi", system_prompt="be a Pokémon")
    assert ok is False
    assert "claude" in text.lower()


def test_invokes_claude_print_with_system_prompt(claude_on_path, monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return _FakeCompleted(0, stdout="hello there")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, text = llm.ask_claude(
        "hi friend",
        system_prompt="You are Pikachu",
    )
    assert ok is True
    assert text == "hello there"
    args = seen["args"]
    assert args[0] == "claude"
    assert "-p" in args
    # System prompt is passed via --system-prompt and the next slot.
    assert "--system-prompt" in args
    sp_idx = args.index("--system-prompt")
    assert args[sp_idx + 1] == "You are Pikachu"
    # User message is the trailing positional argument.
    assert args[-1] == "hi friend"
    # Skip-permissions defaults off.
    assert "--dangerously-skip-permissions" not in args
    # Stdin must be DEVNULL so claude doesn't block on a TTY.
    assert seen["kwargs"]["stdin"] == subprocess.DEVNULL
    # UTF-8 must be forced explicitly: under a LaunchAgent the parent
    # locale is empty and ``text=True`` alone falls back to ASCII,
    # which crashes on the em-dashes claude emits. ``errors=replace``
    # protects against the very rare case of malformed UTF-8 bytes.
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["errors"] == "replace"


def test_skip_permissions_flag_added_when_enabled(claude_on_path, monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return _FakeCompleted(0, stdout="ok")

    monkeypatch.setattr(subprocess, "run", fake_run)
    llm.ask_claude(
        "hi",
        system_prompt="be a Pokémon",
        skip_permissions=True,
    )
    assert "--dangerously-skip-permissions" in seen["args"]
    # The danger flag must come BEFORE the trailing user message so claude
    # parses it as an option rather than part of the prompt.
    danger_idx = seen["args"].index("--dangerously-skip-permissions")
    user_msg_idx = seen["args"].index("hi")
    assert danger_idx < user_msg_idx


def test_timeout_returns_friendly_error(claude_on_path, monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, text = llm.ask_claude(
        "hi", system_prompt="be a Pokémon", timeout=0.01,
    )
    assert ok is False
    assert "no reply" in text.lower() or "timed out" in text.lower()


def test_non_zero_exit_surfaces_first_stderr_line(claude_on_path, monkeypatch):
    def fake_run(args, **kwargs):
        return _FakeCompleted(
            1, stdout="", stderr="auth required\nstack trace line 2\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, text = llm.ask_claude("hi", system_prompt="x")
    assert ok is False
    assert "auth required" in text
    # Multi-line stderr must not flood the chat: only the first line.
    assert "stack trace" not in text


def test_empty_stdout_marked_as_failure(claude_on_path, monkeypatch):
    def fake_run(args, **kwargs):
        return _FakeCompleted(0, stdout="   \n  \n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, text = llm.ask_claude("hi", system_prompt="x")
    assert ok is False
    assert "empty" in text.lower()


def test_filenotfounderror_falls_back_gracefully(claude_on_path, monkeypatch):
    def fake_run(args, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, text = llm.ask_claude("hi", system_prompt="x")
    assert ok is False
    assert "claude" in text.lower()

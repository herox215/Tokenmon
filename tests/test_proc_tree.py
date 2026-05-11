"""Unit tests for tokenmon.proc_tree.

Uses real subprocesses (sleep, sh) instead of mocking out pgrep/ps —
the helpers' whole job is to drive those tools, and the real CLI
behaviour is what matters. Each spawned child is reaped in the test's
``finally`` so a flaky run can't leak procs.
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest

from tokenmon import proc_tree


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="proc_tree shells out to pgrep/ps, POSIX-only.",
)


def _wait_for(pred, timeout: float = 2.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def test_list_descendants_finds_direct_child():
    child = subprocess.Popen(["/bin/sleep", "5"])
    try:
        assert _wait_for(
            lambda: child.pid in proc_tree.list_descendants(os.getpid())
        ), "child not discovered under our pid"
    finally:
        child.terminate()
        child.wait(timeout=2)


def test_list_descendants_returns_empty_for_leaf():
    # A live sleep with no children of its own.
    child = subprocess.Popen(["/bin/sleep", "5"])
    try:
        # Direct check: the sleep has no descendants.
        assert proc_tree.list_descendants(child.pid) == []
    finally:
        child.terminate()
        child.wait(timeout=2)


def test_list_descendants_empty_on_dead_pid():
    child = subprocess.Popen(["/bin/sleep", "0.1"])
    child.wait(timeout=2)
    # PID reaped — pgrep returns nothing, function returns [].
    assert proc_tree.list_descendants(child.pid) == []


def test_process_name_for_self_is_nonempty():
    name = proc_tree.process_name(os.getpid())
    assert name, "expected a non-empty process name for ourselves"


def test_process_name_strips_path():
    # /bin/sleep — `ps -o comm` may include the full path; the helper
    # strips it. We just assert no slash remains.
    child = subprocess.Popen(["/bin/sleep", "5"])
    try:
        # Give the kernel a tick to register the proc.
        time.sleep(0.05)
        name = proc_tree.process_name(child.pid)
        assert name, "no process_name returned"
        assert "/" not in name
        assert name == "sleep"
    finally:
        child.terminate()
        child.wait(timeout=2)


def test_process_argv_contains_executable():
    child = subprocess.Popen(["/bin/sleep", "5"])
    try:
        time.sleep(0.05)
        argv = proc_tree.process_argv(child.pid)
        assert argv
        assert "sleep" in argv
    finally:
        child.terminate()
        child.wait(timeout=2)


def test_process_argv_empty_for_dead_pid():
    child = subprocess.Popen(["/bin/sleep", "0.1"])
    child.wait(timeout=2)
    assert proc_tree.process_argv(child.pid) == ""


def test_descendant_matches_no_hit_returns_none():
    # No claude-named process under our test pid (presumably).
    assert proc_tree.descendant_matches(os.getpid()) is None


def test_descendant_matches_by_name():
    # ``exec -a claude sleep …`` rewrites argv[0] AND ``comm`` to
    # ``claude``, so the name gate fires without us having to install
    # an actual claude binary.
    child = subprocess.Popen(
        ["/bin/sh", "-c", "exec -a claude /bin/sleep 5"],
    )
    try:
        # exec -a swaps argv[0] AFTER fork; give it a moment to land.
        assert _wait_for(
            lambda: proc_tree.descendant_matches(os.getpid()) is not None,
            timeout=2.0,
        ), "claude-named descendant not detected"
    finally:
        child.terminate()
        child.wait(timeout=2)


def test_descendant_matches_by_argv_substring():
    # `comm` is ``node-claude-shim`` (not in the default name set), but
    # argv (via ``ps -o args``) contains the substring ``claude``. This
    # models the npm-shebang case (``node /…/claude/cli.js``) without
    # requiring node installed.
    child = subprocess.Popen(
        ["/bin/sh", "-c", "exec -a node-claude-shim /bin/sleep 5"],
    )
    try:
        assert _wait_for(
            lambda: proc_tree.descendant_matches(
                os.getpid(),
                # Custom name set that does NOT include the shim name —
                # forces the match to go through the argv-substring gate.
                names=frozenset({"claude"}),
                argv_substrings=("claude",),
            ) is not None,
            timeout=2.0,
        ), "argv-substring match did not fire"
    finally:
        child.terminate()
        child.wait(timeout=2)


def test_descendant_matches_respects_custom_names():
    # Custom name set — search for ``sleep`` instead of claude.
    child = subprocess.Popen(["/bin/sleep", "5"])
    try:
        assert _wait_for(
            lambda: proc_tree.descendant_matches(
                os.getpid(), names=frozenset({"sleep"}), argv_substrings=(),
            ) is not None,
            timeout=2.0,
        )
    finally:
        child.terminate()
        child.wait(timeout=2)


def test_cwd_resolver_still_uses_shared_helpers():
    """Smoke: cwd_resolver re-exports proc_tree helpers and they still work.

    Guards the extraction — if someone deletes the re-import in
    cwd_resolver this test fails loudly instead of silently breaking
    the spawn-cwd cascade.
    """
    from tokenmon.claude_session import cwd_resolver

    assert cwd_resolver._list_descendants is proc_tree.list_descendants
    assert cwd_resolver._process_name is proc_tree.process_name

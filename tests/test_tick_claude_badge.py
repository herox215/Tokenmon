"""State-machine tests for ``ticks.tick_claude_badge``.

Drives the tick with a fake app + fake overlay, and monkeypatches the
module-level singleton and ``proc_tree.descendant_matches`` so we can
exercise every transition (companion off, session None, agent running
but quiet, agent running and noisy, etc.) deterministically.
"""
from __future__ import annotations

import pytest


class _FakeOverlay:
    def __init__(self, *, visible: bool = True) -> None:
        self.visible = visible
        self._badge_on = False
        self.show_calls = 0
        self.hide_calls = 0

    @property
    def claude_badge_visible(self) -> bool:
        return self._badge_on

    def show_claude_badge(self) -> None:
        self.show_calls += 1
        self._badge_on = True

    def hide_claude_badge(self) -> None:
        self.hide_calls += 1
        self._badge_on = False


class _FakeApp:
    def __init__(self, *, companion_mode: bool = True,
                 overlay_visible: bool = True) -> None:
        self._companion_mode = companion_mode
        self._overlay = _FakeOverlay(visible=overlay_visible)


_UNSET = object()


class _FakeSession:
    def __init__(self, *, pid: int = 1234, alive: bool = True,
                 recent_bytes: int = 0, root_pid=_UNSET) -> None:
        self.pid = pid
        self._alive = alive
        self._recent = recent_bytes
        # Default the agent_root_pid to the same PID — most tests pass
        # ``patch_matches`` to control the descendant lookup result anyway,
        # so the exact value doesn't matter as long as it's truthy.
        # Tests can pass ``root_pid=None`` to exercise the unresolved-
        # server branch.
        self._root_pid = pid if root_pid is _UNSET else root_pid

    def is_alive(self) -> bool:
        return self._alive

    def recent_output_bytes(self, _window_s: float) -> int:
        return self._recent

    def agent_root_pid(self) -> int | None:
        return self._root_pid


@pytest.fixture
def patch_singleton(monkeypatch):
    """Yields a setter that swaps ``claude_session._session`` for the test."""
    from tokenmon import claude_session

    def _set(value):
        monkeypatch.setattr(claude_session, "_session", value, raising=False)

    yield _set


@pytest.fixture
def patch_matches(monkeypatch):
    """Yields a setter that controls ``proc_tree.descendant_matches``."""
    from tokenmon import proc_tree

    def _set(return_value):
        monkeypatch.setattr(
            proc_tree, "descendant_matches",
            lambda *_args, **_kw: return_value,
        )

    yield _set


def test_tick_noop_when_companion_off(patch_singleton, patch_matches):
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(recent_bytes=1000))
    patch_matches(2222)
    app = _FakeApp(companion_mode=False)
    ticks.tick_claude_badge(app)
    assert app._overlay.show_calls == 0


def test_tick_hides_when_companion_off_and_badge_was_on(patch_singleton, patch_matches):
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(recent_bytes=1000))
    patch_matches(2222)
    app = _FakeApp(companion_mode=False)
    app._overlay._badge_on = True
    ticks.tick_claude_badge(app)
    assert app._overlay.hide_calls == 1
    assert app._overlay.claude_badge_visible is False


def test_tick_noop_when_overlay_hidden(patch_singleton, patch_matches):
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(recent_bytes=1000))
    patch_matches(2222)
    app = _FakeApp(overlay_visible=False)
    ticks.tick_claude_badge(app)
    assert app._overlay.show_calls == 0


def test_tick_hides_when_session_none(patch_singleton, patch_matches):
    from tokenmon.menubar import ticks
    patch_singleton(None)
    patch_matches(2222)
    app = _FakeApp()
    app._overlay._badge_on = True
    ticks.tick_claude_badge(app)
    assert app._overlay.hide_calls == 1


def test_tick_hides_when_session_dead(patch_singleton, patch_matches):
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(alive=False, recent_bytes=1000))
    patch_matches(2222)
    app = _FakeApp()
    app._overlay._badge_on = True
    ticks.tick_claude_badge(app)
    assert app._overlay.hide_calls == 1


def test_tick_hides_when_session_pid_none(patch_singleton, patch_matches):
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(pid=None, recent_bytes=1000))  # type: ignore[arg-type]
    patch_matches(2222)
    app = _FakeApp()
    app._overlay._badge_on = True
    ticks.tick_claude_badge(app)
    assert app._overlay.hide_calls == 1


def test_tick_hides_when_agent_root_pid_none(patch_singleton, patch_matches):
    """Screen/tmux server can't be located (e.g. session not yet
    materialised) → no tree to walk → badge stays off."""
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(root_pid=None, recent_bytes=2000))
    patch_matches(2222)
    app = _FakeApp()
    app._overlay._badge_on = True
    ticks.tick_claude_badge(app)
    assert app._overlay.hide_calls == 1


def test_tick_shows_when_agent_running_and_busy(patch_singleton, patch_matches):
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(recent_bytes=2000))
    patch_matches(2222)
    app = _FakeApp()
    ticks.tick_claude_badge(app)
    assert app._overlay.show_calls == 1
    assert app._overlay.claude_badge_visible is True


def test_tick_quiet_session_keeps_badge_off(patch_singleton, patch_matches):
    """Agent is running but output rate is below threshold (idle prompt)."""
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(recent_bytes=100))   # < 500 threshold
    patch_matches(2222)
    app = _FakeApp()
    ticks.tick_claude_badge(app)
    assert app._overlay.show_calls == 0
    assert app._overlay.claude_badge_visible is False


def test_tick_noisy_without_agent_keeps_badge_off(patch_singleton, patch_matches):
    """Plain shell streaming output (no claude in tree) → badge stays off."""
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(recent_bytes=5000))
    patch_matches(None)   # no agent descendant
    app = _FakeApp()
    ticks.tick_claude_badge(app)
    assert app._overlay.show_calls == 0
    assert app._overlay.claude_badge_visible is False


def test_tick_hides_when_agent_goes_quiet(patch_singleton, patch_matches):
    """Badge currently on, output rate drops below threshold → hide."""
    from tokenmon.menubar import ticks
    sess = _FakeSession(recent_bytes=2000)
    patch_singleton(sess)
    patch_matches(2222)
    app = _FakeApp()
    ticks.tick_claude_badge(app)
    assert app._overlay.claude_badge_visible is True
    # Now the agent has finished generating.
    sess._recent = 50
    ticks.tick_claude_badge(app)
    assert app._overlay.claude_badge_visible is False
    assert app._overlay.hide_calls == 1


def test_tick_is_idempotent_when_already_on(patch_singleton, patch_matches):
    from tokenmon.menubar import ticks
    patch_singleton(_FakeSession(recent_bytes=2000))
    patch_matches(2222)
    app = _FakeApp()
    ticks.tick_claude_badge(app)
    ticks.tick_claude_badge(app)
    assert app._overlay.show_calls == 1  # not called twice

"""Tests for the Carbon-backed global hotkey wrapper.

We can't actually fire a real ⌘⇧Space from a non-graphical test run,
but the module-level safety (Carbon-missing fallback, idempotent
start/stop, bookkeeping of the CFUNCTYPE wrapper) is verified here.
The integration with the menubar driver is covered in
test_companion_app_classes via the install/uninstall hooks.
"""
from __future__ import annotations

import pytest


def test_four_char_code_packs_ascii_bigendian():
    from tokenmon.companion.hotkey import _four_char_code
    # 'keyb' = 0x6B 0x65 0x79 0x62
    assert _four_char_code("keyb") == 0x6B657962


def test_four_char_code_rejects_wrong_length():
    from tokenmon.companion.hotkey import _four_char_code
    with pytest.raises(ValueError):
        _four_char_code("oops!")


def test_modifier_bits_match_carbon_constants():
    """Sanity check — these legacy Carbon values are stable since
    System 7, but if someone ever flips them to NSEvent flags we'd
    silently register the wrong combo. Pin them explicitly."""
    from tokenmon.companion import hotkey
    assert hotkey.cmdKey == 0x100
    assert hotkey.shiftKey == 0x200
    assert hotkey.optionKey == 0x800
    assert hotkey.controlKey == 0x1000


def test_global_hotkey_construct_does_not_install():
    """Constructing a GlobalHotKey must not touch Carbon — start() does
    the registration. This matters because tests run without the
    AppKit run loop, so any premature Carbon call would either fail
    silently or surface as a flaky CI signal."""
    from tokenmon.companion.hotkey import GlobalHotKey, cmdKey, kVK_Space, shiftKey
    hk = GlobalHotKey(kVK_Space, cmdKey | shiftKey, on_press=lambda: None)
    assert not hk.is_active


def test_global_hotkey_stop_is_safe_when_never_started():
    from tokenmon.companion.hotkey import GlobalHotKey, cmdKey, kVK_Space, shiftKey
    hk = GlobalHotKey(kVK_Space, cmdKey | shiftKey, on_press=lambda: None)
    hk.stop()  # no crash, no-op
    assert not hk.is_active


def test_global_hotkey_ids_are_unique_per_instance():
    """Each instance must claim a fresh id so a future multi-hotkey
    setup can route correctly. Internal detail but cheap to lock down."""
    from tokenmon.companion.hotkey import GlobalHotKey, cmdKey, kVK_Space, shiftKey
    a = GlobalHotKey(kVK_Space, cmdKey, on_press=lambda: None)
    b = GlobalHotKey(kVK_Space, cmdKey | shiftKey, on_press=lambda: None)
    assert a._id != b._id


def test_start_returns_false_when_carbon_unavailable(monkeypatch):
    """On non-macOS platforms (or stripped builds) Carbon won't load.
    start() must degrade to False, not raise — callers rely on that
    to keep the rest of Tokenmon working without a hotkey."""
    from tokenmon.companion import hotkey
    monkeypatch.setattr(hotkey, "_carbon", None)
    hk = hotkey.GlobalHotKey(
        hotkey.kVK_Space, hotkey.cmdKey, on_press=lambda: None,
    )
    assert hk.start() is False
    assert hk.is_active is False

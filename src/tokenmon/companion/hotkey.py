"""System-wide keyboard hotkey for the companion chat.

Uses Carbon's ``RegisterEventHotKey`` (via ``ctypes``) — the canonical
way to install a *true* global hotkey on macOS. Unlike
``NSEvent.addGlobalMonitorForEventsMatchingMask_``, this:

- Intercepts the event so the focused app does NOT also receive it.
- Does NOT require Accessibility / Input Monitoring permission.
- Delivers the event on the AppKit main thread (Carbon's HIToolbox
  shares its event target with the application event loop, which is
  what NSApp.run() spins).

We only need a tiny slice of the Carbon Event Manager — register one
hotkey, install one event handler. The rest of Carbon stays untouched
(and stays deprecated).

Virtual keycodes are the same ones AppKit and HIToolbox have always
used; ``49`` is Space. Modifier bits are the legacy Carbon values
(``cmdKey = 1 << 8``, ``shiftKey = 1 << 9``, …) — they intentionally
differ from the NSEvent modifier flags.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    byref,
    c_int32,
    c_uint32,
    c_void_p,
)
from typing import Callable

log = logging.getLogger("tokenmon.companion.hotkey")

# Carbon modifier bits (HIToolbox/Events.h). NOT the same as NSEvent's
# NSEventModifierFlags — Carbon uses its own legacy layout.
cmdKey = 1 << 8        # 256
shiftKey = 1 << 9      # 512
optionKey = 1 << 11    # 2048
controlKey = 1 << 12   # 4096

# Common virtual keycodes. The full set lives in HIToolbox/Events.h
# (kVK_*); we expose just what we use.
kVK_Space = 49
kVK_ANSI_T = 17
kVK_ANSI_Grave = 50  # the ` (backtick) key

# Carbon FourCharCode helpers — Carbon event class/kind constants are
# 32-bit big-endian packings of four ASCII chars (e.g. 'keyb').
def _four_char_code(s: str) -> int:
    if len(s) != 4:
        raise ValueError(f"FourCharCode needs 4 chars, got {s!r}")
    return int.from_bytes(s.encode("ascii"), "big")


kEventClassKeyboard = _four_char_code("keyb")
kEventHotKeyPressed = 5


class _EventTypeSpec(Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


class _EventHotKeyID(Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


# EventHandlerProcPtr: OSStatus (*)(EventHandlerCallRef, EventRef, void*)
_EventHandlerProc = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)


def _load_carbon() -> ctypes.CDLL | None:
    """Load Carbon.framework. Returns None on platforms / builds where
    it isn't available, so callers can degrade gracefully (the rest of
    Tokenmon still works without a global hotkey)."""
    path = ctypes.util.find_library("Carbon")
    if path is None:
        return None
    try:
        return ctypes.CDLL(path)
    except OSError:
        log.exception("Carbon framework load failed")
        return None


_carbon = _load_carbon()
if _carbon is not None:
    # RegisterEventHotKey(UInt32 code, UInt32 mods, EventHotKeyID id,
    #                     EventTargetRef target, OptionBits opts,
    #                     EventHotKeyRef *outRef) -> OSStatus
    _carbon.RegisterEventHotKey.argtypes = [
        c_uint32, c_uint32, _EventHotKeyID, c_void_p, c_uint32, POINTER(c_void_p),
    ]
    _carbon.RegisterEventHotKey.restype = c_int32

    # UnregisterEventHotKey(EventHotKeyRef ref) -> OSStatus
    _carbon.UnregisterEventHotKey.argtypes = [c_void_p]
    _carbon.UnregisterEventHotKey.restype = c_int32

    # InstallEventHandler(EventTargetRef, EventHandlerUPP, ItemCount,
    #                     const EventTypeSpec*, void*, EventHandlerRef*)
    _carbon.InstallEventHandler.argtypes = [
        c_void_p, _EventHandlerProc, c_uint32,
        POINTER(_EventTypeSpec), c_void_p, POINTER(c_void_p),
    ]
    _carbon.InstallEventHandler.restype = c_int32

    # RemoveEventHandler(EventHandlerRef) -> OSStatus
    _carbon.RemoveEventHandler.argtypes = [c_void_p]
    _carbon.RemoveEventHandler.restype = c_int32

    # GetApplicationEventTarget() -> EventTargetRef
    _carbon.GetApplicationEventTarget.argtypes = []
    _carbon.GetApplicationEventTarget.restype = c_void_p

    # GetEventParameter — used to read the EventHotKeyID back out of the
    # event so we can route to the right callback when several hotkeys
    # share a handler. We register one handler per GlobalHotKey instance
    # so this is informational only, but it's good practice.
    _carbon.GetEventParameter.argtypes = [
        c_void_p, c_uint32, c_uint32, POINTER(c_uint32),
        c_uint32, POINTER(c_uint32), c_void_p,
    ]
    _carbon.GetEventParameter.restype = c_int32


_HOTKEY_SIGNATURE = _four_char_code("TKMN")  # "Tokenmon"
_next_hotkey_id = 1


class GlobalHotKey:
    """Install a system-wide hotkey that intercepts a single key combo.

    Usage::

        hk = GlobalHotKey(kVK_Space, cmdKey | shiftKey, on_press=callback)
        hk.start()
        ...
        hk.stop()

    ``on_press`` runs on the AppKit main thread (Carbon dispatches via
    the app's event target, which is the same run loop NSApp.run()
    spins). Exceptions inside the callback are logged but do not
    propagate into Carbon, so a buggy handler can't poison the event
    loop.

    On platforms / builds where Carbon is unavailable, ``start()`` is a
    no-op and ``is_active`` stays False — callers can still use the
    object as a context-managerish stub without special-casing the
    import.
    """

    def __init__(
        self,
        key_code: int,
        modifiers: int,
        on_press: Callable[[], None],
    ) -> None:
        global _next_hotkey_id
        self._key_code = int(key_code)
        self._modifiers = int(modifiers)
        self._on_press = on_press
        # Each instance gets a unique id so a future multi-hotkey setup
        # can route events. The signature stays constant (it's the
        # 'app' the hotkey belongs to, in Carbon terms).
        self._id = _next_hotkey_id
        _next_hotkey_id += 1
        # Carbon handles + the CFUNCTYPE wrapper. The wrapper MUST be
        # kept alive on self — ctypes does not retain CFUNCTYPE
        # callbacks, and a GC'd handler would segfault the event loop.
        self._hotkey_ref: c_void_p | None = None
        self._handler_ref: c_void_p | None = None
        self._handler_proc: _EventHandlerProc | None = None

    @property
    def is_active(self) -> bool:
        return self._hotkey_ref is not None

    def start(self) -> bool:
        """Install the hotkey. Returns True on success, False if Carbon
        is unavailable or the registration failed (already taken by
        another app, invalid keycode, …)."""
        if self._hotkey_ref is not None:
            return True
        if _carbon is None:
            log.warning("Carbon unavailable — hotkey not installed")
            return False

        target = _carbon.GetApplicationEventTarget()
        if not target:
            log.warning("GetApplicationEventTarget returned NULL")
            return False

        # Install the event handler first. If hotkey registration fails
        # we still want to leave the handler uninstalled, hence the
        # rollback in the error path.
        spec = _EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)

        def _trampoline(_next_ref, _event_ref, _user_data):
            try:
                self._on_press()
            except Exception:
                log.exception("global hotkey callback raised")
            # Returning 0 (noErr) tells Carbon we consumed the event —
            # the focused app will NOT receive Cmd-Shift-Space.
            return 0

        proc = _EventHandlerProc(_trampoline)
        handler_ref = c_void_p()
        status = _carbon.InstallEventHandler(
            target, proc, 1, byref(spec), None, byref(handler_ref),
        )
        if status != 0:
            log.warning("InstallEventHandler failed (OSStatus=%d)", status)
            return False
        # Keep both the CFUNCTYPE wrapper and the Carbon handle alive.
        self._handler_proc = proc
        self._handler_ref = handler_ref

        hotkey_id = _EventHotKeyID(_HOTKEY_SIGNATURE, c_uint32(self._id).value)
        hotkey_ref = c_void_p()
        status = _carbon.RegisterEventHotKey(
            c_uint32(self._key_code),
            c_uint32(self._modifiers),
            hotkey_id,
            target,
            0,
            byref(hotkey_ref),
        )
        if status != 0:
            log.warning(
                "RegisterEventHotKey failed (OSStatus=%d) — combo likely in use",
                status,
            )
            # Roll the handler back so we don't leak it; a failed
            # registration with an installed handler would also mean
            # every keyboard hotkey from other apps in our process
            # would invoke our trampoline.
            try:
                _carbon.RemoveEventHandler(self._handler_ref)
            except Exception:
                log.exception("RemoveEventHandler rollback failed")
            self._handler_ref = None
            self._handler_proc = None
            return False

        self._hotkey_ref = hotkey_ref
        log.info(
            "global hotkey registered (code=%d mods=0x%x id=%d)",
            self._key_code, self._modifiers, self._id,
        )
        return True

    def stop(self) -> None:
        if _carbon is None:
            return
        if self._hotkey_ref is not None:
            try:
                _carbon.UnregisterEventHotKey(self._hotkey_ref)
            except Exception:
                log.exception("UnregisterEventHotKey failed")
            self._hotkey_ref = None
        if self._handler_ref is not None:
            try:
                _carbon.RemoveEventHandler(self._handler_ref)
            except Exception:
                log.exception("RemoveEventHandler failed")
            self._handler_ref = None
        # Release the CFUNCTYPE wrapper last — ctypes will only free
        # its trampoline thunk when this reference drops.
        self._handler_proc = None

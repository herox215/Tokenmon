"""WKWebView wrapper hosting xterm.js, bridged to a ``ClaudeSession``.

The view is the *only* AppKit surface in this package — every other piece
of ``claude_session`` is plain Python and importable on Linux for unit
tests. ``import``ing this module on a non-macOS box would raise on the
WebKit import; we therefore lazy-import the AppKit bits inside the class
(rather than at module top-level) so test collection still works.

JS ↔ Python bridge:

    JS  → Python    webkit.messageHandlers.input.postMessage(str)   → session.write
    JS  → Python    webkit.messageHandlers.resize.postMessage(dict) → session.resize
    JS  → Python    webkit.messageHandlers.ready.postMessage(null)  → flush buffer
    Python → JS     webview.evaluateJavaScript("window.feedBytes('<b64>')", None)

The ready signal exists because ``claude``'s startup banner (and its
splash screen) races ahead of xterm.js's ``term.open()``. Without
buffering the first ~kilobyte we'd render a half-blank window for a
second.
"""
from __future__ import annotations

import base64
import logging
import threading
from pathlib import Path

from .session import ClaudeSession

log = logging.getLogger("tokenmon.claude_session.terminal_view")


def _assets_dir() -> Path:
    """Path to the bundled xterm.js assets — same layout in dev and in the
    py2app alias bundle since alias mode points at the source tree."""
    return Path(__file__).resolve().parent.parent / "assets" / "terminal"


_message_handler_cls = None


def _make_message_handler():
    """Return a cached NSObject subclass that forwards WKScriptMessage
    bodies to a Python callable.

    Defining the class lazily lets us import ``terminal_view`` on a
    non-macOS host without blowing up at module load (PyObjC is only
    available on macOS). It MUST be cached: the ObjC runtime tracks
    classes globally by name, so re-defining ``_MessageHandler`` on every
    chat-window open trips ``_MessageHandler is overriding existing
    Objective-C class``.
    """
    global _message_handler_cls
    if _message_handler_cls is not None:
        return _message_handler_cls

    import objc  # type: ignore
    from Foundation import NSObject  # type: ignore

    class _MessageHandler(NSObject):
        def initWithCallback_(self, cb):  # noqa: N802 — Cocoa selector
            # PyObjC requires going through ``objc.super(...).init()``
            # rather than ``NSObject.init(self)`` — the latter raises
            # ``TypeError: Need 0 arguments, got 1`` because PyObjC's
            # bridge expects the unbound selector to be invoked via super.
            self = objc.super(_MessageHandler, self).init()
            if self is None:
                return None
            self._cb = cb
            return self

        # WKScriptMessageHandler protocol selector. The body is whatever
        # JS passed to ``postMessage`` — typically NSString or NSDictionary
        # depending on the channel.
        def userContentController_didReceiveScriptMessage_(  # noqa: N802
            self, _controller, message,
        ):
            try:
                self._cb(message.body())
            except Exception:
                log.exception("script message handler raised")

    _message_handler_cls = _MessageHandler
    return _message_handler_cls


class TerminalWebView:
    """A WKWebView running xterm.js, wired to a ClaudeSession.

    The caller is responsible for placing ``self.view`` in an AppKit
    hierarchy and making it first-responder when the chat panel becomes
    visible. ``detach()`` removes the session listener and the JS bridges
    so the view can be torn down without leaks.
    """

    def __init__(self, session: ClaudeSession, frame) -> None:
        # Imported lazily so plain ``import tokenmon.claude_session`` works
        # in test environments without WebKit installed.
        from WebKit import (  # type: ignore
            WKWebView, WKWebViewConfiguration,
        )
        from Foundation import NSURL  # type: ignore

        self._session = session
        self._lock = threading.Lock()
        self._ready = False
        self._pending_output: list[bytes] = []

        config = WKWebViewConfiguration.alloc().init()
        # We're loading bundled local files — no remote pages, no cookies
        # to worry about. Default config is fine; we only customise the
        # message handlers below.
        ucc = config.userContentController()

        handler_cls = _make_message_handler()
        self._input_handler = handler_cls.alloc().initWithCallback_(self._on_input)
        self._resize_handler = handler_cls.alloc().initWithCallback_(self._on_resize)
        self._ready_handler = handler_cls.alloc().initWithCallback_(self._on_ready)
        ucc.addScriptMessageHandler_name_(self._input_handler, "input")
        ucc.addScriptMessageHandler_name_(self._resize_handler, "resize")
        ucc.addScriptMessageHandler_name_(self._ready_handler, "ready")

        self._webview = WKWebView.alloc().initWithFrame_configuration_(frame, config)
        self._webview.setAllowsBackForwardNavigationGestures_(False)
        # The web view's content is a black terminal panel; let it draw
        # under the surrounding HUD blur without flashing white on load.
        try:
            self._webview.setValue_forKey_(False, "drawsBackground")
        except Exception:
            # Non-critical — older macOS will simply paint white briefly.
            pass

        html = _assets_dir() / "terminal.html"
        page_url = NSURL.fileURLWithPath_(str(html))
        # Allow read access to the parent so xterm.js / fit-addon / css
        # alongside terminal.html can be fetched. Without this WKWebView
        # blocks file:// sub-resource loads.
        root_url = NSURL.fileURLWithPath_(str(html.parent))
        self._webview.loadFileURL_allowingReadAccessToURL_(page_url, root_url)

        # Subscribe AFTER the load kicks off so we definitely receive any
        # output that arrives while the page is parsing. Buffered until
        # ``ready`` flips, then flushed in order.
        session.add_listener(self._on_session_output)

    @property
    def view(self):
        """The NSView (actually WKWebView) to install in the panel."""
        return self._webview

    def detach(self) -> None:
        """Stop listening to the session and unregister JS bridges.

        The PTY itself keeps running — only the view is going away. Safe
        to call multiple times.
        """
        try:
            self._session.remove_listener(self._on_session_output)
        except Exception:
            log.exception("removing session listener failed")
        try:
            ucc = self._webview.configuration().userContentController()
            for name in ("input", "resize", "ready"):
                ucc.removeScriptMessageHandlerForName_(name)
        except Exception:
            log.exception("removing script message handlers failed")

    # --- session → JS --------------------------------------------------

    def _on_session_output(self, data: bytes) -> None:
        """Listener installed on the ClaudeSession. Runs on its reader thread."""
        with self._lock:
            if not self._ready:
                self._pending_output.append(data)
                return
        self._enqueue_feed(data)

    def _enqueue_feed(self, data: bytes) -> None:
        # ``window.feedBytes`` (defined in main.js) decodes the base64,
        # builds a Uint8Array, and hands that to ``term.write``. xterm.js
        # treats Uint8Array as raw UTF-8 bytes — passing the
        # ``atob`` binary string directly would make claude's box-drawing
        # characters render as Latin-1 garbage.
        b64 = base64.b64encode(data).decode("ascii")
        js = f"window.feedBytes('{b64}')"

        webview = self._webview

        def _run():
            try:
                webview.evaluateJavaScript_completionHandler_(js, None)
            except Exception:
                log.exception("evaluateJavaScript(feedBytes) failed")

        try:
            from Foundation import NSOperationQueue  # type: ignore
            NSOperationQueue.mainQueue().addOperationWithBlock_(_run)
        except Exception:
            log.exception("main-thread dispatch failed; dropping output chunk")

    # --- JS → session --------------------------------------------------

    def _on_ready(self, _body) -> None:
        with self._lock:
            self._ready = True
            pending = self._pending_output
            self._pending_output = []
        for chunk in pending:
            self._enqueue_feed(chunk)

    def _on_input(self, body) -> None:
        try:
            data = str(body).encode("utf-8")
        except Exception:
            log.exception("input bridge encode failed")
            return
        self._session.write(data)

    def _on_resize(self, body) -> None:
        try:
            rows = int(body["rows"])
            cols = int(body["cols"])
        except Exception:
            log.exception("resize bridge parse failed (body=%r)", body)
            return
        self._session.resize(rows, cols)

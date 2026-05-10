"""Universal macOS context provider via window screenshot + Vision OCR.

Why this exists: AppleScript / per-app remote control covers maybe ten
apps and needs ten separate Automation prompts. Screen Recording asks
**once** and works on every window the user can see — Kitty without
remote control, Slack, VS Code, Notion, Spotify, even games. Inhalt
ist nicht strukturiert (kein DOM, kein cwd) — dafür kostenlos universell.

Pipeline:
    1. focused_window_bounds(pid) → window_id, rect, title
    2. CGWindowListCreateImage(IncludingWindow, window_id) → CGImage
    3. VNRecognizeTextRequest on the CGImage → list of text observations
    4. Sort observations top-to-bottom, left-to-right → joined text

Vision is local, offline, and ships with macOS — no extra binary, no
network call. Recognition runs ~150–300 ms for a typical chat-window-
sized capture, ~500 ms for a fullscreen IDE.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..snapshot import ContextSnapshot

if TYPE_CHECKING:
    from typing import Any

log = logging.getLogger("tokenmon.context.providers.macos_screenshot")


_PYOBJC_OK: bool
try:  # PyObjC frameworks are macOS-only; importing must never blow up tests.
    from Quartz import (
        CGPreflightScreenCaptureAccess,
        CGRectNull,
        CGWindowListCopyWindowInfo,
        CGWindowListCreateImage,
        kCGNullWindowID,
        kCGWindowImageBoundsIgnoreFraming,
        kCGWindowListExcludeDesktopElements,
        kCGWindowListOptionIncludingWindow,
        kCGWindowListOptionOnScreenOnly,
    )
    from Vision import (
        VNImageRequestHandler,
        VNRecognizeTextRequest,
        VNRequestTextRecognitionLevelAccurate,
        VNRequestTextRecognitionLevelFast,
    )

    _PYOBJC_OK = True
except Exception:  # pragma: no cover — non-macOS / missing framework
    _PYOBJC_OK = False
    log.debug("Vision/Quartz unavailable; ScreenshotOCRProvider will no-op")


# Languages Vision tries to recognise. Order matters — first language is
# preferred when ambiguous. Add more if the user works in others.
_RECOGNITION_LANGUAGES = ["de-DE", "en-US"]

# Fast level is ~3× quicker than Accurate and good enough for terminal
# fonts and UI labels. Switch via constructor arg if needed.
_DEFAULT_RECOGNITION_LEVEL_FAST = True


def has_screen_recording_permission() -> bool:
    """Returns True iff the user has already granted Screen Recording.
    On a fresh install this is False until the system dialog is
    accepted. Calling CGWindowListCreateImage when this returns False
    yields a black/desktop-only image — useful as a permission check
    that doesn't itself spawn a dialog."""
    if not _PYOBJC_OK:
        return False
    try:
        return bool(CGPreflightScreenCaptureAccess())
    except Exception:
        log.exception("CGPreflightScreenCaptureAccess failed")
        return False


def request_screen_recording_permission() -> None:
    """Trigger the system Screen Recording prompt. macOS only shows it
    once per install — after that the user has to flip the switch in
    System Settings → Privacy & Security."""
    if not _PYOBJC_OK:
        return
    try:
        # CGRequestScreenCaptureAccess is the canonical API but PyObjC
        # exposes it lazily; importing here keeps top-level import side-
        # effect-free on systems where it's missing.
        from Quartz import CGRequestScreenCaptureAccess  # type: ignore

        CGRequestScreenCaptureAccess()
    except Exception:
        log.exception("CGRequestScreenCaptureAccess failed")


def _focused_window_for_pid(pid: int) -> tuple[int, str | None] | None:
    """Look up the topmost on-screen layer-0 window owned by ``pid``
    and return ``(window_id, title)``. Title comes from
    ``kCGWindowName`` which itself requires Screen Recording permission;
    when permission is missing the field is empty/None."""
    try:
        info_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
    except Exception:
        log.exception("CGWindowListCopyWindowInfo failed")
        return None
    if info_list is None:
        return None
    for entry in info_list:
        try:
            owner_pid = int(entry.get("kCGWindowOwnerPID", -1))
            layer = int(entry.get("kCGWindowLayer", -1))
        except Exception:
            continue
        if owner_pid != int(pid) or layer != 0:
            continue
        try:
            alpha = float(entry.get("kCGWindowAlpha", 1.0))
        except Exception:
            alpha = 1.0
        if alpha < 0.5:
            continue
        try:
            window_id = int(entry.get("kCGWindowNumber", 0))
        except Exception:
            continue
        if window_id == 0:
            continue
        title = entry.get("kCGWindowName") or None
        if title is not None:
            title = str(title) or None
        return window_id, title
    return None


def _capture_window_image(window_id: int):
    """Grab a CGImage of just the window with the given CG window-id.
    Returns None if capture fails (typical cause: Screen Recording
    permission not granted — image comes back as ``None`` or a tiny
    blank rect)."""
    try:
        img = CGWindowListCreateImage(
            CGRectNull,
            kCGWindowListOptionIncludingWindow,
            window_id,
            kCGWindowImageBoundsIgnoreFraming,
        )
    except Exception:
        log.exception("CGWindowListCreateImage failed")
        return None
    return img


def _recognise_text(cg_image, *, fast: bool = _DEFAULT_RECOGNITION_LEVEL_FAST) -> str | None:
    """Run Vision text recognition on a CGImage and return the text
    sorted in natural reading order. Returns None on failure or when
    Vision finds no text."""
    try:
        req = VNRecognizeTextRequest.alloc().init()
        try:
            level = (
                VNRequestTextRecognitionLevelFast
                if fast
                else VNRequestTextRecognitionLevelAccurate
            )
            req.setRecognitionLevel_(level)
            # Disable language-correction — preserves code, paths,
            # commands. Especially important for terminal scrapes.
            req.setUsesLanguageCorrection_(False)
            try:
                req.setRecognitionLanguages_(_RECOGNITION_LANGUAGES)
            except Exception:
                pass  # older macOS doesn't expose this setter
        except Exception:
            log.exception("Vision request configuration failed")

        handler = VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, {},
        )
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            log.info("Vision performRequests failed: %s", err)
            return None
    except Exception:
        log.exception("Vision recognition failed")
        return None

    observations = req.results() or []
    if not observations:
        return None

    # Sort by Y descending (top first), then X ascending. boundingBox
    # is normalized 0..1 with origin at bottom-left.
    rows: list[tuple[float, float, str]] = []
    for obs in observations:
        try:
            box = obs.boundingBox()
            y = float(box.origin.y) + float(box.size.height)
            x = float(box.origin.x)
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            text = str(candidates[0].string())
        except Exception:
            continue
        if not text.strip():
            continue
        rows.append((y, x, text))

    if not rows:
        return None

    # Group lines whose tops are within 1.5% of each other into the
    # same row, then within a row sort by X. 1.5% of normalized height
    # ≈ 12 px on a 800 px-tall window — close enough to merge wraps.
    rows.sort(key=lambda r: (-r[0], r[1]))
    merged: list[list[tuple[float, str]]] = []
    last_y: float | None = None
    for y, x, text in rows:
        if last_y is not None and abs(y - last_y) < 0.015:
            merged[-1].append((x, text))
        else:
            merged.append([(x, text)])
            last_y = y
    lines = []
    for row in merged:
        row.sort(key=lambda c: c[0])
        lines.append(" ".join(t for _, t in row))
    return "\n".join(lines).strip() or None


# Mapping for nicer kind labels — purely cosmetic, doesn't affect
# capture. Falls back to "generic" for unknown apps.
_KIND_BY_BUNDLE: dict[str, str] = {
    "com.apple.Safari": "browser",
    "com.google.Chrome": "browser",
    "com.brave.Browser": "browser",
    "company.thebrowser.Browser": "browser",  # Arc
    "org.mozilla.firefox": "browser",
    "com.apple.Terminal": "terminal",
    "com.googlecode.iterm2": "terminal",
    "net.kovidgoyal.kitty": "terminal",
    "com.github.wez.wezterm": "terminal",
    "org.alacritty": "terminal",
    "io.alacritty": "terminal",
    "com.mitchellh.ghostty": "terminal",
    "com.microsoft.VSCode": "editor",
    "com.apple.dt.Xcode": "editor",
    "com.apple.finder": "file_manager",
}


def _app_name_from_bundle(app_id: str) -> str:
    # NSWorkspace can resolve this but costs an extra API call; the tail
    # of the bundle id is good enough and matches what we already show.
    if "." not in app_id:
        return app_id or "Window"
    return app_id.rsplit(".", 1)[-1].title()


class ScreenshotOCRProvider:
    """Universal: matches every app, captures a screenshot of the
    focused window, OCRs it via Apple's Vision framework."""

    name = "macos_screenshot_ocr"

    def __init__(self, *, fast: bool = _DEFAULT_RECOGNITION_LEVEL_FAST) -> None:
        self._fast = fast

    def supports(self, app_id: str) -> bool:
        return _PYOBJC_OK and bool(app_id)

    def snapshot(self, app_id: str, pid: int) -> ContextSnapshot | None:
        if not _PYOBJC_OK:
            return None
        kind = _KIND_BY_BUNDLE.get(app_id, "generic")
        app_name = _app_name_from_bundle(app_id)

        # Permission check first — without Screen Recording the capture
        # silently returns either nothing or a desktop-only image.
        # Surface this as an explicit "permission missing" snapshot so
        # the chat header can prompt the user.
        if not has_screen_recording_permission():
            return ContextSnapshot(
                app_name=app_name,
                app_id=app_id,
                kind=kind,
                text=None,
                source=f"{self.name}:no-permission",
            )

        focused = _focused_window_for_pid(pid)
        if focused is None:
            return None
        window_id, title = focused

        img = _capture_window_image(window_id)
        if img is None:
            return None

        text = _recognise_text(img, fast=self._fast)

        if text is None and title is None:
            return None

        return ContextSnapshot(
            app_name=app_name,
            app_id=app_id,
            kind=kind,
            window_title=title,
            text=text,
            source=self.name,
        )

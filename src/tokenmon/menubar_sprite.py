"""Animate a Pokemon GIF in the macOS menubar by ticking frames manually.

macOS does not animate NSImages in NSStatusItem on its own, so we extract each
GIF frame as a separate NSImage and swap them on an NSTimer.
"""

from __future__ import annotations

import logging
from pathlib import Path

import objc
from AppKit import (
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSImage,
    NSTimer,
)
from Foundation import NSData, NSObject

log = logging.getLogger("tokenmon.menubar_sprite")

ICON_SIZE = 22  # menubar height in pt
MIN_FRAME_DURATION = 0.05


def _extract_frames(gif_path: Path) -> tuple[list[NSImage], list[float]]:
    data = NSData.dataWithContentsOfFile_(str(gif_path))
    if data is None:
        return [], []
    source = NSBitmapImageRep.imageRepWithData_(data)
    if source is None:
        return [], []
    count_obj = source.valueForProperty_("NSImageFrameCount")
    try:
        count = max(1, int(count_obj)) if count_obj is not None else 1
    except (TypeError, ValueError):
        count = 1
    frames: list[NSImage] = []
    durations: list[float] = []
    for i in range(count):
        source.setProperty_withValue_("NSImageCurrentFrame", i)
        png = source.representationUsingType_properties_(NSBitmapImageFileTypePNG, None)
        img = NSImage.alloc().initWithData_(png)
        if img is None:
            continue
        img.setSize_((ICON_SIZE, ICON_SIZE))
        img.setTemplate_(False)
        frames.append(img)
        d_obj = source.valueForProperty_("NSImageCurrentFrameDuration")
        try:
            d = float(d_obj) if d_obj is not None else 0.1
        except (TypeError, ValueError):
            d = 0.1
        durations.append(max(MIN_FRAME_DURATION, d))
    return frames, durations


class SpriteAnimator(NSObject):
    """NSObject so we can use NSTimer's Obj-C selector mechanism."""

    def initWithGifPath_setter_(self, gif_path, setter):  # noqa: N802
        self = objc.super(SpriteAnimator, self).init()
        if self is None:
            return None
        self._frames, self._durations = _extract_frames(Path(gif_path))
        self._set_image = setter
        self._idx = 0
        self._timer = None
        if self._frames:
            self._set_image(self._frames[0])
            self._schedule()
        else:
            log.warning("no frames extracted from %s", gif_path)
        return self

    def _schedule(self):
        delay = self._durations[self._idx]
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay, self, b"tick:", None, False
        )

    def tick_(self, _timer):  # noqa: N802
        if not self._frames:
            return
        self._idx = (self._idx + 1) % len(self._frames)
        try:
            self._set_image(self._frames[self._idx])
        except Exception:
            log.exception("set_image failed")
        self._schedule()

    def stop(self):
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

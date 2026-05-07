"""HP-driven GIF-playback speed.

Sprite GIFs ship with per-frame durations baked in. NSImageView's
auto-animation reads those durations directly — there's no public
setter for "play at 0.5×". The trick is mutating the NSBitmapImageRep
frame-durations in place: walk frames, multiply each duration by
``1 / speed``, and the next ``setImage_`` picks up the new pacing.

Speed curve:
- ≥ 80 % HP   → 1.0  (full speed)
- 0..80 % HP  → linear ramp from 0.25 (at 0%) to 1.0 (at 80%)

Below 80 % the animation drags noticeably, signalling "this Pokémon
isn't healthy"; below 20 % it's near-frozen.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("tokenmon.sprite_speed")


HEALTHY_THRESHOLD = 0.8
MIN_SPEED = 0.25


def hp_playback_speed(hp_current: int | None, hp_max: int) -> float:
    """Return a 0.25..1.0 multiplier for the GIF's frame durations.

    ``hp_current is None`` is treated as "full HP" (the same convention
    storage uses for never-damaged Pokémon).
    """
    if hp_max <= 0:
        return 1.0
    if hp_current is None:
        return 1.0
    pct = max(0.0, min(1.0, hp_current / hp_max))
    if pct >= HEALTHY_THRESHOLD:
        return 1.0
    # Linear from MIN_SPEED at 0% to 1.0 at HEALTHY_THRESHOLD.
    return MIN_SPEED + (pct / HEALTHY_THRESHOLD) * (1.0 - MIN_SPEED)


def apply_playback_speed(image, speed: float) -> None:
    """Mutate ``image``'s NSBitmapImageRep frame-durations so playback
    runs at the given speed multiplier. ``speed=1.0`` is a no-op."""
    if image is None:
        return
    if speed <= 0 or abs(speed - 1.0) < 0.02:
        return
    try:
        from AppKit import NSBitmapImageRep
    except Exception:
        return
    factor = 1.0 / speed
    for rep in image.representations():
        if not isinstance(rep, NSBitmapImageRep):
            continue
        try:
            nframes = rep.valueForProperty_("NSImageFrameCount")
        except Exception:
            continue
        if nframes is None:
            continue
        try:
            n = int(nframes)
        except Exception:
            continue
        if n <= 0:
            continue
        # Save then restore the displayed frame so we don't leave the
        # rep pointing at frame N-1 (which the next animation cycle
        # would pick up before NSImageView resets it).
        try:
            saved = rep.valueForProperty_("NSImageCurrentFrame")
        except Exception:
            saved = 0
        for i in range(n):
            try:
                rep.setProperty_withValue_("NSImageCurrentFrame", i)
                dur = rep.valueForProperty_("NSImageCurrentFrameDuration")
                if dur is None:
                    continue
                rep.setProperty_withValue_(
                    "NSImageCurrentFrameDuration",
                    float(dur) * factor,
                )
            except Exception:
                # Skip the bad frame, keep going. A typo in one frame
                # shouldn't tank the whole animation.
                continue
        try:
            rep.setProperty_withValue_(
                "NSImageCurrentFrame", int(saved) if saved is not None else 0,
            )
        except Exception:
            pass


def load_animated_image(path: Path | str, speed: float = 1.0):
    """Load an NSImage from disk and apply ``speed`` to its animation.
    Returns the NSImage (or None on load failure)."""
    try:
        from AppKit import NSImage
    except Exception:
        return None
    img = NSImage.alloc().initWithContentsOfFile_(str(path))
    if img is None:
        return None
    if speed != 1.0:
        apply_playback_speed(img, speed)
    return img

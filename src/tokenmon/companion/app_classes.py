"""Pure classification of macOS bundle IDs into engagement categories.

Used by the companion overlay to decide whether the Pokémon should face
the screen content (back sprite) or face the user (front sprite). Pure
function — no AppKit imports — so it's trivially testable and reusable.
"""
from __future__ import annotations

# Bundle IDs that count as "engagement" — apps where the user is reading
# or producing content, so the Pokémon turns to face the screen.
#
# Heuristic, not exhaustive. Unknown apps fall through to "idle" which
# means the Pokémon stays facing the user (front sprite).
ENGAGEMENT_BUNDLE_IDS: frozenset[str] = frozenset({
    # Terminals
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "dev.warp.Warp-Stable",
    "co.zeit.hyper",
    "net.kovidgoyal.kitty",
    "io.alacritty",
    # Editors / IDEs
    "com.microsoft.VSCode",
    "com.sublimetext.4",
    "com.sublimetext.3",
    "com.jetbrains.intellij",
    "com.jetbrains.intellij.ce",
    "com.jetbrains.pycharm",
    "com.jetbrains.pycharm.ce",
    "com.jetbrains.WebStorm",
    "com.jetbrains.goland",
    "com.jetbrains.rider",
    "com.todesktop.230313mzl4w4u92",  # Cursor
    "dev.zed.Zed",
    "com.panic.Nova",
    "abnerworks.Typora",
    # Browsers (assumed reading/researching)
    "com.apple.Safari",
    "com.google.Chrome",
    "com.google.Chrome.canary",
    "org.mozilla.firefox",
    "org.mozilla.firefoxdeveloperedition",
    "com.brave.Browser",
    "com.microsoft.edgemac",
    "company.thebrowser.Browser",  # Arc
    # Writing / notes
    "com.apple.TextEdit",
    "com.apple.Notes",
    "md.obsidian",
    "com.literatureandlatte.scrivener3",
    "com.bear-writer",
    "notion.id",
    "com.linear",
})


def classify(bundle_id: str | None) -> str:
    """Return ``"engagement"`` if the user is content-engaged in this app
    (Pokémon should face screen content), else ``"idle"`` (Pokémon faces
    the user). ``None`` (no foreground app / system-level focus) → idle.
    """
    if not bundle_id:
        return "idle"
    return "engagement" if bundle_id in ENGAGEMENT_BUNDLE_IDS else "idle"

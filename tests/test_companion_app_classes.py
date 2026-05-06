"""Pure tests for the companion's bundle-id → engagement classifier."""
from __future__ import annotations

import pytest

from tokenmon.companion.app_classes import classify, ENGAGEMENT_BUNDLE_IDS


@pytest.mark.parametrize("bundle_id", [
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "com.microsoft.VSCode",
    "com.apple.Safari",
    "md.obsidian",
])
def test_engagement_apps_classify_as_engagement(bundle_id):
    assert classify(bundle_id) == "engagement"


@pytest.mark.parametrize("bundle_id", [
    "com.apple.finder",
    "com.apple.systempreferences",
    "com.apple.Music",
    "com.apple.weather",
    "com.spotify.client",
])
def test_non_engagement_apps_classify_as_idle(bundle_id):
    assert classify(bundle_id) == "idle"


def test_none_bundle_id_classifies_as_idle():
    assert classify(None) == "idle"


def test_empty_string_classifies_as_idle():
    assert classify("") == "idle"


def test_engagement_set_is_frozen():
    """Defensive — preventing accidental mutation at runtime."""
    assert isinstance(ENGAGEMENT_BUNDLE_IDS, frozenset)
    with pytest.raises(AttributeError):
        ENGAGEMENT_BUNDLE_IDS.add("com.evil.app")  # type: ignore[attr-defined]

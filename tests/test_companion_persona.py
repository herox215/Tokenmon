"""Tests for the companion-chat persona / system-prompt builder."""
from __future__ import annotations

from tokenmon.companion.persona import (
    CompanionIdentity,
    build_system_prompt,
    style_for_nature,
)
from tokenmon.context.snapshot import ContextSnapshot
from tokenmon.pokemon.data import NATURES


def test_every_nature_has_a_style_descriptor():
    """Every nature in ``pokemon.data.NATURES`` must have a mapped tone —
    a missing entry would fall through to the generic default and the
    Pokémon would lose its voice."""
    seen = set()
    for entry in NATURES:
        s = style_for_nature(entry["name"])
        assert isinstance(s, str) and len(s) > 0
        seen.add(entry["name"])
    # All 25 canonical names must round-trip through the lookup.
    assert len(seen) == 25


def test_style_lookup_is_case_insensitive():
    # Stale rows in the wild have been seen in lowercase; the prompt
    # builder shouldn't blow up.
    assert style_for_nature("bold") == style_for_nature("Bold")
    assert style_for_nature("ADAMANT") == style_for_nature("Adamant")


def test_unknown_nature_falls_back_to_default():
    out = style_for_nature("Wibbly")
    assert isinstance(out, str) and len(out) > 0
    # The default must not be the empty string (which would disappear
    # in the rendered system prompt and confuse the model).
    assert out != ""


def test_build_system_prompt_includes_identity_and_style():
    identity = CompanionIdentity(
        species_dex_id=4,            # Charmander
        nickname="Sparky",
        nature="Bold",
        is_shiny=False,
    )
    prompt = build_system_prompt(identity, context=None)
    assert "Sparky" in prompt           # nickname
    assert "Charmander" in prompt        # species
    assert "Bold" in prompt              # nature
    assert style_for_nature("Bold") in prompt
    # The behaviour rules must show up so the model stays in character.
    assert "in character" in prompt.lower()
    assert "english" in prompt.lower()
    # Without a context snapshot, no window-context block.
    assert "<window_context>" not in prompt


def test_build_system_prompt_falls_back_to_species_when_no_nickname():
    identity = CompanionIdentity(
        species_dex_id=25,           # Pikachu
        nickname=None,
        nature="Jolly",
    )
    prompt = build_system_prompt(identity, context=None)
    assert "Pikachu" in prompt
    # Should mention the species twice: once as identity ("you are
    # Pikachu") and once as the species clause ("a Pikachu"). At
    # minimum, we should not see "None" leaked in.
    assert "None" not in prompt


def test_build_system_prompt_marks_shiny_variant():
    identity = CompanionIdentity(
        species_dex_id=150,
        nickname=None,
        nature="Hardy",
        is_shiny=True,
    )
    prompt = build_system_prompt(identity, context=None)
    assert "shiny" in prompt.lower()


def test_build_system_prompt_appends_window_context():
    identity = CompanionIdentity(
        species_dex_id=1,
        nickname=None,
        nature="Calm",
    )
    snap = ContextSnapshot(
        app_name="Visual Studio Code",
        app_id="com.microsoft.VSCode",
        kind="editor",
        window_title="overlay.py",
        text="def show_chat(self): ...",
        source="macos_screenshot",
    )
    prompt = build_system_prompt(identity, context=snap)
    assert "<window_context>" in prompt
    assert "Visual Studio Code" in prompt
    # Body text from the snapshot should reach the prompt so the model
    # can ground its answer.
    assert "show_chat" in prompt

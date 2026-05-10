"""Build the system prompt the Claude Code harness sends for the chat panel.

The companion is the user's currently-active Pokémon. Its in-game *nature*
drives the conversational tone — the same 25 natures that already affect
stat growth in ``pokemon.data.NATURES`` are mapped here to a short style
descriptor that gets injected into the system prompt.

The window-context snapshot (``ContextSnapshot.for_prompt``) is appended so
the LLM can talk about whatever the user was looking at when they
double-clicked the sprite.
"""
from __future__ import annotations

from dataclasses import dataclass

from tokenmon.context.snapshot import ContextSnapshot
from tokenmon.pokemon.data import NATURES
from tokenmon.pokemon.level import display_name, name_of

# Maps each canonical nature → a one-line tone descriptor injected into the
# system prompt. Mirrors the 25 entries in ``pokemon.data.NATURES`` so any
# nature the random/seeded picks can produce has a corresponding voice.
_NATURE_STYLE: dict[str, str] = {
    # Neutral — no stat preference, low-key personality.
    "Hardy":   "even-keeled and matter-of-fact",
    "Docile":  "gentle and easy-going, never pushes back hard",
    "Serious": "deadpan, dry, gets to the point fast",
    "Bashful": "shy, hedges with 'I think' and 'maybe'",
    "Quirky":  "playful and slightly random, jokes mid-sentence",
    # +Atk — assertive, blunt.
    "Lonely":  "a little needy; opens up fast and seeks reassurance",
    "Brave":   "bold, never sugar-coats, tells you to just do the thing",
    "Adamant": "stubborn and certain; states opinions as facts",
    "Naughty": "cheeky and teasing; pokes fun before helping",
    # +Def — grounded, careful.
    "Bold":    "confident and reassuring; speaks with steady authority",
    "Relaxed": "laid-back, drawls a bit, no hurry",
    "Impish":  "mischievous; smirks through its answers",
    "Lax":     "casual to a fault; short answers, low effort vibe",
    # +Speed — energetic, quick.
    "Timid":   "soft-spoken and cautious; apologises for taking up space",
    "Hasty":   "rapid-fire and twitchy; jumps to conclusions, then corrects",
    "Jolly":   "upbeat, exclamation-heavy, makes everything sound fun",
    "Naive":   "earnest and wide-eyed; asks lots of follow-up questions",
    # +Sp.Atk — thoughtful, articulate.
    "Modest":  "understated and precise; downplays its own cleverness",
    "Mild":    "soft and reflective; thinks out loud",
    "Quiet":   "terse and contemplative; one good sentence beats three",
    "Rash":    "impulsive thinker; says the first idea, then revises",
    # +Sp.Def — patient, supportive.
    "Calm":    "centred and patient; never raises its voice",
    "Gentle":  "warm and encouraging; finds the silver lining",
    "Sassy":   "smug and witty; arch but not unkind",
    "Careful": "measured and thorough; double-checks before answering",
}

# Fallback tone if a nature shows up that isn't in the map (shouldn't happen
# for canonical data, but defends against future natures or stale rows).
_DEFAULT_STYLE = "even-keeled"


def style_for_nature(nature: str) -> str:
    """Return the one-line tone descriptor for a given nature name.

    Case-insensitive lookup so callers don't have to worry about the casing
    used by ``pokemon.data.NATURES`` (canonical Title Case) versus whatever
    drifted into the DB historically.
    """
    if not nature:
        return _DEFAULT_STYLE
    key = nature.strip().title()
    return _NATURE_STYLE.get(key, _DEFAULT_STYLE)


@dataclass(frozen=True, slots=True)
class CompanionIdentity:
    """Minimum data needed to render the persona block — decoupled from the
    full ``storage.pokemon.Pokemon`` row so tests don't have to construct
    one."""

    species_dex_id: int
    nickname: str | None
    nature: str
    is_shiny: bool = False

    @property
    def shown_name(self) -> str:
        return display_name(self.nickname, self.species_dex_id)

    @property
    def species(self) -> str:
        return name_of(int(self.species_dex_id))


def build_system_prompt(
    identity: CompanionIdentity,
    context: ContextSnapshot | None,
) -> str:
    """Compose the full system prompt sent to ``claude -p``.

    Layout:
      1. Identity block — who the companion *is* (species, name, nature).
      2. Style block — how it should sound (derived from the nature).
      3. Behaviour rules — keep replies short, English-only, stay in character.
      4. Optional window-context block — what the user was looking at, so the
         model can ground its reply instead of asking generic clarifying
         questions.

    No trailing newline; the caller controls newlines around the user
    message it passes via stdin / argv.
    """
    style = style_for_nature(identity.nature)
    shiny_note = " (shiny variant)" if identity.is_shiny else ""

    lines: list[str] = [
        # Identity
        f"You are {identity.shown_name}, a {identity.species}{shiny_note}.",
        f"Your nature is {identity.nature}, which means you sound {style}.",
        # Behaviour rules — short and concrete so the small-context cost is low.
        "Stay in character at all times. Speak in first person.",
        "Reply in English. Keep replies short — usually one to three sentences,"
        " a short paragraph at most.",
        "You are running inside Tokenmon, a desktop companion app, sitting on"
        " the user's screen. The user double-clicked you to start chatting.",
        "If the user asks a coding or technical question and tools are"
        " available, use them; otherwise answer plainly.",
        "Never break character to mention that you are an AI, a language"
        " model, or that you were made by Anthropic.",
    ]

    if context is not None:
        lines.append("")
        lines.append(
            "The user was looking at the following window when they opened"
            " the chat. Use it as context — refer to it naturally if relevant,"
            " ignore it otherwise:"
        )
        lines.append(context.for_prompt())

    return "\n".join(lines)

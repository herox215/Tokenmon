"""Platform-neutral data structure describing the active window's content.

Providers fill this in; the chat / prompt-builder reads it. No platform-
specific types leak into this module — that's the whole point of the
abstraction so a Linux port becomes additive, not a refactor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ContextKind = Literal["browser", "terminal", "editor", "file_manager", "generic"]


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    app_name: str
    app_id: str
    kind: ContextKind
    window_title: str | None = None
    text: str | None = None
    url: str | None = None
    cwd: str | None = None
    selection: str | None = None
    truncated: bool = False
    source: str = "unknown"

    def short_summary(self, max_chars: int = 600) -> str:
        """Compact, human-readable rendering for the chat header / debug
        view. Truncates ``text`` aggressively — full content is reserved
        for the LLM prompt builder."""
        parts: list[str] = [f"{self.app_name} ({self.kind})"]
        if self.window_title:
            parts.append(f"title: {self.window_title}")
        if self.url:
            parts.append(f"url: {self.url}")
        if self.cwd:
            parts.append(f"cwd: {self.cwd}")
        if self.selection:
            sel = self.selection.strip().replace("\n", " ")
            if len(sel) > 120:
                sel = sel[:117] + "…"
            parts.append(f"selection: {sel}")
        head = " | ".join(parts)
        body = self.text or ""
        body = body.strip()
        if len(body) > max_chars:
            body = body[:max_chars] + f"\n…[+{len(self.text or '') - max_chars} chars]"
        if body:
            return f"{head}\n{body}"
        return head

    def for_prompt(self, max_chars: int = 8000) -> str:
        """Serialised block intended to be prepended as a system message
        to the LLM. Keeps structure even when truncated so the model can
        tell metadata from raw scraped content."""
        lines = [
            "<window_context>",
            f"  app: {self.app_name} ({self.app_id})",
            f"  kind: {self.kind}",
            f"  source: {self.source}",
        ]
        if self.window_title:
            lines.append(f"  title: {self.window_title}")
        if self.url:
            lines.append(f"  url: {self.url}")
        if self.cwd:
            lines.append(f"  cwd: {self.cwd}")
        if self.selection:
            lines.append(f"  selection: {self.selection!r}")
        if self.text:
            txt = self.text
            truncated = self.truncated
            if len(txt) > max_chars:
                txt = txt[:max_chars]
                truncated = True
            lines.append("  text: |")
            lines.extend(f"    {line}" for line in txt.splitlines())
            if truncated:
                lines.append("    [truncated]")
        lines.append("</window_context>")
        return "\n".join(lines)

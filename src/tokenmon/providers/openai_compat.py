"""OpenAI-compatible strategy. Powers direct OpenAI as well as OpenRouter and
any other provider that speaks the same `/chat/completions` API.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tokenmon.storage import Usage

log = logging.getLogger("tokenmon.providers.openai_compat")


class _OpenAIAccumulator:
    """Buffers SSE bytes from an OpenAI-style stream, extracts usage at end.

    OpenAI emits unnamed SSE events (`data: {...}\\n\\n`), terminated by
    `data: [DONE]\\n\\n`. The `usage` field appears when the request opted in
    via `stream_options.include_usage=true`.
    """

    __slots__ = (
        "model", "input_tokens", "output_tokens", "cache_read_tokens",
        "request_id", "stop_reason", "got_any", "_buffer",
    )

    def __init__(self, model_hint: str | None) -> None:
        self.model: str = model_hint or "unknown"
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.request_id: str | None = None
        self.stop_reason: str | None = None
        self.got_any = False
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n\n" in self._buffer:
            block, self._buffer = self._buffer.split(b"\n\n", 1)
            self._consume_block(block)

    def _consume_block(self, block: bytes) -> None:
        for raw_line in block.split(b"\n"):
            line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace")
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data_str = line[5:].lstrip()
            if data_str == "[DONE]":
                continue
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            self.got_any = True
            if data.get("model"):
                self.model = data["model"]
            if data.get("id") and not self.request_id:
                self.request_id = data["id"]
            usage = data.get("usage")
            if isinstance(usage, dict):
                self.input_tokens = int(usage.get("prompt_tokens", 0) or 0)
                self.output_tokens = int(usage.get("completion_tokens", 0) or 0)
                details = usage.get("prompt_tokens_details") or {}
                self.cache_read_tokens = int(details.get("cached_tokens", 0) or 0)
            for choice in data.get("choices") or []:
                fr = choice.get("finish_reason")
                if fr and not self.stop_reason:
                    self.stop_reason = fr

    def to_usage(self, *, override_stop: str | None = None) -> Usage | None:
        # Skip empty rows (no event reached us, or the client didn't opt into
        # `stream_options.include_usage` and we never saw a usage block).
        if not self.got_any:
            return None
        if self.input_tokens == 0 and self.output_tokens == 0:
            return None
        return Usage(
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_creation_tokens=0,
            stop_reason=override_stop or self.stop_reason,
            request_id=self.request_id,
        )


class OpenAIStrategy:
    """Generic OpenAI-format strategy. Subclasses pin the upstream URL.

    Convention: upstream_url is host-only (no /v1 or /api/v1 prefix). Clients
    set their own baseURL with the prefix, e.g.
    ``OPENAI_BASE_URL=http://127.0.0.1:8790/v1``. The proxy receives the path
    including the prefix and forwards it verbatim, which means the same
    strategy works whether the client uses /v1, /api/v1 or some other path.
    """

    name = "openai"
    upstream_url = "https://api.openai.com"
    default_port = 8790
    label_for_user = "OpenAI"

    def is_usage_endpoint(self, method: str, path: str) -> bool:
        # Most clients hit /chat/completions. Some also use /completions
        # (legacy) and /embeddings, but those have different shapes — we
        # only track chat for now.
        return method == "POST" and path.endswith("/chat/completions")

    def request_wants_streaming(self, body: bytes) -> bool:
        if not body:
            return False
        try:
            return bool(json.loads(body).get("stream"))
        except (ValueError, UnicodeDecodeError):
            return False

    def extract_model_from_request(self, body: bytes) -> str | None:
        if not body:
            return None
        try:
            return json.loads(body).get("model")
        except (ValueError, UnicodeDecodeError):
            return None

    def extract_usage_from_response(
        self, body: bytes, model_hint: str | None
    ) -> Usage | None:
        try:
            payload: dict[str, Any] = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        details = usage.get("prompt_tokens_details") or {}
        choices = payload.get("choices") or []
        stop_reason = choices[0].get("finish_reason") if choices else None
        return Usage(
            model=payload.get("model") or model_hint or "unknown",
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cache_read_tokens=int(details.get("cached_tokens", 0) or 0),
            cache_creation_tokens=0,
            stop_reason=stop_reason,
            request_id=payload.get("id"),
        )

    def make_streaming_accumulator(self, model_hint: str | None) -> _OpenAIAccumulator:
        return _OpenAIAccumulator(model_hint)

    def maybe_inject_streaming_options(self, body: bytes) -> bytes:
        """Force `stream_options.include_usage = true` so the upstream emits
        a usage block in the final SSE chunk. Without this, OpenAI-format
        streams hide token counts from us entirely."""
        if not body:
            return body
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return body
        if not data.get("stream"):
            return body
        so = data.get("stream_options") or {}
        if so.get("include_usage"):
            return body
        so["include_usage"] = True
        data["stream_options"] = so
        return json.dumps(data).encode("utf-8")


class OpenRouterStrategy(OpenAIStrategy):
    name = "openrouter"
    upstream_url = "https://openrouter.ai"
    default_port = 8789
    label_for_user = "OpenRouter"


def openai_compat_for(name: str) -> OpenAIStrategy:
    name = name.lower().strip()
    if name == "openrouter":
        return OpenRouterStrategy()
    if name in {"openai", "openai-compat"}:
        return OpenAIStrategy()
    raise ValueError(f"unknown openai-compat provider: {name!r}")

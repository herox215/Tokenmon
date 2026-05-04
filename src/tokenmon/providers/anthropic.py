"""Anthropic strategy — the original Tokenmon behaviour, factored out into a
ProviderStrategy."""

from __future__ import annotations

import json
import logging
from typing import Any

from tokenmon.storage import Usage

log = logging.getLogger("tokenmon.providers.anthropic")


class _AnthropicAccumulator:
    """Holds in-flight usage extracted from SSE events for one Anthropic request."""

    __slots__ = (
        "model", "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_creation_tokens", "request_id", "stop_reason", "got_message_start",
        "_buffer",
    )

    def __init__(self, model_hint: str | None) -> None:
        self.model: str = model_hint or "unknown"
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0
        self.request_id: str | None = None
        self.stop_reason: str | None = None
        self.got_message_start = False
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n\n" in self._buffer:
            block, self._buffer = self._buffer.split(b"\n\n", 1)
            event_type, data = _parse_sse_block(block)
            if event_type and data is not None:
                try:
                    self._feed_event(event_type, data)
                except Exception:
                    log.exception("failed to parse Anthropic SSE event %s", event_type)

    def _feed_event(self, event_type: str, data: dict) -> None:
        if event_type == "message_start":
            msg = data.get("message", {}) or {}
            usage = msg.get("usage") or {}
            self.model = msg.get("model", self.model)
            self.request_id = msg.get("id", self.request_id)
            self.input_tokens = int(usage.get("input_tokens", 0) or 0)
            self.cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
            self.cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
            self.got_message_start = True
        elif event_type == "message_delta":
            usage = data.get("usage") or {}
            if "output_tokens" in usage:
                self.output_tokens = int(usage["output_tokens"] or 0)
            delta = data.get("delta") or {}
            if delta.get("stop_reason") and not self.stop_reason:
                self.stop_reason = delta["stop_reason"]
        elif event_type == "error":
            err = data.get("error") or {}
            log.warning("upstream stream error: %s", err)
            if not self.stop_reason:
                self.stop_reason = "error"

    def to_usage(self, *, override_stop: str | None = None) -> Usage | None:
        if not self.got_message_start:
            return None
        return Usage(
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens,
            stop_reason=override_stop or self.stop_reason,
            request_id=self.request_id,
        )


def _parse_sse_block(block: bytes) -> tuple[str | None, dict | None]:
    """Parse one SSE event block (separated by blank line). Returns (event, data)."""
    event_type: str | None = None
    data_lines: list[str] = []
    for raw_line in block.split(b"\n"):
        line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace")
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return event_type, None
    try:
        return event_type, json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return event_type, None


class AnthropicStrategy:
    name = "anthropic"
    upstream_url = "https://api.anthropic.com"
    default_port = 8788
    label_for_user = "Anthropic"

    def is_usage_endpoint(self, method: str, path: str) -> bool:
        return method == "POST" and path == "/v1/messages"

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
        return Usage(
            model=payload.get("model") or model_hint or "unknown",
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
            stop_reason=payload.get("stop_reason"),
            request_id=payload.get("id"),
        )

    def make_streaming_accumulator(
        self, model_hint: str | None
    ) -> _AnthropicAccumulator:
        return _AnthropicAccumulator(model_hint)

    def maybe_inject_streaming_options(self, body: bytes) -> bytes:
        return body

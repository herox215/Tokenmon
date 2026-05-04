"""SSE streaming handler for /v1/messages with stream:true.

Pipes the upstream SSE stream byte-for-byte to the client (no buffering, low
latency) while sniffing event payloads in parallel to extract token usage.

Anthropic SSE event semantics:
  - event: message_start  -> message.usage has input_tokens, cache_read_input_tokens,
                              cache_creation_input_tokens (output_tokens initial/0).
  - event: message_delta  -> usage.output_tokens is the FINAL cumulative output
                              count (later deltas overwrite, do not sum).
  - event: message_stop   -> end of useful events.
  - event: error          -> log and skip.

Edge cases:
  - Client disconnects (Esc in Claude Code): we stop receiving the stream
    mid-flight. Whatever usage we have so far is recorded with stop_reason
    "cancelled".
  - Upstream error event: usage so far is recorded with stop_reason "error".
"""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

import httpx
from starlette.requests import Request
from starlette.responses import StreamingResponse

from tokenmon.storage import Usage, insert_usage

log = logging.getLogger("tokenmon.proxy.stream")


class _UsageAccumulator:
    """Holds in-flight usage extracted from SSE events for one request."""

    __slots__ = ("model", "input_tokens", "output_tokens", "cache_read_tokens",
                 "cache_creation_tokens", "request_id", "stop_reason", "got_message_start")

    def __init__(self, model_hint: str | None) -> None:
        self.model: str = model_hint or "unknown"
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0
        self.request_id: str | None = None
        self.stop_reason: str | None = None
        self.got_message_start = False

    def feed_event(self, event_type: str, data: dict) -> None:
        if event_type == "message_start":
            msg = data.get("message", {}) or {}
            usage = msg.get("usage") or {}
            self.model = msg.get("model", self.model)
            self.request_id = msg.get("id", self.request_id)
            self.input_tokens = int(usage.get("input_tokens", 0) or 0)
            self.cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
            self.cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
            # message_start sometimes already includes output_tokens=0 — overwrite
            # only on later message_delta events.
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

    def to_usage(self, duration_ms: int, override_stop: str | None = None) -> Usage | None:
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
            duration_ms=duration_ms,
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
    data_str = "\n".join(data_lines)
    try:
        return event_type, json.loads(data_str)
    except json.JSONDecodeError:
        return event_type, None


async def stream_messages(
    request: Request,
    body: bytes,
    headers: dict[str, str],
) -> StreamingResponse:
    from tokenmon.proxy import UPSTREAM, _client_get, _filter_response_headers, _request_model

    started = time.monotonic()
    accum = _UsageAccumulator(_request_model(body))
    client = _client_get()

    # Open the streaming request to upstream. We need the response object both
    # for headers (to forward) and the byte iterator.
    upstream_req = client.build_request(
        request.method,
        request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        headers=headers,
        content=body,
    )
    upstream_resp = await client.send(upstream_req, stream=True)

    async def body_iter() -> AsyncIterator[bytes]:
        buffer = b""
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk  # forward immediately, no buffering of output
                buffer += chunk
                while b"\n\n" in buffer:
                    block, buffer = buffer.split(b"\n\n", 1)
                    event_type, data = _parse_sse_block(block)
                    if event_type and data is not None:
                        try:
                            accum.feed_event(event_type, data)
                        except Exception:
                            log.exception("failed to parse SSE event %s", event_type)
        except (httpx.RequestError, GeneratorExit, ConnectionError) as exc:
            # Client disconnected or upstream broke — record what we have.
            log.info("stream interrupted: %s", type(exc).__name__)
            _record(accum, started, override_stop="cancelled")
            raise
        finally:
            await upstream_resp.aclose()
            # Normal completion path
            if accum.stop_reason != "cancelled":
                _record(accum, started)

    return StreamingResponse(
        body_iter(),
        status_code=upstream_resp.status_code,
        headers=dict(_filter_response_headers(upstream_resp.headers)),
        media_type=upstream_resp.headers.get("content-type"),
    )


_recorded_request_ids: set[str] = set()


def _record(accum: _UsageAccumulator, started: float, override_stop: str | None = None) -> None:
    duration_ms = int((time.monotonic() - started) * 1000)
    usage = accum.to_usage(duration_ms, override_stop=override_stop)
    if usage is None:
        return
    # Guard against double-recording (finally + except both fire on cancel).
    key = usage.request_id or f"{started}"
    if key in _recorded_request_ids:
        return
    _recorded_request_ids.add(key)
    try:
        insert_usage(usage)
    except Exception:
        log.exception("failed to write streaming usage")

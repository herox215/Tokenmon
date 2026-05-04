"""Common protocols for provider strategies."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tokenmon.storage import Usage


@runtime_checkable
class StreamingAccumulator(Protocol):
    """Per-request state container used while a streaming response is in flight.

    The proxy feeds raw SSE bytes into `feed`. When the stream ends (or the
    client disconnects), the proxy calls `to_usage` to materialise whatever
    we managed to extract.
    """

    def feed(self, chunk: bytes) -> None: ...

    def to_usage(self, *, override_stop: str | None = None) -> Usage | None: ...


@runtime_checkable
class ProviderStrategy(Protocol):
    """A pluggable strategy describing how to forward and parse one upstream
    API. Concrete strategies live next to this file (anthropic.py, etc.)."""

    name: str
    """Stable identifier used for logs, LaunchAgent labels, and CLI flags."""

    upstream_url: str
    """Base URL the proxy forwards to (no trailing slash)."""

    default_port: int
    """Loopback port the proxy listens on by default."""

    label_for_user: str
    """Human-readable name shown in tooltips / menubar."""

    def is_usage_endpoint(self, method: str, path: str) -> bool:
        """Should we try to record token usage for this request?"""

    def request_wants_streaming(self, body: bytes) -> bool:
        """Does the request body opt into a streaming response?"""

    def extract_model_from_request(self, body: bytes) -> str | None:
        """Sniff the model id out of the request body, for tagging usage rows
        when the response itself doesn't echo it."""

    def extract_usage_from_response(
        self, body: bytes, model_hint: str | None
    ) -> Usage | None:
        """Parse a non-streaming JSON response body into a Usage record."""

    def make_streaming_accumulator(
        self, model_hint: str | None
    ) -> StreamingAccumulator:
        """Create a fresh accumulator for a streaming response."""

    def maybe_inject_streaming_options(self, body: bytes) -> bytes:
        """Optional hook for providers (OpenAI/OpenRouter) where the client
        must opt in to receiving usage in streaming mode. Default returns the
        body unchanged."""

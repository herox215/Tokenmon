"""Reverse proxy in front of api.anthropic.com that records token usage.

Listens on 127.0.0.1:8788. Forwards every /v1/* request transparently. For
/v1/messages calls, parses the response (or SSE stream — see streaming
support) and records the usage in SQLite.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tokenmon.storage import DB_DIR, Usage, init_db, insert_usage

UPSTREAM = "https://api.anthropic.com"
HOST = "127.0.0.1"
PORT = 8788

# Headers that must NOT be forwarded — either hop-by-hop, or we let httpx set them.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
STRIP_FROM_REQUEST = HOP_BY_HOP | {"host", "content-length", "accept-encoding"}
STRIP_FROM_RESPONSE = HOP_BY_HOP | {"content-encoding", "content-length"}

LOG_PATH = DB_DIR / "proxy.log"
log = logging.getLogger("tokenmon.proxy")

START_TIME = time.monotonic()
REQUEST_COUNT = 0

_client: httpx.AsyncClient | None = None


def _client_get() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=UPSTREAM,
            http2=True,
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
        )
    return _client


def _filter_request_headers(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers:
        name = k.decode("latin-1").lower()
        if name in STRIP_FROM_REQUEST:
            continue
        out[name] = v.decode("latin-1")
    return out


def _filter_response_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    return [(k, v) for k, v in headers.items() if k.lower() not in STRIP_FROM_RESPONSE]


def _extract_usage(payload: dict[str, Any], model_hint: str | None) -> Usage | None:
    """Pull a Usage out of a non-streaming /v1/messages response body."""
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


def _request_wants_stream(body: bytes) -> bool:
    if not body:
        return False
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    return bool(data.get("stream"))


def _request_model(body: bytes) -> str | None:
    if not body:
        return None
    try:
        return json.loads(body).get("model")
    except (ValueError, UnicodeDecodeError):
        return None


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "uptime_s": round(time.monotonic() - START_TIME, 1),
        "request_count": REQUEST_COUNT,
    })


async def proxy(request: Request) -> Response:
    global REQUEST_COUNT
    REQUEST_COUNT += 1

    body = await request.body()
    upstream_path = request.url.path
    if request.url.query:
        upstream_path = f"{upstream_path}?{request.url.query}"

    headers = _filter_request_headers(list(request.scope["headers"]))
    is_messages = request.url.path == "/v1/messages" and request.method == "POST"
    wants_stream = is_messages and _request_wants_stream(body)

    if wants_stream:
        # Streaming path is implemented in proxy_stream.py (next task).
        from tokenmon.proxy_stream import stream_messages
        return await stream_messages(request, body, headers)

    started = time.monotonic()
    try:
        upstream_resp = await _client_get().request(
            request.method,
            upstream_path,
            headers=headers,
            content=body,
        )
    except httpx.RequestError as exc:
        log.warning("upstream error: %s", exc)
        return JSONResponse({"error": {"type": "upstream_error", "message": str(exc)}}, status_code=502)

    duration_ms = int((time.monotonic() - started) * 1000)
    response_body = upstream_resp.content

    if is_messages and upstream_resp.status_code == 200:
        try:
            payload = json.loads(response_body)
            usage = _extract_usage(payload, _request_model(body))
            if usage is not None:
                usage.duration_ms = duration_ms
                insert_usage(usage)
        except Exception:
            log.exception("failed to record usage")

    return Response(
        content=response_body,
        status_code=upstream_resp.status_code,
        headers=dict(_filter_response_headers(upstream_resp.headers)),
        media_type=upstream_resp.headers.get("content-type"),
    )


def build_app() -> Starlette:
    return Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
        ],
    )


def _setup_logging() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> None:
    _setup_logging()
    init_db()
    log.info("tokenmon proxy starting on %s:%d -> %s", HOST, PORT, UPSTREAM)
    uvicorn.run(
        build_app(),
        host=HOST,
        port=PORT,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()

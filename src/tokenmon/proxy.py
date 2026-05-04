"""Generic provider-agnostic reverse proxy that records token usage.

Each invocation is parameterised by a ProviderStrategy (Anthropic, OpenRouter,
…) plus a port. The default invocation — `python -m tokenmon.proxy` — runs the
Anthropic strategy on 127.0.0.1:8788 to preserve the original behaviour.
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import AsyncIterator

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from tokenmon.providers import ProviderStrategy, load as load_provider
from tokenmon.providers.anthropic import AnthropicStrategy
from tokenmon.storage import DB_DIR, init_db, insert_usage

# Backwards-compat constants for the menubar's health check.
HOST = "127.0.0.1"
PORT = AnthropicStrategy.default_port  # 8788

# Headers that must NOT be forwarded — either hop-by-hop, or we let httpx set them.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
STRIP_FROM_REQUEST = HOP_BY_HOP | {"host", "content-length", "accept-encoding"}
STRIP_FROM_RESPONSE = HOP_BY_HOP | {"content-encoding", "content-length"}

LOG_PATH = DB_DIR / "proxy.log"


class ProxyServer:
    """One forwarding proxy bound to a single ProviderStrategy + port."""

    def __init__(self, strategy: ProviderStrategy, host: str = HOST,
                 port: int | None = None) -> None:
        self.strategy = strategy
        self.host = host
        self.port = int(port) if port is not None else strategy.default_port
        self._client: httpx.AsyncClient | None = None
        self._start_time = time.monotonic()
        self._request_count = 0
        self.log = logging.getLogger(f"tokenmon.proxy.{strategy.name}")

    def _client_get(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.strategy.upstream_url,
                http2=True,
                timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0),
            )
        return self._client

    @staticmethod
    def _filter_request_headers(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, v in headers:
            name = k.decode("latin-1").lower()
            if name in STRIP_FROM_REQUEST:
                continue
            out[name] = v.decode("latin-1")
        return out

    @staticmethod
    def _filter_response_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
        return [(k, v) for k, v in headers.items() if k.lower() not in STRIP_FROM_RESPONSE]

    async def healthz(self, _: Request) -> JSONResponse:
        return JSONResponse({
            "ok": True,
            "provider": self.strategy.name,
            "upstream": self.strategy.upstream_url,
            "uptime_s": round(time.monotonic() - self._start_time, 1),
            "request_count": self._request_count,
        })

    async def proxy(self, request: Request) -> Response:
        self._request_count += 1

        body = await request.body()
        upstream_path = request.url.path
        if request.url.query:
            upstream_path = f"{upstream_path}?{request.url.query}"

        headers = self._filter_request_headers(list(request.scope["headers"]))
        is_usage = self.strategy.is_usage_endpoint(request.method, request.url.path)
        wants_stream = is_usage and self.strategy.request_wants_streaming(body)

        if wants_stream:
            body = self.strategy.maybe_inject_streaming_options(body)
            return await self._proxy_streaming(request, body, headers, upstream_path)

        started = time.monotonic()
        try:
            resp = await self._client_get().request(
                request.method, upstream_path, headers=headers, content=body,
            )
        except httpx.RequestError as exc:
            self.log.warning("upstream error: %s", exc)
            return JSONResponse(
                {"error": {"type": "upstream_error", "message": str(exc)}},
                status_code=502,
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        response_body = resp.content

        if is_usage and resp.status_code == 200:
            try:
                model_hint = self.strategy.extract_model_from_request(body)
                usage = self.strategy.extract_usage_from_response(response_body, model_hint)
                if usage is not None:
                    usage.duration_ms = duration_ms
                    insert_usage(usage)
            except Exception:
                self.log.exception("failed to record usage")

        return Response(
            content=response_body,
            status_code=resp.status_code,
            headers=dict(self._filter_response_headers(resp.headers)),
            media_type=resp.headers.get("content-type"),
        )

    async def _proxy_streaming(
        self, request: Request, body: bytes, headers: dict[str, str], upstream_path: str
    ) -> StreamingResponse:
        client = self._client_get()
        started = time.monotonic()
        accum = self.strategy.make_streaming_accumulator(
            self.strategy.extract_model_from_request(body)
        )

        upstream_req = client.build_request(
            request.method, upstream_path, headers=headers, content=body,
        )
        upstream_resp = await client.send(upstream_req, stream=True)

        recorded = False

        def record(override_stop: str | None = None) -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            try:
                usage = accum.to_usage(override_stop=override_stop)
            except Exception:
                self.log.exception("accumulator to_usage failed")
                return
            if usage is None:
                return
            usage.duration_ms = int((time.monotonic() - started) * 1000)
            try:
                insert_usage(usage)
            except Exception:
                self.log.exception("failed to write streaming usage")

        async def body_iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream_resp.aiter_raw():
                    yield chunk
                    try:
                        accum.feed(chunk)
                    except Exception:
                        self.log.exception("accumulator feed failed")
            except (httpx.RequestError, GeneratorExit, ConnectionError) as exc:
                self.log.info("stream interrupted: %s", type(exc).__name__)
                record(override_stop="cancelled")
                raise
            finally:
                await upstream_resp.aclose()
                record()

        return StreamingResponse(
            body_iter(),
            status_code=upstream_resp.status_code,
            headers=dict(self._filter_response_headers(upstream_resp.headers)),
            media_type=upstream_resp.headers.get("content-type"),
        )

    def build_app(self) -> Starlette:
        return Starlette(
            debug=False,
            routes=[
                Route("/healthz", self.healthz, methods=["GET"]),
                Route(
                    "/{path:path}", self.proxy,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                ),
            ],
        )

    def run(self) -> None:
        uvicorn.run(
            self.build_app(),
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )


def _setup_logging() -> None:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tokenmon provider-aware proxy")
    parser.add_argument(
        "--provider", default="anthropic",
        help="Provider strategy name (anthropic, openrouter, ...)",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="Loopback port (defaults to the strategy's default_port)",
    )
    parser.add_argument("--host", default=HOST)
    args = parser.parse_args()

    _setup_logging()
    init_db()
    strategy = load_provider(args.provider)
    server = ProxyServer(strategy, host=args.host, port=args.port)
    server.log.info(
        "tokenmon proxy starting on %s:%d -> %s",
        server.host, server.port, strategy.upstream_url,
    )
    server.run()


if __name__ == "__main__":
    main()

"""Public HTTP endpoint for the MCP server, with its protections.

Protections, in the order a request meets them:
  1. Only GET / or /health (info) and POST /mcp are served; everything else is 404/405.
  2. Per-client rate limit (token bucket keyed by client IP).
  3. Request body capped at 64 KB (tool calls are tiny).
  4. The SDK's host and origin checks (DNS-rebinding protection) and JSON content-type check.
  5. Tool inputs are length-limited in server.py; the data is public and read-only, and this
     service holds no secrets.

Each request gets its own stateless session manager, so the service needs no startup hook and
no shared session state, which suits serverless hosting.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .server import _data, server

MCP_PATH = "/mcp"
PUBLIC_URL = "https://mcp.citizenview.in/mcp"
MAX_BODY_BYTES = 64 * 1024
RATE_PER_MINUTE = float(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))
RATE_BURST = float(os.environ.get("RATE_LIMIT_BURST", "20"))
ON_VERCEL = os.environ.get("VERCEL") == "1"

# One manager per request means the SDK logs start/stop lines on every call; keep only warnings and errors.
logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]


def allowed_hosts() -> list[str]:
    hosts = ["mcp.citizenview.in", "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    for var in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        if os.environ.get(var):
            hosts.append(os.environ[var])
    hosts += [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return hosts


SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=allowed_hosts(),
    allowed_origins=["https://mcp.citizenview.in", "https://app.citizenview.in",
                     "http://localhost:*", "http://127.0.0.1:*"],
)


class RateLimiter:
    """Token bucket per client. In-memory, so on serverless it limits per running instance."""

    def __init__(self, per_minute: float, burst: float, max_clients: int = 10_000):
        self.rate = per_minute / 60.0
        self.burst = burst
        self.max_clients = max_clients
        self._clients: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        tokens, last = self._clients.pop(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        allowed = tokens >= 1
        if allowed:
            tokens -= 1
        self._clients[key] = (tokens, now)
        while len(self._clients) > self.max_clients:
            self._clients.popitem(last=False)
        retry_after = 0 if allowed else max(1, int((1 - tokens) / self.rate) + 1)
        return allowed, retry_after


limiter = RateLimiter(RATE_PER_MINUTE, RATE_BURST)


def _header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def client_key(scope: dict) -> str:
    # Proxy headers are only trustworthy behind Vercel, which sets them; locally they could be forged.
    if ON_VERCEL:
        real = _header(scope, b"x-real-ip") or (_header(scope, b"x-forwarded-for") or "").split(",")[0].strip()
        if real:
            return real
    client = scope.get("client")
    return client[0] if client else "unknown"


def _with_security_headers(send):
    async def wrapped(message):
        if message["type"] == "http.response.start":
            message = {**message, "headers": list(message.get("headers", [])) + SECURITY_HEADERS}
        await send(message)
    return wrapped


async def _json(send, status: int, body: dict, extra_headers: list | None = None) -> None:
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers + (extra_headers or [])})
    await send({"type": "http.response.body", "body": payload})


def _limit_body(receive):
    """Abort reading once the body passes MAX_BODY_BYTES, even without a Content-Length."""
    seen = 0

    async def wrapped():
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > MAX_BODY_BYTES:
                raise _BodyTooLarge()
        return message
    return wrapped


class _BodyTooLarge(Exception):
    pass


def info() -> dict:
    d = _data()
    return {
        "name": "Parliament MCP (India)",
        "version": __version__,
        "mcp_endpoint": PUBLIC_URL,
        "transport": "streamable-http",
        "data_retrieved_on": d.retrieved_on,
        "attribution": d.attribution,
        "about": "Current Indian MPs, parliamentary committees, and which committee is examining a bill. Read-only.",
    }


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return

    send = _with_security_headers(send)
    path, method = scope.get("path", "/"), scope.get("method", "GET")

    if path in ("/", "/health"):
        if method not in ("GET", "HEAD"):
            return await _json(send, 405, {"error": "Method not allowed"}, [(b"allow", b"GET")])
        return await _json(send, 200, info())

    if path.rstrip("/") != MCP_PATH:
        return await _json(send, 404, {"error": "Not found", "mcp_endpoint": PUBLIC_URL})

    if method != "POST":
        return await _json(send, 405, {"error": "This server accepts MCP requests by POST only"}, [(b"allow", b"POST")])

    allowed, retry_after = limiter.check(client_key(scope))
    if not allowed:
        return await _json(send, 429, {"error": "Too many requests, slow down"},
                           [(b"retry-after", str(retry_after).encode())])

    length = _header(scope, b"content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        return await _json(send, 413, {"error": f"Request body over {MAX_BODY_BYTES} bytes"})

    manager = StreamableHTTPSessionManager(
        app=server._lowlevel_server,  # no public accessor in mcp 2.x; covered by tests
        stateless=True,
        json_response=True,
        security_settings=SECURITY,
        max_request_body_size=MAX_BODY_BYTES,
    )
    try:
        async with manager.run():
            await manager.handle_request({**scope, "path": MCP_PATH}, _limit_body(receive), send)
    except BaseException as error:  # the SDK's task group may wrap it in an exception group
        if not _contains(error, _BodyTooLarge):
            raise
        await _json(send, 413, {"error": f"Request body over {MAX_BODY_BYTES} bytes"})


def _contains(error: BaseException, kind: type) -> bool:
    if isinstance(error, kind):
        return True
    return isinstance(error, BaseExceptionGroup) and any(_contains(e, kind) for e in error.exceptions)

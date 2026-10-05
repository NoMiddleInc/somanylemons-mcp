"""
SoManyLemons Remote MCP Server (Streamable HTTP).

Serves MCP tools over HTTP so clients can connect without installing anything.

Usage:
    sml-mcp-remote                          # default port 8080
    sml-mcp-remote --port 3000
    SML_API_KEY=sml_xxx sml-mcp-remote      # single-user mode (dev/testing)

In production, each client passes their own API key via X-API-Key header.
The server extracts it and uses it for all API calls in that session.
"""

import argparse
import contextlib
import logging
import hashlib
import time
import os
from collections import defaultdict

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory rate limiter (per-instance, no Redis needed for Cloud Run).
# Limits connections per API key to prevent brute-force and abuse.
# ---------------------------------------------------------------------------

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 60  # per key per window

_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(api_key: str) -> bool:
    """Return True if this API key has exceeded the per-minute request limit."""
    now = time.monotonic()
    bucket = _rate_buckets[api_key]
    # Prune old entries
    cutoff = now - _RATE_LIMIT_WINDOW
    _rate_buckets[api_key] = [t for t in bucket if t > cutoff]
    bucket = _rate_buckets[api_key]
    if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
        return True
    bucket.append(now)
    return False

import somanylemons_mcp.server as _srv


class SessionKeyBindings:
    """Bounded credential hashes; never retain a plaintext tenant key."""
    def __init__(self, limit=1024, ttl=1800):
        self.owners = {}
        self.limit, self.ttl = limit, ttl

    def check(self, session_id, key):
        now = time.monotonic()
        self.owners = {sid: pair for sid, pair in self.owners.items() if pair[1] > now}
        digest = hashlib.sha256(key.encode()).hexdigest()
        if session_id:
            known = self.owners.get(session_id)
            if known is None or known[0] != digest:
                return False
            self.owners[session_id] = (digest, now + self.ttl)
        return bool(session_id) or len(self.owners) < self.limit

    def bind(self, session_id, key):
        self.owners[session_id] = (hashlib.sha256(key.encode()).hexdigest(), time.monotonic() + self.ttl)


def _create_app() -> ASGIApp:
    """Build the ASGI app with Streamable HTTP endpoint."""

    bindings = SessionKeyBindings()
    session_manager = StreamableHTTPSessionManager(
        app=_srv.server,
        json_response=False,
        stateless=False,
    )

    async def health(request: Request):
        return JSONResponse({"status": "ok", "server": "somanylemons-mcp"})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        routes=[
            Route("/health", endpoint=health),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=[
                    "https://claude.ai",
                    "https://www.claude.ai",
                    "https://cursor.sh",
                    "https://www.cursor.sh",
                    "https://somanylemons.com",
                    "https://qas.somanylemons.com",
                    "http://localhost",
                    "http://localhost:3000",
                    "http://localhost:8000",
                ],
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=["*"],
                expose_headers=["mcp-session-id"],
            ),
        ],
        lifespan=lifespan,
    )

    async def app(scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            # Inject Accept header if missing so older clients don't get 406
            headers = dict(scope.get("headers", []))
            if b"accept" not in headers or b"text/event-stream" not in headers.get(b"accept", b""):
                scope["headers"] = [
                    (k, v) for k, v in scope["headers"] if k != b"accept"
                ] + [(b"accept", b"application/json, text/event-stream")]

            request = Request(scope, receive)
            client_key = request.headers.get("x-api-key", "")
            if not client_key:
                response = JSONResponse(
                    {"error": "X-API-Key header required"},
                    status_code=401,
                )
                await response(scope, receive, send)
                return

            if not client_key.startswith("sml_") or len(client_key) < 20:
                response = JSONResponse(
                    {"error": "Invalid API key format"},
                    status_code=401,
                )
                await response(scope, receive, send)
                return

            if _is_rate_limited(client_key):
                response = JSONResponse(
                    {"error": "Rate limit exceeded. Max 60 requests per minute."},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
                await response(scope, receive, send)
                return

            session_id = request.headers.get("mcp-session-id", "")
            if not bindings.check(session_id, client_key):
                response = JSONResponse({"error": "MCP session is unavailable for this API key; initialize a new session."}, status_code=403)
                await response(scope, receive, send)
                return

            async def bound_send(message):
                if message["type"] == "http.response.start":
                    for header, value in message.get("headers", []):
                        if header.lower() == b"mcp-session-id":
                            bindings.bind(value.decode(), client_key)
                await send(message)

            token = _srv._session_api_key.set(client_key)
            try:
                await session_manager.handle_request(scope, receive, bound_send)
            finally:
                _srv._session_api_key.reset(token)
        else:
            await starlette_app(scope, receive, send)

    return app


def main():
    parser = argparse.ArgumentParser(description="SoManyLemons Remote MCP Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("SML_API_URL", "https://api.somanylemons.com"),
        help="Backend API base URL",
    )
    args = parser.parse_args()

    _srv.API_URL = args.api_url.rstrip("/")
    # Mark this process as a hosted/multi-tenant server. Tools that read from
    # the local filesystem will be rejected explicitly (see server.call_tool).
    _srv.REMOTE_MODE = True

    # Use "warning" in production to avoid logging request headers (which
    # could contain API keys on malformed requests). "info" is safe for local
    # dev but noisy and risky in hosted mode.
    log_level = os.environ.get("LOG_LEVEL", "warning").lower()

    import uvicorn
    uvicorn.run(_create_app(), host=args.host, port=args.port, log_level=log_level)


if __name__ == "__main__":
    main()

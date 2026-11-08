"""
ProducerSpark Remote MCP Server (Streamable HTTP).

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
import json
import os
import time
from collections import defaultdict

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
import httpx
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory rate limiter (per-instance, no Redis needed for Cloud Run).
# claude.ai opens a fresh MCP connection for most tool calls, so one tool call
# arrives as about five HTTP requests (initialize, initialized, tools/list,
# resources/list, tools/call). Tool calls carry the per-account budget; every
# request together only has a loose abuse ceiling.
# ---------------------------------------------------------------------------

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 600  # any MCP request, per key per window
_TOOL_CALL_LIMIT = 120  # tools/call requests, per key per window
_MAX_BODY_BYTES = 4 * 1024 * 1024

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_tool_call_buckets: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(api_key: str, buckets=None, limit=None, weight: int = 1) -> bool:
    """Return True if this API key has exceeded the per-minute limit of these buckets."""
    buckets = _rate_buckets if buckets is None else buckets
    limit = _RATE_LIMIT_MAX_REQUESTS if limit is None else limit
    now = time.monotonic()
    api_key = hashlib.sha256(api_key.encode()).hexdigest()
    cutoff = now - _RATE_LIMIT_WINDOW
    for key in list(buckets):
        if not buckets[key] or buckets[key][-1] <= cutoff:
            del buckets[key]
    if api_key not in buckets and len(buckets) >= 4096:
        return True
    bucket = [t for t in buckets[api_key] if t > cutoff]
    buckets[api_key] = bucket
    if len(bucket) + weight > limit:
        return True
    bucket.extend([now] * weight)
    return False


def _tool_calls_in(body: bytes) -> int:
    """How many JSON-RPC tools/call requests a POST body carries (a batch can carry several)."""
    try:
        message = json.loads(body or b"null")
    except ValueError:
        return 0
    messages = message if isinstance(message, list) else [message]
    return sum(1 for item in messages if isinstance(item, dict) and item.get("method") == "tools/call")


async def _buffered(receive: Receive):
    """Read the whole request body once and return it with a receive that replays it."""
    chunks, size, more = [], 0, True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            return None, receive
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > _MAX_BODY_BYTES:
            return None, receive
        chunks.append(chunk)
        more = message.get("more_body", False)
    body = b"".join(chunks)
    replayed = False

    async def replay():
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay


import somanylemons_mcp.server as _srv


def _create_app() -> ASGIApp:
    """Build the ASGI app with Streamable HTTP endpoint."""

    # Stateless: every request is served on its own, by whichever instance gets it.
    # Session ids lived in one instance's memory, so a redeploy, a second instance or a
    # client that dropped the id turned ordinary calls into 404 and 400 errors. The tools
    # keep no per-session state; each request carries its own credentials.
    session_manager = StreamableHTTPSessionManager(
        app=_srv.server,
        json_response=False,
        stateless=True,
    )

    resource = "https://producerspark.com/mcp"
    issuer = "https://api.producerspark.com"
    challenge_headers = {
        "WWW-Authenticate": f'Bearer resource_metadata="https://producerspark.com/.well-known/oauth-protected-resource", scope="tasks:read tasks:write"'
    }

    async def protected_resource(request: Request):
        return JSONResponse(
            {
                "resource": resource,
                "authorization_servers": [issuer],
                "scopes_supported": ["tasks:read", "tasks:write"],
                "resource_name": "ProducerSpark Research",
                "bearer_methods_supported": ["header"],
            }
        )

    async def skill_download(request: Request):
        import io
        import zipfile

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("producerspark/SKILL.md", _srv.RESEARCH_SKILL)
        return Response(
            output.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="producerspark-skill.zip"'
            },
        )

    async def health(request: Request):
        return JSONResponse({"status": "ok", "server": "producerspark-mcp"})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        routes=[
            Route("/health", endpoint=health),
            Route("/skills/producerspark.zip", endpoint=skill_download),
            Route("/.well-known/oauth-protected-resource", endpoint=protected_resource),
            Route(
                "/.well-known/oauth-protected-resource/mcp", endpoint=protected_resource
            ),
        ],
        lifespan=lifespan,
    )

    async def app(scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            # Inject Accept header if missing so older clients don't get 406
            headers = dict(scope.get("headers", []))
            if b"accept" not in headers or b"text/event-stream" not in headers.get(
                b"accept", b""
            ):
                scope["headers"] = [
                    (k, v) for k, v in scope["headers"] if k != b"accept"
                ] + [(b"accept", b"application/json, text/event-stream")]

            request = Request(scope, receive)
            client_key = request.headers.get("x-api-key", "")
            binding_key = client_key
            research_only = False
            authorization = request.headers.get("authorization", "")
            if authorization.lower().startswith("bearer "):
                client_key = authorization[7:].strip()
                try:
                    async with httpx.AsyncClient(
                        timeout=10, follow_redirects=False
                    ) as client:
                        result = await client.post(
                            issuer + "/oauth/mcp/introspect",
                            headers={"Authorization": "Bearer " + client_key},
                        )
                    identity = result.json() if result.status_code == 200 else {}
                except (httpx.HTTPError, ValueError):
                    response = JSONResponse(
                        {"error": "Account connection temporarily unavailable."},
                        status_code=503,
                    )
                    await response(scope, receive, send)
                    return
                if (
                    not identity.get("active")
                    or identity.get("resource") not in {resource, "https://mcp.somanylemons.com/mcp"}
                    or not set(identity.get("scope", "").split())
                    <= {"tasks:read", "tasks:write"}
                    or not identity.get("scope")
                    or not identity.get("session_binding")
                ):
                    response = JSONResponse(
                        {"error": "Sign in to ProducerSpark to connect this account."},
                        status_code=401,
                        headers=challenge_headers,
                    )
                    await response(scope, receive, send)
                    return
                binding_key = identity["session_binding"]
                research_only = True
            if not client_key:
                response = JSONResponse(
                    {"error": "Connect your ProducerSpark account."},
                    status_code=401,
                    headers=challenge_headers,
                )
                await response(scope, receive, send)
                return

            if not research_only and (
                not client_key.startswith("sml_") or len(client_key) < 20
            ):
                response = JSONResponse(
                    {"error": "Invalid API key format"},
                    status_code=401,
                    headers=challenge_headers,
                )
                await response(scope, receive, send)
                return
            tool_calls = 0
            if scope.get("method") == "POST":
                body, receive = await _buffered(receive)
                if body is None:
                    response = JSONResponse({"error": "Request body too large or incomplete."}, status_code=413)
                    await response(scope, receive, send)
                    return
                tool_calls = _tool_calls_in(body)
            if _is_rate_limited(binding_key) or (
                tool_calls and _is_rate_limited(
                    binding_key, _tool_call_buckets, _TOOL_CALL_LIMIT, weight=tool_calls
                )
            ):
                response = JSONResponse(
                    {"error": f"Rate limit exceeded. Max {_TOOL_CALL_LIMIT} tool calls per minute."},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
                await response(scope, receive, send)
                return

            # The SDK runs a session in a background task. Attach credentials to
            # each request's metadata so tools never retain an expired first key.
            scope = dict(scope)
            scope["state"] = dict(
                scope.get("state", {}),
                producerspark_mcp_identity={
                    "api_key": client_key,
                    "research_only": research_only,
                },
            )
            token = _srv._session_api_key.set(client_key)
            research_token = _srv._research_only.set(research_only)
            try:
                await session_manager.handle_request(scope, receive, send)
            finally:
                _srv._session_api_key.reset(token)
                _srv._research_only.reset(research_token)
        else:
            await starlette_app(scope, receive, send)

    return CORSMiddleware(
        app,
        allow_origins=[
            "https://claude.ai",
            "https://www.claude.ai",
            "https://chatgpt.com",
            "https://producerspark.com",
            "https://cursor.sh",
            "https://www.cursor.sh",
            "http://localhost:3000",
            "http://localhost:8000",
        ],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id", "www-authenticate"],
    )


def main():
    parser = argparse.ArgumentParser(description="ProducerSpark Remote MCP Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("SML_API_URL", "https://api.producerspark.com"),
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

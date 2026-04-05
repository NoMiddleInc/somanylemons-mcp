"""
SoManyLemons Remote MCP Server (Streamable HTTP transport).

Serves the same MCP tools as the local server, but over HTTP
so clients can connect without installing anything.

Supports both:
  - Streamable HTTP at /mcp (modern, used by Claude Code "type": "url")
  - Legacy SSE at /sse + /messages/ (backwards compatibility)

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
import os

import anyio
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

logger = logging.getLogger(__name__)

import somanylemons_mcp.server as _srv


def _create_app() -> ASGIApp:
    """Build the ASGI app with Streamable HTTP + legacy SSE endpoints."""

    # -----------------------------------------------------------------------
    # Streamable HTTP (modern transport)
    # -----------------------------------------------------------------------
    session_manager = StreamableHTTPSessionManager(
        app=_srv.server,
        json_response=False,
        stateless=False,
        session_idle_timeout=1800.0,
    )

    # -----------------------------------------------------------------------
    # Legacy SSE transport (backwards compatibility)
    # -----------------------------------------------------------------------
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        client_key = request.headers.get("x-api-key", "")
        if not client_key:
            return JSONResponse(
                {"error": "X-API-Key header required"},
                status_code=401,
            )

        _srv._session_api_key.set(client_key)

        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await _srv.server.run(
                read_stream,
                write_stream,
                _srv.server.create_initialization_options(),
            )

    async def handle_messages(request: Request):
        """Handle POST messages from SSE clients."""
        await sse_transport.handle_post_message(
            request.scope, request.receive, request._send
        )

    # -----------------------------------------------------------------------
    # Health check
    # -----------------------------------------------------------------------
    async def health(request: Request):
        return JSONResponse({"status": "ok", "server": "somanylemons-mcp"})

    # -----------------------------------------------------------------------
    # Starlette sub-app for non-MCP routes
    # -----------------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=handle_messages, methods=["POST"]),
            Route("/health", endpoint=health),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=["*"],
                expose_headers=["mcp-session-id"],
            ),
        ],
        lifespan=lifespan,
    )

    # -----------------------------------------------------------------------
    # Top-level ASGI app: routes /mcp directly to session manager,
    # everything else to Starlette.
    # -----------------------------------------------------------------------
    async def app(scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            # Extract API key before delegating to session manager
            request = Request(scope, receive)
            client_key = request.headers.get("x-api-key", "")
            if not client_key:
                response = JSONResponse(
                    {"error": "X-API-Key header required"},
                    status_code=401,
                )
                await response(scope, receive, send)
                return

            _srv._session_api_key.set(client_key)
            await session_manager.handle_request(scope, receive, send)
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

    import uvicorn
    uvicorn.run(_create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

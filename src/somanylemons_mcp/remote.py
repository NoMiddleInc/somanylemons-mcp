"""
SoManyLemons Remote MCP Server (SSE transport).

Serves the same MCP tools as the local server, but over HTTP/SSE
so clients can connect without installing anything.

Usage:
    sml-mcp-remote                          # default port 8080
    sml-mcp-remote --port 3000
    SML_API_KEY=sml_xxx sml-mcp-remote      # single-user mode (dev/testing)

In production, each client passes their own API key via X-API-Key header.
The server extracts it and uses it for all API calls in that session.
"""

import argparse
import logging
import os

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp.server.sse import SseServerTransport

logger = logging.getLogger(__name__)

import somanylemons_mcp.server as _srv


def _create_sse_app() -> Starlette:
    """Build the Starlette ASGI app with SSE endpoints."""

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        # Extract the client's API key from the request header.
        client_key = request.headers.get("x-api-key", "")
        if not client_key:
            return JSONResponse(
                {"error": "X-API-Key header required"},
                status_code=401,
            )

        # Set the API key for this session via contextvar (safe for concurrent connections).
        _srv._session_api_key.set(client_key)

        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await _srv.server.run(
                read_stream,
                write_stream,
                _srv.server.create_initialization_options(),
            )
        return None

    async def handle_messages(request: Request):
        """Handle POST messages from SSE clients."""
        await sse_transport.handle_post_message(
            request.scope, request.receive, request._send
        )
        return None

    async def health(request: Request):
        return JSONResponse({"status": "ok", "server": "somanylemons-mcp"})

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=handle_messages, methods=["POST"]),
            Route("/health", endpoint=health),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST"],
                allow_headers=["*"],
            ),
        ],
    )
    return app


def main():
    parser = argparse.ArgumentParser(description="SoManyLemons Remote MCP Server (SSE)")
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
    uvicorn.run(_create_sse_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

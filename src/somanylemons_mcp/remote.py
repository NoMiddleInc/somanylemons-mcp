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
import os

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

logger = logging.getLogger(__name__)

import somanylemons_mcp.server as _srv


def _create_app() -> ASGIApp:
    """Build the ASGI app with Streamable HTTP endpoint."""

    session_manager = StreamableHTTPSessionManager(
        app=_srv.server,
        json_response=False,
        stateless=False,
        session_idle_timeout=1800.0,
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
                allow_origins=["*"],
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

#!/usr/bin/env python3
"""
ProducerSpark MCP Server

Model Context Protocol server for the ProducerSpark agents. Every tool is an
agent task tool served through task_bridge.

Usage:
    sml-mcp --api-key sml_xxxxx
    sml-mcp --api-url https://api.producerspark.com --api-key sml_xxxxx

Or via environment variables:
    SML_API_KEY=sml_xxxxx sml-mcp
"""

import argparse
import os
import sys

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Icon
except ImportError:
    print(
        "Error: mcp package not installed. Install with:\n"
        "  pip install somanylemons-mcp\n",
        file=sys.stderr,
    )
    sys.exit(1)

import contextvars


API_URL = os.environ.get("SML_API_URL", "https://api.producerspark.com")
API_KEY = os.environ.get("SML_API_KEY", "")

# Per-session API key for the remote (multi-tenant) server.
# Each SSE connection sets its own key via contextvars so concurrent
# sessions never share or overwrite each other's credentials.
_session_api_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_api_key", default=""
)

# Set to True by remote.py at startup.
REMOTE_MODE = False


def _request_identity():
    if not REMOTE_MODE:
        return {"api_key": _session_api_key.get() or API_KEY, "research_only": _research_only.get()}
    try:
        request = server.request_context.request
        identity = request.scope.get("state", {}).get("producerspark_mcp_identity") if request else None
    except LookupError:
        identity = None
    if not identity:
        raise ValueError("Authenticated HTTP request context is required.")
    return identity


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_research_only: contextvars.ContextVar[bool] = contextvars.ContextVar("research_only", default=False)

from importlib.resources import files
from .task_bridge import SCHEMA_SERVER
RESEARCH_SKILL = files("somanylemons_mcp").joinpath("skills/producerspark/SKILL.md").read_text()
RESEARCH_INSTRUCTIONS = RESEARCH_SKILL.split("---", 2)[2].strip()

server = Server(
    "ProducerSpark",
    website_url="https://producerspark.com",
    icons=[Icon(src="https://producerspark.com/images/agents/prospect-custodian-v2.png", mimeType="image/png")],
    instructions=(
        RESEARCH_INSTRUCTIONS + "\n\n"
        "No research task authorizes content publishing or outreach. "
        + (SCHEMA_SERVER.instructions or "")
    ),
)


@server.list_tools()
async def list_tools():
    from .task_bridge import task_schemas
    return await task_schemas()



@server.read_resource()
async def read_resource(uri):
    from mcp.server.lowlevel.helper_types import ReadResourceContents
    from .task_bridge import read_task_artifact, read_task_feedback_attachment
    key = _request_identity()["api_key"]
    if str(uri).startswith("producerspark-feedback-attachment://"):
        content, mime = await read_task_feedback_attachment(uri, api_url=API_URL, api_key=key)
    else:
        content = await read_task_artifact(uri, api_url=API_URL, api_key=key)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return [ReadResourceContents(content, mime)]


from .task_bridge import TASK_TOOL_NAMES


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    from .task_bridge import invoke_task
    if name not in TASK_TOOL_NAMES:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    key = _request_identity()["api_key"]
    return await invoke_task(name, arguments, api_url=API_URL, api_key=key)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    global API_URL, API_KEY

    parser = argparse.ArgumentParser(description="ProducerSpark MCP Server")
    parser.add_argument("--api-url", default=API_URL, help="Base URL of the SML API")
    parser.add_argument("--api-key", default=API_KEY, help="API key (sml_xxxxx)")
    args = parser.parse_args()

    API_URL = args.api_url.rstrip("/")
    API_KEY = args.api_key

    if not API_KEY:
        print("Error: API key required. Set SML_API_KEY or use --api-key", file=sys.stderr)
        sys.exit(1)

    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()

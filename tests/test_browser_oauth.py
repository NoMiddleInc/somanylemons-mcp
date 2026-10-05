"""The hosted MCP discovers browser auth and isolates refreshed sessions."""

import unittest
from unittest.mock import patch
import httpx
import somanylemons_mcp.remote as remote
import somanylemons_mcp.server as server


class BrowserAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_unauthenticated_challenge(self):
        app = remote._create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://mcp.example"
        ) as client:
            result = await client.get("/.well-known/oauth-protected-resource")
            self.assertEqual(
                result.json()["resource"], "https://producerspark.com/mcp"
            )
            denied = await client.post("/mcp")
            self.assertEqual(denied.status_code, 401)
            self.assertIn("resource_metadata", denied.headers["www-authenticate"])
            self.assertEqual(
                (await client.get("/skills/producerspark.zip")).status_code, 200
            )

    async def test_bearer_refresh_preserves_owner_but_foreign_account_fails(self):
        seen = []

        class Manager:
            def __init__(self, **kwargs):
                pass

            async def handle_request(self, scope, receive, send):
                seen.append(
                    (server._session_api_key.get(), server._research_only.get())
                )
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"mcp-session-id", b"browser-session")],
                    }
                )
                await send({"type": "http.response.body", "body": b"{}"})

        real_client = httpx.AsyncClient

        def introspection(request):
            token = request.headers["authorization"].split()[1]
            identity = "other" if token == "foreign" else "owner"
            return httpx.Response(
                200,
                json={
                    "active": token != "revoked",
                    "resource": "https://mcp.somanylemons.com/mcp",
                    "scope": "tasks:read tasks:write",
                    "session_binding": "oauth:" + identity,
                },
            )

        def backend_client(*args, **kwargs):
            return real_client(transport=httpx.MockTransport(introspection))

        with patch.object(remote, "StreamableHTTPSessionManager", Manager):
            app = remote._create_app()
            async with real_client(
                transport=httpx.ASGITransport(app=app), base_url="https://mcp.example"
            ) as client:
                with patch.object(remote.httpx, "AsyncClient", backend_client):
                    self.assertEqual(
                        (
                            await client.post(
                                "/mcp", headers={"authorization": "Bearer initial"}
                            )
                        ).status_code,
                        200,
                    )
                    self.assertEqual(
                        (
                            await client.post(
                                "/mcp",
                                headers={
                                    "authorization": "Bearer refreshed",
                                    "mcp-session-id": "browser-session",
                                },
                            )
                        ).status_code,
                        200,
                    )
                    self.assertEqual(
                        (
                            await client.post(
                                "/mcp",
                                headers={
                                    "authorization": "Bearer foreign",
                                    "mcp-session-id": "browser-session",
                                },
                            )
                        ).status_code,
                        403,
                    )
                    self.assertEqual(
                        (
                            await client.post(
                                "/mcp",
                                headers={
                                    "authorization": "Bearer revoked",
                                    "mcp-session-id": "browser-session",
                                },
                            )
                        ).status_code,
                        401,
                    )
        self.assertEqual(seen, [("initial", True), ("refreshed", True)])
        self.assertEqual(server._session_api_key.get(), "")
        self.assertFalse(server._research_only.get())

    async def test_browser_connections_only_advertise_research_tools(self):
        token = server._research_only.set(True)
        try:
            tools = await server.list_tools()
            self.assertEqual({t.name for t in tools}, server.TASK_TOOL_NAMES)
            denied = await server.call_tool("generate_content", {"topic": "test"})
            self.assertIn("research tools only", denied[0].text)
        finally:
            server._research_only.reset(token)
        self.assertIn("get_task", server.RESEARCH_INSTRUCTIONS)
        self.assertIn("name: producerspark", server.RESEARCH_SKILL)


class RealProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_tool_call_uses_refreshed_http_credential(self):
        import asyncio
        from contextlib import asynccontextmanager
        import json

        @asynccontextmanager
        async def lifespan(app):
            incoming, outgoing = asyncio.Queue(), asyncio.Queue()

            async def send(message):
                await outgoing.put(message)

            runner = asyncio.create_task(
                app(
                    {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}},
                    incoming.get,
                    send,
                )
            )
            await incoming.put({"type": "lifespan.startup"})
            self.assertEqual(
                (await asyncio.wait_for(outgoing.get(), 5))["type"],
                "lifespan.startup.complete",
            )
            try:
                yield
            finally:
                await incoming.put({"type": "lifespan.shutdown"})
                self.assertEqual(
                    (await asyncio.wait_for(outgoing.get(), 5))["type"],
                    "lifespan.shutdown.complete",
                )
                await runner

        real_client = httpx.AsyncClient
        forwarded = []

        def backend(request):
            if request.url.path == "/oauth/mcp/introspect":
                return httpx.Response(
                    200,
                    json={
                        "active": True,
                        "resource": "https://mcp.somanylemons.com/mcp",
                        "scope": "tasks:read tasks:write",
                        "session_binding": "oauth:real-protocol-owner",
                    },
                )
            forwarded.append(request.headers.get("x-api-key"))
            return httpx.Response(200, json={"data": {"goals": [], "page": 1}})

        def backend_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(backend)
            return real_client(*args, **kwargs)

        def decoded(response):
            return json.loads(
                next(
                    line[6:]
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                )
            )

        app = remote._create_app()
        with (
            patch.object(server, "REMOTE_MODE", True),
            patch.object(remote.httpx, "AsyncClient", backend_client),
        ):
            async with lifespan(app):
                async with real_client(
                    transport=httpx.ASGITransport(app=app),
                    base_url="https://mcp.somanylemons.com",
                ) as client:
                    old = {
                        "authorization": "Bearer sml_original_abcdefghijklmnopqrstuvwxyz",
                        "origin": "https://claude.ai",
                    }
                    init = await client.post(
                        "/mcp",
                        headers=old,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {
                                    "name": "browser-oauth-test",
                                    "version": "1",
                                },
                            },
                        },
                    )
                    self.assertEqual(init.status_code, 200, init.text)
                    self.assertIn(
                        "durable research", decoded(init)["result"]["instructions"]
                    )
                    self.assertEqual(
                        init.headers.get("access-control-allow-origin"),
                        "https://claude.ai",
                    )
                    headers = {
                        **old,
                        "mcp-session-id": init.headers["mcp-session-id"],
                        "mcp-protocol-version": "2025-03-26",
                    }
                    await client.post(
                        "/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    )
                    tools = await client.post(
                        "/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    )
                    self.assertEqual(
                        {tool["name"] for tool in decoded(tools)["result"]["tools"]},
                        server.TASK_TOOL_NAMES,
                    )
                    headers["authorization"] = (
                        "Bearer sml_refreshed_abcdefghijklmnopqrstuvwxyz"
                    )
                    result = await client.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {"name": "list_tasks", "arguments": {}},
                        },
                    )
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertNotIn("error", decoded(result))
        self.assertEqual(forwarded, ["sml_refreshed_abcdefghijklmnopqrstuvwxyz"])

"""The hosted MCP discovers browser auth and isolates refreshed sessions."""
import unittest
from unittest.mock import patch
import httpx
import somanylemons_mcp.remote as remote
import somanylemons_mcp.server as server

class BrowserAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_unauthenticated_challenge(self):
        app = remote._create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example") as client:
            result = await client.get("/.well-known/oauth-protected-resource")
            self.assertEqual(result.json()["resource"], "https://mcp.somanylemons.com/mcp")
            denied = await client.post("/mcp")
            self.assertEqual(denied.status_code, 401)
            self.assertIn("resource_metadata", denied.headers["www-authenticate"])
            self.assertEqual((await client.get("/skills/producerspark.zip")).status_code, 200)

    async def test_bearer_refresh_preserves_owner_but_foreign_account_fails(self):
        seen = []
        class Manager:
            def __init__(self, **kwargs): pass
            async def handle_request(self, scope, receive, send):
                seen.append((server._session_api_key.get(), server._research_only.get()))
                await send({"type":"http.response.start", "status":200, "headers":[(b"mcp-session-id", b"browser-session")]})
                await send({"type":"http.response.body", "body":b"{}"})
        real_client = httpx.AsyncClient
        def introspection(request):
            token = request.headers["authorization"].split()[1]
            identity = "other" if token == "foreign" else "owner"
            return httpx.Response(200, json={"active":token != "revoked", "resource":"https://mcp.somanylemons.com/mcp", "scope":"tasks:read tasks:write", "session_binding":"oauth:"+identity})
        def backend_client(*args, **kwargs):
            return real_client(transport=httpx.MockTransport(introspection))
        with patch.object(remote, "StreamableHTTPSessionManager", Manager):
            app = remote._create_app()
            async with real_client(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example") as client:
                with patch.object(remote.httpx, "AsyncClient", backend_client):
                    self.assertEqual((await client.post("/mcp", headers={"authorization":"Bearer initial"})).status_code, 200)
                    self.assertEqual((await client.post("/mcp", headers={"authorization":"Bearer refreshed", "mcp-session-id":"browser-session"})).status_code, 200)
                    self.assertEqual((await client.post("/mcp", headers={"authorization":"Bearer foreign", "mcp-session-id":"browser-session"})).status_code, 403)
                    self.assertEqual((await client.post("/mcp", headers={"authorization":"Bearer revoked", "mcp-session-id":"browser-session"})).status_code, 401)
        self.assertEqual(seen, [("initial", True), ("refreshed", True)])
        self.assertEqual(server._session_api_key.get(), "")
        self.assertFalse(server._research_only.get())

    async def test_browser_connections_only_advertise_research_tools(self):
        token = server._research_only.set(True)
        try:
            tools = await server.list_tools()
            self.assertEqual({t.name for t in tools}, server.TASK_TOOL_NAMES)
            denied = await server.call_tool("generate_content", {"topic":"test"})
            self.assertIn("research tools only", denied[0].text)
        finally:
            server._research_only.reset(token)
        self.assertIn("get_task", server.RESEARCH_INSTRUCTIONS)
        self.assertIn("name: producerspark", server.RESEARCH_SKILL)

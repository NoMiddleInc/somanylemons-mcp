"""The hosted endpoint as claude.ai uses it: several chats, several instances, redeploys.

claude.ai opens a fresh MCP connection for most tool calls (initialize, initialized,
tools/list, resources/list, then tools/call) and sometimes sends a request with a session id
issued by another instance or an older revision, or with no session id at all. Production on
2026-11-07 answered those with 404 (session unknown on this instance), 400 (missing session id)
and 429 (protocol overhead counted against a 60 requests a minute budget).
"""
import json
import socket
import threading
import time
import unittest

import httpx
import uvicorn

import somanylemons_mcp.remote as remote

KEY = "sml_transport_test_key_0123456789"
HEADERS = {
    "x-api-key": KEY,
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
    "mcp-protocol-version": "2025-06-18",
}
INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def rpc_result(response: httpx.Response) -> dict:
    """The JSON-RPC message from a JSON or single-event SSE response."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


class HostedTransportTests(unittest.TestCase):
    """Runs the real ASGI app (session manager included) on a local port."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        config = uvicorn.Config(remote._create_app(), host="127.0.0.1", port=cls.port, log_level="error", lifespan="on")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("test server did not start")
            time.sleep(0.05)
        cls.url = f"http://127.0.0.1:{cls.port}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    def setUp(self):
        remote._rate_buckets.clear()
        remote._tool_call_buckets.clear()

    def post(self, message, **headers):
        return httpx.post(self.url, json=message, headers={**HEADERS, **headers}, timeout=10)

    def test_session_id_from_another_instance_or_revision_is_served(self):
        # Session ids live in one instance's memory; after a redeploy or on a second instance
        # the id is unknown. The request is still answered instead of failing with 404.
        response = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, **{"mcp-session-id": "f" * 32})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertIn("find_people", [tool["name"] for tool in rpc_result(response)["result"]["tools"]])

    def test_request_without_a_session_id_is_served(self):
        response = self.post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual(response.status_code, 200, response.text[:200])

    def test_initialize_still_works(self):
        response = self.post(INITIALIZE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(rpc_result(response)["result"]["serverInfo"]["name"], "ProducerSpark")

    def test_connection_overhead_of_several_chats_is_not_rate_limited(self):
        # Five chats, each tool call preceded by initialize, initialized, tools/list and
        # resources/list: 13 tool calls a minute arrive as 65 requests.
        for index in range(13):
            for message in (
                INITIALIZE,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 11, "method": "resources/list"},
            ):
                response = self.post(message)
                self.assertNotEqual(response.status_code, 429, f"overhead request {index} limited")
            call = self.post({"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                              "params": {"name": "no_such_tool", "arguments": {}}})
            self.assertEqual(call.status_code, 200, f"tool call {index}: {call.status_code}")

    def test_tool_calls_are_still_limited_per_account(self):
        statuses = []
        for _ in range(remote._TOOL_CALL_LIMIT + 1):
            statuses.append(self.post({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                       "params": {"name": "no_such_tool", "arguments": {}}}).status_code)
        self.assertEqual(statuses[:-1], [200] * remote._TOOL_CALL_LIMIT)
        self.assertEqual(statuses[-1], 429)


if __name__ == "__main__":
    unittest.main()

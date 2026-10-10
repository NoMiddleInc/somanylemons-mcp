"""Recording and Scheduling tools forward typed arguments to one backend action each."""
import json
import unittest
import httpx
from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.agent_tools import AGENT_TOOL_NAMES

KEY = "33333333-3333-4333-8333-333333333333"


class AgentToolTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, name, arguments, response=None, status=200):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(status, json=response or {"data": {"id": 5, "status": "draft"}})
        result = await invoke_task(name, arguments, api_url="https://example.com", api_key="scoped-owner",
                                   transport=httpx.MockTransport(handler))
        return calls, result

    async def test_every_tool_is_offered_with_honest_annotations(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertEqual(len(AGENT_TOOL_NAMES), 21)
        self.assertTrue(AGENT_TOOL_NAMES <= TASK_TOOL_NAMES)
        self.assertTrue(AGENT_TOOL_NAMES <= set(tools))
        read_only = {"get_posting_overview", "list_posts", "get_post", "list_recordings", "get_recording", "list_recording_designs"}
        destructive = {"publish_post_now", "delete_post", "remove_recording"}
        for name in AGENT_TOOL_NAMES:
            tool = tools[name]
            self.assertEqual(tool.annotations.readOnlyHint, name in read_only, name)
            self.assertEqual(tool.annotations.destructiveHint, name in destructive, name)
            if name not in read_only:
                self.assertIn("idempotency_key", tool.inputSchema["required"], name)

    async def test_reschedule_forwards_only_what_was_given_to_its_own_action(self):
        calls, result = await self._call(
            "schedule_post", {"post_id": 5, "scheduled_for": "2026-10-14T09:00", "idempotency_key": KEY},
            {"data": {"id": 5, "status": "scheduled", "scheduled_for": "2026-10-14T09:00:00-05:00"}})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].method, "POST")
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/agent-tools/schedule_post")
        self.assertEqual(json.loads(calls[0].content), {"post_id": 5, "scheduled_for": "2026-10-14T09:00", "idempotency_key": KEY})
        self.assertEqual(calls[0].headers["x-api-key"], "scoped-owner")
        self.assertIn("2026-10-14T09:00:00-05:00", str(result))

    async def test_read_tool_sends_no_key_and_drops_unset_filters(self):
        calls, _ = await self._call("list_posts", {"status": "scheduled"}, {"data": {"posts": []}})
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/agent-tools/list_posts")
        self.assertEqual(json.loads(calls[0].content), {"status": "scheduled", "page": 1})

    async def test_create_post_forwards_media_links_and_schedule_choice(self):
        calls, _ = await self._call("create_post", {
            "idempotency_key": KEY, "caption": "Hello", "network": "linkedin",
            "media_urls": ["https://files.example/a.png"], "next_open_slot": True})
        self.assertEqual(json.loads(calls[0].content), {
            "idempotency_key": KEY, "caption": "Hello", "network": "linkedin",
            "media_urls": ["https://files.example/a.png"], "next_open_slot": True})

    async def test_write_without_a_key_or_with_an_unknown_network_never_reaches_the_backend(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {}})
        for arguments in ({"post_id": 5}, {"post_id": 5, "idempotency_key": KEY, "network": "facebook"}):
            with self.assertRaises(Exception):
                await invoke_task("update_post", arguments, api_url="https://example.com",
                                  api_key="scoped-owner", transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [])

    async def test_backend_refusal_is_reported_without_task_wording(self):
        with self.assertRaises(Exception) as raised:
            await self._call(
                "update_post", {"post_id": 5, "caption": "x", "idempotency_key": KEY},
                {"detail": "This post is already published and cannot be changed."}, status=409)
        text = str(raised.exception)
        self.assertIn("already published", text)
        self.assertNotIn("Refresh the task", text)

    async def test_recording_tools_reach_their_actions(self):
        for name, arguments in (
            ("change_clip_design", {"clip_id": 9, "design_id": 3, "idempotency_key": KEY}),
            ("update_clip_text", {"clip_id": 9, "text": "New text", "idempotency_key": KEY}),
            ("add_recording", {"media_url": "https://files.example/talk.mp4", "idempotency_key": KEY}),
            ("remove_recording", {"recording_id": "abc", "idempotency_key": KEY}),
        ):
            calls, _ = await self._call(name, arguments)
            self.assertEqual(calls[0].url.path, f"/api/v1/agent-tasks/agent-tools/{name}")
            self.assertEqual(json.loads(calls[0].content), arguments)


if __name__ == "__main__":
    unittest.main()

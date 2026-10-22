"""Recording and Scheduling tools forward typed arguments to one backend action each."""
import json
import unittest
from unittest import mock
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
        self.assertEqual(len(AGENT_TOOL_NAMES), 25)
        self.assertTrue(AGENT_TOOL_NAMES <= TASK_TOOL_NAMES)
        self.assertTrue(AGENT_TOOL_NAMES <= set(tools))
        read_only = {"get_posting_overview", "list_posts", "get_post", "list_recordings", "get_recording", "list_recording_designs", "get_email_sequence", "start_browser_recording", "wait_for_recording"}
        destructive = {"publish_post_now", "delete_post", "remove_recording"}
        for name in AGENT_TOOL_NAMES:
            tool = tools[name]
            self.assertEqual(tool.annotations.readOnlyHint, name in read_only, name)
            self.assertEqual(tool.annotations.destructiveHint, name in destructive, name)
            if name not in read_only:
                self.assertIn("idempotency_key", tool.inputSchema["required"], name)

    async def test_get_started_reads_the_account_without_arguments(self):
        calls, result = await self._call(
            "get_started", {},
            {"data": {"plan": "free_trial", "can_search_people": True, "free_prospects": {"granted": 15, "used": 0, "remaining": 15}}})
        self.assertEqual((calls[0].method, calls[0].url.path), ("GET", "/api/v1/agent-tasks/getting-started"))
        self.assertIn("free_trial", json.dumps(str(result)))
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertTrue(tools["get_started"].annotations.readOnlyHint)
        self.assertEqual(tools["get_started"].inputSchema.get("required", []), [])

    async def test_a_finished_search_links_back_to_its_page_on_the_site(self):
        _, result = await self._call(
            "find_people", {"request": "Find 5 CFOs in Ohio", "idempotency_key": KEY},
            {"data": {"id": 41, "state": "completed", "quick_search": {"rows": [], "coverage": {}}}})
        self.assertIn("https://producerspark.com/agent?tab=tasks&task=41", json.dumps(str(result)))
        self.assertIn("reply_must_end_with", str(result))
        self.assertIn("[Open this search in ProducerSpark](https://producerspark.com/agent?tab=tasks&task=41)", str(result))
        tools = {tool.name: tool for tool in await task_schemas()}
        for name in ("find_people", "deep_search", "full_list"):
            self.assertIn("reply_must_end_with", tools[name].description, name)
        self.assertIn("Open ProducerSpark", tools["get_started"].description)

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

    async def test_every_other_write_tool_reaches_its_own_action(self):
        for name, arguments in (
            ("unschedule_post", {"post_id": 5, "idempotency_key": KEY}),
            ("copy_post_to_network", {"post_id": 5, "network": "twitter", "idempotency_key": KEY}),
            ("recut_recording", {"recording_id": "abc", "instruction": "Only the pricing part", "idempotency_key": KEY}),
            ("retry_recording", {"recording_id": "abc", "idempotency_key": KEY}),
            ("set_default_recording_design", {"design_id": 3, "idempotency_key": KEY}),
            ("send_clip_to_scheduling", {"clip_id": 9, "idempotency_key": KEY}),
            ("pause_email_agent", {"campaign_id": 44, "idempotency_key": KEY}),
        ):
            calls, _ = await self._call(name, arguments)
            self.assertEqual(calls[0].url.path, f"/api/v1/agent-tasks/agent-tools/{name}")
            self.assertEqual(json.loads(calls[0].content), arguments)

    async def test_publishing_shows_what_will_happen_then_sends_the_users_yes(self):
        waiting = {"data": {"needs_confirmation": True, "done": False, "confirmation": "ab12cd34ef56ab78",
                            "will_happen": "Post 5 will be published to X (@acme) IMMEDIATELY."}}
        calls, result = await self._call("publish_post_now", {"post_id": 5, "idempotency_key": KEY}, waiting)
        self.assertEqual(json.loads(calls[0].content), {"post_id": 5, "idempotency_key": KEY})
        self.assertIn("Post 5 will be published to X (@acme) IMMEDIATELY.", str(result))
        self.assertIn("ab12cd34ef56ab78", str(result))

        calls, _ = await self._call("publish_post_now", {"post_id": 5, "idempotency_key": KEY, "confirmation": "ab12cd34ef56ab78"})
        self.assertEqual(json.loads(calls[0].content), {"post_id": 5, "idempotency_key": KEY, "confirmation": "ab12cd34ef56ab78"})

    async def test_only_actions_that_post_or_remove_take_a_confirmation(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        takes = {name for name in AGENT_TOOL_NAMES if "confirmation" in tools[name].inputSchema["properties"]}
        self.assertEqual(takes, {"create_post", "schedule_post", "publish_post_now", "delete_post", "remove_recording", "change_clip_design"})
        for name in takes:
            self.assertIn("will_happen", tools[name].description, name)

    async def test_teammate_can_name_the_account_on_any_tool(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        for name in AGENT_TOOL_NAMES:
            self.assertIn("account_id", tools[name].inputSchema["properties"], name)
            self.assertNotIn("account_id", tools[name].inputSchema.get("required", []), name)
        calls, _ = await self._call("list_posts", {"account_id": 10}, {"data": {"posts": [], "acting_in_account": "ben@example.com"}})
        self.assertEqual(json.loads(calls[0].content), {"page": 1, "account_id": 10})

    async def test_posting_rules_survive_a_client_that_keeps_only_the_start_of_the_instructions(self):
        from somanylemons_mcp import server
        kept = server.server.instructions[:1500]
        for text in ("find_people", "list_posts", "list_recordings", "needs_confirmation", "will_happen", "clear yes"):
            self.assertIn(text, kept)
        self.assertLess(len(server.OVERVIEW), 800)

    async def test_only_one_tool_starts_email_sending_and_it_asks_first(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertIn("pause_email_agent", tools)
        starters = {name for name in tools if "start" in name and ("email" in name or "sending" in name)}
        self.assertEqual(starters, {"start_campaign_sending"})
        self.assertIn("confirmation", tools["start_campaign_sending"].inputSchema["properties"])
        self.assertIn("sends nothing", tools["enroll_campaign_people"].description)


    async def test_browser_recording_hands_off_the_recorder_link_and_the_time_to_wait_from(self):
        started = {"data": {"record_url": "https://producerspark.com/agent?tab=recording", "started_at": "2026-10-12T15:00:00Z", "latest_recording_id": None}}
        calls, result = await self._call("start_browser_recording", {}, started)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/agent-tools/start_browser_recording")
        self.assertEqual(json.loads(calls[0].content), {})
        self.assertIn("https://producerspark.com/agent?tab=recording", str(result))
        self.assertIn("2026-10-12T15:00:00Z", str(result))

    async def test_waiting_for_a_recording_returns_as_soon_as_the_backend_finds_it(self):
        found = {"data": {"status": "found", "recording": {"id": "abc", "status": "editing", "clips": []}}}
        calls, result = await self._call("wait_for_recording", {"since": "2026-10-12T15:00:00Z"}, found)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/agent-tools/wait_for_recording")
        self.assertEqual(json.loads(calls[0].content), {"since": "2026-10-12T15:00:00Z"})
        self.assertIn("found", str(result))

    async def test_waiting_for_a_recording_checks_again_until_its_window_ends(self):
        waiting = {"data": {"status": "waiting", "record_url": "https://producerspark.com/agent?tab=recording"}}
        with mock.patch("somanylemons_mcp.task_tools.agent_tools.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            calls, result = await self._call("wait_for_recording", {"since": "2026-10-12T15:00:00Z", "timeout_seconds": 12}, waiting)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.await_count, 2)
        self.assertIn("waiting", str(result))

    async def test_a_single_check_never_sleeps(self):
        waiting = {"data": {"status": "waiting"}}
        with mock.patch("somanylemons_mcp.task_tools.agent_tools.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            calls, _ = await self._call("wait_for_recording", {"since": "2026-10-12T15:00:00Z", "timeout_seconds": 0}, waiting)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

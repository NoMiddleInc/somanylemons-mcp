"""Campaign tools mirror the backend's /api/v1/agent-tasks/campaigns routes one call each."""
import json
import unittest

import httpx

from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.server import create_server
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig

KEY = "33333333-3333-4333-8333-333333333333"

CAMPAIGN = {
    "id": 12, "name": "Texas producers", "objective": "Book meetings with Texas producers",
    "status": "active", "is_default": True,
    "golden_list": {"id": 44, "name": "Texas", "prospect_count": 120},
    "agents": [{"agent": "prospect_keeper", "label": "Prospect Golden List Agent", "is_enabled": True,
                "settings": {"daily_prospect_count": 10}}],
    "updated_at": "2026-10-14T12:00:00Z",
}


class CampaignToolTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, name, arguments, response, status=200):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(status, json=response)

        result = await invoke_task(name, arguments, api_url="https://example.com", api_key="owner",
                                   transport=httpx.MockTransport(handler))
        return calls, result

    @staticmethod
    def _payload(result):
        content = result[0] if isinstance(result, tuple) else result
        return json.loads(content[0].text)

    async def test_tools_are_offered_with_honest_annotations(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        for name in ("list_campaigns", "create_campaign", "update_campaign"):
            self.assertIn(name, TASK_TOOL_NAMES)
            self.assertIn(name, tools)
            self.assertNotIn("apollo", tools[name].description.lower())
            self.assertNotIn("—", tools[name].description)
        self.assertTrue(tools["list_campaigns"].annotations.readOnlyHint)
        self.assertFalse(tools["create_campaign"].annotations.readOnlyHint)
        self.assertTrue(tools["create_campaign"].annotations.idempotentHint)
        self.assertTrue(tools["update_campaign"].annotations.idempotentHint)
        self.assertEqual(set(tools["create_campaign"].inputSchema["required"]), {"name", "objective", "agents", "idempotency_key"})
        self.assertEqual(tools["update_campaign"].inputSchema["required"], ["campaign_id"])

    async def test_list_campaigns_reads_and_links_each_campaign(self):
        calls, result = await self._call("list_campaigns", {}, {"success": True, "data": {"campaigns": [CAMPAIGN]}})
        self.assertEqual((calls[0].method, calls[0].url.path), ("GET", "/api/v1/agent-tasks/campaigns"))
        campaign = self._payload(result)["campaigns"][0]
        self.assertEqual(campaign["view_in_producerspark"], "https://producerspark.com/agent/12")
        self.assertEqual(campaign["objective"], "Book meetings with Texas producers")

    async def test_create_campaign_posts_the_write_shape(self):
        calls, result = await self._call("create_campaign", {
            "name": "Texas producers", "objective": "Book meetings with Texas producers",
            "agents": [{"agent": "prospect_keeper", "settings": {"daily_prospect_count": 10}},
                       {"agent": "pipeline_piper"}],
            "golden_list_id": 44, "idempotency_key": KEY,
        }, {"data": CAMPAIGN}, status=201)
        self.assertEqual((calls[0].method, calls[0].url.path), ("POST", "/api/v1/agent-tasks/campaigns"))
        self.assertEqual(json.loads(calls[0].content), {
            "name": "Texas producers", "objective": "Book meetings with Texas producers",
            "agents": [{"agent": "prospect_keeper", "settings": {"daily_prospect_count": 10}},
                       {"agent": "pipeline_piper"}],
            "golden_list_id": 44, "creation_key": KEY,
        })
        self.assertEqual(self._payload(result)["view_in_producerspark"], "https://producerspark.com/agent/12")

    async def test_create_campaign_rejects_unknown_agents_before_calling(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {}})

        with self.assertRaises(Exception):
            await invoke_task("create_campaign", {"name": "x", "objective": "y", "agents": [{"agent": "email"}], "idempotency_key": KEY},
                              api_url="https://example.com", api_key="owner", transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [])

    async def test_create_campaign_passes_the_backend_reason_through(self):
        with self.assertRaises(Exception) as raised:
            await self._call("create_campaign", {
                "name": "Texas", "objective": "Book meetings", "agents": [{"agent": "prospect_keeper"}], "idempotency_key": KEY,
            }, {"detail": "Add a Golden List before enabling the Prospect agent."}, status=400)
        text = str(raised.exception)
        self.assertIn("HTTP 400", text)
        self.assertIn("Add a Golden List before enabling the Prospect agent.", text)

    async def test_update_campaign_patches_only_what_changed(self):
        calls, result = await self._call("update_campaign", {
            "campaign_id": 12, "objective": "3 Instagram carousels a week",
            "agents": [{"agent": "recording", "settings": {"format": "carousel", "platforms": ["instagram"]}}],
        }, {"data": {**CAMPAIGN, "objective": "3 Instagram carousels a week"}})
        self.assertEqual((calls[0].method, calls[0].url.path), ("PATCH", "/api/v1/agent-tasks/campaigns/12"))
        self.assertEqual(json.loads(calls[0].content), {
            "objective": "3 Instagram carousels a week",
            "agents": [{"agent": "recording", "settings": {"format": "carousel", "platforms": ["instagram"]}}],
        })
        payload = self._payload(result)
        self.assertEqual(payload["objective"], "3 Instagram carousels a week")
        self.assertEqual(payload["view_in_producerspark"], "https://producerspark.com/agent/12")

    async def test_create_campaign_requires_a_key_and_repeats_it(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": CAMPAIGN})

        arguments = {"name": "Texas", "objective": "Book meetings", "agents": [{"agent": "recording"}]}
        with self.assertRaises(Exception):
            await invoke_task("create_campaign", arguments, api_url="https://example.com", api_key="owner",
                              transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [])
        for _ in range(2):
            await invoke_task("create_campaign", {**arguments, "idempotency_key": KEY}, api_url="https://example.com",
                              api_key="owner", transport=httpx.MockTransport(handler))
        self.assertEqual([json.loads(call.content)["creation_key"] for call in calls], [KEY, KEY])

    async def test_update_campaign_clears_the_golden_list_only_when_asked(self):
        calls, _ = await self._call("update_campaign", {"campaign_id": 12, "clear_golden_list": True}, {"data": CAMPAIGN})
        self.assertEqual(json.loads(calls[0].content), {"golden_list_id": None})
        calls, _ = await self._call("update_campaign", {"campaign_id": 12, "name": "New"}, {"data": CAMPAIGN})
        self.assertEqual(json.loads(calls[0].content), {"name": "New"})
        with self.assertRaises(Exception):
            await self._call("update_campaign", {"campaign_id": 12, "golden_list_id": 44, "clear_golden_list": True},
                             {"data": CAMPAIGN})

    async def test_update_campaign_passes_the_backend_reason_through(self):
        with self.assertRaises(Exception) as raised:
            await self._call("update_campaign", {"campaign_id": 12, "agents": [{"agent": "pipeline_piper"}]},
                             {"detail": "Add a Golden List before enabling the Email agent."}, status=400)
        self.assertIn("Add a Golden List before enabling the Email agent.", str(raised.exception))

    async def test_unknown_campaign_is_a_404(self):
        with self.assertRaises(Exception) as raised:
            await self._call("update_campaign", {"campaign_id": 99, "name": "New"}, {"detail": "Not found."}, status=404)
        text = str(raised.exception)
        self.assertIn("HTTP 404", text)
        self.assertNotIn("Refresh the task", text)

    async def test_get_started_passes_campaigns_through(self):
        data = {"plan": "free_trial", "can_search_people": True, "has_campaign": True, "campaigns": [CAMPAIGN],
                "next_step": "Greet them."}
        calls, result = await self._call("get_started", {}, {"success": True, "data": data})
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/getting-started")
        payload = self._payload(result)
        self.assertTrue(payload["has_campaign"])
        self.assertEqual(payload["campaigns"], [CAMPAIGN])

    async def test_get_started_without_a_campaign_says_so(self):
        _, result = await self._call("get_started", {}, {"data": {"has_campaign": False, "campaigns": []}})
        payload = self._payload(result)
        self.assertIs(payload["has_campaign"], False)
        self.assertEqual(payload["campaigns"], [])
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertIn("has_campaign is false", tools["get_started"].description)
        self.assertIn("create_campaign", tools["get_started"].description)

    def test_instructions_say_to_save_a_stated_goal(self):
        instructions = create_server(TaskApiClient(TaskApiConfig("https://example.com", "k"))).instructions
        self.assertIn("has_campaign false", instructions)
        self.assertIn("create_campaign", instructions)
        self.assertIn("update_campaign", instructions)


if __name__ == "__main__":
    unittest.main()

"""Mock HTTP transports exercise hosted typed MCP forwarding, without research."""
import json
import unittest
import httpx
from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.server import compact_research_answer


class MilestoneToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_scope_and_annotations_are_narrow(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertIn("request_agency_first15_milestone", TASK_TOOL_NAMES)
        create = tools["request_agency_first15_milestone"]
        self.assertEqual(set(create.inputSchema["properties"]), {"goal_id", "expected_version", "expected_revision", "idempotency_key"})
        self.assertFalse(create.annotations.readOnlyHint)
        read = tools["get_agency_milestone"]
        self.assertTrue(read.annotations.readOnlyHint)
        self.assertEqual(read.inputSchema["properties"]["page"]["maximum"], 3)

    async def test_same_goal_original_version_key_forwarded_without_new_intake_or_sends(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {"original_goal_id": 24, "state": "paused", "version": 118,
                "original_all_obligation": "open", "delivery_authorized": False}})
        key = "22222222-2222-4222-8222-222222222222"
        result = await invoke_task("request_agency_first15_milestone", {"goal_id": 24, "expected_version": 117,
            "expected_revision": 3, "idempotency_key": key}, api_url="https://example.com", api_key="scoped-owner",
            transport=httpx.MockTransport(handler))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/24/milestones")
        self.assertEqual(json.loads(calls[0].content), {"expected_version": 117, "expected_revision": 3, "idempotency_key": key})
        self.assertEqual(calls[0].headers["x-api-key"], "scoped-owner")
        self.assertIn("open", str(result))

    async def test_paginated_native_contacts_and_original_progress_remain_separate(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {"original_goal_id": 24, "total": 15, "page": 3, "page_size": 5,
                "final": False, "original_all_obligation": "open", "rows": [{"id": "synthetic-contact"}]}})
        result = await invoke_task("get_agency_milestone", {"goal_id": 24, "milestone_id": 1200, "page": 3},
            api_url="https://example.com", api_key="scoped-owner", transport=httpx.MockTransport(handler))
        self.assertEqual(dict(calls[0].url.params), {"page": "3", "milestone_id": "1200"})
        self.assertIn("synthetic-contact", str(result))
        answer = compact_research_answer({"state": "queued", "fulfillment": "partial",
            "research_answer": {"milestones": [{"count": 15, "final": False, "original_all_obligation": "open"}],
                                "counts": {"agencies_requested": 110, "agencies_unresolved": 100}}})
        self.assertEqual(answer["research_answer"]["milestones"][0]["final"], False)
        self.assertEqual(answer["research_answer"]["counts"]["agencies_requested"], 110)

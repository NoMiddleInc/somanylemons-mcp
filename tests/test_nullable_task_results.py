"""Normal pending DTOs through the real HTTP client, FastMCP and typed handler."""
from copy import deepcopy
import json
import unittest

import httpx
from mcp import types
from mcp.server.lowlevel import Server

from somanylemons_mcp.task_bridge import invoke_task, task_schemas


class NullablePendingResultTests(unittest.IsolatedAsyncioTestCase):
    def task(self):
        return {
            "id": 24, "version": 117, "state": "needs_attention",
            "fulfillment": "partial", "contract_revision": 3,
            "allowed_actions": ["pause", "retry"],
            "progress": {"completed": 2, "total": 110},
            "contract": {"workflow": "agency_producer_research",
                         "request": "Synthetic ALL110 original request",
                         "agencies": [{"name": "Synthetic Agency"}] * 110,
                         "task_manager": {"revision": 3}},
            "research_answer": {"agencies": [], "final_delivery_ready": False},
            "tasks": [
                {"id": 101, "capability": "agency.artifact_prepare",
                 "contract_revision": 3, "state": "pending", "result": None},
                {"id": 102, "capability": "agency.research",
                 "contract_revision": 3, "state": "ready", "result": None},
            ],
        }

    async def read(self, task, name, arguments):
        calls = []
        before = deepcopy(task)

        def respond(request):
            calls.append((request.method, request.url.path, dict(request.url.params),
                          request.headers.get("x-api-key")))
            return httpx.Response(200, json={"success": True, "data": deepcopy(task)})

        low = Server("synthetic-typed-task-bridge")

        @low.list_tools()
        async def list_tools():
            return await task_schemas()

        @low.call_tool()
        async def call_tool(tool, args):
            return await invoke_task(tool, args, api_url="https://synthetic.invalid",
                                     api_key="sml_synthetic_unusable_key",
                                     transport=httpx.MockTransport(respond))

        request = types.CallToolRequest(method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments))
        result = (await low.request_handlers[types.CallToolRequest](request)).root
        self.assertIs(result.isError, False)
        answer = result.structuredContent
        if answer is None:
            answer = json.loads(next(item.text for item in result.content if item.type == "text"))
        self.assertEqual(task, before)
        self.assertEqual(calls, [("GET", "/api/v1/agent-tasks/24", {"view": "answer"},
                                 "sml_synthetic_unusable_key")])
        return answer

    async def test_pending_null_results_preserve_original_controls_and_incomplete_state(self):
        task = self.task()
        for name, args in (
            ("get_task", {"goal_id": 24, "include_history": False, "historical_snapshot": True}),
            ("get_research_answer", {"goal_id": 24, "details": True, "historical_snapshot": True}),
        ):
            with self.subTest(tool=name):
                answer = await self.read(task, name, args)
                self.assertEqual((answer["id"], answer["version"], answer["state"]),
                                 (24, 117, "needs_attention"))
                self.assertEqual(answer["fulfillment"], "partial")
                self.assertFalse(answer["research_answer"]["final_delivery_ready"])
                self.assertEqual(answer["request"]["agencies_total"], 110)
                self.assertTrue(answer["answer_resolution"]["historical_snapshot"])
                if name == "get_research_answer":
                    self.assertEqual(answer["contacts"], [])
                    self.assertEqual(answer["pagination"]["contacts_total"], 0)

    async def test_completed_saved_contact_survives_pending_siblings_and_old_revision_is_excluded(self):
        task = self.task()
        task["research_answer"]["agencies"] = [{"agency": "Synthetic Agency", "qualified_contacts": 1}]
        row = {"id": "native-synthetic", "name": "Synthetic Person", "company": "Synthetic Agency",
               "email": "saved@synthetic.invalid", "email_status": "saved_verified",
               "email_source": "Apollo", "email_observed_at": "2026-10-01T00:00:00Z",
               "enrichment_status": "completed", "enrichment_source": "Apollo",
               "enriched_on": "2026-10-01T00:00:00Z", "linkedin": "https://www.linkedin.com/in/synthetic",
               "task_paid_receipt": {"input_hash": "a" * 64, "receipt_hash": "b" * 64}}
        task["tasks"].extend([
            {"id": 103, "capability": "agency.research", "contract_revision": 3,
             "state": "succeeded", "result": {"agency": {"name": "Synthetic Agency"}, "rows": [row]}},
            {"id": 104, "capability": "agency.research", "contract_revision": 2,
             "state": "succeeded", "result": {"agency": {"name": "Synthetic Agency"},
                "rows": [{**row, "email": "old-revision@synthetic.invalid"}]}},
        ])
        answer = await self.read(task, "get_research_answer",
                                 {"goal_id": 24, "details": True, "historical_snapshot": True})
        self.assertEqual(answer["pagination"]["contacts_total"], 1)
        self.assertEqual(answer["contacts"][0]["email"], row["email"])
        self.assertEqual(answer["contacts"][0]["email_status"], row["email_status"])
        self.assertEqual(answer["contacts"][0]["saved_enrichment_provenance"], row["task_paid_receipt"])
        self.assertEqual(answer["contacts"][0]["provider_reported_professional_profile_url"], row["linkedin"])
        self.assertNotIn("old-revision@synthetic.invalid", json.dumps(answer))
        self.assertEqual(answer["state"], "needs_attention")
        self.assertFalse(answer["research_answer"]["final_delivery_ready"])


if __name__ == "__main__":
    unittest.main()

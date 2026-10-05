import json
import unittest
import httpx
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig
from somanylemons_mcp.task_tools.server import create_server


class HistoryAnswerParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_cannot_replace_canonical_conference_gaps_or_scheduling(self):
        calls = []
        canonical = {"id": 37, "version": 9, "state": "needs_attention",
            "next_run_at": "2026-10-05T03:24:00Z", "action_is_scheduled": False,
            "manual_review_required": True, "progress": {"total": 136},
            "conference_answer": {"rows": [], "sources": [],
                "enrichment_summary": {"unfinished_people": 4, "current_identity_quality_unresolved_people": 55},
                "session_title_coverage": {"rows_without_recorded_session_title": 9},
                "final_delivery_requirements": {"combination": "all_required", "all_required_met": False,
                    "operator_review_is_only_remaining_requirement": False, "review_owner": "operator"}}}
        history = {"id": 37, "version": 10, "state": "completed", "next_run_at": "2099-01-01",
            "tasks": [{"checkpoint": "privatecheckpoint"}],
            "events": [{"id": i, "event_type": "saved", "created_at": "2026-10-05", "payload": "privatecheckpoint"} for i in range(30)]}
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": canonical if request.url.params.get("view") == "answer" else history})
        server = create_server(TaskApiClient(TaskApiConfig("https://example.com", "fake-token"), transport=httpx.MockTransport(handler)))
        detail = json.loads((await server.call_tool("get_task", {"goal_id": 37, "include_history": True}))[0].text)
        self.assertEqual([dict(c.url.params) for c in calls], [{"view": "answer"}, {}])
        self.assertEqual(detail["state"], "needs_attention")
        self.assertEqual(detail["version"], 9)
        self.assertFalse(detail["action_is_scheduled"])
        self.assertTrue(detail["manual_review_required"])
        self.assertEqual(detail["operator_history_version"], 10)
        self.assertEqual(len(detail["operator_history"]), 20)
        self.assertNotIn("privatecheckpoint", json.dumps(detail))
        conference = detail["conference_answer"]
        self.assertEqual(conference["enrichment_summary"]["unfinished_people"], 4)
        self.assertEqual(conference["enrichment_summary"]["current_identity_quality_unresolved_people"], 55)
        self.assertEqual(conference["session_title_coverage"]["rows_without_recorded_session_title"], 9)
        self.assertEqual(conference["final_delivery_requirements"]["combination"], "all_required")
        waited = json.loads((await server.call_tool("wait_for_task", {"goal_id": 37, "timeout_seconds": 0}))[0].text)
        self.assertEqual(waited["task"]["conference_answer"], conference)
        self.assertFalse(waited["task"]["action_is_scheduled"])
        self.assertEqual(dict(calls[-1].url.params), {"view": "answer"})

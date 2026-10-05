import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from somanylemons_mcp.task_tools import server as server_module
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig
from somanylemons_mcp.task_tools.server import create_server, research_progress_snapshot


class VirtualClock:
    def __init__(self):
        self.elapsed = 0
        self.sleeps = []

    def monotonic(self):
        return self.elapsed

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


class ResearchProgressTests(unittest.IsolatedAsyncioTestCase):
    def task(self, **changes):
        task = {
            "id": 74, "state": "running", "version": 1,
            "current_step": "Research contacts and verify business emails",
            "running": True, "updated_at": "2026-10-05T19:00:00Z",
            "progress": {"completed": 0, "total": 4},
            "research_answer": {"counts": {
                "qualified_contacts": 2, "qualified_contacts_with_recorded_email": 1,
            }},
        }
        task.update(changes)
        return task

    async def wait(self, tasks, **arguments):
        clock = VirtualClock()
        calls = []

        def handler(request):
            calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v1/agent-tasks/74")
            return httpx.Response(200, json={"data": copy.deepcopy(tasks[min(len(calls) - 1, len(tasks) - 1)])})

        api = TaskApiClient(TaskApiConfig("https://example.com", "mock-key"),
                            transport=httpx.MockTransport(handler))
        server = create_server(api)
        with patch.object(server_module, "time", SimpleNamespace(monotonic=clock.monotonic)), \
             patch.object(server_module, "asyncio", SimpleNamespace(sleep=clock.sleep)):
            content = await server.call_tool("wait_for_task", {"goal_id": 74, **arguments})
        if isinstance(content, tuple):
            content = content[0]
        return json.loads(content[0].text), clock, calls

    async def test_default_and_cached_long_waits_return_after_ten_seconds(self):
        for args in ({}, {"timeout_seconds": 50}):
            with self.subTest(args=args):
                result, clock, calls = await self.wait([self.task()], **args)
                self.assertEqual(clock.elapsed, 10)
                self.assertEqual(len(calls), 2)
                self.assertEqual(result["wait_status"], "still_running")
                self.assertFalse(result["progress_changed_during_wait"])

    async def test_stage_change_returns_early_with_saved_stage(self):
        result, clock, calls = await self.wait([
            self.task(), self.task(current_step="Prepare the results file"),
        ])
        self.assertEqual(clock.elapsed, 5)
        self.assertEqual(len(calls), 2)
        self.assertTrue(result["progress_changed_during_wait"])
        self.assertEqual(result["task"]["current_step"], "Prepare the results file")

    async def test_email_coverage_change_returns_early_without_worker_step_change(self):
        newer = self.task(research_answer={"counts": {
            "qualified_contacts": 2, "qualified_contacts_with_recorded_email": 2,
        }})
        result, clock, _ = await self.wait([self.task(), newer])
        self.assertEqual(clock.elapsed, 5)
        self.assertTrue(result["progress_changed_during_wait"])
        self.assertEqual(result["task"]["research_answer"]["counts"]["qualified_contacts_with_recorded_email"], 2)
        self.assertEqual(result["task"]["progress"]["completed"], 0)

    async def test_timestamp_and_version_churn_are_not_reported_as_progress(self):
        newer = self.task(updated_at="2026-10-05T19:00:05Z", version=2)
        result, clock, _ = await self.wait([self.task(), newer])
        self.assertEqual(clock.elapsed, 10)
        self.assertFalse(result["progress_changed_during_wait"])
        self.assertEqual(result["task"]["updated_at"], newer["updated_at"])
        self.assertTrue(result["task"]["running"])

    async def test_terminal_and_blocked_states_return_immediately(self):
        for state, status in (("completed", "finished"), ("blocked", "needs_attention"),
                              ("paused", "needs_attention"), ("waiting_customer", "needs_attention")):
            with self.subTest(state=state):
                result, clock, calls = await self.wait([self.task(state=state, running=False)])
                self.assertEqual(result["wait_status"], status)
                self.assertFalse(result["task"]["running"])
                self.assertEqual(clock.sleeps, [])
                self.assertEqual(len(calls), 1)

    async def test_zero_timeout_is_one_read_and_preserves_stage_and_counts(self):
        result, clock, calls = await self.wait([self.task()], timeout_seconds=0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(result["task"]["current_step"], self.task()["current_step"])
        self.assertEqual(result["task"]["research_answer"]["counts"]["qualified_contacts"], 2)

    async def test_schema_and_description_make_progress_updates_available_to_hosted_clients(self):
        server = create_server(TaskApiClient(TaskApiConfig("https://example.com", "mock-key")))
        tool = next(tool for tool in await server.list_tools() if tool.name == "wait_for_task")
        self.assertEqual(tool.title, "Check research progress")
        self.assertEqual(tool.inputSchema["properties"]["timeout_seconds"]["default"], 10)
        self.assertIn("AFTER each response", tool.description)
        self.assertIn("never silently chain waits", server.instructions.lower())

    def test_business_and_conference_coverage_changes_are_meaningful(self):
        for key, field in (("business_answer", "counts"), ("conference_answer", "enrichment_summary")):
            with self.subTest(key=key):
                answer = {key: {field: {"recorded_email_people": 1}}}
                snapshot = research_progress_snapshot(answer)
                answer[key][field]["recorded_email_people"] = 2
                self.assertNotEqual(snapshot, research_progress_snapshot(answer))


if __name__ == "__main__":
    unittest.main()

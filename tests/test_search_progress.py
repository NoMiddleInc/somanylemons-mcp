"""A people search answers within one short window: the rows, or where it stands and what to say."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from somanylemons_mcp.task_tools import server as server_module
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig
from somanylemons_mcp.task_tools.server import create_server

KEY = "33333333-3333-4333-8333-333333333333"
ROW = {"name": "Ada Lane", "title": "CEO", "company": "Acme", "email": "ada@acme.example", "linkedin": "", "photo": ""}


class VirtualClock:
    def __init__(self):
        self.elapsed = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.elapsed

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


def running(goal_id=650):
    return {"id": goal_id, "state": "running", "quick_search": {"stage": "searching"}, "tasks": []}


def finished(goal_id=650, key="quick_search"):
    return {"id": goal_id, "state": "completed", key: {"rows": [ROW], "coverage": {"requested": 1, "returned": 1}, "delivery": {"status": "sent"}}, "tasks": []}


def progress(found, requested=10, finished_flag=False, elapsed=12):
    return {
        "id": 650, "state": "completed" if finished_flag else "running", "finished": finished_flag,
        "stage": "searching", "notice": "",
        "progress": {"stage": "looking_up", "found": found, "with_email": found, "requested": requested,
                     "candidates_screened": 100, "message": f"Looking up contact details: {found} of {requested} found"},
        "elapsed_seconds": elapsed, "updated_at": "2026-11-07T00:00:00Z",
    }


class SearchProgressTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, tool, arguments, progress_reads, detail_reads, *, post_key="quick_search"):
        clock, calls = VirtualClock(), []

        def handler(request):
            calls.append((request.method, request.url.path))
            if request.method == "POST":
                return httpx.Response(201, json={"data": running()})
            if request.url.path.endswith("/progress"):
                reads = [c for c in calls if c[1].endswith("/progress")]
                body = progress_reads[min(len(reads) - 1, len(progress_reads) - 1)]
                return httpx.Response(200, json={"data": body}) if body is not None else httpx.Response(404, json={"detail": "Not Found"})
            reads = [c for c in calls if c[0] == "GET" and not c[1].endswith("/progress")]
            return httpx.Response(200, json={"data": detail_reads[min(len(reads) - 1, len(detail_reads) - 1)]})

        api = TaskApiClient(TaskApiConfig("https://example.com", "mock-key"), transport=httpx.MockTransport(handler))
        server = create_server(api)
        with patch.object(server_module, "time", SimpleNamespace(monotonic=clock.monotonic)), \
             patch.object(server_module, "asyncio", SimpleNamespace(sleep=clock.sleep)):
            content = await server.call_tool(tool, arguments)
        if isinstance(content, tuple):
            content = content[0]
        return json.loads(content[0].text), clock, calls

    async def test_a_search_still_running_after_the_window_says_where_it_stands_and_what_to_do(self):
        result, clock, calls = await self.call(
            "find_people", {"request": "CEOs of law firms in Chicago", "idempotency_key": KEY},
            [progress(3), progress(5)], [running()])
        self.assertEqual(result["status"], "still_running")
        self.assertEqual(result["goal_id"], 650)
        self.assertEqual(result["idempotency_key"], KEY)
        self.assertEqual(result["progress"]["found"], 5)
        self.assertEqual(result["progress"]["message"], "Looking up contact details: 5 of 10 found (12 seconds so far)")
        self.assertIn("Tell the user in one short line what progress.message says", result["next_step"])
        self.assertIn("call find_people again with the same idempotency_key", result["next_step"])
        self.assertEqual(result["rows"], [])
        # The light progress read is asked every couple of seconds; the full task was never read.
        self.assertEqual(calls[0], ("POST", "/api/v1/agent-tasks/quick-search"))
        self.assertEqual({path for method, path in calls[1:]}, {"/api/v1/agent-tasks/650/progress"})
        self.assertEqual(clock.elapsed, server_module.SEARCH_WAIT_SECONDS)
        self.assertEqual(len(calls) - 1, len(clock.sleeps))
        self.assertEqual(clock.sleeps[0], server_module.PROGRESS_POLL_SECONDS)

    async def test_a_search_that_finishes_inside_the_window_returns_its_rows_and_stops_polling(self):
        result, clock, calls = await self.call(
            "find_people", {"request": "CEOs of law firms in Chicago", "idempotency_key": KEY},
            [progress(1), progress(1, finished_flag=True)], [finished()])
        self.assertEqual(result["status"], "completed")
        self.assertEqual([row["name"] for row in result["rows"]], ["Ada Lane"])
        self.assertNotIn("next_step", result)
        self.assertNotIn("progress", result)
        self.assertEqual(calls[-1], ("GET", "/api/v1/agent-tasks/650"))
        self.assertEqual(sum(1 for _m, path in calls if path.endswith("/progress")), 2)
        self.assertLess(clock.elapsed, server_module.SEARCH_WAIT_SECONDS)

    async def test_a_backend_without_the_progress_read_is_followed_through_the_full_task(self):
        result, clock, calls = await self.call(
            "find_people", {"request": "CEOs", "idempotency_key": KEY}, [None], [running(), finished()])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual([path for _m, path in calls if not path.endswith("/progress")][1:], ["/api/v1/agent-tasks/650"] * 2)

    async def test_full_list_and_deep_search_answer_the_same_way(self):
        for tool, key, detail_key in (("full_list", "goal_id", "quick_search"), ("deep_search", "goal_id", "deep_search")):
            with self.subTest(tool=tool):
                result, _clock, _calls = await self.call(tool, {"goal_id": 650}, [progress(2, requested=4)], [running()])
                self.assertEqual(result["status"], "still_running")
                self.assertEqual(result["progress"]["message"], "Looking up contact details: 2 of 4 found (12 seconds so far)")
                self.assertIn(f"call {tool} again with the same goal_id", result["next_step"])
                result, _clock, _calls = await self.call(tool, {"goal_id": 650}, [progress(4, finished_flag=True)], [finished(key=detail_key)])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(result["rows"]), 1)

    async def test_a_conference_notice_reaches_the_person_while_the_pages_are_read(self):
        reading = {**progress(0), "progress": None, "stage": "reading_event_pages", "notice": "Reading the event's own speaker pages; this can take several minutes."}
        result, _clock, _calls = await self.call("find_people", {"request": "ILHTC speakers", "idempotency_key": KEY}, [reading], [running()])
        self.assertEqual(result["status"], "still_running")
        self.assertEqual(result["notice"], reading["notice"])
        self.assertEqual(result["progress"]["message"], reading["notice"] + " (12 seconds so far)")
        self.assertEqual(result["progress"]["stage"], "reading_event_pages")

    async def test_descriptions_and_instructions_tell_the_assistant_to_narrate_and_call_again(self):
        from somanylemons_mcp import server as hosted
        from somanylemons_mcp.task_bridge import task_schemas
        tools = {tool.name: tool for tool in await task_schemas()}
        for name in ("find_people", "full_list", "deep_search"):
            self.assertNotIn("ctx", tools[name].inputSchema["properties"], name)
            self.assertIn("within about 30 seconds", tools[name].description, name)
            self.assertIn("still_running with progress", tools[name].description, name)
        self.assertNotIn("20 to 90 seconds", tools["find_people"].description)
        self.assertIn("tell the user in one short line what its progress.message says", hosted.RESULT_RULES)
        self.assertIn("next_step", tools["find_people"].description)

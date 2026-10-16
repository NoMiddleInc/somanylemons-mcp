"""Campaign tools mirror the backend's /api/v1/agent-tasks/campaigns routes one call each."""
import json
import unittest

import httpx

from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.server import create_server
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig

KEY = "33333333-3333-4333-8333-333333333333"
TOOLS = ("list_campaigns", "create_campaign", "update_campaign", "activate_campaign", "signal_search", "get_prospect_intelligence")

CAMPAIGN = {
    "id": 12, "name": "Texas producers", "objective": "Get leads",
    "targeting": "Independent P&C agencies in Texas, owners and producers",
    "signals": "New producer hires, carrier appointments", "offering": "Outsourced marketing for agencies",
    "messaging": "Short, mention the hire, no hype",
    "status": "draft", "is_default": True, "activated_at": None,
    "golden_list": {"id": 44, "name": "Texas producers", "prospect_count": 0},
    "results": {"prospects": {"total": 0, "pending": 0, "approved": 0, "excluded": 0},
                "signals": {"total": 0, "last_7_days": 0},
                "messages": {"prepared": 0, "sent": 0, "replied": 0}, "last_run": None},
    "updated_at": "2026-10-15T12:00:00Z",
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
        for name in TOOLS:
            self.assertIn(name, TASK_TOOL_NAMES)
            self.assertIn(name, tools)
            description = tools[name].description.lower()
            self.assertNotIn("apollo", description)
            self.assertNotIn("—", description)
            for removed in ("instagram", "carousel", "recording", "scheduling", "golden_list_id", "agents"):
                self.assertNotIn(removed, description, (name, removed))
        self.assertTrue(tools["list_campaigns"].annotations.readOnlyHint)
        self.assertFalse(tools["create_campaign"].annotations.readOnlyHint)
        self.assertTrue(tools["create_campaign"].annotations.idempotentHint)
        self.assertTrue(tools["update_campaign"].annotations.idempotentHint)
        self.assertFalse(tools["activate_campaign"].annotations.readOnlyHint)
        self.assertFalse(tools["activate_campaign"].annotations.destructiveHint)
        self.assertEqual(set(tools["create_campaign"].inputSchema["required"]), {"idempotency_key", "targeting"})
        self.assertIn("two steps", tools["create_campaign"].description)
        self.assertIn("both steps, targeting and signals", tools["activate_campaign"].description)
        self.assertNotIn("five steps", tools["activate_campaign"].description)
        self.assertTrue(tools["get_prospect_intelligence"].annotations.readOnlyHint)
        self.assertFalse(tools["signal_search"].annotations.readOnlyHint)
        self.assertEqual(tools["signal_search"].inputSchema["required"], ["request"])
        self.assertEqual(tools["get_prospect_intelligence"].inputSchema["required"], ["campaign_id"])
        self.assertEqual(set(tools["create_campaign"].inputSchema["properties"]),
                         {"idempotency_key", "objective", "targeting", "signals", "offering", "messaging", "name"})
        self.assertEqual(tools["update_campaign"].inputSchema["required"], ["campaign_id"])
        self.assertEqual(set(tools["update_campaign"].inputSchema["properties"]),
                         {"campaign_id", "name", "objective", "targeting", "signals", "offering", "messaging"})
        self.assertEqual(tools["activate_campaign"].inputSchema["required"], ["campaign_id"])
        self.assertEqual(set(tools["activate_campaign"].inputSchema["properties"]), {"campaign_id", "confirmation"})

    async def test_list_campaigns_reads_and_links_each_campaign(self):
        calls, result = await self._call("list_campaigns", {}, {"success": True, "data": {"campaigns": [CAMPAIGN]}})
        self.assertEqual((calls[0].method, calls[0].url.path), ("GET", "/api/v1/agent-tasks/campaigns"))
        campaign = self._payload(result)["campaigns"][0]
        self.assertEqual(campaign["view_in_producerspark"], "https://producerspark.com/agent/12")
        self.assertEqual(campaign["targeting"], CAMPAIGN["targeting"])
        self.assertEqual(campaign["results"]["prospects"]["total"], 0)

    async def test_create_campaign_posts_all_five_steps(self):
        calls, result = await self._call("create_campaign", {
            "idempotency_key": KEY, "name": "Texas producers", "objective": "Get leads",
            "targeting": CAMPAIGN["targeting"], "signals": CAMPAIGN["signals"],
            "offering": CAMPAIGN["offering"], "messaging": CAMPAIGN["messaging"],
        }, {"data": CAMPAIGN}, status=201)
        self.assertEqual((calls[0].method, calls[0].url.path), ("POST", "/api/v1/agent-tasks/campaigns"))
        self.assertEqual(json.loads(calls[0].content), {
            "name": "Texas producers", "objective": "Get leads", "targeting": CAMPAIGN["targeting"],
            "signals": CAMPAIGN["signals"], "offering": CAMPAIGN["offering"], "messaging": CAMPAIGN["messaging"],
            "creation_key": KEY,
        })
        self.assertEqual(self._payload(result)["view_in_producerspark"], "https://producerspark.com/agent/12")

    async def test_create_campaign_needs_only_the_targeting(self):
        calls, _ = await self._call("create_campaign", {
            "idempotency_key": KEY, "targeting": "Texas agencies",
        }, {"data": CAMPAIGN}, status=201)
        self.assertEqual(json.loads(calls[0].content), {"targeting": "Texas agencies", "creation_key": KEY})

    async def test_create_campaign_refuses_missing_targeting_before_calling(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": CAMPAIGN})

        for arguments in ({"idempotency_key": KEY, "objective": "Get leads"},
                          {"idempotency_key": KEY, "objective": "Get leads", "targeting": ""}):
            with self.assertRaises(Exception):
                await invoke_task("create_campaign", arguments, api_url="https://example.com", api_key="owner",
                                  transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [])

    async def test_create_campaign_requires_a_key_and_repeats_it(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": CAMPAIGN})

        arguments = {"objective": "Get leads", "targeting": "Texas agencies"}
        with self.assertRaises(Exception):
            await invoke_task("create_campaign", arguments, api_url="https://example.com", api_key="owner",
                              transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [])
        results = []
        for _ in range(2):
            results.append(await invoke_task("create_campaign", {**arguments, "idempotency_key": KEY},
                                             api_url="https://example.com", api_key="owner",
                                             transport=httpx.MockTransport(handler)))
        self.assertEqual([json.loads(call.content)["creation_key"] for call in calls], [KEY, KEY])
        self.assertEqual([self._payload(item)["id"] for item in results], [12, 12])

    async def test_create_campaign_passes_the_backend_reason_through(self):
        with self.assertRaises(Exception) as raised:
            await self._call("create_campaign", {
                "idempotency_key": KEY, "objective": "Get leads", "targeting": "Texas agencies",
            }, {"detail": "Tell us who you want to reach."}, status=400)
        text = str(raised.exception)
        self.assertIn("HTTP 400", text)
        self.assertIn("Tell us who you want to reach.", text)

    async def test_update_campaign_patches_only_what_changed(self):
        calls, result = await self._call("update_campaign", {
            "campaign_id": 12, "signals": "Funding rounds", "messaging": "Mention the round",
        }, {"data": {**CAMPAIGN, "signals": "Funding rounds", "messaging": "Mention the round"}})
        self.assertEqual((calls[0].method, calls[0].url.path), ("PATCH", "/api/v1/agent-tasks/campaigns/12"))
        self.assertEqual(json.loads(calls[0].content), {"signals": "Funding rounds", "messaging": "Mention the round"})
        payload = self._payload(result)
        self.assertEqual(payload["signals"], "Funding rounds")
        self.assertEqual(payload["view_in_producerspark"], "https://producerspark.com/agent/12")
        calls, _ = await self._call("update_campaign", {"campaign_id": 12, "name": "New"}, {"data": CAMPAIGN})
        self.assertEqual(json.loads(calls[0].content), {"name": "New"})

    async def test_update_campaign_passes_the_backend_reason_through(self):
        with self.assertRaises(Exception) as raised:
            await self._call("update_campaign", {"campaign_id": 12, "objective": "Get leads"},
                             {"detail": "Archived campaigns cannot be changed."}, status=400)
        text = str(raised.exception)
        self.assertIn("HTTP 400", text)
        self.assertIn("Archived campaigns cannot be changed.", text)

    async def test_unknown_campaign_is_a_404(self):
        for name, arguments in (("update_campaign", {"campaign_id": 99, "name": "New"}),
                                ("activate_campaign", {"campaign_id": 99})):
            with self.assertRaises(Exception) as raised:
                await self._call(name, arguments, {"detail": "Not found."}, status=404)
            text = str(raised.exception)
            self.assertIn("HTTP 404", text)
            self.assertNotIn("Refresh the task", text)

    async def test_activate_needs_confirmation_then_runs_with_it(self):
        will_happen = "Start finding prospects and signals for Texas producers and prepare messaging for your review"
        waiting = {"data": {"needs_confirmation": True, "done": False, "confirmation": "ab12cd34ef56ab78",
                            "will_happen": will_happen}}
        calls, result = await self._call("activate_campaign", {"campaign_id": 12}, waiting)
        self.assertEqual((calls[0].method, calls[0].url.path), ("POST", "/api/v1/agent-tasks/campaigns/12/activate"))
        self.assertEqual(json.loads(calls[0].content), {})
        payload = self._payload(result)
        self.assertTrue(payload["needs_confirmation"])
        self.assertEqual(payload["will_happen"], will_happen)

        active = {**CAMPAIGN, "status": "active", "activated_at": "2026-10-15T12:05:00Z"}
        calls, result = await self._call("activate_campaign", {"campaign_id": 12, "confirmation": "ab12cd34ef56ab78"},
                                         {"data": active})
        self.assertEqual((calls[0].method, calls[0].url.path), ("POST", "/api/v1/agent-tasks/campaigns/12/activate"))
        self.assertEqual(json.loads(calls[0].content), {"confirmation": "ab12cd34ef56ab78"})
        payload = self._payload(result)
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["view_in_producerspark"], "https://producerspark.com/agent/12")

    async def test_activate_passes_the_missing_step_through(self):
        with self.assertRaises(Exception) as raised:
            await self._call("activate_campaign", {"campaign_id": 12, "confirmation": "ab12cd34ef56ab78"},
                             {"detail": "Add your target signals before activating."}, status=400)
        self.assertIn("Add your target signals before activating.", str(raised.exception))

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
        description = tools["get_started"].description
        for text in ("has_campaign is false", "create_campaign", "update_campaign", "activate_campaign"):
            self.assertIn(text, description)

    def test_instructions_walk_through_create_fill_activate(self):
        instructions = create_server(TaskApiClient(TaskApiConfig("https://example.com", "k"))).instructions
        for text in ("has_campaign false", "create_campaign", "update_campaign", "activate_campaign",
                     "signals, offering and messaging", "will_happen"):
            self.assertIn(text, instructions)
        self.assertNotIn("Instagram carousels", instructions)


if __name__ == "__main__":
    unittest.main()


ROW = {
    "candidate_id": "task-1-abc", "name": "Connie Hills", "title": "Sr VP Sales", "company": "Swift Transportation",
    "email": "connie@example.com", "email_status": "verified", "linkedin": "", "location": "Seattle", "fit": None,
    "status": "ready", "summary": "Sales or ownership change: a new EVP of Sales.", "strongest_signal": "sales_or_ownership_change",
    "signals": [{"key": "sales_or_ownership_change", "fired": True, "evidence": "A new EVP of Sales.", "sources": ["https://swift.example/about"]}],
    "facts": ["Swift appointed an EVP of Sales (https://swift.example/about)"], "industry": "trucking",
    "email": {"subject": "swift direct shipper contracts", "body": "Connie,\n\n..."}, "researched_at": "2026-10-16T21:00:00Z", "error": None,
}


class SignalSearchToolTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _payload(result):
        content = result[0] if isinstance(result, tuple) else result
        return json.loads(content[0].text)

    async def test_signal_search_starts_polls_and_returns_the_rows(self):
        from unittest.mock import AsyncMock, patch
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path, request.content))
            if request.method == "POST":
                return httpx.Response(200, json={"data": {"id": 31, "state": "queued", "prospect_intelligence": {
                    "rows": [{**ROW, "status": "queued", "email": None}], "coverage": {"requested": 1, "queued": 1}}}})
            return httpx.Response(200, json={"data": {"id": 31, "state": "completed", "prospect_intelligence": {
                "rows": [ROW], "coverage": {"requested": 1, "ready": 1}}}})

        with patch("asyncio.sleep", new=AsyncMock()):
            result = self._payload(await invoke_task(
                "signal_search", {"request": "Run the signals on Connie Hills", "campaign_id": 12, "idempotency_key": KEY},
                api_url="https://example.com", api_key="owner", transport=httpx.MockTransport(handler)))
        self.assertEqual(calls[0][:2], ("POST", "/api/v1/agent-tasks/signal-search"))
        self.assertEqual(json.loads(calls[0][2]), {"request": "Run the signals on Connie Hills", "idempotency_key": KEY, "campaign_id": 12})
        self.assertEqual(calls[1][:2], ("GET", "/api/v1/agent-tasks/31"))
        self.assertEqual((result["goal_id"], result["status"], result["notice"]), (31, "completed", ""))
        self.assertEqual(result["rows"][0]["email"]["subject"], "swift direct shipper contracts")
        self.assertEqual(result["coverage"], {"requested": 1, "ready": 1})
        self.assertIn("view_in_producerspark", result)
        self.assertIn("reply_must_end_with", result)

    async def test_signal_search_reports_still_running_with_a_notice(self):
        from unittest.mock import AsyncMock, patch

        def handler(request):
            return httpx.Response(200, json={"data": {"id": 32, "state": "running", "prospect_intelligence": {
                "rows": [{**ROW, "status": "running", "email": None}], "coverage": {"requested": 1, "running": 1}}}})

        with patch("asyncio.sleep", new=AsyncMock()), patch("time.monotonic", side_effect=[0, 0, 1000, 1000, 1000]):
            result = self._payload(await invoke_task(
                "signal_search", {"request": "signal search my list"},
                api_url="https://example.com", api_key="owner", transport=httpx.MockTransport(handler)))
        self.assertEqual(result["status"], "still_running")
        self.assertIn("same idempotency_key", result["notice"])
        self.assertEqual(result["rows"][0]["status"], "running")

    async def test_get_prospect_intelligence_reads_a_campaign(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {"campaign_id": 12, "counts": {"ready": 1, "pending": 99}, "rows": [ROW]}})

        result = self._payload(await invoke_task(
            "get_prospect_intelligence", {"campaign_id": 12, "status": "ready", "limit": 20},
            api_url="https://example.com", api_key="owner", transport=httpx.MockTransport(handler)))
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/campaigns/12/intelligence")
        self.assertEqual(dict(calls[0].url.params), {"status": "ready", "limit": "20"})
        self.assertEqual(result["counts"], {"ready": 1, "pending": 99})
        self.assertEqual(result["rows"][0]["name"], "Connie Hills")
        self.assertEqual(result["view_in_producerspark"], "https://producerspark.com/agent/12")

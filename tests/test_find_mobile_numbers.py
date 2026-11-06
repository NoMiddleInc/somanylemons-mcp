"""Mobile numbers: the cost is stated first, the lookup runs on confirm, numbers keep their Do Not Call flag."""
import json
import unittest
from unittest import mock

import httpx

from somanylemons_mcp.task_bridge import invoke_task, task_schemas

KEY = "33333333-3333-4333-8333-333333333333"
OFFER = {"waiting": 3, "eligible": 2, "without_record": 1, "looked_up": 0, "credits_left": 40,
         "credits_per_number": 8, "max_credits": 16, "cost_note": "Mobile numbers come from the contact database.", "run": None}
ADA = {"name": "Ada Lane", "company": "Acme", "title": "VP", "email": "ada@acme.example", "linkedin": "",
       "mobile_phone": "+15125550101", "phone_status": "valid number", "phone_dnc": True,
       "phone_confidence": "high", "phone_source": "Contact database", "phone_date": "2026-11-05"}
BO = {"name": "Bo Reed", "company": "Reed", "mobile_phone": "", "phone_status": "unavailable", "phone_dnc": None}
CY = {"name": "Cy Moss", "company": "Moss", "mobile_phone": "", "phone_status": ""}


def payload(result):
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


class FindMobileNumbersTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, arguments, routes):
        calls = []

        def handler(request):
            calls.append(request)
            status, body = routes(request)
            return httpx.Response(status, json=body)

        result = await invoke_task("find_mobile_numbers", arguments, api_url="https://example.com",
                                   api_key="scoped-owner", transport=httpx.MockTransport(handler))
        return calls, result

    async def test_without_confirm_it_states_the_cost_and_looks_nothing_up(self):
        calls, result = await self.call({"goal_id": 12}, lambda request: (200, {"data": OFFER}))
        self.assertEqual([(c.method, c.url.path) for c in calls], [("GET", "/api/v1/agent-tasks/12/find-phones")])
        data = payload(result)
        self.assertTrue(data["needs_confirmation"])
        self.assertIn("contact database for 2 people", data["will_happen"])
        self.assertIn("8 credits, at most 16 credits", data["will_happen"])
        self.assertIn("Do Not Call", data["will_happen"])
        self.assertEqual((data["credits_per_number"], data["max_credits"], data["credits_left"]), (8, 16, 40))

    async def test_confirmed_lookup_returns_the_numbers_with_their_do_not_call_flags(self):
        finished = {**OFFER, "waiting": 0, "eligible": 0,
                    "run": {"id": 30, "running": False, "total": 3, "looked_up": 3, "found": 1, "unavailable": 2}}

        def routes(request):
            if request.method == "POST":
                self.assertEqual(json.loads(request.content), {"idempotency_key": KEY})
                return 201, {"data": finished}
            if request.url.path == "/api/v1/agent-tasks/12":
                return 200, {"data": {"id": 12, "people": [ADA, BO, CY]}}
            return 200, {"data": OFFER}

        calls, result = await self.call({"goal_id": 12, "confirm": True, "idempotency_key": KEY}, routes)
        self.assertIn(("POST", "/api/v1/agent-tasks/12/find-phones"), [(c.method, c.url.path) for c in calls])
        data = payload(result)
        self.assertEqual(data["status"], "finished")
        # Only people that had a lookup come back, each number with its Do Not Call flag.
        self.assertEqual([row["name"] for row in data["rows"]], ["Ada Lane", "Bo Reed"])
        self.assertEqual((data["rows"][0]["mobile_phone"], data["rows"][0]["phone_dnc"]), ("+15125550101", True))
        self.assertEqual(data["rows"][1]["phone_status"], "unavailable")

    async def test_a_campaign_lookup_reads_back_its_golden_list(self):
        finished = {**OFFER, "golden_list_id": 77, "run": {"id": 31, "running": False}}

        def routes(request):
            if request.url.path == "/api/v1/agent-tasks/golden-lists/read":
                self.assertEqual(request.url.params["campaign_id"], "77")
                return 200, {"data": {"rows": [ADA]}}
            return (201 if request.method == "POST" else 200), {"data": finished}

        calls, result = await self.call({"campaign_id": 5, "confirm": True}, routes)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/campaigns/5/find-phones")
        self.assertEqual(payload(result)["rows"][0]["phone_dnc"], True)

    async def test_used_up_credits_point_to_the_plans_page(self):
        def routes(request):
            if request.method == "POST":
                return 402, {"detail": {"message": "No credits left.", "upgrade_url": "https://producerspark.com/billing"}}
            return 200, {"data": OFFER}

        _calls, result = await self.call({"goal_id": 12, "confirm": True}, routes)
        data = payload(result)
        self.assertEqual(data["status"], "upgrade_required")
        self.assertEqual(data["upgrade_url"], "https://producerspark.com/billing")

    async def test_the_tool_states_cost_and_do_not_call_and_never_names_the_provider(self):
        tool = next(tool for tool in await task_schemas() if tool.name == "find_mobile_numbers")
        self.assertIn("contact database", tool.description)
        self.assertIn("(8)", tool.description)
        self.assertIn("Do Not Call", tool.description)
        self.assertNotIn("apollo", tool.description.lower())
        self.assertFalse(tool.annotations.readOnlyHint)


class PhoneFieldsInRowsTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_people_and_get_prospect_list_rows_carry_the_phone_fields(self):
        def handler(request):
            if request.url.path == "/api/v1/agent-tasks/quick-search":
                return httpx.Response(200, json={"data": {"id": 9, "state": "completed", "quick_search": {"rows": [ADA]}, "tasks": []}})
            return httpx.Response(200, json={"data": {"rows": [ADA], "row_count": 1}})

        for name, arguments in (("find_people", {"request": "Find producers", "idempotency_key": KEY}),
                                ("get_prospect_list", {"campaign_id": 77})):
            result = await invoke_task(name, arguments, api_url="https://example.com", api_key="scoped-owner",
                                       transport=httpx.MockTransport(handler))
            [row] = payload(result)["rows"]
            self.assertEqual({key: row[key] for key in ("mobile_phone", "phone_status", "phone_dnc")},
                             {"mobile_phone": "+15125550101", "phone_status": "valid number", "phone_dnc": True}, name)

    def test_business_research_contacts_keep_the_phone_fields(self):
        from somanylemons_mcp.task_tools.business_research import saved_business_answer

        task = {"id": 255, "contract": {"workflow": "business_research"},
                "business_answer": {"rows": [ADA], "counts": {"contacts": 1}}}
        answer = saved_business_answer(task, {"business_answer": {}}, contact_page=1, source_page=1)
        [contact] = answer["contacts"]
        self.assertEqual({key: contact[key] for key in ("mobile_phone", "phone_status", "phone_dnc")},
                         {"mobile_phone": "+15125550101", "phone_status": "valid number", "phone_dnc": True})

if __name__ == "__main__":
    unittest.main()

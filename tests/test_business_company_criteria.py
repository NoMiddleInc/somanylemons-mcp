"""Company criteria survive the real MCP transport without invented defaults."""

import json
import unittest

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from somanylemons_mcp.task_bridge import invoke_task, task_schemas


class BusinessCompanyCriteriaTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, spec, calls):
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {"id": 71, "state": "queued"}})

        return await invoke_task("create_business_research_request", {
            "request": "Find US SaaS CFOs at companies with 50–500 employees.",
            "idempotency_key": "22222222-2222-4222-8222-222222222222",
            "spec": spec,
        }, api_url="https://example.com", api_key="customer",
            transport=httpx.MockTransport(handler))

    async def test_actual_tool_preserves_industry_employee_range_and_personal_location(self):
        calls = []
        spec = {"kind": "company_contacts", "companies": [], "roles": ["CFO"],
                "industries": ["SaaS", "Software"],
                "employee_range": {"min": 50, "max": 500},
                "person_locations": ["United States"], "fields": ["email"], "count": 10}
        await self.invoke(spec, calls)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/business-research")
        self.assertEqual(json.loads(calls[0].content)["spec"], spec)
        self.assertEqual(calls[0].headers["x-api-key"], "customer")

    async def test_legacy_request_has_no_new_criteria_defaults(self):
        calls = []
        spec = {"kind": "company_contacts", "companies": [{"name": "Dell"}], "roles": ["CIO"]}
        await self.invoke(spec, calls)
        self.assertEqual(json.loads(calls[0].content)["spec"], spec)

    async def test_invalid_employee_ranges_never_reach_backend(self):
        calls = []
        for value in ({"min": 500, "max": 50}, {"min": 0, "max": 500},
                      {"min": 50, "max": 10000001}, {"min": True, "max": 500},
                      {"min": "50", "max": 500}, {"min": 50.5, "max": 500},
                      {"min": 50, "max": False}, {"min": 50, "max": "500"},
                      {"min": 50, "max": 500.5}, {"min": 50, "max": 500, "extra": 1}):
            with self.subTest(value=value), self.assertRaises(ToolError):
                await self.invoke({"kind": "company_contacts", "employee_range": value}, calls)
        self.assertEqual(calls, [])

    async def test_invalid_industry_bounds_never_reach_backend(self):
        calls = []
        for value in ([""], ["x" * 201], ["Software"] * 31):
            with self.subTest(value=value), self.assertRaises(ToolError):
                await self.invoke({"kind": "company_contacts", "industries": value}, calls)
        self.assertEqual(calls, [])

    async def test_schema_exposes_bounded_company_filters_without_defaults(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        definitions = tools["create_business_research_request"].inputSchema["$defs"]
        props = definitions["BusinessResearchSpec"]["properties"]
        industry = next(item for item in props["industries"]["anyOf"] if item.get("type") == "array")
        self.assertEqual(industry["maxItems"], 30)
        self.assertEqual(industry["items"]["minLength"], 1)
        self.assertEqual(industry["items"]["maxLength"], 200)
        self.assertIsNone(props["industries"]["default"])
        self.assertIsNone(props["employee_range"]["default"])
        employee = definitions["BusinessEmployeeRange"]
        self.assertFalse(employee["additionalProperties"])
        for name in ("min", "max"):
            self.assertEqual(employee["properties"][name]["minimum"], 1)
            self.assertEqual(employee["properties"][name]["maximum"], 10000000)

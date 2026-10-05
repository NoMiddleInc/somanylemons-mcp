"""Actual MCP schema and tool invocation across the HTTP facade, no live providers."""

import json
import unittest

import httpx

from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.server import compact_research_answer


def rendered(result):
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


class BusinessResearchToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_tool_schema_exposes_roles_and_typed_bounded_requests(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertEqual(set(tools), TASK_TOOL_NAMES)
        tool = tools["create_business_research_request"]
        self.assertIn("any industry and role", tool.description)
        self.assertIn("Dell", tool.description)
        self.assertTrue(tool.annotations.idempotentHint)
        self.assertFalse(tool.annotations.readOnlyHint)
        schema = tool.inputSchema
        self.assertEqual(set(schema["required"]), {"request", "idempotency_key"})
        spec = schema["$defs"]["BusinessResearchSpec"]
        self.assertFalse(spec["additionalProperties"])
        self.assertIn("person_name", spec["properties"])
        self.assertIn("roles", spec["properties"])
        self.assertNotIn("budget", spec["properties"])
        count = next(value for value in spec["properties"]["count"]["anyOf"] if value.get("type") == "integer")
        self.assertEqual((count["minimum"], count["maximum"]), (1, 500))

    async def test_dell_cio_and_first15_conference_payloads_use_generic_scoped_endpoint(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {
                "id": 71, "state": "queued", "version": 1,
                "contract": {"workflow": "business_research", "request": json.loads(request.content)["request"]},
            }})
        for spec in (
            {"kind": "company_contacts", "companies": [{"name": "Dell", "domain": "dell.com"}], "roles": ["CIO"], "count": 1},
            {"kind": "conference_speakers", "event": {"name": "Technology Congress", "year": 2026, "source_urls": ["https://publisher.example.com/speakers"]}, "count": 15, "all": False},
        ):
            result = await invoke_task("create_business_research_request", {
                "request": "Research the supplied people and include business emails and sources.",
                "spec": spec, "config_id": 6,
                "idempotency_key": "22222222-2222-4222-8222-222222222222",
            }, api_url="https://example.com", api_key="customer", transport=httpx.MockTransport(handler))
            self.assertEqual(rendered(result)["id"], 71)
            body = json.loads(calls[-1].content)
            self.assertEqual(body["spec"], spec)
            self.assertEqual(body["config_id"], 6)
            self.assertEqual(calls[-1].url.path, "/api/v1/agent-tasks/business-research")
            self.assertEqual(calls[-1].headers["x-api-key"], "customer")
            self.assertNotIn("budget", body)
        self.assertEqual(len(calls), 2)

    async def test_request_only_defers_planning_and_uncertain_retry_reuses_exact_uuid(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"id": 72, "state": "queued", "contract": {"workflow": "business_research"}}})
        arguments = {"request": "Find the CIO at Dell", "idempotency_key": "22222222-2222-4222-8222-222222222222"}
        for _ in range(2):
            await invoke_task("create_business_research_request", arguments, api_url="https://example.com", api_key="customer", transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [arguments, arguments])

    def test_current_generic_evidence_projects_saved_rows_without_inventing_fulfillment(self):
        task = {
            "id": 73, "state": "needs_attention", "fulfillment": "partial", "contract_revision": 2,
            "contract": {"workflow": "business_research", "request": "Find 15 executives"},
            "business_answer": {"coverage": {"requested_count": 15, "qualified_rows": 1, "full_coverage_verified": False},
                "rows": [{"id": "one", "name": "Person", "company": "Dell", "title": "CIO", "email": "",
                          "email_status": "unavailable_after_enrichment", "enrichment_status": "enriched",
                          "evidence_refs": [{"source_url": "https://dell.example.com/team", "quote": "a" * 500}]}],
                "sources": [{"url": "https://dell.example.com/team", "access": "opened"}]},
        }
        answer = compact_research_answer(task)
        self.assertEqual(answer["state"], "needs_attention")
        self.assertFalse(answer["business_answer"]["coverage"]["full_coverage_verified"])
        self.assertEqual(answer["contacts"][0]["title"], "CIO")
        self.assertEqual(len(answer["contacts"][0]["evidence_refs"][0]["quote"]), 300)
        self.assertEqual(answer["saved_email_status_counts"], {"unavailable_after_enrichment": 1})
        self.assertEqual(answer["pagination"]["contacts_total"], 1)

    async def test_existing_continuation_and_download_use_returned_goal_without_new_work(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith("download-link"):
                return httpx.Response(200, json={"data": {"filename": "first15.xlsx", "download_url": "https://example.com/file", "sha256": "h"}})
            return httpx.Response(200, json={"data": {
                "id": 74, "state": "completed", "fulfillment": "fulfilled", "version": 2,
                "contract": {"workflow": "business_research"},
                "business_answer": {"counts": {"requested": 15, "returned": 15}, "rows": []},
                "artifacts": [{"id": 8, "name": "first15.xlsx"}],
            }})
        transport = httpx.MockTransport(handler)
        result = await invoke_task("wait_for_task", {"goal_id": 74, "timeout_seconds": 0}, api_url="https://example.com", api_key="customer", transport=transport)
        self.assertEqual(rendered(result)["wait_status"], "finished")
        result = await invoke_task("get_task_artifact", {"goal_id": 74, "artifact_id": 8}, api_url="https://example.com", api_key="customer", transport=transport)
        self.assertEqual(rendered(result)["filename"], "first15.xlsx")
        self.assertTrue(all(request.method == "GET" for request in calls))
        self.assertEqual(calls[0].url.params["view"], "answer")

    def test_real_backend_contact_projection_preserves_counts_and_truncation(self):
        task = {"id": 75, "state": "running", "contract": {"workflow": "business_research"},
            "business_answer": {"counts": {"contacts": 150, "recorded_emails": 130},
                "contacts_total": 150, "contacts_truncated": True,
                "contacts": [{"id": "one", "name": "Person", "company": "Dell", "title": "CIO",
                    "email": "person@dell.example", "email_verified_at": "2026-09-01T10:00:00Z",
                    "email_status": "verified", "enrichment_status": "completed",
                    "website_url": "https://dell.example", "linkedin_status": "not_returned_by_completed_provider",
                    "field_provenance": {"email": {"status": "recorded", "source": "Apollo", "observed_at": "2026-10-05T10:00:00Z", "uncertainty": "Provider reported; no fresh verification."}},
                    "evidence_refs": [{"source_url": "https://publisher.example.com/person", "observed_at": "2026-10-05T10:00:00Z"},
                        {"provider": "apollo", "field": "professional_identity", "source_operation_id": 19, "enrichment_source": "completed_apollo_receipt_reused"}]}]}}
        result = compact_research_answer(task)
        self.assertEqual(result["business_answer"]["counts"]["contacts"], 150)
        self.assertEqual(result["contacts"][0]["email"], "person@dell.example")
        self.assertEqual(result["contacts"][0]["email_verified_at"], "2026-09-01T10:00:00Z")
        self.assertEqual(result["contacts"][0]["website_url"], "https://dell.example")
        self.assertEqual(result["contacts"][0]["linkedin_status"], "not_returned_by_completed_provider")
        self.assertEqual(result["contacts"][0]["field_provenance"]["email"]["source"], "Apollo")
        self.assertEqual(result["contacts"][0]["evidence_refs"][1]["source_operation_id"], 19)
        self.assertEqual(result["pagination"]["contacts_total"], 150)
        self.assertEqual(result["pagination"]["contacts_available"], 1)
        self.assertTrue(result["pagination"]["contacts_truncated"])
        self.assertEqual(result["saved_status_count_scope"], {"contacts_counted": 1, "contacts_total": 150, "complete": False})


if __name__ == "__main__":
    unittest.main()

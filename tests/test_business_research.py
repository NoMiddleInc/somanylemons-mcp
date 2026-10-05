"""Actual MCP schema and tool invocation across the HTTP facade, no live providers."""

import json
import unittest

import httpx

from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.server import compact_research_answer
from somanylemons_mcp.task_tools.business_research import BusinessCompany, BusinessResearchSpec
from pydantic import ValidationError


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
        fields = next(value for value in spec["properties"]["fields"]["anyOf"] if value.get("type") == "array")
        self.assertEqual(fields["maxItems"], 40)
        self.assertTrue({"session_date", "session_time", "room", "email_status", "field_provenance",
                         "organizer_company", "requested_company_identity_hints", "city", "state"}.issubset(fields["items"]["enum"]))
        self.assertIn("identity_hints", schema["$defs"]["BusinessCompany"]["properties"])

    async def test_dell_cio_and_first15_conference_payloads_use_generic_scoped_endpoint(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {
                "id": 71, "state": "queued", "version": 1,
                "contract": {"workflow": "business_research", "request": json.loads(request.content)["request"]},
            }})
        for spec in (
            {"kind": "company_contacts", "companies": [{"name": "Dell", "domain": "dell.com",
                "identity_hints": {"hq_city": "Round Rock", "supplied_literal": {"source_row": 7, "confirmed": False}}}],
                "roles": ["CIO"], "count": 1, "fields": ["email", "name", "website_url", "email_status", "field_provenance", "city", "state"]},
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

    def test_identity_clues_and_field_order_are_bounded_typed_literal_json(self):
        literal = {"source_row": 7, "clues": ["Chicago", None, True], "nested": {"state": "literal clue"}}
        self.assertEqual(BusinessCompany(name="Acme", identity_hints=literal).identity_hints, literal)
        with self.assertRaises(ValidationError):
            BusinessCompany(name="Acme", identity_hints={"clue": "x" * 10000})
        with self.assertRaises(ValidationError):
            BusinessCompany(name="Acme", identity_hints=["wrong container"])
        BusinessResearchSpec(kind="company_contacts", fields=["name"] * 40)
        with self.assertRaises(ValidationError):
            BusinessResearchSpec(kind="company_contacts", fields=["name"] * 41)

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

    def test_delivered_interim_never_closes_original_all_obligation(self):
        task = {"id": 76, "state": "completed", "fulfillment": "fulfilled",
            "contract": {"workflow": "business_research"}, "business_answer": {
                "contacts": [], "contacts_total": 0, "original_goal_id": 24, "original_goal_obligation": "open",
                "closure": "interim_delivered_original_open", "delivery": {"status": "provider_accepted"},
                "coverage": {"interim": True, "original_goal_id": 24, "original_all_obligation": "open_coverage_unproven"},
                "interim_results": [{"goal_id": 76, "state": "completed", "fulfillment": "fulfilled", "original_all_obligation": "open"}]}}
        result = compact_research_answer(task)
        self.assertEqual(result["business_answer"]["original_goal_id"], 24)
        self.assertEqual(result["business_answer"]["original_goal_obligation"], "open")
        self.assertEqual(result["business_answer"]["closure"], "interim_delivered_original_open")
        self.assertEqual(result["business_answer"]["coverage"]["original_all_obligation"], "open_coverage_unproven")
        self.assertEqual(result["business_answer"]["interim_results"][0]["original_all_obligation"], "open")
        self.assertNotIn("full_request_fulfilled", result["business_answer"])
        task["business_answer"].update(original_goal_obligation="fulfilled", closure="interim_delivered_original_fulfilled")
        current = compact_research_answer(task)["business_answer"]
        self.assertEqual(current["original_goal_obligation"], "fulfilled")
        self.assertEqual(current["closure"], "interim_delivered_original_fulfilled")
        self.assertEqual(current["coverage"]["original_all_obligation"], "open_coverage_unproven")

    def test_publisher_and_saved_metadata_preserved_with_explicit_provenance_preview_limits(self):
        row = {"id": "one", "name": "Person", "email": "person@example.com", "city": "Chicago", "state": "Illinois", "session_date": "2026-10-05",
            "session_time": "09:00", "room": "Main Hall", "organizer_company": "Published Company",
            "organizer_title": "Published Title", "recorded_organizer_email": "published@example.com",
            "provider_email_status": "verified", "email_content_hash": "saved-hash",
            "email_candidates": [{"email": "person@example.com", "source": "Apollo", "status": "unverified"}],
            "evidence_refs": [{"source_url": "https://example.com", "quote_omitted_for_delivery_policy": True}],
            "requested_company_identity_hints": {"hq_city": "Chicago"}, "requested_company_name": "Requested company",
            "public_sources": [{"url": f"https://example.com/{i}"} for i in range(4)],
            "field_provenance": {f"field{i}": {"source": "Apollo", "source_operation_id": 7, "uncertainty": "x" * 400} for i in range(42)}}
        answer = compact_research_answer({"id": 79, "business_answer": {"contacts": [row]}})
        projected = answer["contacts"][0]
        for field in ("city", "state", "session_date", "session_time", "room", "organizer_company", "organizer_title",
                      "recorded_organizer_email", "email_content_hash", "provider_email_status", "email_candidates", "requested_company_identity_hints", "requested_company_name"):
            self.assertEqual(projected[field], row[field])
        self.assertFalse(projected["email_candidates_truncated"])
        self.assertTrue(projected["evidence_refs"][0]["quote_omitted_for_delivery_policy"])
        self.assertEqual(projected["field_provenance_total"], 42)
        self.assertEqual(len(projected["field_provenance"]), 40)
        self.assertEqual(projected["field_provenance"]["field0"]["source_operation_id"], 7)
        self.assertTrue(projected["field_provenance_truncated"])
        self.assertTrue(projected["source_references_truncated"])

    def test_current_review_scope_preserved_with_only_four_manifest_bound_checks(self):
        checks = [{"obligation": gate, "artifact_id": artifact_id, "manifest_hashes": [digest]}
            for artifact_id, digest in [(1, "old"), (2, "current")] for gate in (
                "business_artifact_content", "business_request_fulfillment", "whole_request_acceptance", "customer_response_truthfulness")]
        task = {"id": 79, "business_answer": {"contacts": [], "contract_revision": 2,
            "contract_hash": "contract", "current_policy_hash": "policy", "review_scope_current": True,
            "fulfillment_review": {"passed": True, "manifest_hash": "current"}, "scope_quality_checks": checks}}
        projected = compact_research_answer(task)["business_answer"]
        self.assertEqual(projected["contract_hash"], "contract")
        self.assertEqual(projected["current_policy_hash"], "policy")
        self.assertTrue(projected["review_scope_current"])
        self.assertEqual(len(projected["scope_quality_checks"]), 4)
        self.assertTrue(all(check["artifact_id"] == 2 for check in projected["scope_quality_checks"]))
        self.assertEqual(projected["scope_quality_checks_total"], 8)
        self.assertTrue(projected["scope_quality_checks_truncated"])


if __name__ == "__main__":
    unittest.main()

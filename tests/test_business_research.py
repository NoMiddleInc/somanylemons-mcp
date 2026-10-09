"""Actual MCP schema and tool invocation across the HTTP facade, no live providers."""

import json
import unittest

import httpx

from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.server import compact_research_answer
from somanylemons_mcp.task_tools.business_research import BusinessCompany, BusinessResearchSpec
from pydantic import ValidationError
from mcp.server.fastmcp.exceptions import ToolError


def rendered(result):
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


class BusinessResearchToolsTests(unittest.IsolatedAsyncioTestCase):
    def test_compact_contact_answer_preserves_canonical_two_line_response(self):
        canonical = {"answer_text": "Email: saved@example.com\nLinkedIn: Unavailable", "response_policy": "Reply with answer_text only", "contacts": []}
        projected = compact_research_answer({"id": 152, "business_answer": canonical})
        self.assertEqual(projected["business_answer"]["answer_text"], canonical["answer_text"])
        self.assertEqual(projected["business_answer"]["response_policy"], canonical["response_policy"])

    async def test_missing_business_criteria_uses_original_goal_and_exact_control_binding(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertIn("supply_business_inputs", tools)
        schema = tools["supply_business_inputs"].inputSchema
        self.assertEqual(set(schema["required"]), {
            "goal_id", "spec", "expected_version", "expected_revision", "input_binding",
            "idempotency_key", "reason",
        })
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {
                "id": 71, "state": "queued", "version": 4,
                "contract": {"workflow": "business_research"},
            }})
        spec = {"kind": "company_contacts", "companies": [{"name": "Dell"}],
                "roles": ["CIO"], "count": 15, "all": False,
                "fields": ["name", "email", "linkedin"], "fields_explicit": True,
                "person_name": "", "event": {}}
        args = {"goal_id": 71, "spec": spec, "expected_version": 3,
                "expected_revision": 1, "input_binding": "a" * 64,
                "idempotency_key": "22222222-2222-4222-8222-222222222222",
                "reason": "The customer supplied the missing company."}
        result = await invoke_task("supply_business_inputs", args, api_url="https://example.com",
                                   api_key="customer", transport=httpx.MockTransport(handler))
        self.assertEqual(rendered(result)["id"], 71)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/71/actions")
        self.assertEqual(json.loads(calls[0].content), {
            "action": "supply_input", "expected_version": 3,
            "idempotency_key": args["idempotency_key"], "reason": args["reason"],
            "inputs": {"spec": spec, "expected_revision": 1, "input_binding": "a" * 64},
        })
        self.assertEqual(calls[0].headers["x-api-key"], "customer")

    def test_saved_business_input_proof_is_available_without_local_scope_inference(self):
        proof = {"status": "awaiting_criteria", "missing_fields": ["companies"],
                 "partial_spec": {"kind": "company_contacts", "count": 15},
                 "expected_version": 3, "expected_revision": 1, "input_binding": "a" * 64}
        answer = compact_research_answer({"id": 71, "state": "waiting_customer",
            "contract": {"workflow": "business_research"},
            "business_answer": {"input_required": proof, "full_request_fulfilled": False}})
        self.assertEqual(answer["business_answer"]["input_required"], proof)
        self.assertFalse(answer["business_answer"]["full_request_fulfilled"])

    async def test_invalid_business_input_envelopes_never_reach_the_http_action(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {}})
        base = {"goal_id": 71, "spec": {"kind": "company_contacts"}, "expected_version": 3,
                "expected_revision": 1, "input_binding": "a" * 64,
                "idempotency_key": "22222222-2222-4222-8222-222222222222", "reason": "Missing criteria."}
        for update in ({"input_binding": "invalid"}, {"expected_revision": 0},
                       {"spec": {"person_name": "x" * 20001}}):
            with self.assertRaises(ToolError):
                await invoke_task("supply_business_inputs", {**base, **update},
                    api_url="https://example.com", api_key="customer", transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [])

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
        self.assertEqual(
            schema["properties"]["research_depth"],
            {
                "default": "standard",
                "enum": ["standard", "deep"],
                "title": "Research Depth",
                "type": "string",
            },
        )
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
        self.assertIn("person_locations", spec["properties"])
        self.assertIn("per_company_count", spec["properties"])
        self.assertIn("company_scope", spec["properties"])
        self.assertIn("same background interpretation as email", tool.description)
        self.assertIn("original customer request verbatim", tool.description)
        self.assertIn("never overrides the original question", tool.description)
        self.assertIn("never company headquarters as personal residence", tool.description)
        self.assertNotIn("Perplexity", tool.description)
        self.assertIsNone(spec["properties"]["person_locations"]["default"])

    async def test_named_speakers_use_individual_enrichment_with_conference_context(self):
        from uuid import uuid4
        tools = {tool.name: tool for tool in await task_schemas()}
        description = tools['create_business_research_request'].description
        self.assertIn('one individual request per person', description)
        self.assertIn('never concatenate names', description.lower())
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={'data': {'id': 71, 'state': 'queued'}})
        for name in ('James Benham', 'Niji Sabharwal', 'James Thom', 'Dave Rose', 'Regina Felts'):
            spec = {'kind': 'company_contacts', 'person_name': name, 'companies': [],
                    'count': 1, 'all': False, 'fields': ['email', 'linkedin']}
            question = f'Get email and LinkedIn for {name}, one of the ITC conference speakers.'
            await invoke_task('create_business_research_request', {
                'request': question, 'spec': spec, 'idempotency_key': str(uuid4()),
            }, api_url='https://example.com', api_key='customer', transport=httpx.MockTransport(handler))
            self.assertEqual(calls[-1]['spec'], spec)
            self.assertEqual(calls[-1]['request'], question)
            self.assertEqual(calls[-1]['intake_channel'], 'mcp_v1')
        self.assertEqual(len({call['idempotency_key'] for call in calls}), 5)

    async def test_find_people_posts_the_original_request_and_returns_rows_in_one_call(self):
        calls = []
        rows = [{"name": "Pat Example", "email": "pat@example.com", "unverified": []}]

        def handler(request):
            calls.append((request.method, request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"data": {"rows": rows, "coverage": {"returned": 1}}})

        question = "Find 1 commercial insurance producer based in Virginia."
        result = rendered(await invoke_task(
            "find_people", {"request": question}, api_url="https://example.com",
            api_key="customer", transport=httpx.MockTransport(handler)))
        self.assertEqual(calls, [("POST", "/api/v1/agent-tasks/quick-search", {"request": question})])
        self.assertEqual(result["rows"], rows)
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertEqual(tools["find_people"].inputSchema["required"], ["request"])
        with self.assertRaises(ToolError):
            await invoke_task("find_people", {"request": ""}, api_url="https://example.com",
                              api_key="customer", transport=httpx.MockTransport(handler))

    async def test_off_list_variety_preserves_location_identity_and_each_company_scope(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"id": 71, "state": "queued"}})
        specs = [
            {"kind": "company_contacts", "companies": [{"name": "Verified Chicago Company"}], "roles": ["GTM engineer"], "person_locations": ["Chicago"], "count": 5},
            {"kind": "company_contacts", "companies": [], "person_name": "Satya Nadella", "count": 1},
            {"kind": "company_contacts", "companies": [{"name": "Costco", "domain": "costco.com"}], "roles": ["CFO"], "count": 1},
            {"kind": "company_contacts", "companies": [{"name": f"Company {number}"} for number in range(10)], "roles": ["CEO", "co-CEO"], "per_company_count": 1, "count": 10},
            {"kind": "company_contacts", "companies": [{"name": "Verified New York Company"}], "roles": ["CFO"], "person_locations": ["New York City"], "count": 3},
        ]
        for spec in specs:
            await invoke_task("create_business_research_request", {
                "request": "Research exactly the supplied individual and company criteria outside saved lists.",
                "spec": spec, "idempotency_key": "22222222-2222-4222-8222-222222222222",
            }, api_url="https://example.com", api_key="customer", transport=httpx.MockTransport(handler))
            self.assertEqual(calls[-1]["spec"], spec)
            self.assertNotIn("campaign_id", calls[-1])
        self.assertEqual(len(calls), 5)


    async def test_new_request_geography_preserves_explicit_and_global_and_event_scope(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"id": 71, "state": "queued"}})
        cases = [
            ("US first assumed because personal geography was omitted; find five CFOs.",
             {"kind": "company_contacts", "companies": [{"name": "Dell"}], "roles": ["CFO"], "person_locations": ["United States"], "count": 5}),
            ("Find five CFOs in Chicago.",
             {"kind": "company_contacts", "companies": [{"name": "Dell"}], "roles": ["CFO"], "person_locations": ["Chicago"], "count": 5}),
            ("Find five CFOs in Canada.",
             {"kind": "company_contacts", "companies": [{"name": "Dell"}], "roles": ["CFO"], "person_locations": ["Canada"], "count": 5}),
            ("Find five CFOs worldwide, with no personal geography restriction.",
             {"kind": "company_contacts", "companies": [{"name": "Dell"}], "roles": ["CFO"], "person_locations": [], "count": 5}),
            ("All published speakers at the supplied 2026 conference edition, globally.",
             {"kind": "conference_speakers", "event": {"name": "Global Congress", "year": 2026,
              "source_urls": ["https://publisher.example.com/speakers"]}, "all": True}),
        ]
        for question, spec in cases:
            await invoke_task("create_business_research_request", {
                "request": question, "spec": spec,
                "idempotency_key": "22222222-2222-4222-8222-222222222222",
            }, api_url="https://example.com", api_key="customer", transport=httpx.MockTransport(handler))
            self.assertEqual(calls[-1]["request"], question)
            self.assertEqual(calls[-1]["spec"], spec)
        self.assertEqual(len(calls), 5)

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
            self.assertEqual(body["research_depth"], "standard")
            self.assertEqual(calls[-1].url.path, "/api/v1/agent-tasks/business-research")
            self.assertEqual(calls[-1].headers["x-api-key"], "customer")
            self.assertNotIn("budget", body)
        self.assertEqual(len(calls), 2)

    async def test_deep_research_preserves_explicit_customer_depth_without_retired_provider_claims(self):
        calls = []

        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"data": {"id": 73, "state": "queued", "contract": {"workflow": "business_research"}}},
            )

        await invoke_task(
            "create_business_research_request",
            {
                "request": "Deep research the supplied company contacts.",
                "spec": {"kind": "company_contacts", "companies": [{"name": "Dell"}]},
                "research_depth": "deep",
                "idempotency_key": "22222222-2222-4222-8222-222222222222",
            },
            api_url="https://example.com",
            api_key="customer",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(calls[0]["research_depth"], "deep")

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

    async def test_named_scope_uncertain_retry_reuses_exact_uuid(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"id": 72, "state": "queued", "contract": {"workflow": "business_research"}}})
        arguments = {"spec": {"kind": "company_contacts", "companies": [{"name": "Dell"}], "roles": ["CIO"]}, "request": "Find the CIO at Dell", "idempotency_key": "22222222-2222-4222-8222-222222222222"}
        for _ in range(2):
            await invoke_task("create_business_research_request", arguments, api_url="https://example.com", api_key="customer", transport=httpx.MockTransport(handler))
        expected = {**arguments, "research_depth": "standard", "intake_channel": "mcp_v1"}
        self.assertEqual(calls, [expected, expected])

    async def test_request_only_uses_shared_background_interpretation(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"id": 74, "state": "queued",
                "acknowledgment": "We're working on it.",
                "interpretation_version": "shared-business-interpretation-v1"}})
        arguments = {"request": "10 CEOs of B2B marketing agencies in Chicago",
                     "idempotency_key": "22222222-2222-4222-8222-222222222222"}
        result = await invoke_task("create_business_research_request", arguments,
            api_url="https://example.com", api_key="customer", transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [{**arguments, "research_depth": "standard", "intake_channel": "mcp_v1"}])
        result = json.loads(result[0].text)
        self.assertEqual(result["acknowledgment"], "We're working on it.")
        self.assertEqual(result["interpretation_version"], "shared-business-interpretation-v1")

    async def test_backend_named_scope_rejection_is_reported_without_fallback_search(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(400, json={"detail": "MCP v1 accepts named companies or an individual person. Find and verify matching company names first."})
        spec = {"kind": "company_contacts", "roles": ["CEO"], "person_locations": ["Chicago"], "count": 10}
        with self.assertRaisesRegex(ToolError, "named companies"):
            await invoke_task("create_business_research_request", {
                "request": "10 CEOs of B2B marketing agencies in Chicago", "spec": spec,
                "idempotency_key": "22222222-2222-4222-8222-222222222222",
            }, api_url="https://example.com", api_key="customer", transport=httpx.MockTransport(handler))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["intake_channel"], "mcp_v1")
        self.assertEqual(calls[0]["spec"], spec)

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

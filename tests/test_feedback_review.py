"""Portable source integration tests; synthetic fixtures and mocked transports only."""

from copy import deepcopy
import base64
from contextlib import asynccontextmanager
import asyncio
import hashlib
import json
import unittest
from unittest.mock import patch

import httpx

from tests.fixtures.feedback_review import attachment_bytes, review_rows, sequence_rows, snapshot_fixture

from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig, TaskApiError
from somanylemons_mcp.task_tools.current_answer import resolve_current_answer, resolution_metadata
from somanylemons_mcp.task_tools.answer_navigation import list_task_navigation
from somanylemons_mcp.task_tools.feedback_review import (
    feedback_projection, read_feedback_attachment_resource, resource_uri, validate_feedback_snapshot,
)
from somanylemons_mcp.task_tools.server import brief_answer, compact_research_answer, create_server
from somanylemons_mcp.task_bridge import read_task_feedback_attachment


def task_fixture(goal_id=52, *, native_pointer=True, native_scope=True):
    snapshot = snapshot_fixture(goal_id)
    # Deliberately different native qualification numbers. The feedback batch
    # must supplement this saved native result, never overwrite or qualify it.
    task = {
        "id": goal_id, "customer": {"id": 79}, "state": "paused", "version": 17 if goal_id == 52 else 9,
        "allowed_actions": ["cancel"] if goal_id == 52 else ["resume", "cancel"],
        "contract_revision": 1 if goal_id == 52 else 2,
        "contract": {"request": "Original saved request", "task_manager": {"revision": 1 if goal_id == 52 else 2}},
        "action_is_scheduled": False, "progress": {"completed": 10 if goal_id == 52 else 3, "total": 10 if goal_id == 52 else 110},
        "fulfillment": {"satisfied": False}, "manual_review_required": True, "artifacts": [],
        "research_answer": {"counts": {"qualified_contacts": 1, "requested_agencies": 10 if goal_id == 52 else 110},
            "agencies": [{"agency": "Native fixture agency", "qualified_contacts": 1}]},
        "tasks": [{"id": 533, "contract_revision": 1 if goal_id == 52 else 2,
                   "capability": "agency.research", "title": "Native fixture agency", "state": "succeeded",
                   "result": {"agency": {"name": "Native fixture agency"}, "rows": [{"id": "native-only", "name": "Native saved fixture", "email": "", "email_status": "not_recorded"}]}}],
        "feedback_review_snapshot": snapshot,
    }
    if native_pointer:
        task["current_answer_goal_id"] = 52
    if native_scope:
        task["answer_lineage_scope"] = deepcopy(snapshot["answer_lineage_scope"])
    return task


class FeedbackProjectionTests(unittest.TestCase):
    def setUp(self):
        self.task = task_fixture()

    def test_interoperates_with_backend_dto_contract_and_preserves_all_csv_strings(self):
        original = deepcopy(self.task)
        snapshot = validate_feedback_snapshot(self.task)
        self.assertEqual(snapshot["counts"], {"selected_review_contact_count": 39, "recorded_business_email_count": 35, "sequence_step_count": 234})
        review, sequence = review_rows(), sequence_rows()
        self.assertEqual([row["fields"] for row in snapshot["selected_review_contacts"]], review)
        self.assertEqual([row["fields"] for row in snapshot["draft_sequences"]], sequence)
        self.assertEqual(self.task, original)

    def test_full_feedback_pagination_is39_contacts234_step_metadata_not_native_qualification(self):
        selected, sequence = [], []
        for page in range(1, 9):
            answer = compact_research_answer(self.task, contact_page=page)
            snapshot = answer["feedback_review_snapshot"]
            selected += snapshot["selected_review_contacts"]
            sequence += snapshot["draft_sequences"]
            self.assertEqual(answer["research_answer"]["counts"]["qualified_contacts"], 1)
            self.assertEqual(answer["pagination"]["contacts_total"], 1)
            self.assertEqual(snapshot["pagination"]["draft_sequence_steps_total"], 234)
            self.assertFalse(snapshot["native_research_counts_replaced"])
        self.assertEqual(len(selected), 39)
        self.assertEqual(len(sequence), 234)
        self.assertEqual(len({row["stable_prospect_id"] for row in selected}), 39)
        self.assertEqual(selected, self.task["feedback_review_snapshot"]["selected_review_contacts"])
        self.assertEqual([(row["stable_prospect_id"], row["step_number"]) for row in sequence],
                         [(row["stable_prospect_id"], row["step_number"]) for row in self.task["feedback_review_snapshot"]["draft_sequences"]])
        self.assertTrue(all(row["body_omitted_from_compact_projection"] for row in sequence))
        self.assertTrue(all("Body" not in row["fields"] for row in sequence))

    def test_brief_retains_exact_counts_scope_hold_sources_and_attachment_paths_without_copy_bodies(self):
        answer = brief_answer(compact_research_answer(self.task))
        snapshot = answer["feedback_review_snapshot"]
        self.assertNotIn("selected_review_contacts", snapshot)
        self.assertNotIn("draft_sequences", snapshot)
        self.assertEqual(snapshot["source"], self.task["feedback_review_snapshot"]["source"])
        self.assertEqual(snapshot["counts"]["recorded_business_email_count"], 35)
        self.assertEqual(snapshot["review_scope"]["remaining_original_agency_count"], 100)
        self.assertFalse(snapshot["review_scope"]["original_request_fulfilled"])
        self.assertFalse(snapshot["recorded_verification_is_fresh"])
        self.assertFalse(snapshot["saved_delivery"]["inbox_receipt_proven"])
        self.assertEqual(answer["allowed_actions"], self.task["allowed_actions"])
        self.assertEqual(len(snapshot["attachments"]), 3)
        self.assertTrue(all(row["mcp_resource_uri"].startswith("producerspark-feedback-attachment://52/251/") for row in snapshot["attachments"]))

    def test_nested_feedback_scope_is_valid_without_inventing_native_scope_or_pointer(self):
        task = task_fixture(native_pointer=False, native_scope=False)
        answer = compact_research_answer(task)
        self.assertEqual(answer["feedback_review_snapshot"]["current_answer_goal_id"], 52)
        self.assertIsNone(answer["current_answer_goal_id"])
        self.assertNotIn("answer_lineage_scope", answer)

    def test_absent_or_explicit_unavailable_binding_does_not_fabricate_selected_rows(self):
        task = deepcopy(self.task); task.pop("feedback_review_snapshot")
        self.assertIsNone(feedback_projection(task))
        task["feedback_review_snapshot"] = {"status": "unavailable", "reason": "saved_feedback_binding_or_integrity_invalid", "read_only": True, "new_research_or_delivery": False}
        self.assertEqual(feedback_projection(task)["status"], "unavailable")
        self.assertNotIn("counts", feedback_projection(task))

    def test_foreign_missing_bad_scope_revision_and_hold_fail_closed(self):
        mutations = [
            lambda t: t["customer"].update(id=80),
            lambda t: t.pop("customer"),
            lambda t: t["answer_lineage_scope"].update(config_id=9),
            lambda t: t["feedback_review_snapshot"]["answer_lineage_scope"].update(client_id=True),
            lambda t: t["feedback_review_snapshot"]["source"].update(organization_id=99),
            lambda t: t["feedback_review_snapshot"].update(requested_goal_id=24),
            lambda t: t.update(contract_revision=2),
            lambda t: t.update(current_answer_goal_id=999),
            lambda t: t.update(state="running"),
            lambda t: t.update(action_is_scheduled=True),
            lambda t: t["feedback_review_snapshot"]["review_scope"].update(remaining_work_held=False),
            lambda t: t["feedback_review_snapshot"]["review_scope"].update(original_request_fulfilled=True),
            lambda t: t["feedback_review_snapshot"].update(recorded_verification_is_fresh=True),
            lambda t: t["feedback_review_snapshot"]["saved_delivery"].update(inbox_receipt_proven=True),
        ]
        for mutation in mutations:
            t = deepcopy(self.task); mutation(t)
            with self.subTest(mutation=mutation), self.assertRaises(TaskApiError):
                compact_research_answer(t)

    def test_invalid_counts_duplicate_ids_draft_rewrites_or_outreach_flags_fail_closed(self):
        mutations = [
            lambda s: s["counts"].update(selected_review_contact_count=40),
            lambda s: s["counts"].update(recorded_business_email_count=39),
            lambda s: s["selected_review_contacts"].append(s["selected_review_contacts"][0]),
            lambda s: s["selected_review_contacts"][0].update(stable_prospect_id=[]),
            lambda s: s["selected_review_contacts"][0]["fields"].update({"Membership change applied": "Yes"}),
            lambda s: s["draft_sequences"][0].update(step_number=True),
            lambda s: s["draft_sequences"][0]["fields"].update({"Body": "rewritten"}),
            lambda s: s["draft_sequences"][0]["fields"].update({"Outreach enrolled": "Yes"}),
            lambda s: s["retrieval_effects"].update(customer_email_sent=True),
        ]
        for mutation in mutations:
            t = deepcopy(self.task); mutation(t["feedback_review_snapshot"])
            with self.subTest(mutation=mutation), self.assertRaises(TaskApiError):
                validate_feedback_snapshot(t)

    def test_no_artifact_id_local_filepath_or_unsigned_download_link_is_invented(self):
        snapshot = feedback_projection(self.task)
        self.assertEqual(self.task["artifacts"], [])
        for attachment in snapshot["attachments"]:
            self.assertNotIn("artifact_id", attachment)
            self.assertNotIn("download_url", attachment)
            self.assertNotIn("/Users/", json.dumps(attachment))
            self.assertTrue(attachment["attachment_retrieval"]["authentication_required"])

    def test_agency_list_navigation_labels_original_obligation_without_conference_language(self):
        original = task_fixture(24)
        response = {"tasks": [original]}
        before = deepcopy(response)
        listed = list_task_navigation(response)["tasks"][0]
        self.assertEqual(listed["canonical_answer_action"], {"tool": "get_research_answer", "goal_id": 52})
        self.assertEqual(listed["listed_goal_progress"]["scope_goal_id"], 24)
        self.assertIn("feedback", listed["outcome_summary"])
        self.assertNotIn("conference", listed["current_answer_goal_id_basis"])
        self.assertEqual(response, before)

    def test_foreign_agency_list_navigation_scope_fails_closed(self):
        original = task_fixture(24)
        original["customer"]["id"] = 99
        with self.assertRaises(TaskApiError): list_task_navigation({"tasks": [original]})

    def test_list_preserves_backend_feedback_summary_without_rows_or_draft_bodies(self):
        original = task_fixture(24)
        snapshot = original["feedback_review_snapshot"]
        summary = {key: deepcopy(snapshot[key]) for key in (
            "version", "counts", "review_scope", "saved_delivery", "counting_basis", "native_research_counts_replaced",
        )}
        row = {key: deepcopy(original[key]) for key in (
            "id", "current_answer_goal_id", "customer", "answer_lineage_scope", "progress", "version", "state", "allowed_actions",
        )}
        row["feedback_review_summary"] = summary
        listed = list_task_navigation({"tasks": [row]})["tasks"][0]
        self.assertEqual(listed["feedback_review_summary"], summary)
        self.assertEqual(listed["feedback_review_summary"]["counts"], {
            "selected_review_contact_count": 39, "recorded_business_email_count": 35, "sequence_step_count": 234,
        })
        self.assertEqual(listed["listed_goal_progress"]["completed"], 3)
        self.assertEqual(listed["feedback_review_summary"]["review_scope"]["remaining_original_agency_count"], 100)
        self.assertNotIn("selected_review_contacts", json.dumps(listed))
        self.assertNotIn("draft_sequences", json.dumps(listed))

    def test_coherent_task_and_attachment_revision_relabeling_fails_reviewed_publication_binding(self):
        for goal_id in (24, 52):
            task = task_fixture(goal_id)
            task["contract_revision"] += 1
            task["contract"]["task_manager"]["revision"] += 1
            source = task["feedback_review_snapshot"]["source"]
            key = "current_original_contract_revision" if goal_id == 24 else "current_pilot_contract_revision"
            source[key] += 1
            if goal_id == 52:
                for descriptor in task["feedback_review_snapshot"]["attachments"]:
                    descriptor["validation_contract_revision"] += 1
            # These are coherent labels; simple task==descriptor checks would
            # pass. The independently reviewed publication2/1 pin must reject.
            with self.subTest(goal_id=goal_id), self.assertRaises(TaskApiError):
                validate_feedback_snapshot(task)

    def test_delivered_revisions_remain_explicitly_unknown_and_validation_pins_are_not_delivery_proof(self):
        projection = feedback_projection(self.task)
        source = projection["source"]
        self.assertEqual((source["current_original_contract_revision"], source["current_pilot_contract_revision"]), (2, 1))
        self.assertIsNone(source["delivered_original_contract_revision"])
        self.assertIsNone(source["delivered_pilot_contract_revision"])
        self.assertEqual(source["delivered_revision_status"], "not_recorded")
        for descriptor in projection["attachments"]:
            self.assertEqual(descriptor["validation_contract_revision"], 1)
            self.assertIsNone(descriptor["delivered_contract_revision"])
            self.assertNotIn("source_contract_revision", descriptor)
        for mutation in (
            lambda s: s["source"].update(delivered_original_contract_revision=2),
            lambda s: s["source"].update(delivered_pilot_contract_revision=1),
            lambda s: s["source"].update(validation_receipt_sha256="0" * 64),
            lambda s: s["source"]["publication_validation"].update(pilot_contract_revision=2),
            lambda s: s["source"]["publication_validation"].update(pilot_contract_revision=True),
            lambda s: s["attachments"][0].update(validation_contract_revision=True),
            lambda s: s["attachments"][0].update(delivered_contract_revision=1),
            lambda s: s["source"].pop("delivered_original_contract_revision"),
        ):
            task = deepcopy(self.task); mutation(task["feedback_review_snapshot"])
            with self.subTest(mutation=mutation), self.assertRaises(TaskApiError):
                validate_feedback_snapshot(task)

    def test_coherently_rebound_foreign_scope_fails_narrow_saved_delivery_publication_pin(self):
        task = deepcopy(self.task)
        task["customer"]["id"] = 80
        task["answer_lineage_scope"].update(client_id=80, config_id=9)
        task["feedback_review_snapshot"]["answer_lineage_scope"].update(client_id=80, config_id=9)
        task["feedback_review_snapshot"]["source"].update(client_id=80, config_id=9)
        with self.assertRaises(TaskApiError): validate_feedback_snapshot(task)


class FeedbackProtocolTests(unittest.IsolatedAsyncioTestCase):
    def api(self, tasks, *, attachment=None, corrupt=False, denied=False):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.method, "GET")
            if request.url.path.endswith("/attachments"):
                self.assertEqual(request.url.path, "/api/v1/agent-tasks/52/feedback-snapshots/251/attachments")
                self.assertEqual(dict(request.url.params), {"name": attachment["name"], "sha256": attachment["sha256"]})
                if denied:
                    return httpx.Response(403, json={"detail": "Scope denied"})
                raw = attachment_bytes()[attachment["name"]]
                return httpx.Response(200, content=raw + b"changed" if corrupt else raw)
            goal = int(request.url.path.rsplit("/", 1)[-1])
            self.assertEqual(dict(request.url.params), {"view": "answer"})
            return httpx.Response(200, json={"data": tasks[goal]})
        transport = httpx.MockTransport(handler)
        return TaskApiClient(TaskApiConfig("https://example.invalid", "offline-key"), transport=transport), calls, transport

    async def test_three_default_tools_follow24_to52_preserving_original_controls_and_hold(self):
        original, current = task_fixture(24), task_fixture(52)
        saved = deepcopy((original, current))
        for tool in ("get_task", "get_research_answer", "wait_for_task"):
            api, calls, _ = self.api({24: original, 52: current})
            args = {"goal_id": 24}
            if tool == "get_research_answer": args["details"] = True
            if tool == "wait_for_task": args["timeout_seconds"] = 0
            response = await create_server(api).call_tool(tool, args)
            if isinstance(response, tuple): response = response[0]
            answer = json.loads(response[0].text)
            if tool == "wait_for_task": answer = answer["task"]
            self.assertEqual([r.url.path.rsplit("/", 1)[-1] for r in calls], ["24", "52"])
            self.assertEqual((answer["id"], answer["version"], answer["allowed_actions"]), (52, 17, ["cancel"]))
            self.assertEqual(answer["requested_goal_controls"], {"goal_id": 24, "version": 9, "allowed_actions": ["resume", "cancel"]})
            self.assertEqual(answer["requested_goal_progress"]["scope_goal_id"], 24)
            self.assertEqual(answer["research_answer"]["counts"]["qualified_contacts"], 1)
            self.assertEqual(answer["feedback_review_snapshot"]["counts"]["selected_review_contact_count"], 39)
            self.assertTrue(answer["feedback_review_snapshot"]["review_scope"]["remaining_work_held"])
            self.assertIn("agency", answer["answer_resolution"]["validation_basis"])
        self.assertEqual((original, current), saved)

    async def test_historical_opt_out_preserves_original_controls_and_feedback_scope(self):
        api, calls, _ = self.api({24: task_fixture(24), 52: task_fixture(52)})
        resolved = await resolve_current_answer(api, 24, historical_snapshot=True)
        answer = resolution_metadata(compact_research_answer(resolved.current), resolved)
        self.assertEqual(len(calls), 1)
        self.assertEqual(answer["id"], 24)
        self.assertEqual(answer["feedback_review_snapshot"]["requested_goal_id"], 24)
        self.assertNotIn("requested_goal_controls", answer)
        self.assertTrue(answer["answer_resolution"]["historical_snapshot"])

    async def test_native_pointer_follow_requires_both_explicit_typed_scopes(self):
        for goal in (24, 52):
            tasks = {24: task_fixture(24), 52: task_fixture(52)}
            tasks[goal].pop("answer_lineage_scope")
            api, _, _ = self.api(tasks)
            with self.assertRaises(TaskApiError): await resolve_current_answer(api, 24)

    async def test_cross_config_client_campaign_original_scope_rejected_before_projection(self):
        for field in ("config_id", "client_id", "organization_id", "campaign_id", "original_goal_id"):
            original, current = task_fixture(24), task_fixture(52)
            current["answer_lineage_scope"][field] += 1
            api, _, _ = self.api({24: original, 52: current})
            with self.subTest(field=field), self.assertRaises(TaskApiError): await resolve_current_answer(api, 24)

    async def test_default_follow_rejects_coherent_revision_relabel_on_requested_or_returned_answer(self):
        for goal_id in (24, 52):
            tasks = {24: task_fixture(24), 52: task_fixture(52)}
            task = tasks[goal_id]
            task["contract_revision"] += 1
            task["contract"]["task_manager"]["revision"] += 1
            key = "current_original_contract_revision" if goal_id == 24 else "current_pilot_contract_revision"
            task["feedback_review_snapshot"]["source"][key] += 1
            if goal_id == 52:
                for descriptor in task["feedback_review_snapshot"]["attachments"]:
                    descriptor["validation_contract_revision"] += 1
            api, calls, _ = self.api(tasks)
            with self.subTest(goal_id=goal_id), self.assertRaises(TaskApiError):
                await resolve_current_answer(api, 24)
            self.assertEqual(len(calls), 1 if goal_id == 24 else 2)

    async def test_binary_resource_returns_three_exact_original_hashes_and_recorded_mime(self):
        task = task_fixture()
        for descriptor in task["feedback_review_snapshot"]["attachments"]:
            api, calls, transport = self.api({52: task}, attachment=descriptor)
            content, mime = await read_feedback_attachment_resource(api, resource_uri(task, descriptor))
            self.assertEqual(hashlib.sha256(content).hexdigest(), descriptor["sha256"])
            self.assertEqual(mime, descriptor["mime"])
            self.assertEqual(len(calls), 2)
            bridged, bridge_mime = await read_task_feedback_attachment(resource_uri(task, descriptor), api_url="https://example.invalid", api_key="offline-key", transport=transport)
            self.assertEqual((bridged, bridge_mime), (content, mime))

    async def test_attachment_http_denial_corruption_and_foreign_uri_fail_without_fallback(self):
        task = task_fixture(); descriptor = task["feedback_review_snapshot"]["attachments"][0]
        for options in ({"corrupt": True}, {"denied": True}):
            api, calls, _ = self.api({52: task}, attachment=descriptor, **options)
            with self.subTest(options=options), self.assertRaises(TaskApiError):
                await read_feedback_attachment_resource(api, resource_uri(task, descriptor))
            self.assertEqual(len(calls), 2)
        api, calls, _ = self.api({52: task}, attachment=descriptor)
        uri = resource_uri(task, descriptor).replace("/251/", "/252/")
        with self.assertRaises(TaskApiError): await read_feedback_attachment_resource(api, uri)
        self.assertEqual(len(calls), 1)

    async def test_attachment_route_injection_foreign_hash_and_unsafe_filename_fail_before_binary_get(self):
        mutations = [
            lambda d: d["attachment_retrieval"].update(api_url="https://foreign.invalid/attachment"),
            lambda d: d["attachment_retrieval"].update(api_url=d["attachment_retrieval"]["api_url"].replace("/52/", "/24/")),
            lambda d: d["attachment_retrieval"].update(api_url=d["attachment_retrieval"]["api_url"] + "&sha256=" + "0" * 64),
            lambda d: d.update(name="../customer.csv"),
            lambda d: d.update(validation_contract_revision=99),
        ]
        for mutation in mutations:
            task = task_fixture(); original = deepcopy(task["feedback_review_snapshot"]["attachments"][0])
            mutation(task["feedback_review_snapshot"]["attachments"][0])
            api, calls, _ = self.api({52: task}, attachment=original)
            with self.subTest(mutation=mutation), self.assertRaises(TaskApiError):
                await read_feedback_attachment_resource(api, resource_uri(task, original))
            self.assertEqual(len(calls), 1)

    async def test_hosted_feedback_resource_uses_scoped_bridge_and_recorded_csv_mime(self):
        from somanylemons_mcp import server as hosted
        task = task_fixture()
        descriptor = next(row for row in task["feedback_review_snapshot"]["attachments"] if row["mime"] == "text/csv")
        raw = attachment_bytes()[descriptor["name"]]
        calls = []
        async def fake_bridge(uri, *, api_url, api_key):
            calls.append((str(uri), api_url, api_key))
            return raw, "text/csv"
        with patch.object(hosted, "_request_identity", return_value={"api_key": "offline-key"}), \
             patch.object(hosted, "API_URL", "https://example.invalid"), \
             patch("somanylemons_mcp.task_bridge.read_task_feedback_attachment", fake_bridge):
            result = await hosted.read_resource(resource_uri(task, descriptor))
        self.assertEqual(calls, [(resource_uri(task, descriptor), "https://example.invalid", "offline-key")])
        self.assertEqual(result[0].content, raw)
        self.assertEqual(result[0].mime_type, "text/csv")

    async def test_task_server_resource_preserves_exact_bytes_and_uses_account_authentication(self):
        task = task_fixture()
        for descriptor in task["feedback_review_snapshot"]["attachments"]:
            api, calls, _ = self.api({52: task}, attachment=descriptor)
            contents = await create_server(api).read_resource(resource_uri(task, descriptor))
            self.assertEqual(contents[0].content, attachment_bytes()[descriptor["name"]])
            self.assertEqual(contents[0].mime_type, descriptor["mime"])
            self.assertTrue(all(r.headers["authorization"] == "Bearer offline-key" for r in calls))

    async def test_hosted_authenticated_protocol_resource_reads_refresh_credential_preserve_mime_and_reject_foreign_session(self):
        import somanylemons_mcp.remote as remote
        import somanylemons_mcp.server as hosted

        @asynccontextmanager
        async def lifespan(app):
            incoming, outgoing = asyncio.Queue(), asyncio.Queue()
            async def send(message): await outgoing.put(message)
            runner = asyncio.create_task(app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, incoming.get, send))
            await incoming.put({"type": "lifespan.startup"})
            self.assertEqual((await asyncio.wait_for(outgoing.get(), 5))["type"], "lifespan.startup.complete")
            try:
                yield
            finally:
                await incoming.put({"type": "lifespan.shutdown"})
                self.assertEqual((await asyncio.wait_for(outgoing.get(), 5))["type"], "lifespan.shutdown.complete")
                await runner

        task = task_fixture()
        raw = attachment_bytes()
        real_client = httpx.AsyncClient
        forwarded = []
        def backend(request):
            if request.url.path == "/oauth/mcp/introspect":
                token = request.headers["authorization"].split()[1]
                owner = "foreign" if token == "synthetic_foreign_token" else "owner"
                return httpx.Response(200, json={"active": True, "resource": "https://mcp.somanylemons.com/mcp",
                    "scope": "tasks:read", "session_binding": "oauth:synthetic-feedback-" + owner})
            forwarded.append(request)
            self.assertEqual(request.method, "GET")
            if request.url.path == "/api/v1/agent-tasks/52":
                self.assertEqual(dict(request.url.params), {"view": "answer"})
                return httpx.Response(200, json={"data": task})
            self.assertEqual(request.url.path, "/api/v1/agent-tasks/52/feedback-snapshots/251/attachments")
            descriptor = next(d for d in task["feedback_review_snapshot"]["attachments"] if d["name"] == request.url.params["name"])
            self.assertEqual(request.url.params["sha256"], descriptor["sha256"])
            return httpx.Response(200, content=raw[descriptor["name"]], headers={"Content-Type": descriptor["mime"]})
        def backend_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(backend)
            return real_client(*args, **kwargs)
        def decoded(response):
            return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))

        app = remote._create_app()
        with patch.object(hosted, "REMOTE_MODE", True), patch.object(remote.httpx, "AsyncClient", backend_client):
            async with lifespan(app):
                async with real_client(transport=httpx.ASGITransport(app=app), base_url="https://mcp.somanylemons.com") as client:
                    init = await client.post("/mcp", headers={"authorization": "Bearer synthetic_initial_token"}, json={
                        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "synthetic-feedback-test", "version": "1"}}})
                    self.assertEqual(init.status_code, 200, init.text)
                    headers = {"authorization": "Bearer synthetic_refreshed_token", "mcp-session-id": init.headers["mcp-session-id"], "mcp-protocol-version": "2025-03-26"}
                    await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                    for index, descriptor in enumerate(task["feedback_review_snapshot"]["attachments"], 2):
                        response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": index, "method": "resources/read", "params": {"uri": resource_uri(task, descriptor)}})
                        self.assertEqual(response.status_code, 200, response.text)
                        packet = decoded(response)
                        self.assertNotIn("error", packet)
                        content = packet["result"]["contents"][0]
                        self.assertEqual(content["mimeType"], descriptor["mime"])
                        self.assertEqual(base64.b64decode(content["blob"]), raw[descriptor["name"]])
                    before = len(forwarded)
                    denied = await client.post("/mcp", headers={**headers, "authorization": "Bearer synthetic_foreign_token"}, json={"jsonrpc": "2.0", "id": 9, "method": "resources/read", "params": {"uri": resource_uri(task, task["feedback_review_snapshot"]["attachments"][0])}})
                    self.assertEqual(denied.status_code, 403)
                    self.assertEqual(len(forwarded), before)
        self.assertEqual(len(forwarded), 6)
        self.assertTrue(all(r.headers["x-api-key"] == "synthetic_refreshed_token" for r in forwarded))
        self.assertEqual(hosted._session_api_key.get(), "")


if __name__ == "__main__":
    unittest.main()

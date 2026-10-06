"""Synthetic wire-policy/continuation regression; no production data or network."""

from copy import deepcopy
import json
import re
import unittest

import httpx

from tests.test_feedback_review import task_fixture
from tests.fixtures.feedback_review import attachment_bytes
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig, TaskApiError
from somanylemons_mcp.task_tools.feedback_review import (
    CONTINUATION_BINDING, feedback_projection, read_feedback_attachment_resource,
    resource_uri, validate_feedback_snapshot,
)
from somanylemons_mcp.task_tools.server import compact_research_answer, create_server


def backend_no_draft_wire(value):
    """Canonical backend strip_prospect_drafts policy, independent of MCP parsing.

    Header string values and immutable descriptors intentionally remain intact.
    The separate backend-source integration check executes the actual policy and
    build_snapshot, and compares this fixture projection to those source bytes.
    """
    def draft(name):
        separated = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name or ""))
        key = re.sub(r"[^a-z0-9]+", " ", separated.casefold()).strip()
        return bool(
            re.search(r"\b(?:draft|drafts|sequence|sequences)\b", key)
            or key.startswith("intro copy") or key in {"prospect intro", "intro overrides"}
            or (key.startswith("intro ") and re.search(r"\b(?:email|subject|body|source)\b", key))
            or key in {"prospect email body", "prospect email subject", "prospect email copy",
                       "email body", "email subject", "email copy"}
        )
    if isinstance(value, dict):
        return {key: backend_no_draft_wire(item) for key, item in value.items() if not draft(key)}
    if isinstance(value, list):
        return [backend_no_draft_wire(item) for item in value]
    return deepcopy(value)


def continuation_task(goal_id=24, *, facts_only=True):
    task = task_fixture(goal_id)
    source = task["feedback_review_snapshot"]["source"]
    source.update(
        current_original_contract_revision=3,
        validation_original_contract_revision=2,
        validation_revision_basis="reviewed_immutable_pre_continuation_snapshot",
        continuation_provenance=deepcopy(CONTINUATION_BINDING),
    )
    if goal_id == 24:
        task.update(state="running", version=117, contract_revision=3,
                    current_answer_goal_id=24, allowed_actions=["cancel", "pause"], action_is_scheduled=True)
        task["contract"]["task_manager"]["revision"] = 3
        task["progress"] = {"completed": 4, "total": 111}
        task["research_answer"]["counts"]["qualified_contacts"] = 2
    return backend_no_draft_wire(task) if facts_only else task


class FeedbackPresentationTests(unittest.TestCase):
    def test_backend_recursive_policy_shape_is_readable_without_reconstructing_copy(self):
        authored = task_fixture()
        task = backend_no_draft_wire(authored)
        original = deepcopy(task)
        snapshot = validate_feedback_snapshot(task)
        self.assertNotIn("draft_sequences", snapshot)
        self.assertNotIn("sequence_step_count", snapshot["counts"])
        self.assertEqual(snapshot["counts"], {"selected_review_contact_count": 39, "recorded_business_email_count": 35})
        self.assertEqual(snapshot["attachments"], authored["feedback_review_snapshot"]["attachments"])
        self.assertEqual(task, original)
        self.assertIn("Draft email for you to send", authored["feedback_review_snapshot"]["selected_review_contacts"][0]["fields"])

    def test_facts_projection_omits_draft_columns_and_step_totals_without_claiming_new_file(self):
        task = backend_no_draft_wire(task_fixture())
        snapshot = feedback_projection(task)
        self.assertNotIn("draft_sequences", snapshot)
        self.assertNotIn("csv_headers", snapshot)
        self.assertNotIn("draft_sequence_steps_total", snapshot["pagination"])
        self.assertNotIn("Draft email for you to send", snapshot["selected_review_contacts"][0]["fields"])
        self.assertIn("original sent bytes", snapshot["row_projection_basis"])
        self.assertEqual(len(snapshot["attachments"]), 3)
        self.assertFalse(snapshot["retrieval_effects"]["customer_email_sent"])

    def test_full_39_contact_facts_pagination_preserves_every_email_source_and_uncertainty(self):
        task = backend_no_draft_wire(task_fixture())
        returned = []
        for page in range(1, 9):
            returned.extend(feedback_projection(task, contact_page=page)["selected_review_contacts"])
        self.assertEqual(returned, task["feedback_review_snapshot"]["selected_review_contacts"])
        self.assertEqual(sum(bool(row["fields"]["Business email"]) for row in returned), 35)
        self.assertEqual(returned[0]["fields"]["Enriched on (UTC)"], "")

    def test_partial_or_mixed_policy_shapes_fail_closed(self):
        for mutate in (
            lambda s: s.pop("draft_sequences"),
            lambda s: s["counts"].pop("sequence_step_count"),
            lambda s: s["csv_headers"].pop("draft_sequences"),
            lambda s: s["csv_attachment_roles"].pop("draft_sequences"),
        ):
            task = task_fixture(); mutate(task["feedback_review_snapshot"])
            with self.subTest(mutate=mutate), self.assertRaises(TaskApiError):
                validate_feedback_snapshot(task)

    def test_projected_copy_reintroduction_and_extra_schema_keys_fail_closed(self):
        for mutate in (
            lambda s: s["counts"].update(sequence_step_count=234),
            lambda s: s["selected_review_contacts"][0]["fields"].update({"Draft email for you to send": "copy"}),
            lambda s: s["selected_review_contacts"][0]["fields"].update({"introEmailDraft": "copy"}),
            lambda s: s["selected_review_contacts"][0]["fields"].update({"Email body": "copy"}),
            lambda s: s["csv_headers"].update(draft_sequences=[]),
            lambda s: s["csv_attachment_roles"].update(draft_sequences="fixture-feedback-draft-sequences.csv"),
            lambda s: s.update(extra_unreviewed_field=True),
        ):
            task = backend_no_draft_wire(task_fixture()); mutate(task["feedback_review_snapshot"])
            with self.subTest(mutate=mutate), self.assertRaises(TaskApiError):
                validate_feedback_snapshot(task)

    def test_exact_continuation_keeps_current_native_controls_counts_and_historical_hold_separate(self):
        for facts_only in (False, True):
            task = continuation_task(facts_only=facts_only)
            answer = compact_research_answer(task)
            self.assertEqual((answer["id"], answer["version"], answer["state"], answer["current_answer_goal_id"]), (24, 117, "running", 24))
            self.assertEqual(answer["allowed_actions"], ["cancel", "pause"])
            self.assertEqual(answer["progress"], {"completed": 4, "total": 111})
            self.assertEqual(answer["research_answer"]["counts"]["qualified_contacts"], 2)
            snapshot = answer["feedback_review_snapshot"]
            self.assertEqual(snapshot["current_answer_goal_id"], 52)
            self.assertTrue(snapshot["review_scope"]["remaining_work_held"])
            self.assertFalse(snapshot["review_scope"]["original_request_fulfilled"])
            self.assertEqual(snapshot["source"]["publication_validation"]["original_contract_revision"], 2)
            self.assertIsNone(snapshot["source"]["delivered_original_contract_revision"])
            self.assertTrue(all(row["validation_contract_revision"] == 1 for row in snapshot["attachments"]))

    def test_continuation_pilot_remains_paused_historical_read(self):
        task = continuation_task(52)
        self.assertIsNotNone(validate_feedback_snapshot(task))
        task.update(state="running", action_is_scheduled=True)
        with self.assertRaises(TaskApiError): validate_feedback_snapshot(task)

    def test_unproven_or_arbitrary_continuation_relabeling_fails_closed(self):
        for mutate in (
            lambda t: t["feedback_review_snapshot"]["source"]["continuation_provenance"].update(approval_key="foreign"),
            lambda t: t["feedback_review_snapshot"]["source"]["continuation_provenance"].update(manifest_hash="0" * 64),
            lambda t: t["feedback_review_snapshot"]["source"]["continuation_provenance"].update(current_native_revision=4),
            lambda t: t["feedback_review_snapshot"]["source"]["continuation_provenance"].update(validation_revision=3),
            lambda t: t["feedback_review_snapshot"]["source"].update(validation_original_contract_revision=3),
            lambda t: t["feedback_review_snapshot"]["source"]["publication_validation"].update(original_contract_revision=3),
            lambda t: t["feedback_review_snapshot"]["source"].update(validation_revision_basis="reviewed_post_delivery_current_revisions"),
            lambda t: t["feedback_review_snapshot"]["source"].pop("continuation_provenance"),
            lambda t: t["feedback_review_snapshot"]["source"]["continuation_provenance"].update(unreviewed=True),
            lambda t: t.update(current_answer_goal_id=52),
        ):
            task = continuation_task(); mutate(task)
            with self.subTest(mutate=mutate), self.assertRaises(TaskApiError):
                validate_feedback_snapshot(task)

    def test_projected_account_scope_attachments_hashes_and_saved_publication_remain_strict(self):
        for mutate in (
            lambda t: t["customer"].update(id=80),
            lambda t: t["feedback_review_snapshot"]["answer_lineage_scope"].update(config_id=9),
            lambda t: t["feedback_review_snapshot"]["source"].update(delivery_job_id=257),
            lambda t: t["feedback_review_snapshot"]["attachments"][0].update(sha256="bad"),
            lambda t: t["feedback_review_snapshot"]["attachments"][0].update(delivered_contract_revision=1),
            lambda t: t["feedback_review_snapshot"]["attachments"][0]["attachment_retrieval"].update(api_url="https://foreign.invalid/file"),
            lambda t: t["feedback_review_snapshot"]["csv_attachment_roles"].update(workbook="foreign.xlsx"),
            lambda t: t["feedback_review_snapshot"]["counts"].update(recorded_business_email_count=39),
        ):
            task = continuation_task(); mutate(task)
            with self.subTest(mutate=mutate), self.assertRaises(TaskApiError): validate_feedback_snapshot(task)

    def test_bool_string_and_zero_goal_scope_and_revision_ids_are_not_coerced(self):
        for invalid in (True, False, "24", 0):
            for mutate in (
                lambda t: t.update(id=invalid),
                lambda t: t.update(contract_revision=invalid),
                lambda t: t["feedback_review_snapshot"].update(requested_goal_id=invalid),
                lambda t: t["feedback_review_snapshot"]["source"].update(config_id=invalid),
            ):
                task = continuation_task(); mutate(task)
                with self.subTest(invalid=invalid, mutate=mutate), self.assertRaises(TaskApiError): validate_feedback_snapshot(task)


class FeedbackPresentationProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_fastmcp_tools_return_current_original_once_without_following_old_pilot(self):
        task, calls = continuation_task(), []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v1/agent-tasks/24")
            self.assertEqual(dict(request.url.params), {"view": "answer"})
            return httpx.Response(200, json={"data": task})
        client = TaskApiClient(TaskApiConfig("https://example.invalid", "synthetic-key"), transport=httpx.MockTransport(handler))
        server = create_server(client)
        for tool in ("get_task", "get_research_answer", "wait_for_task"):
            result = await server.call_tool(tool, {"goal_id": 24, **({"timeout_seconds": 0} if tool == "wait_for_task" else {})})
            answer = result[1] if isinstance(result, tuple) else json.loads(result[0].text)
            if tool == "wait_for_task":
                answer = answer["task"]
            self.assertEqual(answer["id"], 24)
            self.assertFalse(answer["answer_resolution"]["followed_current_answer"])
            self.assertEqual(answer["allowed_actions"], ["cancel", "pause"])
        self.assertTrue(calls)

    async def test_policy_projected_historical_resource_returns_only_exact_saved_bytes(self):
        task = backend_no_draft_wire(task_fixture())
        descriptor = task["feedback_review_snapshot"]["attachments"][2]
        raw = attachment_bytes()[descriptor["name"]]
        for corrupt in (False, True):
            calls = []
            def handler(request):
                calls.append(request)
                self.assertEqual(request.method, "GET")
                if request.url.path.endswith("/attachments"):
                    return httpx.Response(200, content=raw + b"changed" if corrupt else raw)
                return httpx.Response(200, json={"data": task})
            client = TaskApiClient(TaskApiConfig("https://example.invalid", "synthetic-key"), transport=httpx.MockTransport(handler))
            if corrupt:
                with self.assertRaises(TaskApiError): await read_feedback_attachment_resource(client, resource_uri(task, descriptor))
            else:
                self.assertEqual(await read_feedback_attachment_resource(client, resource_uri(task, descriptor)), (raw, "text/csv"))
            self.assertEqual(len(calls), 2)

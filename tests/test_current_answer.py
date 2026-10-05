import copy
import json
import unittest
from unittest.mock import patch

import httpx

from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig, TaskApiError
from somanylemons_mcp.task_tools.current_answer import history_metadata, recorded_delivery_history, resolve_current_answer
from somanylemons_mcp.task_tools.server import create_server


class CurrentAnswerTests(unittest.IsolatedAsyncioTestCase):
    def fixtures(self):
        gates = {
            "combination": "all_required", "all_required_met": False,
            "requirements": {key: {"satisfied": False} for key in
                             ("enrichment", "identity_quality", "source_coverage", "session_titles", "required_review")},
        }
        clocks = {
            "email_observed_at": {"groups": [{"date": "2026-10-05", "people": 158}, {"date": "2026-09-16", "people": 8}], "recorded_people": 166, "not_recorded_or_unproven_people": 0},
            "email_verified_at": {"groups": [], "recorded_people": 0, "not_recorded_or_unproven_people": 166},
            "enriched_on": {"groups": [{"date": "2026-10-05", "people": 19}], "recorded_people": 19, "not_recorded_or_unproven_people": 147},
        }
        current = {
            "id": 51, "current_answer_goal_id": 51, "customer": {"id": 60},
            "version": 7, "allowed_actions": ["cancel"], "state": "needs_attention",
            "progress": {"completed": 2, "total": 3}, "action_is_scheduled": False,
            "conference_answer": {
                "event": {"name": "Example conference", "year": 2026},
                "enrichment_summary": {
                    "unique_people": 191, "identity_count_basis": "Saved name/company roster keys, including aliases.",
                    "recorded_email_people": 166, "distinct_recorded_email_addresses": 164,
                    "missing_email_people": 25, "saved_verified_email_people": 156,
                    "recorded_email_date_summary": clocks,
                },
                "coverage": {"session_appearance_count": 237},
                "final_delivery_requirements": gates, "saved_verification_is_fresh": False,
                "rows": [{"name": f"Roster {i}", "email": f"record{i}@example.com", "email_verified_at": "", "email_observed_at": "2026-10-05", "quality_note": "Needs review; no recorded review date."} for i in range(237)],
                "sources": [{"url": "https://publisher.example/captured", "observed_at": "2026-10-05"}],
            },
            "artifacts": [
                {"id": 16, "origin_goal_id": 51, "row_count": 191, "hash": "a" * 64, "schema": {"customer_presentation": True, "canonical_artifact_id": 15, "recorded_email_people": 166}},
                {"id": 15, "origin_goal_id": 51, "row_count": 237, "hash": "b" * 64, "schema": {"version": "conference.speakers.v1"}},
            ],
        }
        original = copy.deepcopy(current)
        original.update(id=34, version=3, allowed_actions=["retry"], progress={"completed": 43, "total": 44})
        original["conference_answer"]["enrichment_summary"].update(recorded_email_people=32, missing_email_people=159)
        original["conference_answer"]["rows"] = original["conference_answer"]["rows"][:228]
        original["artifacts"] = [{"id": 99, "origin_goal_id": 999, "row_count": 999, "hash": "c" * 64}]
        return original, current

    def server(self, tasks, histories=None, denied=None):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.method, "GET")
            goal = int(request.url.path.rsplit("/", 1)[-1])
            if goal == denied:
                return httpx.Response(403, json={"detail": "Denied current answer"})
            data = tasks[goal] if request.url.params.get("view") == "answer" else histories[goal]
            return httpx.Response(200, json={"data": data})
        api = TaskApiClient(TaskApiConfig("https://example.com", "mock-key"), transport=httpx.MockTransport(handler))
        return create_server(api), api, calls

    async def read(self, server, method, arguments):
        content = await server.call_tool(method, arguments)
        if isinstance(content, tuple):
            content = content[0]
        answer = json.loads(content[0].text)
        return answer["task"] if method == "wait_for_task" else answer

    async def test_all_three_default_reads_resolve_original34_to51_and_preserve_every_fact(self):
        original, current = self.fixtures()
        before = copy.deepcopy((original, current))
        for method in ("get_task", "get_research_answer", "wait_for_task"):
            with self.subTest(method=method):
                server, _, calls = self.server({34: original, 51: current})
                args = {"goal_id": 34}
                if method == "get_research_answer": args.update(details=True, contact_page=2)
                if method == "wait_for_task": args["timeout_seconds"] = 0
                answer = await self.read(server, method, args)
                self.assertEqual([c.url.path.rsplit("/", 1)[-1] for c in calls], ["34", "51"])
                self.assertTrue(all(dict(c.url.params) == {"view": "answer"} for c in calls))
                self.assertEqual((answer["id"], answer["version"], answer["allowed_actions"]), (51, 7, ["cancel"]))
                self.assertEqual(answer["requested_goal_controls"], {"goal_id": 34, "version": 3, "allowed_actions": ["retry"]})
                self.assertEqual(answer["requested_goal_progress"], {"completed": 43, "total": 44, "unit": "worker_steps", "scope_goal_id": 34})
                self.assertEqual(answer["progress"], current["progress"])
                self.assertEqual(answer["conference_answer"]["enrichment_summary"], current["conference_answer"]["enrichment_summary"])
                self.assertEqual(answer["conference_answer"]["final_delivery_requirements"], current["conference_answer"]["final_delivery_requirements"])
                self.assertEqual(answer["artifacts"], current["artifacts"])
                review_file = answer["conference_answer"]["saved_review_workbook"]
                self.assertEqual(review_file["artifact_retrieval"], {"tool": "get_task_artifact", "goal_id": 51, "artifact_id": 16})
                self.assertFalse(review_file["reading_authorizes_send"])
                self.assertIn("do-not-send instruction does not prohibit", review_file["basis"])
                self.assertFalse(answer["action_is_scheduled"])
                self.assertTrue(answer["answer_is_current_recorded_goal"])
                if method == "get_research_answer":
                    self.assertEqual(answer["pagination"]["rows_total"], 237)
                    self.assertEqual(answer["contacts"][0]["name"], "Roster 5")
                    self.assertEqual(answer["sources"], current["conference_answer"]["sources"])
                    self.assertEqual(answer["contacts"][0]["email_verified_at"], "")
                    self.assertNotIn("reviewed_at", answer["contacts"][0])
        self.assertEqual((original, current), before)

    async def test_pending_current_child_without_artifact_does_not_fall_back(self):
        original, current = self.fixtures()
        current.update(state="running", artifacts=[])
        current["conference_answer"].update(rows=[], sources=[], enrichment_summary={"unfinished_people": 13}, research_not_started=True)
        server, _, _ = self.server({34: original, 51: current})
        answer = await self.read(server, "get_task", {"goal_id": 34})
        self.assertEqual(answer["id"], 51)
        self.assertEqual(answer["artifacts"], [])
        self.assertEqual(answer["conference_answer"]["enrichment_summary"], {"unfinished_people": 13})
        self.assertFalse(answer["conference_answer"]["saved_review_workbook"]["metadata_recorded"])
        self.assertFalse(answer["conference_answer"]["final_delivery_requirements"]["all_required_met"])

    async def test_all_three_historical_optouts_preserve_exact_original_controls_and_counts(self):
        original, current = self.fixtures()
        for method in ("get_task", "get_research_answer", "wait_for_task"):
            with self.subTest(method=method):
                server, _, calls = self.server({34: original, 51: current})
                args = {"goal_id": 34, "historical_snapshot": True}
                if method == "wait_for_task": args["timeout_seconds"] = 0
                answer = await self.read(server, method, args)
                self.assertEqual(len(calls), 1)
                self.assertEqual((answer["id"], answer["version"], answer["allowed_actions"]), (34, 3, ["retry"]))
                self.assertEqual(answer["conference_answer"]["enrichment_summary"]["recorded_email_people"], 32)
                self.assertFalse(answer["answer_is_current_recorded_goal"])
                self.assertTrue(answer["answer_resolution"]["historical_snapshot"])
                self.assertNotIn("requested_goal_controls", answer)

    async def test_history_labels_actual_canonical_and_original_metadata_without_receipts(self):
        original, current = self.fixtures()
        histories = {pk: {"id": pk, "version": pk, "events": [{"id": pk, "event": "saved", "created_at": "2026-10-05", "note": "private receipt body"}]} for pk in (34, 51)}
        server, _, calls = self.server({34: original, 51: current}, histories)
        answer = await self.read(server, "get_task", {"goal_id": 34, "include_history": True})
        self.assertEqual(len(calls), 4)
        self.assertTrue(answer["operator_history_available"])
        self.assertEqual(answer["operator_history_goal_id"], 51)
        self.assertEqual(answer["operator_history"][0]["id"], 51)
        self.assertEqual(answer["requested_goal_history"]["goal_id"], 34)
        self.assertNotIn("private receipt body", json.dumps(answer))
        server, _, calls = self.server({34: original, 51: current}, histories)
        historical = await self.read(server, "get_task", {"goal_id": 34, "historical_snapshot": True, "include_history": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(historical["operator_history_goal_id"], 34)
        self.assertEqual(historical["version"], 3)

    async def test_grouped_history_is_not_mislabeled_canonical_or_negative_delivery_proof(self):
        original, current = self.fixtures()
        grouped = {"id": 34, "version": 10, "events": [{"id": 1, "event": "customer_snapshot_sent", "created_at": "2026-10-05", "note": "private receipt"}],
                   "conversation": [{"id": "job:244:reply", "role": "team", "status": "sent", "subject": "Saved snapshot", "created_at": "2026-10-05", "body": "private receipt"}], "conversation_total": 1, "conversation_truncated": False}
        server, _, calls = self.server({34: original, 51: current}, {51: grouped})
        answer = await self.read(server, "get_task", {"goal_id": 34, "include_history": True})
        self.assertEqual(len(calls), 3)
        self.assertFalse(answer["operator_history_available"])
        self.assertIsNone(answer["operator_history"])
        self.assertEqual(answer["requested_goal_history"]["goal_id"], 34)
        self.assertIn("Event metadata", answer["operator_history_scope"])
        self.assertIn("recorded_delivery_history", answer["operator_history_scope"])
        delivery = answer["recorded_delivery_history"]
        self.assertEqual(delivery["grouped_outcome_goal_id"], 34)
        self.assertEqual(delivery["sent_turns"][0]["id"], "job:244:reply")
        self.assertTrue(delivery["source_conversation_complete"])
        self.assertIn("does not establish inbox delivery", delivery["basis"])
        self.assertNotIn("private receipt", json.dumps(answer))

    async def test_denied_latest_fails_without_historical_count_fallback(self):
        original, current = self.fixtures()
        _, api, calls = self.server({34: original, 51: current}, denied=51)
        with self.assertRaisesRegex(TaskApiError, "HTTP 403"):
            await resolve_current_answer(api, 34)
        self.assertEqual(len(calls), 2)

    async def test_cross_customer_or_event_and_missing_follow_identity_fail_closed(self):
        original, current = self.fixtures()
        mutations = [
            lambda x: x.update(customer={"id": 61}),
            lambda x: x["conference_answer"]["event"].update(name="Other conference"),
            lambda x: x["conference_answer"]["event"].update(year=2027),
            lambda x: x.pop("customer"),
            lambda x: x.update(id=52),
            lambda x: x.update(current_answer_goal_id=None),
        ]
        for mutation in mutations:
            changed = copy.deepcopy(current); mutation(changed)
            _, api, _ = self.server({34: original, 51: changed})
            with self.subTest(mutation=mutation), self.assertRaises(TaskApiError):
                await resolve_current_answer(api, 34)

    async def test_malformed_pointer_or_origin_identity_fails_before_follow(self):
        original, current = self.fixtures()
        for value in (True, 0, -1, "51", [], {}):
            changed = copy.deepcopy(original); changed["current_answer_goal_id"] = value
            _, api, calls = self.server({34: changed, 51: current})
            with self.subTest(value=value), self.assertRaises(TaskApiError):
                await resolve_current_answer(api, 34)
            self.assertEqual(len(calls), 1)
        changed = copy.deepcopy(original); changed.pop("customer")
        _, api, calls = self.server({34: changed, 51: current})
        with self.assertRaises(TaskApiError): await resolve_current_answer(api, 34)
        self.assertEqual(len(calls), 1)

    async def test_cycle_and_pointer_hops_are_bounded(self):
        original, current = self.fixtures()
        current["current_answer_goal_id"] = 34
        _, api, calls = self.server({34: original, 51: current})
        with self.assertRaisesRegex(TaskApiError, "cycle"):
            await resolve_current_answer(api, 34)
        self.assertEqual(len(calls), 2)
        tasks = {}
        for pk in (34, 51, 52, 53):
            task = copy.deepcopy(current); task.update(id=pk, current_answer_goal_id=51 if pk == 34 else pk + 1); tasks[pk] = task
        _, api, calls = self.server(tasks)
        with self.assertRaisesRegex(TaskApiError, "bounded pointer limit"):
            await resolve_current_answer(api, 34)
        self.assertEqual(len(calls), 4)

    async def test_all49_tools_remain_registered_with_explicit_historical_option(self):
        from somanylemons_mcp import server as hosted
        from somanylemons_mcp.task_bridge import task_schemas
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertEqual(len(tools), 20)
        for method in ("get_task", "get_research_answer", "wait_for_task"):
            self.assertFalse(tools[method].inputSchema["properties"]["historical_snapshot"]["default"])
        with patch.object(hosted, "_request_identity", return_value={"research_only": False}):
            self.assertEqual(len(await hosted.list_tools()), 49)
        self.assertNotIn("get_task_conversation", tools)
        for text in ("that exact saved file", "Missing verification/enrichment clocks stay unknown", "never infer a person's review or verification date", "historical_snapshot=true", "does not prohibit already-requested"):
            self.assertIn(text, hosted.server.instructions)

    def test_malformed_history_cannot_copy_payloads_or_receipts(self):
        self.assertIsNone(history_metadata({"id": True, "events": []}, 1))
        value = history_metadata({"id": 51, "events": [{"id": 1, "event": {"body": "private"}, "created_at": ["private"], "payload": "private"}]}, 51)
        self.assertEqual(value["events"], [{"id": 1}])
        self.assertNotIn("private", json.dumps(value))

    def test_recorded_sent_turns_are_bounded_exact_metadata_without_body_or_receipt_claims(self):
        conversation = [{"id": f"job:{i}:reply", "role": "team", "status": "sent", "subject": "Saved subject", "created_at": "2026-10-05", "body": "privatebody", "recipient": "private@example.com", "provider_receipt": "private"} for i in range(1, 13)]
        conversation += [{"id": "job:13:reply", "role": "agent", "status": "sent", "subject": "Confirmed agent reply"},
                         {"id": "job:99:reply", "role": "team", "status": "draft"}, {"id": "job:100:reply", "role": "customer", "status": "sent"},
                         {"id": "job:101:reply", "role": "agent", "status": "received"}, {"id": "task:102:progress", "role": "agent", "status": "sent"},
                         {"id": "job:103:reply", "role": ["agent"], "status": "sent"},
                         {"id": "event:private", "role": "team", "status": "sent"}]
        value = recorded_delivery_history({"id": 34, "conversation": conversation, "conversation_total": len(conversation), "conversation_truncated": False})
        self.assertEqual((value["sent_turns_total"], value["sent_turns_returned"], value["sent_turns_omitted"]), (13, 10, 3))
        self.assertEqual(value["sent_turns"][0]["id"], "job:4:reply")
        self.assertEqual(value["sent_turns"][-1]["role"], "agent")
        self.assertIn("Zero here does not establish", value["sent_turns_total_basis"])
        self.assertNotIn("private", json.dumps(value))
        self.assertEqual(set(value["sent_turns"][0]), {"id", "role", "status", "subject", "created_at"})
        missing = recorded_delivery_history({"id": 34})
        self.assertFalse(missing["available"])
        self.assertIsNone(missing["sent_turns"])
        self.assertIn("unknown", missing["basis"])


if __name__ == "__main__":
    unittest.main()

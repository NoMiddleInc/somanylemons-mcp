import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from somanylemons_mcp.task_tools import server as server_module
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig
from somanylemons_mcp.task_tools.live_progress import live_update
from somanylemons_mcp.task_tools.server import compact_research_answer, create_server, public_blocker
from tests.test_research_progress import VirtualClock


class LiveProgressTests(unittest.IsolatedAsyncioTestCase):
    def test_saved_contact_can_be_displayed_without_ending_shortfall_recovery(self):
        answer = {'id': 244, 'state': 'waiting_provider', 'fulfillment': 'unknown',
                  'business_answer': {'counts': {'contacts': 1, 'requested': 5}},
                  'contacts': [{'name': 'Saved Person', 'company': 'Observed Company',
                                'title': 'CFO', 'email': 'saved@example.com',
                                'linkedin': 'https://linkedin.com/in/saved-person',
                                'email_status': 'guessed'}]}
        update = live_update(answer, 244)
        self.assertEqual(update['findings'][0]['email'], 'saved@example.com')
        self.assertEqual(update['findings'][0]['linkedin'], answer['contacts'][0]['linkedin'])
        self.assertEqual(update['findings'][0]['email_status'], 'guessed')
        self.assertTrue(update['continue_watching'])
        self.assertFalse(update['final_response_ready'])
        brief = server_module.brief_answer(answer)
        self.assertEqual(brief['contacts'], answer['contacts'])
        self.assertIn('qualified saved contacts immediately', brief['response_policy'])
        self.assertIn('automatically until final_response_ready', brief['response_policy'])

    def test_partial_current_goal_does_not_authorize_a_completed_contact_table(self):
        answer = {'id': 230, 'state': 'waiting_provider', 'fulfillment': 'unknown',
                  'business_answer': {'counts': {'contacts': 1, 'requested': 5}}}
        update = live_update(answer, 230)
        self.assertTrue(update['continue_watching'])
        self.assertFalse(update['final_response_ready'])
        self.assertIn('Never combine old partial', update['contact_table_policy'])
        self.assertIn('until final_response_ready', update['completion_instruction'])

    def test_saved_contacts_visible_when_export_fails_or_research_continues(self):
        rows = [{"name": "Saved Person", "email": "saved@example.com", "email_status": "guessed"}]
        for state in ("running", "needs_attention", "completed"):
            with self.subTest(state=state):
                answer = server_module.brief_answer({
                    "state": state, "business_answer": {"coverage": {"saved": 1}},
                    "contacts": rows, "pagination": {"contacts_total": 30},
                    "blocker": "business_artifact_failed",
                })
                self.assertEqual(answer["contacts"], rows)
                self.assertEqual(answer["pagination"]["contacts_total"], 30)
                self.assertNotIn("Show the available saved contacts now", answer["response_policy"])
                self.assertFalse(answer["final_response_ready"])
                self.assertIn("never label guessed emails verified", answer["response_policy"])
                self.assertIn("successive contact_page", answer["response_policy"])

    def test_legacy_polling_response_requires_continuation_and_exposes_result_reader(self):
        answer = server_module.brief_answer(compact_research_answer(self.task()))
        self.assertTrue(answer["continue_watching"])
        self.assertIn("Do not ask the customer to check again", answer["response_policy"])
        self.assertFalse(answer["final_response_ready"])
        self.assertIn("qualified saved contacts immediately", answer["response_policy"])
        self.assertIn("keep watching this current goal automatically", answer["response_policy"])
        self.assertIn("get_research_answer(details=true)", answer["response_policy"])
        completed = server_module.brief_answer(compact_research_answer(self.task(state="completed", running=False)))
        self.assertNotIn("continue_watching", completed)
        held = server_module.brief_answer(compact_research_answer(self.task(state="needs_attention", running=False)))
        self.assertNotIn("continue_watching", held)

    def test_contact_answer_stops_chat_watch_without_claiming_delivery_or_suggesting_campaign(self):
        task = self.task()
        task["business_answer"].update(answer_text="Email: saved@example.com\nLinkedIn: Unavailable", response_policy="Reply with answer_text only")
        update = live_update(compact_research_answer(task), task["id"])
        self.assertEqual(update["summary"], task["business_answer"]["answer_text"])
        self.assertEqual(update["answer_text"], update["summary"])
        self.assertEqual(update["suggestions"], [])
        self.assertFalse(update["continue_watching"])
        self.assertEqual(update["monitor_status"], "answer_available")
        self.assertEqual(update["state"], "running")

    def task(self, **changes):
        task = {
            "id": 74, "state": "running", "running": True,
            "current_step": "Enrich contacts", "version": 1,
            "contract": {"workflow": "business_research", "spec": {"count": 50}},
            "business_answer": {
                "coverage": {"requested_count": 50, "qualified_rows": 5},
                "counts": {"contacts": 8, "recorded_emails": 3},
                "rows": [{"name": "Example CFO", "company": "Example Co", "title": "CFO",
                          "evidence_refs": [{"source_url": "https://example.com/team"}]}],
            },
            "progress": {"completed": 49, "total": 50},
        }
        task.update(changes)
        return task

    def update(self, task):
        return live_update(compact_research_answer(task), 74)

    async def watch(self, tasks, **arguments):
        clock = VirtualClock()
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/api/v1/agent-tasks/74")
            return httpx.Response(200, json={"data": copy.deepcopy(tasks[min(len(calls)-1, len(tasks)-1)])})
        api = TaskApiClient(TaskApiConfig("https://example.com", "mock-key"),
                            transport=httpx.MockTransport(handler))
        with patch.object(server_module, "time", SimpleNamespace(monotonic=clock.monotonic)), \
             patch.object(server_module, "asyncio", SimpleNamespace(sleep=clock.sleep)):
            content = await create_server(api).call_tool("watch_research", {"goal_id": 74, **arguments})
        if isinstance(content, tuple):
            content = content[0]
        return json.loads(content[0].text), clock, calls

    async def test_first_response_is_immediate_and_uses_contacts_not_steps(self):
        result, clock, calls = await self.watch([self.task()])
        self.assertEqual(clock.elapsed, 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result["changed"])
        self.assertIn("5 of 50 qualified prospects found", result["summary"])
        self.assertEqual(result["recorded_emails"], 3)
        self.assertEqual(result["findings"][0]["evidence_refs"][0]["source_url"], "https://example.com/team")

    async def test_cursor_detects_changes_between_calls(self):
        task = self.task()
        cursor = self.update(task)["cursor"]
        newer = copy.deepcopy(task)
        newer["business_answer"]["coverage"]["qualified_rows"] = 10
        result, clock, _ = await self.watch([newer], cursor=cursor)
        self.assertTrue(result["changed"])
        self.assertEqual(result["found"], 10)
        self.assertEqual(clock.elapsed, 0)

    async def test_unchanged_and_timestamp_churn_return_bounded_quiet_heartbeat(self):
        task = self.task()
        newer = self.task(version=99, updated_at="2026-10-06T20:00:00Z")
        result, clock, calls = await self.watch([newer], cursor=self.update(task)["cursor"])
        self.assertFalse(result["changed"])
        self.assertEqual(clock.elapsed, 25)
        self.assertEqual(len(calls), 5)
        self.assertTrue(result["continue_watching"])

    async def test_active_scheduling_churn_coalesces_but_preserves_observed_state(self):
        task = self.task()
        queued = self.task(state="queued", running=False, current_step="Research contacts", next_action="Wait")
        result, clock, calls = await self.watch([queued], cursor=self.update(task)["cursor"])
        self.assertEqual(clock.elapsed, 25)
        self.assertFalse(result["changed"])
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["stage"], "Research contacts")
        self.assertEqual(result["next_tool"], "watch_research")
        self.assertEqual(result["next_tool_arguments"], {
            "goal_id": 74, "cursor": result["cursor"], "timeout_seconds": 25})
        self.assertFalse(result["final_response_ready"])
        self.assertIn("does not authorize ending", result["completion_instruction"])

    async def test_review_gate_and_actual_blocker_wake_immediately(self):
        task = self.task()
        for changed in (self.task(blocker={"reason": "provider authentication rejected"}),
                        self.task(manual_review_required=True)):
            result, clock, _ = await self.watch([task, changed], cursor=self.update(task)["cursor"])
            self.assertEqual(clock.elapsed, 5)
            self.assertTrue(result["changed"])
            self.assertFalse(result["continue_watching"])
            self.assertIsNone(result["next_tool_arguments"])
        reviewed = copy.deepcopy(task)
        reviewed["business_answer"]["fulfillment_review"] = {"passed": True}
        result, clock, _ = await self.watch([task, reviewed], cursor=self.update(task)["cursor"])
        self.assertEqual(clock.elapsed, 5)
        self.assertTrue(result["changed"])
        self.assertTrue(result["continue_watching"])

    async def test_slow_read_consumes_deadline_without_extra_sleep_or_read(self):
        clock = VirtualClock()
        calls = []
        task = self.task()
        def handler(request):
            calls.append(request)
            clock.elapsed += 10
            return httpx.Response(200, json={"data": task})
        api = TaskApiClient(TaskApiConfig("https://example.com", "mock-key"),
                            transport=httpx.MockTransport(handler))
        with patch.object(server_module, "time", SimpleNamespace(monotonic=clock.monotonic)), \
             patch.object(server_module, "asyncio", SimpleNamespace(sleep=clock.sleep)):
            content = await create_server(api).call_tool("watch_research", {
                "goal_id": 74, "cursor": self.update(task)["cursor"], "timeout_seconds": 10})
        if isinstance(content, tuple):
            content = content[0]
        result = json.loads(content[0].text)
        self.assertEqual(clock.elapsed, 10)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["changed"])
        self.assertTrue(result["continue_watching"])

    async def test_zero_timeout_returns_immediately_even_with_same_cursor(self):
        task = self.task()
        result, clock, calls = await self.watch([task], cursor=self.update(task)["cursor"], timeout_seconds=0)
        self.assertEqual(clock.elapsed, 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["changed"])
        self.assertTrue(result["continue_watching"])

    async def test_email_and_evidence_updates_return_early(self):
        task = self.task()
        for field in ("email", "evidence"):
            newer = copy.deepcopy(task)
            if field == "email":
                newer["business_answer"]["counts"]["recorded_emails"] = 4
            else:
                newer["business_answer"]["rows"][0]["evidence_refs"][0]["source_url"] = "https://example.com/new"
            result, clock, _ = await self.watch([task, newer], cursor=self.update(task)["cursor"])
            self.assertTrue(result["changed"])
            self.assertEqual(clock.elapsed, 5)

    async def test_stop_states_and_campaign_suggestion(self):
        for state in ("blocked", "waiting_customer", "paused", "cancelled", "completed"):
            result, clock, _ = await self.watch([self.task(state=state, running=False, contract={"workflow": "business_research"},
                business_answer={"coverage": {"requested_count": 5, "qualified_rows": 5}, "rows": []})])
            self.assertFalse(result["continue_watching"])
            self.assertEqual(clock.elapsed, 0)
            self.assertEqual(result["campaign_action"], "suggest_only")
            self.assertFalse(any("email campaign" in text for text in result["suggestions"]))

    def test_partial_completion_does_not_offer_launch(self):
        for changes in ({}, {"fulfillment": "partial"}, {"manual_review_required": True}):
            result = self.update(self.task(state="completed", **changes))
            self.assertEqual(result["monitor_status"], "needs_attention")
            self.assertFalse(any("email campaign" in text for text in result["suggestions"]))

    async def test_completed_bounded_delivery_does_not_keep_historical_retry_blockers(self):
        business = {
            "state": "completed", "fulfillment": "fulfilled", "closure": "fulfilled",
            "coverage": {"requested_count": 1, "qualified_rows": 1,
                         "all_requested": False, "full_coverage_verified": False},
            "counts": {"contacts": 1, "recorded_emails": 1}, "rows": [],
            "review_scope_current": True, "fulfillment_review": {"passed": True},
            "delivery": {"status": "provider_accepted", "receipt": "actual-provider-receipt"},
            "blockers": [{"reason": "Previous failed attempt"}],
        }
        task = self.task(state="completed", running=False, fulfillment="fulfilled",
                         blocker={"reason": "Previous failed attempt"}, manual_review_required=True,
                         business_answer=business, contract={"workflow": "business_research", "spec": {"count": 1, "all": False}})
        result, clock, calls = await self.watch([task])
        self.assertEqual(result["monitor_status"], "finished")
        self.assertFalse(result["continue_watching"])
        self.assertIsNone(result["blocker"])
        self.assertEqual(result["blockers"], [])
        self.assertFalse(result["manual_review_required"])
        self.assertTrue(result["historical_blockers"])
        self.assertEqual(clock.elapsed, 0)
        self.assertEqual(len(calls), 1)
        for change in ({"full_request_fulfilled": False}, {"review_scope_current": False},
                       {"fulfillment_review": {"passed": False}},
                       {"coverage": {"requested_count": 1, "qualified_rows": 1,
                                     "all_requested": True, "full_coverage_verified": False}}):
            held = copy.deepcopy(task)
            held["business_answer"].update(change)
            update = self.update(held)
            self.assertEqual(update["monitor_status"], "needs_attention")
            self.assertTrue(update["blocker"])
        held = copy.deepcopy(task)
        held["business_answer"]["coverage"] = {"requested_count": 1, "qualified_rows": 1, "all_requested": True}
        self.assertEqual(self.update(held)["monitor_status"], "needs_attention")

    def test_unknown_counts_and_unqualified_counts_are_honest(self):
        task = self.task(business_answer={"rows": [{"name": "Candidate"}]})
        result = self.update(task)
        self.assertIsNone(result["found"])
        self.assertIn("not yet recorded", result["summary"])
        task["business_answer"]["counts"] = {"contacts": 5}
        self.assertEqual(self.update(task)["count_basis"], "saved contacts")

    async def test_tool_is_exposed_in_hosted_bridge_and_read_only(self):
        from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES
        self.assertIn("watch_research", TASK_TOOL_NAMES)
        tool = next(tool for tool in await create_server(TaskApiClient(
            TaskApiConfig("https://example.com", "mock-key"))).list_tools() if tool.name == "watch_research")
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertEqual(tool.inputSchema["properties"]["timeout_seconds"]["maximum"], 25)

    def test_plugin_and_wheel_skill_match(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        skill = (root / "skills/producerspark/SKILL.md").read_bytes()
        self.assertEqual(skill, (root / "src/somanylemons_mcp/skills/producerspark/SKILL.md").read_bytes())
        self.assertEqual(skill, (root / "plugins/producerspark/skills/producerspark/SKILL.md").read_bytes())
        self.assertIn(b"timeout_seconds=25", skill)
        self.assertIn(b"does not permit a terminal answer while final_response_ready=false", skill)


class HelpfulBlockerTests(unittest.TestCase):
    def test_edition_failure_explains_page_verification_without_backend_outage(self):
        result = public_blocker({"party": "operator", "reason": "managed_browser_edition_unverified"})
        self.assertIn("page was opened", result["reason"])
        self.assertIn("dates could not be verified", result["reason"])
        self.assertNotIn("managed_browser_edition_unverified", result["reason"])

    def test_unknown_operator_details_are_not_leaked(self):
        result = public_blocker({"party": "operator", "reason": "private credential details"})
        self.assertEqual(result["reason"], "Research needs an internal review before completion.")

    def test_blocked_research_offers_saved_results_without_a_campaign_or_duplicate(self):
        result = live_update({"id": 67, "state": "needs_attention", "blocker": {"reason": "Wrong edition"}}, 67)
        self.assertFalse(result["continue_watching"])
        self.assertIn("reuse this research task", " ".join(result["suggestions"]))
        self.assertNotIn("email campaign", " ".join(result["suggestions"]))


class ContinuationContractTests(unittest.TestCase):
    def test_unchanged_active_zero_results_preserve_next_call_without_customer_followup(self):
        for state in ('queued', 'running', 'waiting_provider'):
            answer = {'id': 288, 'state': state, 'fulfillment': 'unknown',
                      'business_answer': {'counts': {'contacts': 0, 'requested': 10}},
                      'updated_at': '2026-10-09T00:00:00Z'}
            first = live_update(answer, 288)
            for _ in range(40):
                current = live_update(answer, 288)
                self.assertEqual(current['cursor'], first['cursor'])
                self.assertTrue(current['continue_watching'])
                self.assertFalse(current['final_response_ready'])
                self.assertFalse(current['customer_followup_required'])
                self.assertEqual(current['monitoring_action'], 'call_next_tool')
                self.assertEqual(current['next_tool_arguments']['goal_id'], 288)
                self.assertEqual(current['next_tool_arguments']['timeout_seconds'], 25)
                self.assertEqual(current['next_tool_arguments']['cursor'], first['cursor'])

    def test_terminal_outcomes_do_not_receive_active_continuation_metadata(self):
        for state in ('completed', 'cancelled', 'paused'):
            result = live_update({'id': 288, 'state': state}, 288)
            self.assertFalse(result['continue_watching'])
            self.assertNotIn('customer_followup_required', result)
            self.assertNotIn('monitoring_action', result)


    def test_validated_contact_slug_difference_preserves_values_and_uncertainty(self):
        answer = {'id': 287, 'state': 'running', 'fulfillment': 'unknown',
                  'business_answer': {'counts': {'contacts': 1, 'requested': 5}},
                  'contacts': [{'name': 'Tiffany Payne', 'company': 'Observed employer',
                                'title': 'Sales Director', 'email': 'tthompson@example.com',
                                'linkedin': 'https://linkedin.com/in/tiffany-thompson-renee',
                                'email_status': 'provider_reported'}]}
        result = live_update(answer, 287)
        self.assertEqual(result['findings'][0]['email'], answer['contacts'][0]['email'])
        self.assertEqual(result['findings'][0]['linkedin'], answer['contacts'][0]['linkedin'])
        self.assertEqual(result['findings'][0]['email_status'], 'provider_reported')
        self.assertIn('slug alone does not establish an identity conflict', result['contact_table_policy'])
        self.assertIn('Preserve explicit recorded conflicts', result['contact_table_policy'])
        self.assertFalse(result['final_response_ready'])


    def test_actual_running_scheduler_execute_is_not_client_or_customer_action(self):
        answer = {'id': 288, 'state': 'running', 'fulfillment': 'unknown',
                  'current_step': 'Complete next step', 'next_action': 'execute',
                  'business_answer': {'counts': {'contacts': 0, 'requested': 10}}}
        result = live_update(answer, 288)
        self.assertEqual(result['next_action'], 'execute')
        self.assertEqual(result['stage'], 'Complete next step')
        self.assertEqual(result['next_tool'], 'watch_research')
        self.assertEqual(result['monitoring_action'], 'call_next_tool')
        self.assertFalse(result['customer_followup_required'])
        self.assertFalse(result['final_response_ready'])
        self.assertIn('not a customer command', result['backend_action_role'])

    def test_conference_clarification_retains_exception_and_no_no_followup_flag(self):
        result = live_update({'id': 300, 'state': 'waiting_customer',
                              'conference_answer': {'spec': {'kind': 'conference_speakers'}},
                              'blocker': {'reason': 'Missing conference edition'}}, 300)
        self.assertFalse(result['continue_watching'])
        self.assertFalse(result['final_response_ready'])
        self.assertNotIn('customer_followup_required', result)
        self.assertIsNone(result['next_tool_arguments'])

class WatchDeltaEndpointTests(LiveProgressTests):
    async def test_quiet_endpoint_omits_previously_delivered_findings(self):
        task = self.task()
        result, clock, calls = await self.watch(
            [task], cursor=self.update(task)["cursor"]
        )
        self.assertEqual(clock.elapsed, 25)
        self.assertEqual(len(calls), 5)
        self.assertEqual(result["payload_mode"], "unchanged_delta")
        self.assertTrue(result["findings_unchanged"])
        self.assertEqual(result["count_basis"], self.update(task)["count_basis"])
        self.assertNotIn("findings", result)
        self.assertEqual(result["next_tool_arguments"]["cursor"], result["cursor"])

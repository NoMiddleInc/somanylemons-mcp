import copy
import json
import unittest
from unittest.mock import patch

import httpx

from somanylemons_mcp.task_tools.answer_navigation import (
    bounded_examples_and_artifacts,
    list_task_navigation,
)
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig
from somanylemons_mcp.task_tools.server import compact_research_answer, create_server


class NavigationTests(unittest.TestCase):
    def row(self):
        return {
            "id": 34,
            "current_answer_goal_id": 49,
            "outcome_goal_ids": [49, 48, 34],
            "outcome_summary": "237 speakers 190 emails; review only",
            "progress": {"completed": 43, "total": 44},
            "blocker": {"reason": "awaiting_review"},
            "next_run_at": "past",
            "artifacts": [
                {
                    "id": 13,
                    "origin_goal_id": 48,
                    "row_count": 237,
                    "hash": "a" * 64,
                    "schema": {"version": "conference.speakers.v1"},
                }
            ],
        }

    def test_pending_current_answer_does_not_fall_back_to_saved_artifact_or_original(
        self,
    ):
        value = {"tasks": [self.row()]}
        original = copy.deepcopy(value)
        row = list_task_navigation(value)["tasks"][0]
        self.assertEqual(
            row["canonical_answer_action"],
            {"tool": "get_research_answer", "goal_id": 49},
        )
        self.assertEqual(row["artifacts"][0]["origin_goal_id"], 48)
        self.assertEqual(
            row["listed_goal_progress"],
            {"completed": 43, "total": 44, "unit": "worker_steps", "scope_goal_id": 34},
        )
        for key in ("progress", "blocker", "next_run_at"):
            self.assertNotIn(key, row)
        self.assertNotIn("190 emails", row["outcome_summary"])
        self.assertEqual(value, original)

    def test_absent_canonical_id_is_only_labeled_historical_hint(self):
        row = self.row()
        row.pop("current_answer_goal_id")
        projected = list_task_navigation({"tasks": [row]})["tasks"][0]
        self.assertIsNone(projected["current_answer_goal_id"])
        self.assertIsNone(projected["canonical_answer_action"])
        self.assertEqual(projected["saved_artifact_answer_goal_id_hint"], 48)
        self.assertFalse(projected["saved_artifact_hint_is_current_answer"])

    def test_artifact_free_current_conference_and_agency_are_kept_distinct(self):
        conference = {"id": 50, "current_answer_goal_id": 50, "artifacts": []}
        agency = {
            "id": 24,
            "progress": {"completed": 3, "total": 110},
            "outcome_summary": "Agency facts",
            "artifacts": [],
        }
        result = list_task_navigation({"tasks": [conference, agency]})
        self.assertEqual(result["tasks"][0]["canonical_answer_action"]["goal_id"], 50)
        self.assertEqual(result["tasks"][1], agency)

    def test_malformed_optional_metadata_never_crashes_or_invents_canonical_id(self):
        for response in (
            None,
            [],
            {},
            {"tasks": None},
            {
                "tasks": [
                    None,
                    7,
                    {"id": True},
                    {
                        "id": 1,
                        "current_answer_goal_id": True,
                        "artifacts": [{"schema": None}],
                    },
                    {
                        "id": 2,
                        "artifacts": [
                            {
                                "schema": {"version": "conference.speakers.v1"},
                                "origin_goal_id": [],
                            }
                        ],
                        "progress": None,
                        "outcome_goal_ids": None,
                    },
                ]
            },
        ):
            list_task_navigation(response)

    def test_trimmed_examples_cannot_remain_complete_or_become_whole_cohort_totals(
        self,
    ):
        for total, source_complete in ((3, True), (5, False)):
            summary = {
                "candidate_people": 42,
                "candidate_examples_are_complete": source_complete,
                "candidate_examples": list(range(total)),
                "status_counts_by_person": {"blocked": 4},
                "status_examples_by_person": {"blocked": [1, 2, 3]},
            }
            out = bounded_examples_and_artifacts(
                {"conference_answer": {"enrichment_summary": summary}}
            )["conference_answer"]["enrichment_summary"]
            self.assertFalse(out["candidate_examples_are_complete"])
            self.assertEqual(out["candidate_examples_saved_preview_total"], total)
            self.assertEqual(out["candidate_people"], 42)
            self.assertEqual(
                out["candidate_examples_source_preview_was_complete"], source_complete
            )
            self.assertEqual(out["status_counts_by_person"], {"blocked": 4})

    def test_review_file_metadata_never_becomes_live_document_or_delivery_clearance(
        self,
    ):
        artifact = self.row()["artifacts"][0]
        value = {
            "conference_answer": {
                "preliminary_review_artifact": None,
                "final_delivery_requirements": {
                    "combination": "all_required",
                    "all_required_met": False,
                },
            },
            "artifacts": [artifact],
        }
        original = copy.deepcopy(value)
        out = bounded_examples_and_artifacts(value)
        saved = out["conference_answer"]["saved_review_workbook"]
        self.assertTrue(saved["metadata_recorded"])
        self.assertEqual(
            saved["artifact_retrieval"],
            {"tool": "get_task_artifact", "goal_id": 48, "artifact_id": 13},
        )
        self.assertFalse(saved["reading_authorizes_send"])
        self.assertFalse(saved["is_live_working_document"])
        self.assertEqual(
            out["conference_answer"]["final_delivery_requirements"],
            value["conference_answer"]["final_delivery_requirements"],
        )
        self.assertEqual(value, original)

    def test_current_pointer_clock_gates_pagination_and_exact_draft_survive_compaction(
        self,
    ):
        draft = "Dear Tom,\n\nLet’s discuss…\n\nRegards"
        summary = {
            "missing_email_people": 42,
            "missing_email_enrichment_status_counts_by_person": {
                "completed_identity_unresolved": 37,
                "blocked": 2,
                "completed_email_unavailable": 3,
            },
            "unfinished_with_recorded_email_people": 2,
            "unfinished_without_recorded_email_people": 2,
            "recorded_email_date_summary": {
                "email_observed_at": {
                    "groups": [
                        {"date": "2026-09-16", "people": 8},
                        {"date": "2026-10-05", "people": 141},
                    ]
                }
            },
        }
        task = {
            "id": 48,
            "current_answer_goal_id": 49,
            "conference_answer": {
                "rows": [
                    {
                        "name": f"Person {i}",
                        "intro_email_draft": draft,
                        "email_observed_at": "2026-10-05T04:50:58Z",
                    }
                    for i in range(237)
                ],
                "sources": [],
                "enrichment_summary": summary,
                "final_delivery_requirements": {"all_required_met": False},
                "client_session_end_time_recorded": False,
            },
        }
        out = compact_research_answer(task, contact_page=2)
        self.assertFalse(out["answer_is_current_recorded_goal"])
        self.assertEqual(out["current_answer_goal_id"], 49)
        self.assertEqual(out["pagination"]["rows_total"], 237)
        self.assertEqual(
            [r["name"] for r in out["contacts"]], [f"Person {i}" for i in range(5, 10)]
        )
        self.assertEqual(out["contacts"][0]["intro_email_draft"], draft)
        self.assertEqual(out["conference_answer"]["enrichment_summary"], summary)
        self.assertFalse(out["conference_answer"]["client_session_end_time_recorded"])

    def test_roster_aliases_addresses_and_completed_keys_keep_distinct_units(self):
        people = [
            {
                "name": f"Roster entry {i}",
                "company": "Saved company",
                "email": f"address{i if i < 164 else i - 164}@example.com"
                if i < 166
                else "",
                "session_title": f"Session {i}",
            }
            for i in range(191)
        ]
        people[0]["name"], people[164]["name"] = "Pat Example", "Patricia Example"
        people[1]["name"], people[165]["name"] = (
            "Chris Example",
            "Christopher Example",
        )
        order = [0, 164, 1, 165, *range(2, 164), *range(166, 191)]
        rows = [people[index] for index in order] + people[:46]
        summary = {
            "unique_people": 191,
            "identity_count_basis": "Saved name/company roster keys may contain aliases; not distinct individuals.",
            "recorded_email_address_count_basis": "Trimmed, casefolded recorded addresses; shared addresses do not prove identity or deliverability.",
            "distinct_recorded_email_addresses": 164,
            "shared_recorded_email_address_groups": 2,
            "roster_keys_in_shared_recorded_email_address_groups": 4,
            "missing_email_people": 25,
            "recorded_email_enrichment_status_counts_by_person": {
                "completed": 13,
                "saved_result_reused": 153,
            },
            "recorded_email_provenance_groups": [
                {
                    "email_source": "saved_public_source",
                    "enrichment_status": "completed",
                    "people": 13,
                },
                {
                    "email_source": "saved_provider",
                    "enrichment_status": "saved_result_reused",
                    "people": 153,
                },
            ],
        }
        task = {
            "conference_answer": {
                "rows": rows,
                "sources": [],
                "email_coverage": {"unique_speakers": 191, "recorded_emails": 166},
                "enrichment_summary": summary,
                "final_delivery_requirements": {"all_required_met": False},
            }
        }
        original = copy.deepcopy(task)
        out = compact_research_answer(task)
        projected = out["conference_answer"]
        for key, value in summary.items():
            self.assertEqual(projected["enrichment_summary"][key], value)
        self.assertEqual(
            projected["email_coverage"], task["conference_answer"]["email_coverage"]
        )
        self.assertEqual(out["pagination"]["rows_total"], 237)
        self.assertEqual(
            [row["name"] for row in out["contacts"]],
            [row["name"] for row in rows[:5]],
        )
        self.assertEqual(out["contacts"][0]["email"], out["contacts"][1]["email"])
        self.assertEqual(out["contacts"][2]["email"], out["contacts"][3]["email"])
        self.assertEqual(
            projected["enrichment_summary"]["recorded_email_provenance_people_total"],
            166,
        )
        self.assertFalse(projected["final_delivery_requirements"]["all_required_met"])
        self.assertEqual(task, original)


class NavigationToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_list_tool_preserves_params_and_one_scoped_request(self):
        calls = []
        value = {"tasks": [NavigationTests().row()]}

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": value})

        server = create_server(
            TaskApiClient(
                TaskApiConfig("https://example.com", "owner"),
                transport=httpx.MockTransport(handler),
            )
        )
        row = json.loads(
            (
                await server.call_tool(
                    "list_tasks", {"state": "needs_attention", "page": 2}
                )
            )[0].text
        )["tasks"][0]
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            dict(calls[0].url.params), {"state": "needs_attention", "page": "2"}
        )
        self.assertEqual(calls[0].headers["authorization"], "Bearer owner")
        self.assertEqual(row["canonical_answer_action"]["goal_id"], 49)

    async def test_all_tools_remain_available_and_hosted_receives_navigation_guidance(
        self,
    ):
        from somanylemons_mcp import server as hosted
        from somanylemons_mcp.task_bridge import task_schemas

        self.assertEqual(len(await task_schemas()), 25)
        with patch.object(
            hosted, "_request_identity", return_value={"research_only": False}
        ):
            self.assertEqual(len(await hosted.list_tools()), 54)
        self.assertIn("current_answer_goal_id", hosted.server.instructions)
        self.assertIn(
            "missing_email_enrichment_status_counts_by_person",
            hosted.server.instructions,
        )
        for field in (
            "identity_count_basis",
            "distinct_recorded_email_addresses",
            "recorded_email_address_count_basis",
        ):
            self.assertIn(field, hosted.server.instructions)
        self.assertIn("delete or merge alias rows", hosted.server.instructions)


if __name__ == "__main__":
    unittest.main()

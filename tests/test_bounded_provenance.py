import json
import unittest
from somanylemons_mcp.task_tools.provenance import bounded_email_provenance
from somanylemons_mcp.task_tools.server import compact_research_answer


class BoundedProvenanceTests(unittest.TestCase):
    def groups(self):
        return [{"email_source": "apollo", "email_status": "verified", "enrichment_status": "saved_result_reused",
            "email_observed_at": "2026-09-16T03:17:00Z", "email_verified_at": "not_recorded", "enriched_on": "2026-09-16", "people": 8}] + [
            {"email_source": "apollo_native_business_email", "email_status": "verified", "enrichment_status": "completed",
             "email_observed_at": f"2026-10-05T03:{i // 60:02}:{i % 60:02}Z", "email_verified_at": "not_recorded",
             "enriched_on": "2026-10-04", "people": 1} for i in range(98)]

    def test_calendar_summary_counts_all_groups_and_keeps_distinct_clocks(self):
        groups = self.groups()
        summary = {"recorded_email_provenance_groups": groups, "unfinished_people": 4}
        result = bounded_email_provenance(summary)
        self.assertEqual([g["people"] for g in result["recorded_email_provenance_calendar_summary"]], [8, 98])
        new = result["recorded_email_provenance_calendar_summary"][1]
        self.assertEqual((new["email_observed_at"], new["enriched_on"], new["email_verified_at"]), ("2026-10-05", "2026-10-04", "not_recorded"))
        self.assertEqual(result["unfinished_people"], 4)
        self.assertEqual(len(result["recorded_email_provenance_groups"]), 5)
        self.assertEqual(result["recorded_email_provenance_pagination"]["groups_omitted"], 94)
        self.assertEqual(len(summary["recorded_email_provenance_groups"]), 99)

    def test_exact_timestamp_pagination_never_changes_original_values(self):
        groups = self.groups()
        result = bounded_email_provenance({"recorded_email_provenance_groups": groups}, 2)
        self.assertEqual(result["recorded_email_provenance_groups"], groups[5:10])
        self.assertEqual(result["recorded_email_provenance_pagination"]["page"], 2)
        self.assertEqual(result["recorded_email_provenance_pagination"]["next_page"], 3)

    def test_large_clock_history_and_evidence_fit_readable_page_with_exact_draft(self):
        draft = "Hi Chris,\n\nYou’re invited.\n\nThanks."
        row = {"name": "Speaker", "email_observed_at": "2026-09-16T03:17:00Z", "intro_email_draft": draft,
               "evidence_refs": [{"url": "https://publisher.example/person", "quote": "x" * 1000, "content_hash": "a" * 64} for _ in range(20)]}
        task = {"conference_answer": {"rows": [row for _ in range(237)], "sources": [],
            "enrichment_summary": {"recorded_email_provenance_groups": self.groups(), "unfinished_people": 4}}}
        result = compact_research_answer(task, contact_page=2)
        self.assertLess(len(json.dumps(result, indent=2)), 20000)
        self.assertEqual(result["pagination"]["rows_total"], 237)
        self.assertEqual(result["pagination"]["contact_page_size"], 5)
        self.assertEqual(result["contacts"][0]["intro_email_draft"], draft)
        self.assertEqual(result["contacts"][0]["email_observed_at"], "2026-09-16T03:17:00Z")
        self.assertEqual(result["contacts"][0]["evidence_refs_total"], 20)
        self.assertEqual(len(result["contacts"][0]["evidence_refs"]), 2)
        self.assertEqual(len(result["contacts"][0]["evidence_refs"][0]["quote"]), 160)

    def test_many_calendar_dates_are_bounded_and_total_counts_not_lost(self):
        groups = [{"email_source": "apollo", "email_status": "verified", "enrichment_status": "completed", "email_observed_at": f"2026-{i // 28 + 1:02}-{i % 28 + 1:02}T01:00:00Z", "email_verified_at": "not_recorded", "enriched_on": "not_recorded", "people": 1} for i in range(99)]
        result = bounded_email_provenance({"recorded_email_provenance_groups": groups})
        self.assertEqual(result["recorded_email_provenance_people_total"], 99)
        self.assertEqual(len(result["recorded_email_provenance_calendar_summary"]), 10)
        self.assertEqual(result["recorded_email_provenance_pagination"]["calendar_groups_omitted"], 89)
        self.assertFalse(result["recorded_email_provenance_pagination"]["calendar_summary_covers_all_groups"])

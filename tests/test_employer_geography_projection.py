"""Recorded employer facts survive presentation without becoming residence proof."""
import unittest
from somanylemons_mcp.task_tools.server import compact_research_answer
from somanylemons_mcp.task_tools.live_progress import live_update


class EmployerGeographyProjectionTests(unittest.TestCase):
    def answer(self, rows):
        return compact_research_answer({
            "id": 265, "state": "running", "contract": {
                "workflow": "business_research", "spec": {
                    "count": 3, "roles": ["president"], "employer_locations": ["Ohio"],
                    "person_locations": []}},
            "business_answer": {"rows": rows, "coverage": {
                "qualified_rows": len(rows), "requested_count": 3}}})

    def test_actual_ohio_case_preserves_employer_and_person_scopes_in_answer_and_watch(self):
        rows = [
            {"name": "Marty Brodnax", "company": "Baker Construction", "company_city": "Monroe",
             "company_state": "Ohio", "company_location": "Monroe, Ohio, United States",
             "person_city": "Prairieville", "person_state": "Louisiana",
             "location": "Prairieville, Louisiana, United States"},
            {"name": "Jon Small", "company": "Cleveland Construction, Inc.", "company_city": "Mentor",
             "company_state": "Ohio", "company_location": "Mentor, Ohio, United States",
             "person_city": "Naples", "person_state": "Florida",
             "location": "Naples, Florida, United States"},
            {"name": "Dale Griffis", "company": "Cold Harbor Building Company", "company_city": "Chardon",
             "company_state": "Ohio", "company_location": "Chardon, Ohio, United States",
             "person_city": "Chardon", "person_state": "Ohio", "location": "Chardon, Ohio, United States"},
        ]
        for row in rows:
            row.update(company_country="United States", person_country="United States",
                       person_geography_source="apollo", field_provenance={"state": {
                           "scope": "person", "source": "apollo", "status": "provider_reported"}})
        answer = self.answer(rows)
        watch = live_update(answer, 265)
        for original, contact, finding in zip(rows, answer["contacts"], watch["findings"]):
            for key in original:
                self.assertEqual(contact[key], original[key])
                self.assertEqual(finding[key], original[key])
        self.assertFalse(watch["final_response_ready"])
        self.assertTrue(watch["continue_watching"])
        self.assertIn("not independent verification", watch["contact_table_policy"])

    def test_missing_employer_geography_is_not_inferred_from_person_or_request(self):
        answer = self.answer([{"name": "Person", "company": "Employer", "location": "Ohio",
                               "person_state": "Ohio"}])
        for contact in (answer["contacts"][0], live_update(answer, 265)["findings"][0]):
            self.assertNotIn("company_state", contact)
            self.assertNotIn("company_location", contact)
        self.assertEqual(answer["request"]["spec"]["employer_locations"], ["Ohio"])

    def test_conflicting_recorded_geography_remains_visible_and_values_are_bounded(self):
        answer = self.answer([{"name": "Person", "company": "Employer", "company_state": "Michigan",
                               "person_state": "Ohio", "raw_address": "x" * 501,
                               "person_location": "Ohio", "field_provenance": {
                                   "company_state": {"scope": "employer", "source": "apollo", "status": "provider_reported"}}}])
        contact = answer["contacts"][0]
        self.assertEqual(contact["company_state"], "Michigan")
        self.assertEqual(contact["person_state"], "Ohio")
        self.assertEqual(len(contact["raw_address"]), 500)
        self.assertTrue(contact["geography_fields_truncated"])
        self.assertEqual(contact["field_provenance"]["company_state"]["scope"], "employer")
        self.assertFalse(live_update(answer, 265)["final_response_ready"])

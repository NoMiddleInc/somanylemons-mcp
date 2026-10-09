"""Native employer classifications survive compact answers and live watches."""
import unittest
from somanylemons_mcp.task_tools.server import compact_research_answer
from somanylemons_mcp.task_tools.live_progress import live_update

class EmployerClassificationProjectionTests(unittest.TestCase):
    def project(self, row):
        return compact_research_answer({"id":283,"state":"running","contract":{"workflow":"business_research","spec":{"industries":["manufacturing"],"employee_range":{"min":50,"max":500}}},"business_answer":{"rows":[row]}})

    def test_native_secondary_industry_and_codes_survive_answer_and_watch(self):
        row={"name":"Saved COO","company":"Saved food producer","industry":"food & beverages","industries":["food & beverages","food production"],"secondary_industries":["food production"],"company_naics_codes":["311830","493110"],"company_sic_codes":["2099"],"employees":"100"}
        answer=self.project(row)
        for published in (answer["contacts"][0],live_update(answer,283)["findings"][0]):
            for key,value in row.items():
                self.assertEqual(published[key],value)
            self.assertFalse(published["classification_fields_truncated"])
        self.assertEqual(answer["request"]["spec"]["industries"],["manufacturing"])

    def test_missing_codes_are_not_inferred_from_request_or_company_name(self):
        answer=self.project({"name":"COO","company":"Manufacturing Co","industry":"retail"})
        for published in (answer["contacts"][0],live_update(answer,283)["findings"][0]):
            self.assertNotIn("company_naics_codes",published)
            self.assertNotIn("industries",published)
            self.assertEqual(published["industry"],"retail")

    def test_oversized_codes_are_bounded_and_report_truncation_without_raw_pages(self):
        answer=self.project({"name":"COO","company":"Company","industries":["saved industry"]*21+["x"*201],"company_naics_codes":["311830"],"opened_text":"private raw page","keywords":"private raw keywords"})
        row=answer["contacts"][0]
        self.assertEqual(len(row["industries"]),20)
        self.assertEqual(row["industries"],["saved industry"]*20)
        self.assertTrue(row["classification_fields_truncated"])
        self.assertNotIn("private raw",str(answer))

    def test_backend_truncation_remains_visible_in_watch(self):
        answer=self.project({"company":"Company","industries":["food production"],"industries_truncated":True})
        for row in (answer["contacts"][0],live_update(answer,283)["findings"][0]):
            self.assertTrue(row["classification_fields_truncated"])

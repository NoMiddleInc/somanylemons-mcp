"""Safe saved qualification evidence reaches answers and live watches."""
import unittest
from somanylemons_mcp.task_tools.server import compact_research_answer
from somanylemons_mcp.task_tools.live_progress import live_update

class EmployerScopeProofTests(unittest.TestCase):
    def project(self,row):
        answer=compact_research_answer({"id":284,"state":"running","contract":{"workflow":"business_research"},"business_answer":{"rows":[row]}})
        return answer["contacts"][0],live_update(answer,284)["findings"][0]

    def test_official_saas_and_startup_quotes_keep_binding_without_raw_payload(self):
        proof={"company":"BetterUp","domain":"betterup.com","source_url":"https://www.betterup.com/solutions/government","quote":"AI-powered SaaS solution for workforce resilience and performance","source_hash":"sourcehash","observed_at":"2026-10-09T00:42:34Z","decision_operation_key":"model:owned-decision","source_operation_key":"web_fetch:owned-source","decision_input":{"opened_text":"PRIVATE RAW PAGE"},"model_quote":"PRIVATE RAW MODEL COPY"}
        for row in self.project({"name":"COO","company":"BetterUp","industry":"professional training & coaching","employer_sector_proof":proof,"employer_startup_proof":proof}):
            for field in ("employer_sector_proof","employer_startup_proof"):
                for key in ("company","domain","source_url","quote","source_hash","observed_at","decision_operation_key","source_operation_key"):
                    self.assertEqual(row[field][key],proof[key])
            self.assertNotIn("PRIVATE RAW",str(row))
            self.assertEqual(row["industry"],"professional training & coaching")

    def test_specialized_required_scope_and_literal_evidence_remain_bounded(self):
        proof={"version":"owned-v1","decision_operation_key":"model:owned","decision_input":{"opened_text":"PRIVATE RAW PAGE"},"claims":[{"requirement":"Sells commercial insurance","matches":True,"reason":"Recorded source matches","evidence":[{"source_url":"https://employer.example/team","quote":"commercial insurance producer"}]}],"sources":[{"url":"https://employer.example/team","content_hash":"hash","observed_at":"date","operation_key":"web_fetch:owned","text":"PRIVATE RAW PAGE"}]}
        for row in self.project({"company":"Employer","eligibility_proof":proof}):
            claim=row["eligibility_proof"]["claims"][0]
            for key in ("requirement","matches","reason"):
                self.assertEqual(claim[key],proof["claims"][0][key])
            for key in ("source_url","quote"):
                self.assertEqual(claim["evidence"][0][key],proof["claims"][0]["evidence"][0][key])
            self.assertEqual(row["eligibility_proof"]["sources"][0]["operation_key"],"web_fetch:owned")
            self.assertNotIn("PRIVATE RAW",str(row))
        for row in self.project({"company":"Employer","employer_sector_proof":{"quote":"x"*1001,"source_url":"https://source.example"}}):
            self.assertEqual(len(row["employer_sector_proof"]["quote"]),1000)
            self.assertTrue(row["employer_sector_proof"]["truncated"])

    def test_absent_or_malformed_proof_is_not_inferred_from_requested_industry(self):
        for row in self.project({"company":"SaaS Vendor","employer_sector_proof":"not an evidence object"}):
            self.assertNotIn("employer_sector_proof",row)
            self.assertNotIn("eligibility_proof",row)

    def test_safe_scope_and_employer_location_metadata_survive_without_inference(self):
        proof={"company_name":"Employer","location":{"city":"Austin","state":"Texas","country":"United States"},"requested_locations":["Austin"],"requested_industries":["SaaS"],"qualification_requirements":["Provides SaaS"],"quote":"Headquarters in Austin","source_url":"https://employer.example/contact"}
        for row in self.project({"company":"Employer","employer_geography_proof":proof}):
            published=row["employer_geography_proof"]
            for key in ("city","state","country"):
                self.assertEqual(published["location"][key],proof["location"][key])
            self.assertEqual(published["requested_locations"],["Austin"])
            self.assertEqual(published["requested_industries"],["SaaS"])
            self.assertNotIn("company_state",row)

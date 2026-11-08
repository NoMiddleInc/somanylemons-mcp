"""Ordinary contact requests reach the fast people search, not the slow background research.

On 2026-11-07 "Find VPs of Sales at US logistics software companies, with their business emails"
(goal 676) and "Find the CFO and the head of HR at each of these companies: Zillow, Expedia,
Nordstrom" (goal 659) went to create_business_research_request and were then watched in silence
for 17 and 4 minutes, while the same requests through find_people finish in 15 to 40 seconds.
The skill, the server instructions and the tool description each also sent "business-contact
requests across any industry or role" to background research.
"""
import unittest
from pathlib import Path

from somanylemons_mcp.task_bridge import SCHEMA_SERVER, task_schemas
from somanylemons_mcp.task_tools.live_progress import live_update, watch_response

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("skills/producerspark/SKILL.md", "plugins/producerspark/skills/producerspark/SKILL.md",
          "src/somanylemons_mcp/skills/producerspark/SKILL.md")
GENERAL_ROUTES_TO_BACKGROUND = (
    "For new business-contact or conference requests across any industry or role, use create_business_research_request",
    "For every new business research request, call create_business_research_request",
    "Use create_business_research_request for new business-contact and conference questions across any industry or role",
    "For a prospect-finding request, show qualified saved contacts immediately",
    "Use watch_research as the primary live progress tool for interactive prospect finding",
    "keep watching quietly without a customer-facing heartbeat",
)


class PeopleSearchRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_no_guidance_sends_ordinary_contact_requests_to_background_research(self):
        sources = {path: (ROOT / path).read_text() for path in SKILLS}
        sources["server instructions"] = SCHEMA_SERVER.instructions
        found = [(where, phrase[:60]) for where, text in sources.items()
                 for phrase in GENERAL_ROUTES_TO_BACKGROUND if phrase in text]
        self.assertEqual(found, [])
        for where, text in sources.items():
            self.assertIn("find_people", text, where)

    async def test_background_research_describes_itself_as_the_slow_path(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        description = tools["create_business_research_request"].description
        self.assertNotIn("any industry and role", description)
        self.assertIn("more than 200 people", description)
        self.assertIn("minutes", description)
        self.assertIn("up to 200", description)

    def test_a_continuing_watch_gives_a_line_for_the_person_waiting(self):
        answer = {"id": 676, "state": "running", "current_step": "Finding contacts",
                  "business_answer": {"counts": {"contacts": 4, "requested": 10}}}
        result = watch_response(live_update(answer, 676), changed=False)
        self.assertTrue(result["continue_watching"])
        self.assertIn("4 of 10", result["progress_message"])
        self.assertIn("progress_message", result["completion_instruction"])
        self.assertIn("until final_response_ready", result["completion_instruction"])


if __name__ == "__main__":
    unittest.main()

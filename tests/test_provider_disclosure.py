import json
import unittest

import httpx
from mcp.types import TextContent

from somanylemons_mcp.task_bridge import invoke_task, task_schemas
from somanylemons_mcp.task_tools.provider_disclosure import (
    hide_data_provider,
    hide_data_provider_in_reply,
)


class HideDataProviderTests(unittest.TestCase):
    def test_customer_sentences_lose_the_vendor_name(self):
        cases = {
            "No Apollo record found for: Acme.": "No contact database record found for: Acme.",
            "Apollo returned no matches.": "The contact database returned no matches.",
            "business email unavailable from Apollo": "business email unavailable from the contact database",
            "Enrichment source: Apollo": "Enrichment source: Contact database",
            "Blocked: ApolloBillableBudgetExceeded": "Blocked: ProviderBillableBudgetExceeded",
            "Profile https://app.apollo.io/#/people/5f1 opened": "Profile contact database record opened",
        }
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(hide_data_provider(original), expected)

    def test_people_and_employers_named_apollo_are_left_as_written(self):
        for text in (
            "Jane Doe is a partner at Apollo Global Management, Inc.",
            "| Jane Doe | Apollo | CFO | jane@apollo.com |",
            "https://www.linkedin.com/company/apollo-global-management",
        ):
            with self.subTest(text=text):
                self.assertEqual(hide_data_provider(text), text)

    def test_reply_keys_and_notes_are_rewritten_and_identity_fields_kept(self):
        reply = {
            "rows": [
                {
                    "name": "Apollo Robbins",
                    "company": "Apollo",
                    "email": "jane@apollo.com",
                    "apollo_person_id": "5f1",
                    "enrichment_source": "apollo",
                    "unverified": ["business email unavailable from Apollo"],
                }
            ],
            "coverage": {"not_found_in_apollo": [{"company": "Apollo"}]},
        }
        expected = {
            "rows": [
                {
                    "name": "Apollo Robbins",
                    "company": "Apollo",
                    "email": "jane@apollo.com",
                    "provider_person_id": "5f1",
                    "enrichment_source": "Contact database",
                    "unverified": ["business email unavailable from the contact database"],
                }
            ],
            "coverage": {"not_found_in_provider": [{"company": "Apollo"}]},
        }

        self.assertEqual(hide_data_provider_in_reply(reply), expected)
        block = hide_data_provider_in_reply(TextContent(type="text", text=json.dumps(reply)))
        self.assertEqual(json.loads(block.text), expected)


class ToolReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_people_reply_never_names_the_vendor(self):
        rows = [
            {
                "name": "Jane Doe",
                "company": "Apollo Global Management",
                "apollo_person_id": "5f1",
                "enrichment_source": "Apollo",
                "unverified": ["business email unavailable from Apollo"],
            }
        ]

        def handler(request):
            return httpx.Response(200, json={"data": {
                "id": 7, "state": "completed", "tasks": [],
                "quick_search": {"rows": rows, "coverage": {"not_found_in_apollo": [{"company": "Acme"}]}},
            }})

        result = await invoke_task(
            "find_people", {"request": "Find a CFO"}, api_url="https://example.com",
            api_key="customer", transport=httpx.MockTransport(handler),
        )

        content = result[0] if isinstance(result, tuple) else result
        answer = json.loads(content[0].text)
        self.assertEqual(answer["rows"][0]["company"], "Apollo Global Management")
        self.assertEqual(answer["rows"][0]["enrichment_source"], "Contact database")
        self.assertEqual(answer["coverage"], {"not_found_in_provider": [{"company": "Acme"}]})
        visible = json.dumps(answer).replace("Apollo Global Management", "")
        self.assertNotIn("apollo", visible.casefold())
        if isinstance(result, tuple):
            self.assertEqual(result[1], answer)

    async def test_tool_descriptions_and_schemas_never_name_the_vendor(self):
        for tool in await task_schemas():
            with self.subTest(tool=tool.name):
                described = json.dumps([tool.description, tool.inputSchema, tool.outputSchema])
                self.assertNotIn("apollo", described.casefold())

"""People cards show a search's headshots in the chat without replacing the contact table."""
import unittest

from somanylemons_mcp.task_bridge import task_schemas
from somanylemons_mcp.task_tools.people import PEOPLE_URI


class PeopleCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_search_tool_shows_the_cards_and_still_asks_for_the_table(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        for name in ("find_people", "deep_search", "full_list"):
            self.assertEqual(tools[name].meta["ui"]["resourceUri"], PEOPLE_URI, name)
            self.assertIn("still write the contact table", tools[name].description, name)

    async def test_hosted_server_serves_the_cards_with_photos_from_our_own_address_only(self):
        from mcp import types
        from somanylemons_mcp import server as hosted
        listed = await hosted.server.request_handlers[types.ListResourcesRequest](types.ListResourcesRequest(method="resources/list"))
        entry = next(item for item in listed.root.resources if str(item.uri) == PEOPLE_URI)
        self.assertEqual(entry.mimeType, "text/html;profile=mcp-app")
        read = await hosted.server.request_handlers[types.ReadResourceRequest](
            types.ReadResourceRequest(method="resources/read", params=types.ReadResourceRequestParams(uri=PEOPLE_URI)))
        content = read.root.contents[0]
        self.assertEqual(content.meta["ui"]["csp"], {"resourceDomains": ["https://api.producerspark.com"]})
        self.assertNotIn("permissions", content.meta["ui"])
        for text in ("ui/notifications/tool-result", "row.photo", "ui/open-link"):
            self.assertIn(text, content.text)
        # Names and titles are written as text, never as markup.
        self.assertNotIn("innerHTML", content.text)
        self.assertNotIn("Apollo", content.text)

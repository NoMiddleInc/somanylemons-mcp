"""The recorder test widget is offered as an MCP App that asks for the camera and microphone."""
import unittest

from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.recorder_test import RECORDER_TEST_URI


class RecorderTestWidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_points_at_the_widget_and_is_read_only(self):
        tool = {tool.name: tool for tool in await task_schemas()}["open_recorder_test"]
        self.assertIn("open_recorder_test", TASK_TOOL_NAMES)
        self.assertEqual(tool.meta["ui"]["resourceUri"], RECORDER_TEST_URI)
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertEqual(tool.inputSchema.get("required", []), [])

    async def test_calling_it_reaches_no_backend(self):
        result = await invoke_task("open_recorder_test", {}, api_url="https://example.invalid", api_key="scoped-owner")
        self.assertIn("recorder test is shown", str(result))

    async def test_hosted_server_lists_and_serves_the_widget_with_its_permission_request(self):
        from mcp import types
        from somanylemons_mcp import server as hosted
        listed = await hosted.server.request_handlers[types.ListResourcesRequest](types.ListResourcesRequest(method="resources/list"))
        entry = next(item for item in listed.root.resources if str(item.uri) == RECORDER_TEST_URI)
        self.assertEqual(entry.mimeType, "text/html;profile=mcp-app")
        read = await hosted.server.request_handlers[types.ReadResourceRequest](
            types.ReadResourceRequest(method="resources/read", params=types.ReadResourceRequestParams(uri=RECORDER_TEST_URI)))
        content = read.root.contents[0]
        self.assertEqual(content.mimeType, "text/html;profile=mcp-app")
        self.assertEqual(content.meta["ui"]["permissions"], {"camera": {}, "microphone": {}})
        self.assertIn("getUserMedia", content.text)
        self.assertNotIn("Apollo", content.text)


if __name__ == "__main__":
    unittest.main()

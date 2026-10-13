"""The in-chat recorder is an MCP App that records, uploads straight to storage and saves the recording."""
import json
import unittest

import httpx

from somanylemons_mcp.task_bridge import TASK_TOOL_NAMES, invoke_task, task_schemas
from somanylemons_mcp.task_tools.recorder import RECORDER_URI

KEY = "33333333-3333-4333-8333-333333333333"


class RecorderTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, name, arguments, response):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=response)
        result = await invoke_task(name, arguments, api_url="https://example.com", api_key="scoped-owner", transport=httpx.MockTransport(handler))
        return calls, result

    async def test_record_in_chat_shows_the_recorder_and_reaches_no_backend(self):
        tool = {tool.name: tool for tool in await task_schemas()}["record_in_chat"]
        self.assertIn("record_in_chat", TASK_TOOL_NAMES)
        self.assertEqual(tool.meta["ui"]["resourceUri"], RECORDER_URI)
        self.assertEqual(tool.inputSchema.get("required", []), [])
        self.assertIn("start_browser_recording", tool.description)
        result = await invoke_task("record_in_chat", {"title": "Launch"}, api_url="https://example.invalid", api_key="scoped-owner")
        self.assertIn("recorder is shown", str(result))

    async def test_the_test_widget_is_gone(self):
        self.assertNotIn("open_recorder_test", {tool.name for tool in await task_schemas()})
        self.assertNotIn("open_recorder_test", TASK_TOOL_NAMES)

    async def test_upload_tools_are_for_the_recorder_only_and_reach_their_backend_actions(self):
        tools = {tool.name: tool for tool in await task_schemas()}
        for name in ("create_recording_upload", "finish_recording_upload"):
            self.assertEqual(tools[name].meta["ui"]["visibility"], ["app"], name)
        reserved = {"data": {"upload_id": "tok", "upload_url": "https://storage.googleapis.com/upload/x", "content_type": "video/webm", "max_bytes": 1073741824}}
        arguments = {"origin": "https://abc.claudemcpcontent.com", "content_type": "video/webm", "file_name": "claude-recording.webm", "idempotency_key": KEY}
        calls, result = await self._call("create_recording_upload", arguments, reserved)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/agent-tools/start_chat_recording")
        self.assertEqual(json.loads(calls[0].content), arguments)
        self.assertIn("https://storage.googleapis.com/upload/x", str(result))

        saved = {"data": {"id": "rec-1", "status": "transcribing", "title": "Launch", "clips": []}}
        calls, result = await self._call("finish_recording_upload", {"upload_id": "tok", "title": "Launch", "idempotency_key": KEY}, saved)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/agent-tools/finish_chat_recording")
        self.assertEqual(json.loads(calls[0].content), {"upload_id": "tok", "title": "Launch", "idempotency_key": KEY})
        self.assertIn("rec-1", str(result))

    async def test_hosted_server_serves_the_recorder_with_camera_mic_and_storage_access(self):
        from mcp import types
        from somanylemons_mcp import server as hosted
        listed = await hosted.server.request_handlers[types.ListResourcesRequest](types.ListResourcesRequest(method="resources/list"))
        entry = next(item for item in listed.root.resources if str(item.uri) == RECORDER_URI)
        self.assertEqual(entry.mimeType, "text/html;profile=mcp-app")
        read = await hosted.server.request_handlers[types.ReadResourceRequest](
            types.ReadResourceRequest(method="resources/read", params=types.ReadResourceRequestParams(uri=RECORDER_URI)))
        content = read.root.contents[0]
        self.assertEqual(content.meta["ui"]["permissions"], {"camera": {}, "microphone": {}})
        self.assertEqual(content.meta["ui"]["csp"]["connectDomains"], ["https://storage.googleapis.com"])
        for text in ("getUserMedia", "create_recording_upload", "finish_recording_upload"):
            self.assertIn(text, content.text)
        self.assertNotIn("Apollo", content.text)


if __name__ == "__main__":
    unittest.main()

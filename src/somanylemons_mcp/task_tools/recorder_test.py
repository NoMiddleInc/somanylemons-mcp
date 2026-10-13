"""A widget that checks whether the chat lets ProducerSpark use the camera and microphone.

It reads no account data and saves nothing: it exists to find out whether an in-chat recorder
is possible before one is built.
"""
from importlib.resources import files

from mcp.types import ToolAnnotations

RECORDER_TEST_URI = "ui://producerspark/recorder-test.html"
RECORDER_TEST_MIME = "text/html;profile=mcp-app"
RECORDER_TEST_META = {"ui": {"permissions": {"camera": {}, "microphone": {}}, "prefersBorder": True}}
RECORDER_TEST_TOOL = "open_recorder_test"


def recorder_test_html() -> str:
    return files("somanylemons_mcp").joinpath("task_tools/recorder_test.html").read_text()


def register(server) -> None:
    @server.resource(RECORDER_TEST_URI, name="recorder_test", mime_type=RECORDER_TEST_MIME, meta=RECORDER_TEST_META)
    def recorder_test() -> str:
        return recorder_test_html()

    @server.tool(
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
        # Older hosts read the flat key.
        meta={"ui": {"resourceUri": RECORDER_TEST_URI}, "ui/resourceUri": RECORDER_TEST_URI},
    )
    async def open_recorder_test() -> str:
        """Show a test widget that checks whether this chat allows camera and microphone access. Use only when the user asks to test the recorder or camera. Nothing is recorded to the account, uploaded or saved. To make a real recording use start_browser_recording."""
        return "The recorder test is shown. Ask the user to press 'Turn on camera and mic', then 'Send result to chat'."

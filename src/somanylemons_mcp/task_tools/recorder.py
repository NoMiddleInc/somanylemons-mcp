"""The in-chat recorder: an MCP App that records with the user's camera and microphone.

The widget uploads the take straight to storage through an upload the backend reserves for it,
then asks the backend to save it as a recording. The backend owns every rule; this module only
shows the widget and forwards its two requests.
"""
from importlib.resources import files
from typing import Annotated
from uuid import UUID

from mcp.types import ToolAnnotations
from pydantic import Field

from .agent_tools import AccountId, RecordingId, WRITE

RECORDER_URI = "ui://producerspark/recorder.html"
RECORDER_MIME = "text/html;profile=mcp-app"
RECORDER_META = {"ui": {
    "permissions": {"camera": {}, "microphone": {}},
    # The take goes directly to storage, never through the chat.
    # Finished clips play from the same storage.
    "csp": {"connectDomains": ["https://storage.googleapis.com"], "resourceDomains": ["https://storage.googleapis.com"]},
    "prefersBorder": True,
}}
RECORDER_TOOL_NAMES = frozenset({"record_in_chat", "show_recording", "create_recording_upload", "finish_recording_upload"})
APP_ONLY = {"ui": {"visibility": ["app"]}}
# Older hosts read the flat key.
RECORDER_UI = {"ui": {"resourceUri": RECORDER_URI}, "ui/resourceUri": RECORDER_URI}
SHOWN = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def recorder_html() -> str:
    return files("somanylemons_mcp").joinpath("task_tools/recorder.html").read_text()


def register(server, api) -> None:
    async def act(name: str, **arguments):
        body = {key: (str(value) if isinstance(value, UUID) else value) for key, value in arguments.items() if value not in (None, "")}
        return await api.request("POST", f"/api/v1/agent-tasks/agent-tools/{name}", body=body, timeout_seconds=180.0)

    @server.resource(RECORDER_URI, name="recorder", mime_type=RECORDER_MIME, meta=RECORDER_META)
    def recorder() -> str:
        return recorder_html()

    @server.tool(title="Record in chat", annotations=SHOWN, meta=RECORDER_UI)
    async def record_in_chat(title: Annotated[str, Field(max_length=255)] = "", instruction: Annotated[str, Field(max_length=4000, description="Optional guidance for the edit, such as which topics to cut clips from.")] = "", account_id: AccountId | None = None) -> str:
        """Shows a recorder inside the chat for recording a new video or audio take with the camera and microphone. The user presses record, then Use this recording; the take is saved to their account and edited into clips. The recorder shows editing progress, plays the finished clips and reports the recording's id back. Calling this records nothing by itself."""
        return "The recorder is shown in the chat. Tell the user to record there and press Use this recording. If no recorder appeared for them, use start_browser_recording instead."

    @server.tool(title="Show recording", annotations=SHOWN, meta=RECORDER_UI)
    async def show_recording(recording_id: RecordingId, account_id: AccountId | None = None) -> str:
        """Plays a recording's clips as video inside the chat, each with its title and post text. Changes nothing."""
        return "The recording's clips are playing in the chat. Do not also paste video links."

    @server.tool(title="Start recording upload", annotations=WRITE, meta=APP_ONLY)
    async def create_recording_upload(origin: Annotated[str, Field(max_length=200)], content_type: Annotated[str, Field(max_length=100)], idempotency_key: UUID, file_name: Annotated[str, Field(max_length=255)] = "", account_id: AccountId | None = None) -> dict:
        """Reserves storage for a take the in-chat recorder is about to upload. Called by the recorder itself."""
        return await act("start_chat_recording", origin=origin, content_type=content_type, file_name=file_name, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(title="Finish recording upload", annotations=WRITE, meta=APP_ONLY)
    async def finish_recording_upload(upload_id: Annotated[str, Field(max_length=200)], idempotency_key: UUID, title: Annotated[str, Field(max_length=255)] = "", instruction: Annotated[str, Field(max_length=4000)] = "", account_id: AccountId | None = None) -> dict:
        """Saves a take the in-chat recorder uploaded as a new recording and starts editing it. Counts against the account's recording allowance. Called by the recorder itself."""
        return await act("finish_chat_recording", upload_id=upload_id, title=title, instruction=instruction, idempotency_key=idempotency_key, account_id=account_id)

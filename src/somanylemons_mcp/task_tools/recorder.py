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
        """Show a recorder inside this chat so the user can record a new video or audio take with their camera and microphone. The user presses record, then Use this recording, and it is saved to their account and edited into clips. The recorder itself shows editing progress and plays the finished clips, and shares the recording's id with you, so do not ask the user to report back; when they next write, use get_recording or list_recordings for the details. Use this first whenever the user wants to record. Calling it records nothing by itself. If the user says no recorder appeared, or prefers the browser, use start_browser_recording instead."""
        return "The recorder is shown in the chat. Tell the user to record there and press Use this recording. If no recorder appeared for them, use start_browser_recording instead."

    @server.tool(title="Show recording", annotations=SHOWN, meta=RECORDER_UI)
    async def show_recording(recording_id: RecordingId, account_id: AccountId | None = None) -> str:
        """Play a recording's clips as video inside this chat, each with its title and post text. Use this whenever the user wants to see, watch or review a clip or a recording, and after a recording finishes editing, instead of giving a link to the video. Take recording_id from list_recordings. Changes nothing."""
        return "The recording's clips are playing in the chat. Do not also paste video links."

    @server.tool(title="Start recording upload", annotations=WRITE, meta=APP_ONLY)
    async def create_recording_upload(origin: Annotated[str, Field(max_length=200)], content_type: Annotated[str, Field(max_length=100)], idempotency_key: UUID, file_name: Annotated[str, Field(max_length=255)] = "", account_id: AccountId | None = None) -> dict:
        """Used by the in-chat recorder itself to reserve storage for the take it is about to upload. Not for direct use: to record, call record_in_chat."""
        return await act("start_chat_recording", origin=origin, content_type=content_type, file_name=file_name, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(title="Finish recording upload", annotations=WRITE, meta=APP_ONLY)
    async def finish_recording_upload(upload_id: Annotated[str, Field(max_length=200)], idempotency_key: UUID, title: Annotated[str, Field(max_length=255)] = "", instruction: Annotated[str, Field(max_length=4000)] = "", account_id: AccountId | None = None) -> dict:
        """Used by the in-chat recorder itself to save the take it uploaded as a new recording and start editing. Counts against the account's recording allowance. Not for direct use: to record, call record_in_chat."""
        return await act("finish_chat_recording", upload_id=upload_id, title=title, instruction=instruction, idempotency_key=idempotency_key, account_id=account_id)

"""Recording Agent and Scheduling Agent tools.

Each tool is a typed front for one backend action at /api/v1/agent-tasks/agent-tools/{name}.
The backend owns every rule (what may be edited when, network limits, who may act); nothing
here decides anything beyond the shape of the arguments.
"""
from typing import Annotated, Literal
from uuid import UUID

from mcp.types import ToolAnnotations
from pydantic import Field

PostId = Annotated[int, Field(gt=0, description="The post's id, from list_posts or get_recording.")]
ClipId = Annotated[int, Field(gt=0, description="The clip's id, from list_recordings or get_recording.")]
DesignId = Annotated[int, Field(gt=0, description="A design id from list_recording_designs.")]
RecordingId = Annotated[str, Field(min_length=1, max_length=64, description="The recording's id, from list_recordings.")]
Network = Literal["linkedin", "twitter"]
Caption = Annotated[str, Field(max_length=3000)]
Account = Annotated[str, Field(max_length=200, description="Only for X when more than one X account is connected: the handle to post from.")]
MediaUrls = Annotated[list[Annotated[str, Field(max_length=2000)]], Field(max_length=9, description="Public https links that point directly at image or video files (not web pages). One video, or up to nine images.")]
When = Annotated[str, Field(description="Date and time, for example 2026-10-14T09:00. Without an offset it is read in the account's own timezone.")]
Zone = Annotated[str, Field(description="IANA timezone name such as America/Chicago. Leave out to use the account's timezone.")]
PostStatus = Literal["draft", "queued", "approved", "scheduled", "posted", "publishing", "failed", "missed", "cancelled", "rejected", "pending_approval", "changes_requested"]

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
PUBLIC = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)

AGENT_TOOL_NAMES = frozenset({
    "get_posting_overview", "list_posts", "get_post", "create_post", "update_post", "schedule_post",
    "unschedule_post", "publish_post_now", "delete_post", "copy_post_to_network",
    "list_recordings", "get_recording", "add_recording", "recut_recording", "retry_recording",
    "remove_recording", "list_recording_designs", "set_default_recording_design",
    "change_clip_design", "update_clip_text", "send_clip_to_scheduling",
})

INSTRUCTIONS = (
    " Posts and recordings: the account's Scheduling Agent holds its social posts (drafts, scheduled, published) and its Recording Agent turns a recording into short clips, each handed to Scheduling as a draft post. "
    "Start with list_posts or list_recordings to get real ids; never guess an id. To change a post's text, network or media use update_post, which keeps a scheduled post's time. To move a post use schedule_post with the new time. "
    "Write times as local date and time (2026-10-14T09:00) unless the user gave a timezone. Use next_open_slot only when the user asks for the queue or the next available slot. "
    "publish_post_now makes a post public immediately, and delete_post and remove_recording remove the user's work: call them only when the user asked for exactly that, and confirm first if there is any doubt about which post or recording they mean. "
    "Every result reports what actually happened. If a result has a non-empty not_done list, tell the user what was not done and why; never describe the request as complete. "
    "A tool error saying the agent is not on the account means the user does not have that agent; say so plainly and do not retry. "
    "Recording a new video from a camera happens in ProducerSpark in the browser; from here, add_recording brings in an existing video or audio file by direct link."
)


def register(server, api) -> None:
    """Add the Recording and Scheduling tools to the task server."""

    async def act(name: str, timeout: float = 60.0, **arguments):
        body = {key: (str(value) if isinstance(value, UUID) else value) for key, value in arguments.items() if value is not None}
        return await api.request("POST", f"/api/v1/agent-tasks/agent-tools/{name}", body=body, timeout_seconds=timeout)

    @server.tool(annotations=READ)
    async def get_posting_overview() -> dict:
        """Show where this account can post (connected LinkedIn and X accounts), its timezone and posts per week, how many posts are scheduled, in drafts or failed, and the next open slots. Read-only."""
        return await act("get_posting_overview")

    @server.tool(annotations=READ)
    async def list_posts(status: PostStatus | None = None, network: Network | None = None, deleted: bool = False, page: Annotated[int, Field(ge=1)] = 1) -> dict:
        """List the account's social posts, 30 per page, with id, status, network, text, scheduled time and media. Filter by status (draft, scheduled, posted, failed...) or network; deleted=true lists removed posts. Media links expire after a few minutes. Read-only."""
        return await act("list_posts", status=status, network=network, deleted=deleted or None, page=page)

    @server.tool(annotations=READ)
    async def get_post(post_id: PostId) -> dict:
        """Read one post: status, network, text, scheduled time, media and any problem blocking it. Read-only."""
        return await act("get_post", post_id=post_id)

    @server.tool(annotations=WRITE)
    async def create_post(idempotency_key: UUID, caption: Caption = "", network: Network | None = None, account: Account | None = None, media_urls: MediaUrls | None = None, scheduled_for: When | None = None, timezone: Zone | None = None, next_open_slot: bool = False) -> dict:
        """Create a social post as a draft. Optionally attach media from direct file links, and schedule it for a time or the next open slot in the same call. Without a time it stays an unscheduled draft and nothing is published. If not_done is non-empty, the draft was saved but that step did not happen."""
        return await act("create_post", timeout=180.0, idempotency_key=idempotency_key, caption=caption, network=network, account=account, media_urls=media_urls, scheduled_for=scheduled_for, timezone=timezone, next_open_slot=next_open_slot or None)

    @server.tool(annotations=WRITE)
    async def update_post(post_id: PostId, idempotency_key: UUID, caption: Caption | None = None, network: Network | None = None, account: Account | None = None, media_urls: MediaUrls | None = None, remove_media: bool = False) -> dict:
        """Change a post's text, network or media. A scheduled post keeps its scheduled time. media_urls replaces all current media; remove_media=true removes it. A published post cannot be changed. If not_done is non-empty, read it: the post may now be an unscheduled draft."""
        return await act("update_post", timeout=180.0, post_id=post_id, idempotency_key=idempotency_key, caption=caption, network=network, account=account, media_urls=media_urls, remove_media=remove_media or None)

    @server.tool(annotations=WRITE)
    async def schedule_post(post_id: PostId, idempotency_key: UUID, scheduled_for: When | None = None, timezone: Zone | None = None, next_open_slot: bool = False) -> dict:
        """Schedule a draft, or move an already scheduled post to a new time (reschedule). Give scheduled_for, or set next_open_slot=true to take the account's next free posting slot. The post publishes automatically at that time."""
        return await act("schedule_post", post_id=post_id, idempotency_key=idempotency_key, scheduled_for=scheduled_for, timezone=timezone, next_open_slot=next_open_slot or None)

    @server.tool(annotations=WRITE)
    async def unschedule_post(post_id: PostId, idempotency_key: UUID) -> dict:
        """Take a post off the schedule. It becomes a draft and keeps its text and media."""
        return await act("unschedule_post", post_id=post_id, idempotency_key=idempotency_key)

    @server.tool(annotations=PUBLIC)
    async def publish_post_now(post_id: PostId, idempotency_key: UUID) -> dict:
        """Publish a post to its network right now. This is public and cannot be undone from here. Use only when the user explicitly asked to publish this post now."""
        return await act("publish_post_now", post_id=post_id, idempotency_key=idempotency_key)

    @server.tool(annotations=PUBLIC)
    async def delete_post(post_id: PostId, idempotency_key: UUID) -> dict:
        """Delete a post from drafts and the calendar. A post already published stays on the network. Use only when the user asked to delete or remove this post."""
        return await act("delete_post", post_id=post_id, idempotency_key=idempotency_key)

    @server.tool(annotations=WRITE)
    async def copy_post_to_network(post_id: PostId, network: Network, idempotency_key: UUID, account: Account | None = None) -> dict:
        """Make a copy of an unscheduled draft for another network (LinkedIn or X) as its own draft, sharing the same media. Schedule the copy separately."""
        return await act("copy_post_to_network", post_id=post_id, network=network, account=account, idempotency_key=idempotency_key)

    @server.tool(annotations=READ)
    async def list_recordings() -> dict:
        """List the account's recent recordings with status and the clips each produced: clip id, title, post text, video link, design, and the id and status of the clip's post in Scheduling. Read-only."""
        return await act("list_recordings")

    @server.tool(annotations=READ)
    async def get_recording(recording_id: RecordingId) -> dict:
        """Read one recording and its clips with their live scheduling status. Read-only."""
        return await act("get_recording", recording_id=recording_id)

    @server.tool(annotations=WRITE)
    async def add_recording(media_url: Annotated[str, Field(max_length=2000, description="Public https link that points directly at a video or audio file, up to 1 GB.")], idempotency_key: UUID, title: Annotated[str, Field(max_length=255)] = "", instruction: Annotated[str, Field(max_length=4000, description="Optional guidance for the edit, such as which topics to cut clips from.")] = "") -> dict:
        """Bring in an existing video or audio file by direct link as a new recording and start editing it into clips. Counts against the account's recording allowance. Editing takes several minutes; check progress with get_recording."""
        return await act("add_recording", timeout=300.0, media_url=media_url, title=title or None, instruction=instruction or None, idempotency_key=idempotency_key)

    @server.tool(annotations=WRITE)
    async def recut_recording(recording_id: RecordingId, instruction: Annotated[str, Field(min_length=1, max_length=4000)], idempotency_key: UUID) -> dict:
        """Cut a fresh set of clips from a recording following a new instruction (for example different topics or a different focus). Existing clips and their posts are kept. Counts against the account's edited-clip allowance."""
        return await act("recut_recording", recording_id=recording_id, instruction=instruction, idempotency_key=idempotency_key)

    @server.tool(annotations=WRITE)
    async def retry_recording(recording_id: RecordingId, idempotency_key: UUID) -> dict:
        """Run a recording again after it failed or stalled."""
        return await act("retry_recording", recording_id=recording_id, idempotency_key=idempotency_key)

    @server.tool(annotations=PUBLIC)
    async def remove_recording(recording_id: RecordingId, idempotency_key: UUID) -> dict:
        """Remove a recording and stop any editing in progress. Posts already created from its clips stay in Scheduling. Use only when the user asked to remove this recording."""
        return await act("remove_recording", recording_id=recording_id, idempotency_key=idempotency_key)

    @server.tool(annotations=READ)
    async def list_recording_designs() -> dict:
        """List the designs (templates) available for clips, for video and for audio recordings, and which one new recordings use by default. Read-only."""
        return await act("list_recording_designs")

    @server.tool(annotations=WRITE)
    async def set_default_recording_design(design_id: DesignId, idempotency_key: UUID) -> dict:
        """Choose the design new recordings will use. Existing clips are not changed."""
        return await act("set_default_recording_design", design_id=design_id, idempotency_key=idempotency_key)

    @server.tool(annotations=WRITE)
    async def change_clip_design(clip_id: ClipId, design_id: DesignId, idempotency_key: UUID) -> dict:
        """Re-render one clip in a different design. The current video stays in place until the new one is ready, then the clip's draft post is updated. A clip whose post is scheduled or published cannot be changed; unschedule it first."""
        return await act("change_clip_design", clip_id=clip_id, design_id=design_id, idempotency_key=idempotency_key)

    @server.tool(annotations=WRITE)
    async def update_clip_text(clip_id: ClipId, text: Caption, idempotency_key: UUID) -> dict:
        """Change the post text that goes with a clip. If the clip already has a post in Scheduling, that post's text is changed (a scheduled post keeps its time)."""
        return await act("update_clip_text", clip_id=clip_id, text=text, idempotency_key=idempotency_key)

    @server.tool(annotations=WRITE)
    async def send_clip_to_scheduling(clip_id: ClipId, idempotency_key: UUID) -> dict:
        """Create the clip's draft post in Scheduling, or bring back the draft if it was deleted. Does not schedule or publish it."""
        return await act("send_clip_to_scheduling", clip_id=clip_id, idempotency_key=idempotency_key)

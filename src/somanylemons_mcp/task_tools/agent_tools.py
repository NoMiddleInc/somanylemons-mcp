"""Recording Agent and Scheduling Agent tools.

Each tool is a typed front for one backend action at /api/v1/agent-tasks/agent-tools/{name}.
The backend owns every rule (what may be edited when, network limits, who may act); nothing
here decides anything beyond the shape of the arguments.
"""
import asyncio
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
AccountId = Annotated[int, Field(gt=0, description="Only for a teammate who was added to more than one account: the id of the account to work in. Leave out otherwise.")]
Confirmation = Annotated[str, Field(max_length=64, description="Leave out on the first call. The first call does nothing and returns will_happen and a confirmation value. After the user has read will_happen and clearly said yes, call again with the same arguments and that value.")]
ListId = Annotated[int, Field(gt=0, description="A campaign_id from list_golden_lists. Leave out to use the account's main golden list.")]
PostStatus = Literal["draft", "queued", "approved", "scheduled", "posted", "publishing", "failed", "missed", "cancelled", "rejected", "pending_approval", "changes_requested"]

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
PUBLIC = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)

RECORDING_POLL_SECONDS = 5

AGENT_TOOL_NAMES = frozenset({
    "get_posting_overview", "list_posts", "get_post", "create_post", "update_post", "schedule_post",
    "unschedule_post", "publish_post_now", "delete_post", "copy_post_to_network",
    "list_recordings", "get_recording", "add_recording", "recut_recording", "retry_recording",
    "remove_recording", "list_recording_designs", "set_default_recording_design",
    "change_clip_design", "update_clip_text", "send_clip_to_scheduling",
    "start_browser_recording", "wait_for_recording",
    "get_email_sequence", "pause_email_agent",
})

INSTRUCTIONS = (
    " Posts and recordings: the account's Scheduling Agent holds its social posts (drafts, scheduled, published) and its Recording Agent turns a recording into short clips, each handed to Scheduling as a draft post. "
    "Start with list_posts or list_recordings to get real ids; never guess an id. To change a post's text, network or media use update_post, which keeps a scheduled post's time. To move a post use schedule_post with the new time. "
    "Write times as local date and time (2026-10-14T09:00) unless the user gave a timezone. Use next_open_slot only when the user asks for the queue or the next available slot. "
    "publish_post_now makes a post public immediately, and delete_post and remove_recording remove the user's work: call them only when the user asked for exactly that. "
    "Nothing is published, scheduled, deleted or removed on a first call. The result has needs_confirmation, will_happen and confirmation: show will_happen to the user word for word (which post, which account, the exact day, time and timezone), ask for a clear yes, and only then call the same tool again with the same arguments plus confirmation. Never send confirmation the user has not given in this conversation, and if they change anything, start again without it. "
    "A teammate works in the account owner's account: results then carry acting_in_account, so say whose account it is. If a tool says the person works in more than one account, ask which and pass account_id. "
    "Every result reports what actually happened. If a result has a non-empty not_done list, tell the user what was not done and why; never describe the request as complete. "
    "A tool error saying the agent is not on the account means the user does not have that agent; say so plainly and do not retry. "
    "Email Agent: get_email_sequence reads the emails a list sends and its sending counts, and pause_email_agent stops sending; pausing is always safe. Starting the Email Agent, editing its emails and enrolling people are done in ProducerSpark in the browser, never from here. "
    "Recording a new video from a camera happens in ProducerSpark in the browser. When the user wants to record, call start_browser_recording, give them its record_url as a link to open, and then call wait_for_recording with its started_at, repeating while the status is waiting; do not ask them to tell you when they are done. Once it is found, follow it with get_recording until the clips are ready. add_recording brings in an existing video or audio file by direct link instead."
)


def register(server, api) -> None:
    """Add the Recording and Scheduling tools to the task server."""

    async def act(name: str, timeout: float = 60.0, **arguments):
        body = {key: (str(value) if isinstance(value, UUID) else value) for key, value in arguments.items() if value is not None}
        return await api.request("POST", f"/api/v1/agent-tasks/agent-tools/{name}", body=body, timeout_seconds=timeout)

    @server.tool(annotations=READ)
    async def get_posting_overview(account_id: AccountId | None = None) -> dict:
        """Show where this account can post (connected LinkedIn and X accounts), its timezone and posts per week, how many posts are scheduled, in drafts or failed, and the next open slots. Read-only."""
        return await act("get_posting_overview", account_id=account_id)

    @server.tool(annotations=READ)
    async def list_posts(status: PostStatus | None = None, network: Network | None = None, deleted: bool = False, page: Annotated[int, Field(ge=1)] = 1, account_id: AccountId | None = None) -> dict:
        """List the account's social posts, 30 per page, with id, status, network, text, scheduled time and media. Filter by status (draft, scheduled, posted, failed...) or network; deleted=true lists removed posts. Media links expire after a few minutes. Read-only."""
        return await act("list_posts", status=status, network=network, deleted=deleted or None, page=page, account_id=account_id)

    @server.tool(annotations=READ)
    async def get_post(post_id: PostId, account_id: AccountId | None = None) -> dict:
        """Read one post: status, network, text, scheduled time, media and any problem blocking it. Read-only."""
        return await act("get_post", post_id=post_id, account_id=account_id)

    @server.tool(annotations=WRITE)
    async def create_post(idempotency_key: UUID, caption: Caption = "", network: Network | None = None, account: Account | None = None, media_urls: MediaUrls | None = None, scheduled_for: When | None = None, timezone: Zone | None = None, next_open_slot: bool = False, confirmation: Confirmation | None = None, account_id: AccountId | None = None) -> dict:
        """Create a social post as a draft. Optionally attach media from direct file links, and schedule it for a time or the next open slot in the same call. Without a time it stays an unscheduled draft and nothing is published. With a time, the first call only returns needs_confirmation and will_happen (where and exactly when it will post): show that to the user, and call again with confirmation after they say yes. If not_done is non-empty, the draft was saved but that step did not happen."""
        return await act("create_post", timeout=180.0, idempotency_key=idempotency_key, caption=caption, network=network, account=account, media_urls=media_urls, scheduled_for=scheduled_for, timezone=timezone, next_open_slot=next_open_slot or None, account_id=account_id, confirmation=confirmation)

    @server.tool(annotations=WRITE)
    async def update_post(post_id: PostId, idempotency_key: UUID, caption: Caption | None = None, network: Network | None = None, account: Account | None = None, media_urls: MediaUrls | None = None, remove_media: bool = False, account_id: AccountId | None = None) -> dict:
        """Change a post's text, network or media. A scheduled post keeps its scheduled time. media_urls replaces all current media; remove_media=true removes it. A published post cannot be changed. If not_done is non-empty, read it: the post may now be an unscheduled draft."""
        return await act("update_post", timeout=180.0, post_id=post_id, idempotency_key=idempotency_key, caption=caption, network=network, account=account, media_urls=media_urls, remove_media=remove_media or None, account_id=account_id)

    @server.tool(annotations=WRITE)
    async def schedule_post(post_id: PostId, idempotency_key: UUID, scheduled_for: When | None = None, timezone: Zone | None = None, next_open_slot: bool = False, confirmation: Confirmation | None = None, account_id: AccountId | None = None) -> dict:
        """Schedule a draft, or move an already scheduled post to a new time (reschedule). Give scheduled_for, or set next_open_slot=true to take the account's next free posting slot. The post publishes automatically at that time. The first call schedules nothing: it returns needs_confirmation and will_happen, stating the account, weekday, date, time and timezone. Show will_happen to the user word for word; call again with confirmation only after they say yes."""
        return await act("schedule_post", post_id=post_id, idempotency_key=idempotency_key, scheduled_for=scheduled_for, timezone=timezone, next_open_slot=next_open_slot or None, account_id=account_id, confirmation=confirmation)

    @server.tool(annotations=WRITE)
    async def unschedule_post(post_id: PostId, idempotency_key: UUID, account_id: AccountId | None = None) -> dict:
        """Take a post off the schedule. It becomes a draft and keeps its text and media."""
        return await act("unschedule_post", post_id=post_id, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(annotations=PUBLIC)
    async def publish_post_now(post_id: PostId, idempotency_key: UUID, confirmation: Confirmation | None = None, account_id: AccountId | None = None) -> dict:
        """Publish a post to its network right now. This is public and cannot be undone from here. Use only when the user explicitly asked to publish this post now. The first call publishes nothing: it returns needs_confirmation and will_happen. Show will_happen to the user word for word; call again with confirmation only after they say yes."""
        return await act("publish_post_now", post_id=post_id, idempotency_key=idempotency_key, account_id=account_id, confirmation=confirmation)

    @server.tool(annotations=PUBLIC)
    async def delete_post(post_id: PostId, idempotency_key: UUID, confirmation: Confirmation | None = None, account_id: AccountId | None = None) -> dict:
        """Delete a post from drafts and the calendar. A post already published stays on the network. Use only when the user asked to delete or remove this post. The first call deletes nothing: it returns needs_confirmation and will_happen. Call again with confirmation only after the user says yes."""
        return await act("delete_post", post_id=post_id, idempotency_key=idempotency_key, account_id=account_id, confirmation=confirmation)

    @server.tool(annotations=WRITE)
    async def copy_post_to_network(post_id: PostId, network: Network, idempotency_key: UUID, account: Account | None = None, account_id: AccountId | None = None) -> dict:
        """Make a copy of an unscheduled draft for another network (LinkedIn or X) as its own draft, sharing the same media. Schedule the copy separately."""
        return await act("copy_post_to_network", post_id=post_id, network=network, account=account, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(annotations=READ)
    async def list_recordings(account_id: AccountId | None = None) -> dict:
        """List the account's recent recordings with status and the clips each produced: clip id, title, post text, video link, design, and the id and status of the clip's post in Scheduling. Read-only."""
        return await act("list_recordings", account_id=account_id)

    @server.tool(annotations=READ)
    async def get_recording(recording_id: RecordingId, account_id: AccountId | None = None) -> dict:
        """Read one recording and its clips with their live scheduling status. Read-only."""
        return await act("get_recording", recording_id=recording_id, account_id=account_id)

    @server.tool(annotations=WRITE)
    async def add_recording(media_url: Annotated[str, Field(max_length=2000, description="Public https link that points directly at a video or audio file, up to 1 GB.")], idempotency_key: UUID, title: Annotated[str, Field(max_length=255)] = "", instruction: Annotated[str, Field(max_length=4000, description="Optional guidance for the edit, such as which topics to cut clips from.")] = "", account_id: AccountId | None = None) -> dict:
        """Bring in an existing video or audio file by direct link as a new recording and start editing it into clips. Counts against the account's recording allowance. Editing takes several minutes; check progress with get_recording."""
        return await act("add_recording", timeout=300.0, media_url=media_url, title=title or None, instruction=instruction or None, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(annotations=WRITE)
    async def recut_recording(recording_id: RecordingId, instruction: Annotated[str, Field(min_length=1, max_length=4000)], idempotency_key: UUID, account_id: AccountId | None = None) -> dict:
        """Cut a fresh set of clips from a recording following a new instruction (for example different topics or a different focus). Existing clips and their posts are kept. Counts against the account's edited-clip allowance."""
        return await act("recut_recording", recording_id=recording_id, instruction=instruction, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(annotations=WRITE)
    async def retry_recording(recording_id: RecordingId, idempotency_key: UUID, account_id: AccountId | None = None) -> dict:
        """Run a recording again after it failed or stalled."""
        return await act("retry_recording", recording_id=recording_id, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(annotations=PUBLIC)
    async def remove_recording(recording_id: RecordingId, idempotency_key: UUID, confirmation: Confirmation | None = None, account_id: AccountId | None = None) -> dict:
        """Remove a recording and stop any editing in progress. Posts already created from its clips stay in Scheduling. Use only when the user asked to remove this recording. The first call removes nothing: it returns needs_confirmation and will_happen. Call again with confirmation only after the user says yes."""
        return await act("remove_recording", recording_id=recording_id, idempotency_key=idempotency_key, account_id=account_id, confirmation=confirmation)

    @server.tool(annotations=READ)
    async def start_browser_recording(account_id: AccountId | None = None) -> dict:
        """Start a new camera or microphone recording: returns record_url, the ProducerSpark recorder page for the user to open in their browser, and started_at. Show record_url as a link, then call wait_for_recording with started_at so the new recording is picked up without the user reporting back. Nothing is recorded or changed by this call."""
        return await act("start_browser_recording", account_id=account_id)

    @server.tool(annotations=READ)
    async def wait_for_recording(since: Annotated[str, Field(max_length=40, description="The started_at value from start_browser_recording.")], timeout_seconds: Annotated[int, Field(ge=0, le=50)] = 45, account_id: AccountId | None = None) -> dict:
        """Wait for the recording the user is making in the browser. Returns status found with the recording as soon as one made after since exists, or status waiting when the window ends: call again while the user is still recording, and stop if they say they are not going to. A found recording may still be editing; follow it with get_recording. Read-only."""
        for check in range(timeout_seconds // RECORDING_POLL_SECONDS + 1):
            if check:
                await asyncio.sleep(RECORDING_POLL_SECONDS)
            result = await act("wait_for_recording", since=since, account_id=account_id)
            if result.get("status") != "waiting":
                break
        return result

    @server.tool(annotations=READ)
    async def list_recording_designs(account_id: AccountId | None = None) -> dict:
        """List the designs (templates) available for clips, for video and for audio recordings, and which one new recordings use by default. Read-only."""
        return await act("list_recording_designs", account_id=account_id)

    @server.tool(annotations=WRITE)
    async def set_default_recording_design(design_id: DesignId, idempotency_key: UUID, account_id: AccountId | None = None) -> dict:
        """Choose the design new recordings will use. Existing clips are not changed."""
        return await act("set_default_recording_design", design_id=design_id, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(annotations=WRITE)
    async def change_clip_design(clip_id: ClipId, design_id: DesignId, idempotency_key: UUID, confirmation: Confirmation | None = None, account_id: AccountId | None = None) -> dict:
        """Re-render one clip in a different design (template) after recording. The current video stays in place until the new one is ready, then the clip's draft post is updated. If the clip's post is scheduled, the first call changes nothing and returns needs_confirmation and will_happen, because the post comes off the schedule: show will_happen, and call again with confirmation after the user says yes. The result's unscheduled_post gives the time it had; when get_recording shows the new video is ready, offer to schedule it again. A published clip cannot be changed."""
        return await act("change_clip_design", clip_id=clip_id, design_id=design_id, idempotency_key=idempotency_key, account_id=account_id, confirmation=confirmation)

    @server.tool(annotations=WRITE)
    async def update_clip_text(clip_id: ClipId, text: Caption, idempotency_key: UUID, account_id: AccountId | None = None) -> dict:
        """Change the post text that goes with a clip. If the clip already has a post in Scheduling, that post's text is changed (a scheduled post keeps its time)."""
        return await act("update_clip_text", clip_id=clip_id, text=text, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(annotations=WRITE)
    async def send_clip_to_scheduling(clip_id: ClipId, idempotency_key: UUID, account_id: AccountId | None = None) -> dict:
        """Create the clip's draft post in Scheduling, or bring back the draft if it was deleted. Does not schedule or publish it."""
        return await act("send_clip_to_scheduling", clip_id=clip_id, idempotency_key=idempotency_key, account_id=account_id)

    @server.tool(annotations=READ)
    async def get_email_sequence(campaign_id: ListId | None = None, account_id: AccountId | None = None) -> dict:
        """Read the Email Agent's sequence for a golden list: each email's day, subject and body in order, plus whether sending is on and the enrolled, sent and replied counts. Read-only."""
        return await act("get_email_sequence", campaign_id=campaign_id, account_id=account_id)

    @server.tool(annotations=WRITE)
    async def pause_email_agent(idempotency_key: UUID, campaign_id: ListId | None = None, account_id: AccountId | None = None) -> dict:
        """Stop the Email Agent sending for a golden list. Enrolled people keep their place. Nothing is sent or deleted. Sending can only be switched back on in ProducerSpark in the browser, so tell the user that."""
        return await act("pause_email_agent", campaign_id=campaign_id, idempotency_key=idempotency_key, account_id=account_id)

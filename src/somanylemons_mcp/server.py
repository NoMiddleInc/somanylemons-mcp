#!/usr/bin/env python3
"""
SoManyLemons MCP Server

Model Context Protocol server that wraps the SML Public API.
Provides tools for AI agents to create branded video reels, generate content,
extract quotes, and more.

Usage:
    sml-mcp --api-key sml_xxxxx
    sml-mcp --api-url https://api.somanylemons.com --api-key sml_xxxxx

Or via environment variables:
    SML_API_KEY=sml_xxxxx sml-mcp
"""

import argparse
import json
import os
import sys

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print(
        "Error: mcp package not installed. Install with:\n"
        "  pip install somanylemons-mcp\n",
        file=sys.stderr,
    )
    sys.exit(1)

import contextvars

import httpx

API_URL = os.environ.get("SML_API_URL", "https://api.somanylemons.com")
API_KEY = os.environ.get("SML_API_KEY", "")

# Per-session API key for the remote (multi-tenant) server.
# Each SSE connection sets its own key via contextvars so concurrent
# sessions never share or overwrite each other's credentials.
_session_api_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_api_key", default=""
)

# Set to True by remote.py at startup. When True, tools that read from the
# local filesystem (upload_file, file_path uploads) are explicitly rejected,
# because the "local filesystem" of a hosted server is not the user's machine.
REMOTE_MODE = False

# Tools that require local filesystem access. These ONLY work in stdio mode.
_LOCAL_FS_TOOLS = frozenset({"upload_file"})

CAPTION_STYLES = [
    "LEMON", "VITAMIN_C", "PLAIN", "SPOTLIGHT",
    "GLITCH", "RANSOM", "WAVE", "BOUNCE",
]


def get_headers():
    key = _session_api_key.get() or API_KEY
    return {
        "X-API-Key": key,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Security: sanitize error responses before returning to MCP consumer
# ---------------------------------------------------------------------------

# Keys whose values could contain secrets if the backend leaks them in errors.
_SENSITIVE_KEYS = frozenset({
    "authorization", "x-api-key", "api_key", "apikey", "token",
    "secret", "password", "cookie", "set-cookie",
})


def _sanitize_response(data):
    """Remove fields that could contain API keys or auth headers from error
    responses. Operates recursively on dicts and lists."""
    if isinstance(data, dict):
        return {
            k: "(redacted)" if k.lower() in _SENSITIVE_KEYS else _sanitize_response(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_sanitize_response(item) for item in data]
    if isinstance(data, str) and data.startswith("sml_") and len(data) > 20:
        return data[:8] + "…(redacted)"
    return data


# ---------------------------------------------------------------------------
# Helper: generic API call -> MCP TextContent
# ---------------------------------------------------------------------------

async def _api_call(method, path, payload=None, params=None, timeout=30):
    """Make an API call and return MCP TextContent with the result."""
    url = f"{API_URL}{path}"
    async with httpx.AsyncClient() as client:
        if method == "GET":
            resp = await client.get(url, headers=get_headers(), params=params, timeout=timeout)
        elif method == "PUT":
            resp = await client.put(url, json=payload or {}, headers=get_headers(), timeout=timeout)
        elif method == "DELETE":
            resp = await client.delete(url, headers=get_headers(), params=params, timeout=timeout)
        else:
            resp = await client.post(url, json=payload or {}, headers=get_headers(), timeout=timeout)

    # DELETE with 204 No Content
    if resp.status_code == 204:
        return [TextContent(type="text", text=json.dumps({"deleted": True}, indent=2))]

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text}

    if resp.status_code >= 400:
        return [TextContent(type="text", text=json.dumps({
            "error": True,
            "status_code": resp.status_code,
            "detail": _sanitize_response(data),
        }, indent=2))]

    return [TextContent(type="text", text=json.dumps(data, indent=2))]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

server = Server("somanylemons")


@server.list_tools()
async def list_tools():
    return [
        # --- Content Creation (async) ---
        Tool(
            name="create_reels",
            description=(
                "Turn a recording into branded, captioned short-form video reels. "
                "Submit a URL to a video or audio file. Returns a job ID to poll. "
                "The pipeline transcribes, extracts the best moments, and renders "
                "captioned clips with your brand styling. Typical time: 2-5 minutes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Public URL of the recording (video or audio). Required unless file_path is provided.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Local file path to upload directly. Use this instead of url for local files.",
                    },
                    "brand_profile_id": {
                        "type": "integer",
                        "description": "Brand profile ID for styling. Use list_brands to see options.",
                    },
                    "caption_style": {
                        "type": "string",
                        "description": "Caption animation style",
                        "enum": CAPTION_STYLES,
                    },
                    "webhook_url": {
                        "type": "string",
                        "description": "URL to POST when processing completes or fails",
                    },
                },
            },
        ),
        Tool(
            name="check_job_status",
            description=(
                "Check the status of a reels creation job. Returns progress (0-100), "
                "status (pending/processing/completed/failed), and download URLs when done."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Job ID returned by create_reels",
                    },
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="create_upload_session",
            description=(
                "Create a resumable direct-to-cloud upload session for a local video or audio file. "
                "Upload the file bytes to the returned session_uri, then pass full_url into create_reels."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Original filename including extension (e.g. 'podcast.mp4')",
                    },
                    "content_type": {
                        "type": "string",
                        "description": "MIME type (e.g. 'video/mp4')",
                    },
                    "file_size": {
                        "type": "integer",
                        "description": "Optional file size in bytes",
                    },
                },
                "required": ["filename", "content_type"],
            },
        ),
        Tool(
            name="check_upload_status",
            description=(
                "Check progress for a resumable upload session. "
                "Use this if a direct upload was interrupted and you need the resume point."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_uri": {
                        "type": "string",
                        "description": "The resumable upload session URI returned by create_upload_session",
                    },
                },
                "required": ["session_uri"],
            },
        ),

        # --- Simple file upload ---
        Tool(
            name="upload_file",
            description=(
                "Upload a file (image, video, or audio, max 50 MB) to cloud storage "
                "and get a public URL. Use this to upload logos, headshots, or short "
                "recordings before passing the URL to create_reels or create_brand."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Local file path to upload",
                    },
                },
                "required": ["file_path"],
            },
        ),

        # --- Transcription ---
        Tool(
            name="transcribe",
            description=(
                "Transcribe a video or audio recording with word-level timestamps. "
                "Returns a session token for polling transcription status. "
                "Useful for previewing content before creating reels."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Public URL of the media to transcribe. Required unless file_path is provided.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Local file path to upload and transcribe directly.",
                    },
                },
            },
        ),

        # --- Content Writing ---
        Tool(
            name="generate_content",
            description=(
                "Generate a LinkedIn post from a topic. Returns ready-to-publish text "
                "with a strong hook, short paragraphs, and a call to action."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic or idea for the post (e.g., 'AI in healthcare')",
                    },
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="score_content",
            description=(
                "Score a LinkedIn post draft for engagement potential. Returns AI + heuristic "
                "scores (0-100), specific feedback, predicted view range, and strengths."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "post_text": {
                        "type": "string",
                        "description": "The LinkedIn post text to score (min 50 characters)",
                    },
                },
                "required": ["post_text"],
            },
        ),
        Tool(
            name="rewrite_content",
            description=(
                "AI-rewrite a LinkedIn post to improve engagement while keeping "
                "the author's voice. Optionally provide feedback to address."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "post_text": {
                        "type": "string",
                        "description": "The original post text to rewrite",
                    },
                    "feedback": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific feedback to incorporate in the rewrite",
                    },
                },
                "required": ["post_text"],
            },
        ),

        # --- Quote Extraction ---
        Tool(
            name="extract_quotes",
            description=(
                "Extract the most quotable, share-worthy lines from text. "
                "Scores each quote on Voice, Substance, and Completeness (Squeeze Score /50). "
                "Great for finding image quote material from transcripts or articles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to extract quotes from (transcript, article, blog post). Min 20 words.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of quotes to extract (default 8)",
                    },
                },
                "required": ["text"],
            },
        ),

        # --- Discovery ---
        Tool(
            name="list_templates",
            description="List available video and image templates with their styles and dimensions.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="list_brands",
            description=(
                "List brand profiles for your organization. Returns brand names, "
                "colors, logos. Use the ID when creating reels to apply branding."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="create_brand",
            description=(
                "Create a brand profile with your colors and styling. "
                "This should be the FIRST thing you do after getting an API key. "
                "Your brand colors will be applied to all rendered reels and content."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Brand name (e.g., 'Acme Corp')",
                    },
                    "primary_color": {
                        "type": "string",
                        "description": "Primary brand color as hex (e.g., '#1a73e8')",
                    },
                    "secondary_color": {
                        "type": "string",
                        "description": "Secondary brand color as hex (e.g., '#ffffff')",
                    },
                    "accent_color": {
                        "type": "string",
                        "description": "Accent color for highlights (optional, default '#FFD700')",
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Background color (optional, default '#FFFFFF')",
                    },
                    "text_color": {
                        "type": "string",
                        "description": "Text color (optional, default '#000000')",
                    },
                    "font_family": {
                        "type": "string",
                        "description": "Font family name (optional, default 'Arial')",
                    },
                    "logo_url": {
                        "type": "string",
                        "description": "Logo image URL. Upload via upload_file first if you have a local file.",
                    },
                },
                "required": ["name", "primary_color", "secondary_color"],
            },
        ),
        Tool(
            name="update_brand",
            description=(
                "Update an existing brand profile. Pass only the fields you want to change. "
                "Use list_brands to get the brand ID first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Brand profile ID to update",
                    },
                    "name": {
                        "type": "string",
                        "description": "Updated brand name",
                    },
                    "primary_color": {
                        "type": "string",
                        "description": "Updated primary color as hex",
                    },
                    "secondary_color": {
                        "type": "string",
                        "description": "Updated secondary color as hex",
                    },
                    "accent_color": {
                        "type": "string",
                        "description": "Updated accent color as hex",
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Updated background color as hex",
                    },
                    "text_color": {
                        "type": "string",
                        "description": "Updated text color as hex",
                    },
                    "font_family": {
                        "type": "string",
                        "description": "Updated font family name",
                    },
                    "logo_url": {
                        "type": "string",
                        "description": "Updated logo URL",
                    },
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="delete_brand",
            description=(
                "Delete a brand profile. This cannot be undone. "
                "Use list_brands to get the brand ID first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Brand profile ID to delete",
                    },
                },
                "required": ["id"],
            },
        ),

        # --- Drafts ---
        Tool(
            name="create_draft",
            description=(
                "Create a draft post in the content queue. Send a caption for a text-only "
                "LinkedIn draft, or include a job_id from create_reels to attach rendered media. "
                "To attach an image quote, first call create_image_quote with draft_id. "
                "Drafts appear in the queue and can be scheduled for posting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "caption": {
                        "type": "string",
                        "description": "Post caption / body text",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Optional render job ID from create_reels to attach rendered media",
                    },
                },
                "required": ["caption"],
            },
        ),
        Tool(
            name="list_drafts",
            description=(
                "List draft posts in your content queue. Returns captions, media URLs, "
                "engagement scores, and status. Optionally filter by status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status (draft, queued, scheduled, posted). Default: all.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20, max 100)",
                    },
                },
            },
        ),
        Tool(
            name="update_draft",
            description=(
                "Update an existing draft. Change the caption, attach media, "
                "or update status. Use list_drafts to get the draft ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Draft ID to update",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Updated caption text",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Render job ID to attach or replace media",
                    },
                    "status": {
                        "type": "string",
                        "description": "Move draft to a new status (draft, queued)",
                        "enum": ["draft", "queued"],
                    },
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="delete_draft",
            description=(
                "Delete a draft from the content queue. This cannot be undone. "
                "Use list_drafts to get the draft ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Draft ID to delete",
                    },
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="schedule_draft",
            description=(
                "Schedule a draft for publishing at a specific date and time. "
                "The draft must exist. Use list_drafts to find draft IDs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Draft ID to schedule",
                    },
                    "scheduled_at": {
                        "type": "string",
                        "description": "ISO 8601 datetime for publishing (e.g. '2026-04-10T09:00:00Z')",
                    },
                },
                "required": ["id", "scheduled_at"],
            },
        ),
        Tool(
            name="duplicate_draft",
            description=(
                "Duplicate an existing draft to use as a starting point. "
                "Creates a new draft with the same caption and media. "
                "Useful for creating variations of high-scoring posts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Draft ID to duplicate",
                    },
                },
                "required": ["id"],
            },
        ),

        # --- Jobs ---
        Tool(
            name="list_jobs",
            description=(
                "List your recent render jobs. Returns job IDs, status, progress, "
                "and download URLs for completed jobs. Useful when you've lost a job ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status: pending, processing, completed, failed. Default: all.",
                        "enum": ["pending", "processing", "completed", "failed"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20, max 100)",
                    },
                    "owner": {
                        "type": "string",
                        "description": "Scope filter. 'me' returns only recordings uploaded by the API key's user. 'org' (default) returns all recordings from the organization.",
                        "enum": ["me", "org"],
                    },
                    "source": {
                        "type": "string",
                        "description": "Filter by origin. 'web' = uploaded via web app, 'api' = created via this API/MCP, 'all' (default) = both.",
                        "enum": ["web", "api", "all"],
                    },
                },
            },
        ),

        # --- Plans ---
        Tool(
            name="list_plans",
            description=(
                "List available API key tier plans with pricing and features. "
                "Shows free, pro, agency, and enterprise tiers."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        # --- Image Quotes ---
        Tool(
            name="create_image_quote",
            description=(
                "Render a branded image quote from text. Picks a template, applies "
                "your brand colors, and returns a download URL. Optionally attach "
                "the result to an existing draft by passing draft_id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "quote_text": {
                        "type": "string",
                        "description": "The quote text to render on the image.",
                    },
                    "brand_profile_id": {
                        "type": "integer",
                        "description": "Brand profile ID for styling. Uses default if omitted.",
                    },
                    "speaker_name": {
                        "type": "string",
                        "description": "Speaker name to display on the image.",
                    },
                    "speaker_title": {
                        "type": "string",
                        "description": "Speaker title/role to display on the image.",
                    },
                    "size": {
                        "type": "string",
                        "description": "Image size (default: square)",
                        "enum": ["square", "portrait", "horizontal"],
                    },
                    "draft_id": {
                        "type": "integer",
                        "description": "Optional draft ID to attach the rendered image to.",
                    },
                },
                "required": ["quote_text"],
            },
        ),

        # --- Usage ---
        Tool(
            name="get_usage",
            description=(
                "Check your current API usage for this billing period. "
                "Returns render count, limits, tier, and remaining quota."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        # --- Account ---
        Tool(
            name="get_account",
            description=(
                "Return the identity of the currently connected account. "
                "Shows email, organization name, plan/tier, and a masked "
                "API key prefix. Use this to verify which account is active."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="update_account",
            description=(
                "Update account profile settings like organization name "
                "or notification preferences."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "organization": {
                        "type": "string",
                        "description": "Organization or company name",
                    },
                    "name": {
                        "type": "string",
                        "description": "Display name",
                    },
                },
            },
        ),

        # --- Search ---
        Tool(
            name="search_transcripts",
            description=(
                "Search across all your transcripts by keyword or topic. "
                "Returns matching excerpts with job IDs and timestamps. "
                "Use this to find what someone said about a specific topic."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keyword, phrase, or topic)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                    },
                },
                "required": ["query"],
            },
        ),

        # --- Downloads ---
        Tool(
            name="download_clip",
            description=(
                "Get a direct download URL for a specific rendered clip from a completed job. "
                "Use check_job_status to see available clips first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The render job ID",
                    },
                    "clip_index": {
                        "type": "integer",
                        "description": "Index of the clip to download (0-based). Default: 0 (first clip).",
                    },
                },
                "required": ["job_id"],
            },
        ),

    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

TOOL_ROUTES = {
    # Content creation
    "create_reels": ("POST", "/api/v1/clip"),
    "check_job_status": ("GET", "/api/v1/clip/{id}"),
    "create_image_quote": ("POST", "/api/v1/image-quote"),
    "transcribe": ("POST", "/api/v1/transcribe"),
    # Content writing
    "generate_content": ("POST", "/api/v1/write/generate"),
    "score_content": ("POST", "/api/v1/write/score"),
    "rewrite_content": ("POST", "/api/v1/write/rewrite"),
    "extract_quotes": ("POST", "/api/v1/extract-clips"),
    # Uploads
    "create_upload_session": ("POST", "/api/v1/uploads/resumable"),
    "check_upload_status": ("POST", "/api/v1/uploads/resumable/status"),
    # Brands
    "list_brands": ("GET", "/api/v1/brands"),
    "create_brand": ("POST", "/api/v1/brands"),
    "update_brand": ("PUT", "/api/v1/brands/{id}"),
    "delete_brand": ("DELETE", "/api/v1/brands/{id}"),
    # Drafts
    "create_draft": ("POST", "/api/v1/drafts"),
    "list_drafts": ("GET", "/api/v1/drafts"),
    "update_draft": ("PUT", "/api/v1/drafts/{id}"),
    "delete_draft": ("DELETE", "/api/v1/drafts/{id}"),
    "schedule_draft": ("POST", "/api/v1/drafts/{id}/schedule"),
    "duplicate_draft": ("POST", "/api/v1/drafts/{id}/duplicate"),
    # Jobs
    "list_jobs": ("GET", "/api/v1/jobs"),
    "download_clip": ("GET", "/api/v1/clip/{job_id}/download"),
    # Discovery
    "list_templates": ("GET", "/api/v1/templates"),
    "list_plans": ("GET", "/api/v1/developer/plans/"),
    # Account & usage
    "get_usage": ("GET", "/api/v1/usage"),
    "get_account": ("GET", "/api/v1/account"),
    "update_account": ("PUT", "/api/v1/account"),
    # Search
    "search_transcripts": ("GET", "/api/v1/transcripts/search"),
}


async def _multipart_post(endpoint: str, file_path: str, extra_fields: dict = None, timeout: int = 120):
    """Upload a local file via multipart POST to any endpoint."""
    import mimetypes

    if not os.path.isfile(file_path):
        return [TextContent(type="text", text=json.dumps({
            "error": True,
            "detail": f"File not found: {file_path}",
        }, indent=2))]

    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    url = f"{API_URL}{endpoint}"

    data_fields = {}
    if extra_fields:
        for k, v in extra_fields.items():
            if v is not None:
                data_fields[k] = str(v) if not isinstance(v, str) else v

    async with httpx.AsyncClient() as client:
        with open(file_path, "rb") as f:
            resp = await client.post(
                url,
                headers={"X-API-Key": API_KEY},
                files={"file": (filename, f, content_type)},
                data=data_fields,
                timeout=timeout,
            )

    resp_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text}

    if resp.status_code >= 400:
        return [TextContent(type="text", text=json.dumps({
            "error": True,
            "status_code": resp.status_code,
            "detail": resp_data,
        }, indent=2))]

    return [TextContent(type="text", text=json.dumps(resp_data, indent=2))]


async def _upload_file(file_path: str):
    """Upload a local file via multipart POST to /api/v1/upload."""
    return await _multipart_post("/api/v1/upload", file_path)


def _reject_local_fs_tool(tool_name: str) -> list:
    """Return an explicit error TextContent for tools that need a local filesystem.

    These tools cannot work over a hosted MCP server: the file_path the user
    provides refers to *their* machine, not the server's. The user must either
    use a public URL, or run the MCP server locally via `pip install
    somanylemons-mcp` + stdio transport.
    """
    return [TextContent(type="text", text=json.dumps({
        "error": True,
        "code": "local_filesystem_unavailable",
        "detail": (
            f"The '{tool_name}' tool reads from your local filesystem and is "
            "only available when running the MCP server locally (stdio mode). "
            "On the hosted server (mcp.somanylemons.com), pass a public URL "
            "instead, or install the MCP server locally with "
            "'pip install somanylemons-mcp' and configure it as a stdio server."
        ),
    }, indent=2))]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    # In remote (hosted) mode, reject tools that require local filesystem access.
    if REMOTE_MODE and name in _LOCAL_FS_TOOLS:
        return _reject_local_fs_tool(name)
    if REMOTE_MODE and name in {"create_reels", "transcribe"} and arguments.get("file_path"):
        return _reject_local_fs_tool(f"{name} (file_path argument)")

    # Special handler for file uploads (multipart, not JSON)
    if name == "upload_file":
        return await _upload_file(arguments.get("file_path", ""))

    # create_reels with file_path: multipart POST directly to /api/v1/clip
    if name == "create_reels" and arguments.get("file_path"):
        file_path = arguments.pop("file_path")
        return await _multipart_post("/api/v1/clip", file_path, extra_fields=arguments)

    # transcribe with file_path: multipart POST directly to /api/v1/transcribe
    if name == "transcribe" and arguments.get("file_path"):
        file_path = arguments.pop("file_path")
        return await _multipart_post("/api/v1/transcribe", file_path, extra_fields=arguments)

    route = TOOL_ROUTES.get(name)
    if not route:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    method, path_template = route

    # Handle path parameters (e.g., {id}, {job_id})
    path = path_template
    if "{id}" in path:
        path = path.replace("{id}", str(arguments.pop("id", "")))
    if "{job_id}" in path:
        path = path.replace("{job_id}", str(arguments.pop("job_id", "")))

    # Rendering endpoints need longer timeouts (image/video generation)
    _SLOW_TOOLS = {"create_image_quote", "create_reels", "transcribe"}
    timeout = 120 if name in _SLOW_TOOLS else 30

    if method == "GET":
        return await _api_call("GET", path, params=arguments if arguments else None, timeout=timeout)
    elif method == "DELETE":
        return await _api_call("DELETE", path, timeout=timeout)
    elif method == "PUT":
        return await _api_call("PUT", path, payload=arguments, timeout=timeout)
    else:
        return await _api_call("POST", path, payload=arguments, timeout=timeout)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    global API_URL, API_KEY

    parser = argparse.ArgumentParser(description="SoManyLemons MCP Server")
    parser.add_argument("--api-url", default=API_URL, help="Base URL of the SML API")
    parser.add_argument("--api-key", default=API_KEY, help="API key (sml_xxxxx)")
    args = parser.parse_args()

    API_URL = args.api_url.rstrip("/")
    API_KEY = args.api_key

    if not API_KEY:
        print("Error: API key required. Set SML_API_KEY or use --api-key", file=sys.stderr)
        sys.exit(1)

    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()

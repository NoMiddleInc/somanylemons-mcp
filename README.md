# ProducerSpark MCP

AI-powered content marketing via [Model Context Protocol](https://modelcontextprotocol.io). Create branded video reels, LinkedIn posts, image quotes, and more — just type `/lemons`.

## Browser research connection

See [Claude and ChatGPT browser setup](docs/browser-setup.md) for the account sign-in flow and first read-only test. The initial pilot uses a custom connection; public directory installation is a separate publication step. Use `https://producerspark.com/mcp`, sign in to ProducerSpark, then ask: "Use ProducerSpark to download my prospect list as Excel." No local package or copied API key is needed.

## Install (one line)

```bash
curl -sL https://raw.githubusercontent.com/NoMiddleInc/somanylemons-mcp/main/install.sh | bash
```

The installer will:
1. Install the `/lemons` command globally
2. Ask for your API key (get one free at [somanylemons.com/developers/portal](https://producerspark.com/developers/portal))
3. Connect the MCP server

Restart Claude Code after installing. That's it.

### Manual install (if you prefer)

```bash
# 1. Get an API key from https://producerspark.com/developers/portal

# 2. Register the MCP server
claude mcp add --scope user --transport http somanylemons \
  https://producerspark.com/mcp \
  --header "X-API-Key: sml_YOUR_KEY"

# 3. Install /lemons command
mkdir -p ~/.claude/commands && \
curl -sL https://raw.githubusercontent.com/NoMiddleInc/somanylemons-mcp/main/commands/lemons.md \
  -o ~/.claude/commands/lemons.md

# 4. Restart Claude Code
```

## Use it

Restart Claude Code (or start a new conversation), then:

```
/lemons
```

That's the only command. Describe what you want and it happens.

## What /lemons can do

- **"Make a reel from [file or URL]"** - Branded video clips with captions
- **"Write a post about [topic]"** - LinkedIn posts, scored and polished
- **"Make 5 posts from my latest recording"** - Batch content from one source
- **"Extract quotes from [text]"** - Find shareable lines
- **"Score my drafts"** - Bulk engagement scoring
- **"Plan my week"** - Content calendar from your recordings
- **"Set up my brand"** - Logo, colors, styling
- **"What do I have?"** - See your recordings, drafts, queue

## How it works

```
/lemons (prompt)
    |
MCP Server (producerspark.com/mcp)
    |
Backend API (api.producerspark.com)
```

1. `/lemons` is a prompt that tells Claude how to use 19 content creation tools.
2. The MCP server receives tool calls and forwards them to the backend API.
3. The backend does the real work: transcription, rendering, AI writing, scoring.

## Tools

| Tool | Description |
|------|-------------|
| `create_reels` | Turn a recording into branded videograms, audiograms, or image quotes (async) |
| `check_job_status` | Poll processing status and get download URLs |
| `create_upload_session` | Create a resumable upload session for large files |
| `check_upload_status` | Check resumable upload progress |
| `upload_file` | Upload a local file and get a public URL |
| `transcribe` | Transcribe media with word-level timestamps |
| `generate_content` | Generate a LinkedIn post from a topic |
| `score_content` | Score a post draft (0-100) |
| `rewrite_content` | AI-rewrite a post with optional feedback |
| `extract_quotes` | Extract quotable lines with Squeeze Scores |
| `create_image_quote` | Render a branded image quote from text |
| `list_templates` | Browse available templates |
| `list_brands` | List your brand profiles |
| `create_brand` | Create a brand profile |
| `create_draft` | Create a draft post in your queue |
| `list_drafts` | List drafts with status and media |
| `list_jobs` | List recent render jobs |
| `list_plans` | View available pricing tiers |
| `get_usage` | Check render quota and billing usage |

## Caption Styles

LEMON (default), VITAMIN_C, PLAIN, SPOTLIGHT, GLITCH, RANSOM, WAVE, BOUNCE.

## Pricing

| Tier | Renders/mo | Price |
|------|-----------|-------|
| Free | 5 | $0 |
| Pro | 100 | $49/mo |
| Agency | 500 | $199/mo |
| Enterprise | Unlimited | Contact us |

Transcription, writing, scoring, and quote extraction are free and unlimited. Only rendered video clips count against quota.

## Manual MCP config

If you prefer to edit your config file directly instead of using `claude mcp add`:

```json
{
  "mcpServers": {
    "somanylemons": {
      "type": "url",
      "url": "https://producerspark.com/mcp",
      "headers": {
        "X-API-Key": "sml_your_key_here"
      }
    }
  }
}
```

## Local server (advanced)

For development or if you prefer running locally:

```bash
pip install somanylemons-mcp
SML_API_KEY=sml_your_key sml-mcp
```

Then add to your MCP config:

```json
{
  "mcpServers": {
    "somanylemons": {
      "command": "sml-mcp",
      "env": {
        "SML_API_KEY": "sml_your_key_here"
      }
    }
  }
}
```

## License

MIT

## Durable prospect and conference research

The server also exposes 18 account-scoped durable task tools, including agency research, supported conference research, saved-answer pagination, waiting/reconnection and authenticated workbook resources. The backend performs research in production; the MCP process forwards authenticated requests. The conference identifier currently supported is `acams-las-vegas-2026`. Conference work keeps mandatory review and communication holds and cannot send customer or prospect emails. A queued goal does not establish successful source access or complete coverage.

Use a customer-owned API key with `tasks:read` and `tasks:write` for creation and controls, or `tasks:read` for retrieval only. The backend enforces active membership, organization, enabled configuration and existing allowances. Remote MCP sessions bind to a hash of the initializing key; a different key must initialize a new session. Existing content tools retain their original behavior. Standalone `producerspark-tasks-mcp` installation remains available for direct Claude Code use without the hosted content server. Claude Enterprise administrators must permit the selected MCP server; account scope and Enterprise login must be validated separately.

The task transport is vendored from backend commit `5bb0a30e` to keep the hosted image self-contained. Update it from the reviewed standalone package and run the task-bridge, session-isolation and existing content checks together before release. Do not insert real keys into this repository.

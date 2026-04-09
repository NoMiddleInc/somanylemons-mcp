# SoManyLemons MCP

AI-powered content marketing via [Model Context Protocol](https://modelcontextprotocol.io). Create branded video reels, LinkedIn posts, image quotes, and more directly from Claude Code, Cursor, or any MCP-compatible client.

## Get Started

### 1. Get a free API key

**Option A: Sign up on the website**

1. Create an account at [somanylemons.com/signup](https://somanylemons.com/signup)
2. Go to the [Developer Portal](https://somanylemons.com/developers/portal)
3. Click **New Key**, give it a name, and copy your `sml_` key

**Option B: Sign up via command line**

```bash
curl -X POST https://api.somanylemons.com/api/v1/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
```

Save the `sml_` key from the response. It won't be shown again.

You can always create and manage additional API keys from the [Developer Portal](https://somanylemons.com/developers/portal).

### 2. Add to Claude Code

```bash
claude mcp add --transport http somanylemons \
  https://mcp.somanylemons.com/mcp \
  --header "X-API-Key: sml_YOUR_KEY"
```

### 3. Install the /lemons skill

The MCP server provides the tools. The `/lemons` skill tells Claude how to use them. Install it:

```bash
mkdir -p .claude/skills/lemons
curl -s https://raw.githubusercontent.com/NoMiddleInc/somanylemons-mcp/main/skills/lemons/SKILL.md \
  -o .claude/skills/lemons/SKILL.md
```

### 4. Use it

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
MCP Server (mcp.somanylemons.com)
    |
Backend API (api.somanylemons.com)
```

1. `/lemons` is a prompt that tells Claude how to use 19 content creation tools.
2. The MCP server receives tool calls and forwards them to the backend API.
3. The backend does the real work: transcription, rendering, AI writing, scoring.

## Tools

| Tool | Description |
|------|-------------|
| `create_reels` | Turn a recording into branded, captioned video reels (async) |
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
      "url": "https://mcp.somanylemons.com/mcp",
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

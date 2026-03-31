# SoManyLemons MCP

AI-powered content marketing via [Model Context Protocol](https://modelcontextprotocol.io). Create branded video reels, LinkedIn posts, image quotes, and more — directly from Claude Code, Cursor, or any MCP-compatible client.

## Install

```bash
pip install somanylemons-mcp
```

## Setup

### 1. Get a free API key

```bash
curl -X POST https://somanylemons.com/api/v1/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
```

### 2. Add to Claude Code

Add to your `~/.claude.json` (or project `.claude.json`):

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

### 3. Install skills (optional but recommended)

```bash
sml-mcp --install-skills
```

This copies workflow prompts to `~/.claude/commands/` so you can run `/make-me-famous`, `/repurpose`, etc.

## Tools

| Tool | Description |
|------|-------------|
| `create_reels` | Turn a recording URL into branded, captioned video reels (async) |
| `check_job_status` | Poll processing status and get download URLs |
| `create_upload_session` | Create a resumable direct-to-cloud upload session |
| `upload_file` | Upload a local file and get a public URL |
| `transcribe` | Transcribe media with word-level timestamps |
| `generate_content` | Generate a LinkedIn post from a topic |
| `score_content` | Score a post draft (0-100, AI + heuristics) |
| `rewrite_content` | AI-rewrite a post with optional feedback |
| `extract_quotes` | Extract quotable lines from text with Squeeze Scores |
| `create_image_quote` | Render a branded image quote from text |
| `list_templates` | Browse available video/image templates |
| `list_brands` | List your brand profiles |
| `create_brand` | Create a brand profile (colors, fonts, logo) |
| `create_draft` | Create a draft post in your content queue |
| `list_drafts` | List drafts with status and media |
| `list_jobs` | List recent render jobs |
| `get_usage` | Check render quota and billing usage |
| `list_plans` | View available pricing tiers |

## Skills

After `sml-mcp --install-skills`:

| Command | What it does |
|---------|-------------|
| `/make-me-famous` | Full pipeline: recording → branded reels + LinkedIn posts, scored and ready |
| `/repurpose` | One recording → every format: reels, quotes, written posts |
| `/content-week` | Generate a Monday–Friday LinkedIn content calendar |
| `/brand-setup` | Interactive brand profile creation |
| `/score-my-post` | Score a draft, get feedback, auto-improve |

## Caption Styles

| Style | Description |
|-------|-------------|
| `LEMON` | Bold green highlight, drop shadow, glow (default) |
| `VITAMIN_C` | Gold background box highlight with fade |
| `PLAIN` | Simple white text, no effects |
| `SPOTLIGHT` | One word at a time, centered, scale emphasis |
| `GLITCH` | RGB color-split with jitter |
| `RANSOM` | Newspaper cut-out boxes with rotation |
| `WAVE` | Words bob up/down in a sine wave |
| `BOUNCE` | Words fall from above with spring physics |

## Pricing

| Tier | Renders/mo | Price |
|------|-----------|-------|
| Free | 5 | $0 |
| Pro | 100 | $29/mo |
| Agency | 500 | $99/mo |
| Enterprise | Unlimited | Pay-per-render |

Transcription, quote extraction, content writing, and scoring are **free and unlimited**. Only rendered video clips count against quota.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SML_API_KEY` | (required) | Your API key (`sml_xxxxx`) |
| `SML_API_URL` | `https://somanylemons.com` | API base URL |

## License

MIT

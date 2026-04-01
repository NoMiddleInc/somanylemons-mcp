# SoManyLemons MCP

AI-powered content marketing via [Model Context Protocol](https://modelcontextprotocol.io). Create branded video reels, LinkedIn posts, image quotes, and more directly from Claude Code, Cursor, or any MCP-compatible client.

## Quick Install (Claude Code Plugin)

```
/plugin install somanylemons
```

That's it. You'll be prompted for your API key on first use.

No API key yet? Get one free:

```bash
curl -X POST https://somanylemons.com/api/v1/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
```

## Manual Install

If you prefer manual setup:

### 1. Install the package

```bash
pip install somanylemons-mcp
```

### 2. Add to Claude Code

Add to your project `.mcp.json` or `~/.claude.json`:

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

### 3. Install skills (optional)

```bash
sml-mcp --install-skills
```

Copies workflow prompts to `~/.claude/commands/` so you can run `/make-me-famous`, `/repurpose`, etc.

## Remote MCP (no install)

Skip pip entirely. Add this to your MCP config:

```json
{
  "mcpServers": {
    "somanylemons": {
      "type": "url",
      "url": "https://mcp.somanylemons.com/sse",
      "headers": {
        "X-API-Key": "sml_your_key_here"
      }
    }
  }
}
```

This gives you all the tools but not the slash commands.

## Skills and Commands

| Command | What it does |
|---------|-------------|
| `/lemons` | All-in-one content creator. Describe what you want and it happens. |
| `/make-me-famous` | Full pipeline: recording to branded reels + LinkedIn posts, scored and ready |
| `/repurpose` | One recording to every format: reels, quotes, written posts |
| `/content-week` | Generate a Monday to Friday LinkedIn content calendar |
| `/brand-setup` | Interactive brand profile creation |
| `/score-my-post` | Score a draft, get feedback, auto-improve |
| `/leemon` | Chat with LeeMon, your AI content strategist |

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

## Plugin Structure

```
somanylemons-mcp/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── skills/
│   └── lemons/
│       └── SKILL.md         # /lemons all-in-one skill
├── commands/
│   ├── brand-setup.md       # /brand-setup
│   ├── content-week.md      # /content-week
│   ├── leemon.md            # /leemon
│   ├── make-me-famous.md    # /make-me-famous
│   ├── repurpose.md         # /repurpose
│   └── score-my-post.md     # /score-my-post
├── src/                     # MCP server source
├── pyproject.toml
├── README.md
└── LICENSE
```

## License

MIT

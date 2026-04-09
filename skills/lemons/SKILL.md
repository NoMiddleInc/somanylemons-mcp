# /lemons - So Many Lemons Content Creator

Create branded video reels, LinkedIn posts, and image quotes through conversation. One command, no subcommands. Describe what you want and it happens.

## Tone

- Action first. Show results, then ask if they want changes.
- No em dashes. Use periods, commas, or restructure.
- Concise. No filler. No over-explaining what tools do.
- Default to doing, not asking. When the request is clear, execute.

## Context Preload (MANDATORY)

Every time `/lemons` is invoked, BEFORE responding to the user, silently load context by calling these tools in parallel:

1. `list_jobs` (limit: 10) - their recent recordings and transcripts
2. `list_brands` - their brand profiles
3. `list_drafts` (limit: 10) - their content queue

Store this context mentally. Use it to:
- Resolve references like "my latest recording," "that video from yesterday," "the one about AI"
- Know if they have a brand set up (skip brand setup prompts if they do)
- Know what's already in their queue (avoid duplicate topics, spot gaps)
- Answer "what do I have?" without extra calls

Do NOT dump this info to the user unprompted. Just know it. Use it when relevant.

## Onboarding

When `/lemons` is invoked, check if the SML MCP tools are available (look for tools like `create_reels`, `list_brands`, etc.).

### MCP tools ARE available

Run the Context Preload, then route directly to whatever the user asked for.

### MCP tools are NOT available

**Step 1: Get API key**

Ask: "Got an SML API key? Paste it here. No key? Give me your email and I'll create a free account."

- If they paste a key (starts with `sml_`): validate the format, move to step 2.
- If they give an email: run this curl to sign up:

```bash
curl -s -X POST https://api.somanylemons.com/api/v1/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "USER_EMAIL"}'
```

The response contains the raw API key. Show it to the user and warn them to save it (it won't be shown again).

**Step 2: Configure remote MCP**

Tell the user to run this exact command in Claude Code's terminal:

```bash
claude mcp add --transport http somanylemons \
  https://mcp.somanylemons.com/mcp \
  --header "X-API-Key: sml_xxxxx"
```

This writes the MCP server to Claude Code's real config automatically.

**Step 2b: Install the /lemons skill file**

The MCP server provides the tools, but the `/lemons` slash command needs a local skill file. Install it:

```bash
mkdir -p .claude/skills/lemons
curl -s https://raw.githubusercontent.com/NoMiddleInc/somanylemons-mcp/main/skills/lemons/SKILL.md \
  -o .claude/skills/lemons/SKILL.md
```

Then tell them to restart Claude Code or start a new conversation for both the MCP tools and `/lemons` command to become available.

Only if they specifically ask for manual config, show this fallback:

```json
{
  "mcpServers": {
    "somanylemons": {
      "type": "url",
      "url": "https://mcp.somanylemons.com/mcp",
      "headers": {
        "X-API-Key": "sml_xxxxx"
      }
    }
  }
}
```

**Step 3: Brand setup (ask but don't block)**

Say: "Want to set up your brand? Adding your logo and colors keeps your results consistent and on-brand. You can also skip this and do it later."

If they provide info:
- Collect whatever they give (name, logo file/URL, colors).
- If they provide a local logo file, upload it first with `upload_file`, then use the returned URL.
- Call `create_brand` with their info.

If they skip:
- Auto-create a starter brand with defaults:
  - Name: derive from email domain (e.g. "Acme" from user@acme.com), or "My Brand" as fallback.
  - Colors: pick a random curated palette from this list:
    - `#1a73e8` / `#ffffff` (blue/white)
    - `#2d8a4e` / `#f0f7f3` (green/light)
    - `#e63946` / `#f1faee` (red/cream)
    - `#6c5ce7` / `#ffeaa7` (purple/gold)
    - `#00b4d8` / `#023e8a` (cyan/navy)
  - Logo: use a sample template logo.
- Call `create_brand` with defaults.
- Say: "No problem. I set up a starter brand with sample styling. Update it anytime with /lemons."

**Step 4: First action**

"You're set. What do you want to make?"

## Routing

Parse intent from the user's natural language message after `/lemons`.

| Intent signal | Route to |
|---|---|
| No message, vague, "help", "what can you do" | Capabilities menu |
| "what do I have", "show me my stuff", "dashboard", "overview" | Dashboard |
| URL, file path, "reel", "video", "clip", "record" | Reels workflow |
| "post", "write", "LinkedIn", "draft", "content" | Content writing workflow |
| "from my transcripts", "from my recordings", "latest recording", "use my latest", "what did [name] say about" | Transcript-based content workflow |
| "quotes", "extract", "best lines", "quotable" | Quote extraction workflow |
| "batch", "week of posts", "fill my queue", "5 posts", "multiple posts", number + "posts" | Batch content workflow |
| "queue", "scheduled", "what's next", "what's lined up" | Queue management |
| "score my drafts", "score all", "review my queue" | Bulk scoring |
| "more like", "that one did well", "similar to" | More-like-this workflow |
| "brand", "colors", "logo", "setup", "branding" | Brand setup |
| "status", "job", "check", a UUID | Job status check |
| "usage", "quota", "renders left", "how many" | Usage check |
| "plan my content", "content strategy", "plan next week" | Content planning |

When showing the capabilities menu, use this:

```
Here's what I can do:

**Create**
- "Make a reel from [file or URL]" - Branded video clips
- "Write a post about [topic]" - LinkedIn posts, scored and polished
- "Make 5 posts from my latest recording" - Batch content from one source
- "Extract quotes from [text]" - Find shareable lines

**From your recordings**
- "Use my latest recording" - I already know your uploads
- "What did [CEO] talk about?" - Search your transcripts by topic
- "Turn that into a post and a reel" - Cross-format from one source

**Manage**
- "What do I have?" - See your recordings, drafts, queue
- "What's in my queue?" - Check scheduled content
- "Score my drafts" - Bulk engagement scoring
- "Plan my week" - Content calendar from your recordings

**Setup**
- "Set up my brand" - Logo, colors, styling
- "Check my usage" - Render quota

What would you like?
```

## Dashboard

Show a concise overview using the preloaded context:

```
**Your Recordings** (last 10)
1. [title/date] - completed - [X clips rendered]
2. [title/date] - processing - 65%
...

**Queue** ([N] drafts)
- [N] draft, [N] queued, [N] scheduled, [N] posted

**Brand:** [brand name] | **Usage:** [X/Y renders] this period
```

Then: "What do you want to work on?"

## Reels Workflow

### 1. Handle the source

**"My latest recording" / "that video" / relative reference:**
- Use the preloaded `list_jobs` data to resolve which recording they mean.
- If ambiguous, show a numbered list of recent recordings and ask them to pick.
- If clear (e.g., "latest" = most recent completed job), use it directly.

**Local file:**
- If file size < 50MB: upload via `upload_file` tool. Use the returned URL.
- If file size >= 50MB: use `create_upload_session` to get a resumable session, then upload via curl to the session_uri, then use the `full_url`.

**URL:** Use directly.

### 2. Check brand profile

Use the preloaded brand data. If no profiles exist, run brand setup (Step 3 from onboarding). If profiles exist, use the default one (or ask if they have multiple).

### 3. Videogram vs. audiogram

Detect file type from extension:

**Audio file** (.mp3, .wav, .m4a, .aac, .ogg):
- Submit 1 audiogram job. No need to ask.

**Video file** (.mp4, .mov, .webm, .avi):
- Say: "I'll create both a videogram and an audiogram. The videogram uses your video as the background with captions overlaid. The audiogram uses a template background and can include a talking head overlay. Want both (recommended), or just one?"
- Default to both if they agree or just say "go" or "yes".
- Submit 2 jobs via `create_reels` (one for each type).

### 4. Submit and wait

Call `create_reels` with the URL and brand_profile_id.

Then ask: "Want me to wait for results, or give you the job IDs to check later?"

**If they want to wait:**
- Poll `check_job_status` every 15 seconds.
- Show progress updates: "Transcribing... 30%", "Extracting clips... 65%", "Rendering... 90%"
- When done, show download URLs for all completed clips.
- If two jobs (videogram + audiogram), track both and show results together.

**If they want to check later:**
- Return the job ID(s).
- "Check back anytime. Just type /lemons or ask 'what's the status of my renders'."

## Transcript-Based Content Workflow

When the user references their recordings or transcripts, do NOT ask them to paste text. They're referring to content already in the system.

### 1. Resolve the recording

Use the preloaded `list_jobs` context to figure out which recording they mean:
- "latest" / "most recent" / "last one" = most recent completed job
- "the one about [topic]" = match by title or scan transcript content
- "from yesterday" / "from last week" = match by date
- Ambiguous = show numbered list, ask them to pick

### 2. Get transcript content

Call `check_job_status` with the selected job ID. Extract the transcript text from the job details.

### 3. Route based on what they asked for

**"Write a post from..."** - Extract quotes, auto-pick the strongest one, feed it as context into `generate_content`. Show the post, enter the Content Writing iterate loop.

**"What did [name] say about [topic]?"** - Search the transcript text for the topic. Show relevant excerpts. Then offer: "Want me to turn any of this into a post?"

**"Extract quotes from..."** - Call `extract_quotes` on the transcript. Enter the Quote Extraction workflow.

**"Make a reel from..."** - They already have a completed job. Show the existing rendered clips. If they want different clips or a re-render, re-submit via `create_reels`.

**No specific format mentioned** - Default to extracting quotes first, showing the top 5, then asking what they want to make from them.

## Content Writing Workflow

### 1. Generate

Call `generate_content` with the user's topic (or transcript context if coming from transcript workflow). Show the generated post.

### 2. Iterate

After showing the post, offer:

"Here's your post. Want me to:
A) Score it (see engagement prediction)
B) Rewrite it (with optional feedback)
C) Add it as a draft to your queue
D) Done"

**Score:** Call `score_content`. Show the score (0-100), strengths, and feedback. Then offer: "Want me to rewrite it based on this feedback?"
- If yes: call `rewrite_content` with the feedback auto-attached. Show new version. Repeat the menu.

**Rewrite:** Ask "Any specific feedback, or should I just improve it?" Call `rewrite_content`. Show the new version. Repeat the menu.

**Draft:** Call `create_draft` with the caption. "Added to your queue."

**Done:** End the workflow.

The user can cycle through score/rewrite as many times as they want until they're happy.

## Batch Content Workflow

When the user asks for multiple pieces of content from one source (e.g., "make me 5 posts from my latest recording," "fill my queue for next week").

### 1. Resolve the source

Same as Transcript-Based Content Workflow step 1. If no source specified, use the most recent completed recording.

### 2. Extract quotes

Call `extract_quotes` with the transcript text, requesting enough quotes (count = requested posts + 3 extras for variety). This gives a pool of raw material.

### 3. Generate posts in batch

For each of the top N quotes (where N = number of posts requested):
- Call `generate_content` with the quote as context/topic.
- Call `score_content` on the result.
- Store the post + score.

### 4. Present results

Show all generated posts with their scores in a numbered list:

```
**Post 1** (Score: 82/100)
[post text preview, first 2 lines]

**Post 2** (Score: 76/100)
[post text preview, first 2 lines]

...
```

Then: "Want me to:
A) Add all to your queue
B) Rewrite the weak ones (below 75)
C) Show any post in full
D) Pick which ones to keep"

**Add all:** Call `create_draft` for each post.
**Rewrite weak ones:** Auto-rewrite posts scoring below 75, show improved versions, re-score.
**Show full:** Display the full text of the requested post number.
**Pick:** Let them select which to queue by number.

## Queue Management

### 1. Show queue status

Use the preloaded `list_drafts` data. Display:

```
**Your Queue** ([N] total)

Draft ([N]):
- [preview] - created [date]
...

Queued ([N]):
- [preview] - scheduled [date]
...

Scheduled ([N]):
- [preview] - goes live [date/time]
...
```

### 2. Offer actions

"Want me to:
A) Score all drafts
B) Fill gaps (generate posts for empty days)
C) Rewrite a specific draft
D) Move a draft to scheduled"

## Bulk Scoring

When the user says "score my drafts," "review my queue," or similar:

1. Call `list_drafts` to get all drafts.
2. Call `score_content` on each draft's caption.
3. Show results sorted by score:

```
**Draft Scores**
1. (92/100) "The best leaders don't..." - Strong hook, good structure
2. (78/100) "AI is changing..." - Needs stronger CTA
3. (61/100) "Had a great meeting..." - Weak hook, too generic
```

4. Offer: "Want me to auto-rewrite everything below 75?"

If yes: call `rewrite_content` on each low-scorer, re-score, show improvements.

## More-Like-This Workflow

When the user references a past success ("that one did well," "more like the AI post," "similar to #3"):

1. Identify the referenced post from preloaded drafts or from context in the conversation.
2. Analyze what made it work: topic, structure, hook style, tone.
3. Call `generate_content` with a topic prompt that captures the same pattern but with fresh angle.
4. Score the new post. If below 75, auto-rewrite once.
5. Show the result. Enter the normal Content Writing iterate loop.

## Content Planning

When the user asks to "plan my week," "content strategy," or "plan next week":

### 1. Gather inputs

- Check preloaded `list_jobs` for recent recordings with transcripts (content pool).
- Check preloaded `list_drafts` for what's already queued (avoid duplicates).

### 2. Build the plan

From available transcripts, extract themes and topics. Propose a content calendar:

```
**Content Plan: [Week]**

Mon: Post about [topic from transcript 1] (quote: "[best line]")
Tue: Reel from [recording title] (top clip)
Wed: Post about [topic from transcript 2] (thought leadership angle)
Thu: Image quote ("[quotable line]")
Fri: Post about [different angle from transcript 1]

Source recordings: [list which recordings feed which days]
```

### 3. Execute on approval

"Want me to generate all of these now, or go day by day?"

- **All at once:** Run the Batch Content Workflow for the posts. Submit `create_reels` for the reel days.
- **Day by day:** Generate one at a time, get approval, move to next.

## Quote Extraction Workflow

User provides text (transcript, article, blog post, or points to a file). Or says "from my latest recording" (resolve via preloaded jobs).

Call `extract_quotes` with the text. Show the extracted quotes with their Squeeze Scores (/50).

Then offer: "Want me to turn any of these into a LinkedIn post or an image quote?"

- LinkedIn post: take the selected quote, feed it to `generate_content` as context, enter the content writing workflow.
- Image quote: this bridges into the reels workflow with the quote text.
- "All of them" / "make posts from all": enter Batch Content Workflow with these quotes as the source material.

## Cross-Format Workflow

When the user wants multiple formats from one piece of content ("turn that into a post AND a reel," "make a post, a reel, and image quotes from this"):

1. Identify the source content (quote, transcript, recording).
2. For each requested format, run the appropriate workflow in parallel where possible:
   - Post: `generate_content` + score
   - Reel: `create_reels` with the source recording URL
   - Image quote: extract the best quote, submit for rendering
3. Show all results together with clear labels.

## Brand Setup

When the user wants to update their brand:

1. Use preloaded brand data to show current brand(s).
2. Ask what they want to update (name, logo, colors, or start fresh).
3. If uploading a new logo from a local file, use `upload_file` first.
4. Call `create_brand` with the updated info.
5. "Brand updated. All future renders will use this styling."

## Job Status Check

If the user provides a job ID (UUID) or asks about status:

Call `check_job_status` with the ID. Show:
- Status (pending, processing, completed, failed)
- Progress percentage
- If completed: download URLs
- If failed: error message and offer to retry

If no specific ID given, use preloaded `list_jobs` to show recent jobs with their statuses.

## Usage Check

Call `get_usage`. Show:
- Renders used this billing period
- Render limit
- Tier name
- If near the limit, mention they can upgrade.

## Available MCP Tools

These are the tools available when the SML MCP server is connected:

| Tool | What it does |
|---|---|
| `create_reels` | Submit a recording URL for branded clip creation (async) |
| `check_job_status` | Poll processing status by job ID |
| `upload_file` | Upload a local file (image/video/audio, max 50MB) to get a hosted URL |
| `create_upload_session` | Create resumable upload session for large files |
| `check_upload_status` | Check resumable upload progress |
| `generate_content` | Generate a LinkedIn post from a topic |
| `score_content` | Score a post for engagement (0-100) |
| `rewrite_content` | AI-rewrite a post with optional feedback |
| `extract_quotes` | Extract quotable lines with Squeeze Scores |
| `list_templates` | List available video/image templates |
| `list_brands` | List brand profiles |
| `create_brand` | Create a brand profile (name, colors, logo_url) |
| `create_draft` | Add a post to the content queue |
| `list_drafts` | List drafts in the queue |
| `list_jobs` | List recent render jobs with status and results |
| `get_usage` | Check render quota and usage stats |
| `create_image_quote` | Render a branded image quote from text, optionally attach to a draft |
| `transcribe` | Transcribe a video/audio file with word-level timestamps |

## Error Handling

**Quota exceeded:** Show current usage via `get_usage`. Say: "You've hit your render limit for this month. You can upgrade your tier for more renders."

**Invalid or expired API key:** Say: "Your API key isn't working. Want to set up a new one?" Re-run onboarding.

**Upload failure:** Say: "Upload failed. Want to try again, or provide a URL instead?"

**Job failed:** Show the error from `check_job_status`. Offer: "Want me to retry with the same settings?"

**MCP server unreachable:** Say: "Can't reach the SML server. Check your internet connection or try again in a moment."

**No recordings found:** If `list_jobs` returns empty and user asks for transcript-based content: "You don't have any recordings yet. Upload a video or audio file and I'll work from that. Or give me a topic and I'll write from scratch."

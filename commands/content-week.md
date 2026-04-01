# Content Week

Generate a full week of LinkedIn content from a single recording or topic list.

## Instructions

You have access to the So Many Lemons MCP tools. Follow these steps:

### Step 1: Get the source material
Ask the user for either:
- A recording URL (podcast, interview, talk)
- A list of 5-7 topics they want to post about this week

### Step 2: If recording provided
Call `create_reels` to get video clips. While waiting, call `extract_quotes` if they can provide the transcript text. Poll `check_job_status` for the reels.

### Step 3: Generate 5 posts
Call `generate_content` for 5 different posts. If working from a recording, base each post on a different clip or quote. If working from topics, use each topic.

Vary the format across the week:
- **Monday**: Story post (personal experience, lesson learned)
- **Tuesday**: Tactical post (how-to, framework, steps)
- **Wednesday**: Contrarian take (challenge conventional wisdom)
- **Thursday**: Data/insight post (stat, trend, observation)
- **Friday**: Engagement post (question, poll-style, "agree or disagree")

### Step 4: Score and improve
Call `score_content` on all 5 posts. Rewrite any that score below 60 using `rewrite_content` with specific feedback from the scoring.

### Step 5: Present the weekly calendar

Format as a Monday-Friday schedule:

**Monday**
- Post text
- Score: X/100 | Predicted views: X-Y
- Video clip: [URL] (if available)

**Tuesday**
... and so on.

End with:
- Total renders used
- Remaining quota (call `get_usage`)
- "Copy each post and schedule in your LinkedIn queue, or use the SML web app to auto-schedule."

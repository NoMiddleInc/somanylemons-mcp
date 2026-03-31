# Repurpose

Turn one piece of content into every format: video reels, quotable image cards, and written posts.

## Instructions

You have access to the So Many Lemons MCP tools. Follow these steps:

### Step 1: Get the content
Ask the user for their recording URL. If they provide text (like a blog post or transcript) instead of a URL, skip straight to Step 3 and use extract_quotes + generate_content only.

### Step 2: Create video reels
Call `create_reels` with the URL. While it processes, move to Step 3.

### Step 3: Extract quotes
If the user provided text, call `extract_quotes` with the full text. Request 8 quotes. These will become standalone image quote cards (the user can render them in the SML web app).

### Step 4: Generate written posts
Take the top 5 quotes (by squeeze_total score) and call `generate_content` for each, using the quote as the topic/seed. Each post should take a different angle:
1. A personal story inspired by the quote
2. A contrarian take or hot take
3. A how-to or tactical breakdown
4. A "most people think X, but actually Y" format
5. A question/engagement post

### Step 5: Score and refine
Call `score_content` on each post. For any scoring below 65, call `rewrite_content` to improve.

### Step 6: Check on video reels
If you submitted a recording, poll `check_job_status` until complete.

### Step 7: Present everything
Show the user their content package:

**Video Reels** (if applicable)
- Clip URLs, durations, transcripts

**Top Quotes for Image Cards**
- The quote text, squeeze score, and which zone it falls in
- Note: "Render these as branded image cards at somanylemons.com"

**LinkedIn Posts** (5 posts, scored)
- Post text, score, predicted views
- Suggested posting order (lead with the highest-scored post)

Tell them: "You now have a week of content from one recording."

# Score My Post

Get your LinkedIn post scored and improved before you publish.

## Instructions

You have access to the So Many Lemons MCP tools. Follow these steps:

### Step 1: Get the post
Ask the user to paste their LinkedIn post draft. It needs to be at least 50 characters.

### Step 2: Score it
Call `score_content` with their post text.

### Step 3: Show the results
Present clearly:
- **Overall score**: X/100
- **AI score**: X/100
- **Heuristic score**: X/100
- **Predicted views**: X - Y
- **Strengths**: what's working
- **Feedback**: what to improve

Show each heuristic check (character count, hook strength, CTA, readability, etc.) with pass/fail.

### Step 4: Offer to improve
If the score is below 75, ask if they want an AI rewrite. If yes, call `rewrite_content` with the original text and the feedback from scoring. Then re-score the rewritten version and show the before/after comparison.

### Step 5: Final version
Show the final post (original or rewritten) ready to copy-paste. If there's a significant score improvement, highlight it: "Score improved from X to Y."

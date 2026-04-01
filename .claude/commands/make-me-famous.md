# Make Me Famous

Turn a recording into a full social media content package: branded video reels, LinkedIn posts, and engagement scores.

## Instructions

You have access to the So Many Lemons MCP tools. Follow these steps:

### Step 1: Check brand setup
Call `list_brands` to check if the user has a brand profile. If they don't have one, ask for their brand name, primary color, and secondary color, then call `create_brand` before proceeding.

### Step 2: Get the recording
Ask the user for their recording URL or file path. This can be a podcast episode, interview, webinar, or any video/audio recording.

### Step 3: Create reels
Call `create_reels` with the URL and their default brand profile ID. Tell the user this typically takes 2-5 minutes.

### Step 4: Monitor progress
Poll `check_job_status` every 15 seconds. Give the user brief progress updates ("Transcribing your recording...", "Extracting the best moments...", "Rendering branded clips..."). Do not flood them with updates.

### Step 5: Generate LinkedIn posts
While waiting for renders, or after they complete, take the transcript text from the completed clips and call `generate_content` for each clip to create a LinkedIn post that introduces or contextualizes the clip.

### Step 6: Score the posts
Call `score_content` on each generated post. If any score below 60, call `rewrite_content` to improve it, then re-score.

### Step 7: Present the package
Show the user everything in a clean format:
- Each video clip with its download URL and duration
- The matching LinkedIn post for each clip
- The engagement score and predicted view range
- A recommended posting schedule (spread across the week)

End by showing their remaining render quota with `get_usage`.

# Brand Setup

Set up your brand profile so all your content comes out looking like YOU.

## Instructions

You have access to the So Many Lemons MCP tools. Follow these steps:

### Step 1: Check existing brands
Call `list_brands`. If the user already has brand profiles, show them and ask if they want to create a new one or if they're all set.

### Step 2: Gather brand info
Ask the user for:
- **Brand name** (required)
- **Primary color** (required, hex code like #1a73e8). If they say a color name like "blue", pick a good hex value and confirm.
- **Secondary color** (required). Suggest a complementary color based on their primary.

Then ask if they want to customize further:
- Accent color (for highlights and emphasis)
- Background color
- Text color
- Font family

If they say no or seem unsure, use sensible defaults based on their primary/secondary.

### Step 3: Create the profile
Call `create_brand` with their choices. Show them what was created.

### Step 4: Next steps
Tell them their brand is ready and suggest:
- "Now try `/make-me-famous` with a recording to see your branding in action"
- "Or use `create_reels` directly with your brand_profile_id"

Show the brand_profile_id they should use.

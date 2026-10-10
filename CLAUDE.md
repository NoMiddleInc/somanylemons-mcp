# SoManyLemons MCP

## Key Rules

- All MCP-related functionality belongs directly in the MCP server code (`src/somanylemons_mcp/`), not in external wrappers or separate repos.
- Every tool is an agent task tool defined in `task_tools/` and exposed through `task_bridge`. The old content tools (reels, quotes, writing, drafts, brands) were removed on 2026-10-10.
- The skill prompt (`skills/producerspark/SKILL.md`) orchestrates the user experience but should never duplicate backend logic.
- The backend Public API owns the capability registry and contract. MCP may expose an approved
  operation but must not invent a route, schema or business rule. Report missing capabilities to the backend owner first.
- Onboarding should use `claude mcp add --transport http` as the primary setup method, not manual JSON config.

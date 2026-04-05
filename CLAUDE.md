# SoManyLemons MCP

## Key Rules

- All MCP-related functionality belongs directly in the MCP server code (`src/somanylemons_mcp/`), not in external wrappers or separate repos.
- Tool definitions go in `server.py` under `list_tools()`, with routing in `TOOL_ROUTES`.
- The skill prompt (`skills/lemons/SKILL.md`) orchestrates the user experience but should never duplicate backend logic.
- Onboarding should use `claude mcp add --transport http` as the primary setup method, not manual JSON config.

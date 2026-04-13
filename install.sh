#!/bin/bash
# SoManyLemons MCP — One-line installer
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/NoMiddleInc/somanylemons-mcp/main/install.sh | bash
#
# Or with your API key pre-set:
#   curl -sL https://raw.githubusercontent.com/NoMiddleInc/somanylemons-mcp/main/install.sh | bash -s -- sml_YOUR_KEY

set -e

REPO="https://raw.githubusercontent.com/NoMiddleInc/somanylemons-mcp/main"
API_KEY="${1:-}"

echo ""
echo "  🍋 SoManyLemons MCP Installer"
echo "  ─────────────────────────────"
echo ""

# ── Step 1: Install /lemons command ──────────────────────────────────────────
mkdir -p ~/.claude/commands
curl -sL "$REPO/commands/lemons.md" -o ~/.claude/commands/lemons.md
echo "  ✓ Installed /lemons command"

# ── Step 2: Configure MCP server ─────────────────────────────────────────────
if command -v claude &>/dev/null && claude mcp list 2>/dev/null | grep -q "somanylemons"; then
    echo "  ✓ MCP server already configured"
else
    # Get API key if not provided as argument
    if [[ -z "$API_KEY" ]]; then
        echo ""
        echo "  🔑 Enter your SoManyLemons API key (starts with sml_)."
        echo ""
        echo "  Don't have one? Get it at:"
        echo "  → https://somanylemons.com/developers/portal"
        echo ""
        read -rp "  API Key: " API_KEY
    fi

    if [[ -z "$API_KEY" || "$API_KEY" != sml_* ]]; then
        echo ""
        echo "  ✗ Invalid key. Must start with 'sml_'."
        echo "    Get one at: https://somanylemons.com/developers/portal"
        exit 1
    fi

    if command -v claude &>/dev/null; then
        claude mcp add --scope user --transport http somanylemons \
            "https://mcp.somanylemons.com/mcp" \
            --header "X-API-Key: $API_KEY" 2>/dev/null
        echo "  ✓ MCP server configured"
    else
        echo ""
        echo "  ⚠ 'claude' CLI not found. Add this to your Claude Code config manually:"
        echo ""
        echo '  {
    "mcpServers": {
      "somanylemons": {
        "type": "url",
        "url": "https://mcp.somanylemons.com/mcp",
        "headers": {
          "X-API-Key": "'"$API_KEY"'"
        }
      }
    }
  }'
        echo ""
    fi
fi

echo ""
echo "  🍋 Done! Restart Claude Code and type /lemons"
echo ""

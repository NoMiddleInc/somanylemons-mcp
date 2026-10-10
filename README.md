# ProducerSpark MCP

Agent tools for ProducerSpark via [Model Context Protocol](https://modelcontextprotocol.io): durable business, prospect and conference research, saved lists, and Email Agent status. The earlier content tools (reels, image quotes, post writing, drafts, brands) and the `/lemons` skill were removed on 2026-10-10.

## Setup

```bash
curl -sL https://raw.githubusercontent.com/NoMiddleInc/somanylemons-mcp/main/install.sh | bash
```

Or add the hosted server yourself:

```bash
claude mcp add --scope user --transport http somanylemons https://producerspark.com/mcp --header "X-API-Key: sml_your_key_here"
```

## License

MIT

## Durable business, prospect and conference research

The server exposes 21 account-scoped durable task and main-list tools. `create_business_research_request` accepts business-contact and conference questions across industries and roles, including a named executive, supplied companies, or an event's publisher URLs. Supply the customer's actual request and one UUID; optional `spec` provides structured companies, roles, `person_name`, event, fields and requested count. Company `identity_hints` retain literal JSON clues up to 10,000 serialized characters. Field orders support up to 40 entries, including verification, enrichment and source metadata, session date/time/room, and organizer facts. Compact results explicitly flag truncated evidence/provenance previews; artifact downloads retain the saved file bytes. The default bounded request asks for 15 contacts; a named-person request defaults to one. Requested counts up to 500 and `all: true` describe the requested scope and never increase spending authority. Use `count: 15, all: false` for a first-15 delivery. All-result requests omit a bounded count. Request-only interpretation runs in a persisted cloud planning task.

Once the matching backend API and worker release is active and the account is enrolled, accepted work continues after the MCP client closes. The backend researches, checks saved evidence and the exact artifact, and sends only to the authorized customer according to current delivery policy. Required unfinished enrichment, uncertain external effects, unavailable provider authority and failed checks retain an open obligation with a blocker. A queued goal is not proof of source access, complete coverage or delivery. No business request authorizes prospect outreach. Customer workbooks contain recorded business emails, verification/enrichment status and provenance, and omit draft-email content and draft sequences.

Use `wait_for_task` and `get_research_answer` with the returned goal ID, and `get_task_artifact` or `read_task_spreadsheet` for the full saved workbook. Reuse the same UUID and original inputs after an uncertain response. Existing `create_research_request` remains the saved-criteria agency workflow; `create_conference_research_request` remains the registered-event compatibility workflow with its recorded holds. Use the generic business tool for broader questions. Use `list_golden_lists` to discover owned and explicitly shared main lists, `get_my_icp` for saved targeting, `get_prospect_list` for a main-list Excel download, and `read_golden_list` for all saved contacts in one call. The MCP process only forwards authenticated requests; it has no worker, customer database or provider credentials.

Use a customer-owned API key with `tasks:read` and `tasks:write` for creation and controls, or `tasks:read` for retrieval only. The backend enforces active membership, organization, enabled configuration and existing allowances. Remote MCP sessions bind to a hash of the initializing key; a different key must initialize a new session. Existing content tools retain their original behavior. Standalone `producerspark-tasks-mcp` installation remains available for direct Claude Code use without the hosted content server. Claude Enterprise administrators must permit the selected MCP server; account scope and Enterprise login must be validated separately.

The task transport is vendored from backend commit `5bb0a30e` to keep the hosted image self-contained. Update it from the reviewed standalone package and run the task-bridge and session-isolation checks together before release. Do not insert real keys into this repository.


## Live prospect finding in Claude Code

Use `/producerspark` after running the installer, or the ProducerSpark skill in the plugin. Ask, for example: “Find 50 manufacturing CFOs in Chicago and keep me updated.” The workflow creates one durable backend task and calls `watch_research` with a cursor to show actual saved counts, email coverage, stages and source-backed findings as they change. Research continues if the conversation closes; reconnect to the same task to resume updates.

On completion, it offers to plan an email campaign. This research-only connection cannot enroll or send prospects. Active monitoring uses bounded reads; unsolicited push into idle Claude sessions is not included. See [experience design](docs/live-prospect-experience.md) for behavior and rollout details. The updated hosted MCP must be deployed before the new tool is available to hosted clients.

## Email Agent status and answers

For outreach status, use `get_email_agent_status` with the selected `campaign_id`
from `list_golden_lists` when needed. For a question about the saved list or email
activity, use `ask_email_agent` with the user's question. Present the returned
`message` directly and preserve paragraph breaks: the backend uses the email
agent's reply prompt, model and saved facts. Keep sent, queued, sending, uncertain,
failed, skipped and replied counts distinct. Sent means provider send success,
not inbox receipt or an open. These tools do not send emails, change lists or
start research. They have no previous email-thread context; name the subject of
a follow-up or preserve the returned clarification. Status is a current read,
not unsolicited push while the client is idle.

Backend endpoints must be deployed before the hosted MCP tools become usable.

### Shared email and MCP interpretation

New business research sends the original request verbatim plus a UUID to `create_business_research_request`; `spec` is optional. The same backend background planner interprets email, Claude and Codex requests. Clients do not preselect companies or construct title, geography, count, industry or size criteria. Older structured specs remain validated audit hints and cannot override the original request. Literal uploaded seed files retain their provenance. The backend owns title equivalence, candidate discovery, qualification, retries, validated artifacts and authorized customer delivery. MCP carries the recorded acknowledgment and reads the same durable goal after the connection closes. Reuse exact inputs and UUID after uncertain responses. Existing named-person, conference-review and tenant protections remain in force.

Backend, task-worker and hosted MCP releases must match before claiming production parity. Local interpretation tests do not certify live prospect research or email delivery.

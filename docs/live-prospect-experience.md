# ProducerSpark live prospect experience

The first useful interaction is: find prospects, show saved discoveries as the job progresses, deliver the result, then suggest campaign planning.

Example interaction (illustrative counts and stages, never hard-coded output):

> Finding 50 manufacturing decision-makers in Chicago. I’ll report saved prospects and business-email coverage as the research progresses.
>
> 5 of 50 qualified prospects found. 3 with recorded business emails. Saved stage: contact research. One saved match is a CFO at a manufacturing company; its fit reason and source are available.
>
> 24 of 50 qualified prospects found. 17 with recorded business emails. Saved stage: enrichment.
>
> 50 of 50 qualified prospects found. 41 with recorded business emails. Research completed. Here’s the workbook. Would you like to plan an email campaign for these prospects?

## Runtime

`watch_research(goal_id, cursor?, timeout_seconds=10)` is read-only. Initial reads return immediately. Subsequent calls return a changed canonical snapshot or yield within a bounded polling window. Cursor comparison ignores timestamp/version churn. Contact counts come from canonical saved answer counts and coverage, never task steps or preview lengths. Unknown counts remain unknown. Returned findings contain up to three saved samples and their recorded evidence. They are not labeled as newly discovered contacts.

The client repeats bounded calls while `continue_watching=true`. On a meaningful change, it displays `summary` and useful facts. On unchanged snapshots it emits a heartbeat at most every 30 seconds. `poll_after_seconds` guides clients outside Claude to avoid tight polling. Blockers, pauses and terminal states stop the watch. Partial fulfillment remains visibly incomplete, even when a worker reports completed.

No server-side mutable customer cache or detached task is created. Existing authenticated backend workers retain research state and execution. The original task ID remains the reconnect handle and canonical resolution follows approved continuation IDs. Credentials and account boundaries stay in the existing transport. A cursor is an opaque presentation hash, not authority or an access token.

## Installation and rollout

The plugin ships `skills/producerspark/SKILL.md`; the wheel contains the matching packaged skill. The existing installer installs the workflow alongside `/lemons`. Local plugin preview: `claude --plugin-dir /absolute/path/to/spark-mcp` with the existing SML_API_KEY configured outside chat. Plugin skill commands are namespaced by Claude; the installed user skill is `/producerspark`.

Deploy the updated hosted MCP before customers use the new watch tool. This change does not deploy any service or start customer research. Existing clients can continue using `wait_for_task`.

## Continuous operation boundaries

Backend research continues after Claude closes. Active conversations stay interactive through repeated bounded reads. Ordinary HTTP MCP does not wake an idle conversation. True unsolicited session alerts require a local Claude channel bridge; custom channels currently require Claude’s research-preview development flag. That bridge is a later integration, not a dependency of this release. The plugin does not override a user's status-line configuration or claim background push support.

## Campaign handoff

At completed, unblocked research, suggest campaign planning and workbook review. Saved emails do not imply current deliverability. Planning never enrolls or sends prospects. The current research-only connection has no campaign launch capability; report that accurately if asked to launch. Future launch support must use canonical Public API operations, explicit sending authorization, idempotent writes, and recorded receipts.

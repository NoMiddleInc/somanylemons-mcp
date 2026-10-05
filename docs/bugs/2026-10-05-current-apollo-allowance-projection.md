# Current Apollo allowance omitted from MCP task answers

The backend full task/control response included `apollo_credit_budget`, but the MCP compact projection omitted it. This left an earlier monthly quota blocker visible without the current shared lookup allowance. The backend answer view also needs the canonical budget field so normal reads and waits receive it without an extra full-detail request.

MCP 0.6.6 copies only nonnegative integer allowance fields and exact canonical scope/accounting text. Unknown fields, diagnostics and URLs are omitted. The projection performs no calculations, provider calls, authorization changes or allowance changes. Recorded blockers and actual task states remain independent from current lookup authority.

Regression coverage includes answer/history reads, waiting, create/retry responses, strict numeric types, malformed text, missing fields and unchanged request scope/idempotency. Lookup units describe conservative billable attempts, not provider balance or invoiced credits.

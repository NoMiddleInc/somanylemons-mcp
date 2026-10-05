---
name: producerspark
description: Research prospects through a connected ProducerSpark account, retrieve existing research tasks and workbooks, and prepare saved-format drafts.
---

Keep replies concise: normally no more than 75 words plus the requested download link. Omit sample rows and operational detail unless asked.
Use get_my_icp for saved ideal customer profile and its Excel download. Use get_prospect_list for the MAIN golden/ICP/prospect list Excel, including explicitly shared lists. Use list_golden_lists to discover accessible lists and read_golden_list for all their saved contacts in one call. Conference task workbooks are separate and require an explicit task request. Return download_url as a clickable link; it expires in ten minutes. Use read_task_spreadsheet to analyze all saved rows in one call, never five-contact pagination.

ProducerSpark connects to the signed-in account's durable research tasks.
Start with list_tasks to find existing work. Never hard-code another customer's configuration.
For an explicit new agency research request, use create_research_request with a fresh UUID idempotency key and supplied agencies. Use campaign_id for the saved owned list/ICP selected in this conversation; discover list IDs with list_golden_lists. This changes only this request's research context, not the default agent or scheduled list. OAuth discovers the signed-in account's own agents across active memberships; never choose another customer's configuration. If multiple configurations are eligible, use the actual matching configuration or ask which to use. New OAuth requests with multiple owned lists require a selected list.
Return the task number. The production backend continues when this chat closes. Use bounded wait_for_task calls and get_research_answer on the same task; reuse creation idempotency keys after uncertain responses. Never recreate an uncertain request.
For a download request, return the file link and one short sentence about any recorded incompleteness. Do not add unsolicited outreach/newsletter commentary or sample tables. When asked about research quality, report requested and qualified counts, actual business-email coverage, enrichment/verification status and dates, source-backed fit, saved drafts and limitations. Saved verification is not a fresh deliverability check. Distinguish unavailable email after completed enrichment from unfinished or blocked work.
Use get_task_artifact for the saved workbook; retrieval must not create another delivery. Only report delivery when a saved receipt establishes it.
Research creation may trigger an already-authorized customer delivery through the backend. Explain that before accepting new work. This connection cannot authorize prospect outreach, list membership changes or new recipients. Change schedules only on an explicit request. Read current task version and allowed_actions before controls. Reconnect by reading the same task number.
Supported conference research currently uses only acams-las-vegas-2026; never promise other conference execution or exhaustive coverage.

For a download request, respond with the requested file link and at most one short saved-data limitation. Omit file hashes, byte counts, sample rows, and commentary about actions the user did not request.

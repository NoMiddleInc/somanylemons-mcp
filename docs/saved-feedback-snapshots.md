# Saved agency feedback snapshots

The task tools may return `feedback_review_snapshot` alongside native agency research. The snapshot is an immutable selected-contact review batch. Its contacts are separate from native qualified-contact counts, full staff rosters and fulfillment of the original request.

Ordinary brief answers expose complete saved counts, the original/batch/remaining agency scope, source metadata, feedback hold and attachment descriptors. They omit contact and draft bodies. `get_research_answer(details=true)` returns five selected contacts per page with exact saved CSV fields and authored introduction copy, plus six-step draft metadata per selected contact. Sequence steps are not people. Monthly bodies remain in the saved sequence CSV rather than being expanded into the compact answer.

Recorded verification is historical, not a fresh deliverability check. Saved provider acceptance/readback does not prove inbox receipt. Missing dates, conflicting titles, unavailable emails and field-level source/uncertainty strings retain their saved meaning. Reading a snapshot or attachment never starts research, sends mail, enrolls prospects, changes memberships or clears a feedback hold.

## Lineage and controls

Native current-answer navigation follows only the backend's task-level pointer and exact typed agency scope: workflow, config, client, organization, campaign and original goal. Requested original controls/progress remain separately labeled from the returned current goal's version and actions. Authentication and account-owned cross-workspace visibility remain enforced by the existing backend scope; source client/organization IDs are not compared with the operator's user ID or login workspace.

The feedback DTO has its own source lineage. Its nested pointer alone never creates a native pointer. A present invalid saved binding returns an explicit unavailable snapshot; it must not be substituted with fabricated contacts or a local file.

`agency.feedback_review_snapshot.v2` publishes the specifically reviewed saved batch. The code-owned `publication_validation` binding records the independently reviewed **current validation revisions** and receipt hash. These are compared with the current requested goal, source and attachment validation metadata. Changing a task and every source/descriptor revision label together still fails the fixed reviewed binding. This prevents silent attribution of immutable files to an unreviewed new revision.

**Delivery-time revisions were not recorded.** `delivered_original_contract_revision`, `delivered_pilot_contract_revision` and attachment `delivered_contract_revision` remain `null`, with `delivered_revision_status: "not_recorded"`. Never present current validation revisions as proof of historical delivery revisions. A future amendment requires a new reviewed publication binding.

## Authenticated attachments

Descriptors expose `mcp_resource_uri` in this form:

```
producerspark-feedback-attachment://{requested_goal_id}/{delivery_job_id}/{safe_filename_utf8_hex}/{raw_sha256}
```

The resource consumer reads the exact requested goal's answer, validates the saved customer/config/goal/revision/hold binding, then retrieves the matching original bytes through the existing account-scoped Tasks client:

```
GET /api/v1/agent-tasks/{requested_goal_id}/feedback-snapshots/{delivery_job_id}/attachments
```

The filename and SHA-256 are query parameters. The response must match the saved raw size/hash. Both standalone task resources and hosted JSON-RPC resource reads preserve the recorded XLSX or CSV MIME type. Foreign paths, unsafe filenames, duplicate query values, wrong hashes, wrong revisions and corrupted bytes fail closed.

These URLs require authentication; they are not unsigned share links. A saved delivery job is never a TaskArtifact ID. No local filesystem fallback or new credential is involved. The existing 20 task tools and 49 hosted tools remain available.

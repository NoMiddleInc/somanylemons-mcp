"""Typed MCP tools; deliberately no Django models, workers or alternate task state."""

import asyncio
import sys
import time
from collections import Counter
from typing import Annotated, Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.types import ResourceLink, ToolAnnotations
from pydantic import BaseModel, Field

from .client import TaskApiClient, TaskApiConfig, TaskApiError

PositiveId = Annotated[int, Field(gt=0)]
ContactCount = Annotated[int, Field(ge=1, le=10)]
AgencyName = Annotated[str, Field(min_length=1, max_length=200)]


class AgencyIdentity(BaseModel):
    name: AgencyName
    domain: Annotated[str, Field(min_length=1, max_length=253)] | None = None
    evidence: Annotated[str, Field(max_length=2000)] | None = None


AgencyInput = AgencyName | AgencyIdentity
Agencies = Annotated[list[AgencyInput], Field(min_length=1, max_length=20)]
Reason = Annotated[str, Field(min_length=1, max_length=1000)]
TaskView = Literal[
    "", "needs_attention", "in_progress", "scheduled", "waiting_customer", "completed"
]

READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
CANCEL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)


def agency_payload(agencies):
    return [
        item.model_dump(exclude_none=True) if isinstance(item, AgencyIdentity) else item
        for item in agencies
    ]


def compact_research_answer(task, agency_page=1, contact_page=1, source_page=1):
    """Project only saved, current-scope customer facts; never qualify rows locally."""
    result = {key: task.get(key) for key in (
        "id", "title", "state", "fulfillment", "progress", "next_action",
        "next_run_at", "artifacts", "version", "allowed_actions",
    )}
    conference = task.get("conference_answer")
    if isinstance(conference, dict):
        rows = conference.get("rows", [])
        result["conference_answer"] = {
            key: value for key, value in conference.items() if key not in {"rows", "sources"}
        }
        result["contacts"] = []
        for row in rows[(contact_page - 1) * 5 : contact_page * 5]:
            projected = {
                key: value
                for key, value in row.items()
                if key not in {"evidence_refs", "research_evidence", "sources", "public_sources"}
            }
            for key in ("email_candidates", "employer_email_candidates", "public_company_research"):
                candidates = row.get(key) or []
                projected[key + "_total"] = row.get(key + "_total", len(candidates))
                projected[key] = candidates[:5]
                if key != "email_candidates":
                    projected[key] = [
                        {field: ({key: str(text)[:300] for key, text in value.items() if key in {"name_quote", "context_quote", "href_quote"}} if field == "email_evidence" and isinstance(value, dict) else str(value)[:500] if field == "email_evidence" else value)
                         for field, value in candidate.items()
                         if field in {"company", "email", "email_status", "email_source", "email_observed_at", "email_source_hash", "email_evidence", "public_research_status", "public_research_blocker", "public_research_partial_access"}}
                        for candidate in projected[key] if isinstance(candidate, dict)
                    ]
            for key in ("quality_note", "reason"):
                if isinstance(projected.get(key), str) and len(projected[key]) > 300:
                    projected[key] = projected[key][:300]
                    projected[key + "_truncated"] = True
            evidence = row.get("evidence_refs", [])
            projected["evidence_refs"] = [
                {
                    key: (value[:300] if key == "quote" and isinstance(value, str) else value)
                    for key, value in ref.items()
                    if key in {"url", "source_url", "content_hash", "observed_at", "quote"}
                }
                for ref in evidence[:5]
                if isinstance(ref, dict)
            ]
            projected["evidence_refs_total"] = len(evidence)
            result["contacts"].append(projected)
        result["sources"] = conference.get("sources", [])[(source_page - 1) * 3 : source_page * 3]
        result["pagination"] = {
            "contact_page": contact_page,
            "contact_page_size": 5,
            "rows_total": len(rows),
            "source_page": source_page,
            "source_page_size": 3,
            "sources_total": len(conference.get("sources", [])),
        }
        result["request"] = task.get("contract", {}).get("request")
        return result
    answer = task.get("research_answer")
    result["research_answer"] = dict(answer) if isinstance(answer, dict) else None
    agencies = answer.get("agencies", []) if isinstance(answer, dict) else []
    if result["research_answer"] is not None and "agencies" in answer:
        selected_agencies = []
        for agency in agencies[(agency_page - 1) * 5:agency_page * 5]:
            projected = {key: value for key, value in agency.items() if key != "sources"}
            sources = agency.get("sources", [])
            projected["sources"] = sources[(source_page - 1) * 3:source_page * 3]
            projected["sources_total"] = len(sources)
            projected["sources_omitted"] = len(sources) - len(projected["sources"])
            selected_agencies.append(projected)
        result["research_answer"]["agencies"] = selected_agencies
    # The facade already limits child tasks to the accepted revision. Older
    # responses omit revision metadata; never assume those current tasks are v1.
    revision = task.get("contract_revision") or (task.get("contract") or {}).get("task_manager", {}).get("revision")
    tasks = [row for row in task.get("tasks", []) if revision is None or row.get("contract_revision", 1) == revision]
    contract = task.get("contract") or {}
    result["request"] = {key: contract[key] for key in ("request", "count") if key in contract}
    if "agencies" in contract:
        requested = contract["agencies"]
        result["request"]["agencies"] = [{key: row[key] for key in ("name", "domain") if key in row} if isinstance(row, dict) else row for row in requested[(agency_page - 1) * 5:agency_page * 5]]
        result["request"]["agencies_total"] = len(requested)
    drafts = {}
    for child in tasks:
        if child.get("capability") == "agency.artifact_prepare":
            for row in child.get("result", {}).get("rows", []):
                if row.get("id"):
                    drafts[str(row["id"])] = row
    accepted = {row.get("agency"): row.get("qualified_contacts", 0) for row in agencies}
    contacts = {}
    fields = (
        "id", "name", "title", "company", "location", "email", "email_status",
        "email_verified_at", "email_source", "email_source_url", "email_observed_at", "email_content_hash", "provider_email_status", "enrichment_status", "enrichment_source", "enriched_on",
        "evidence_status", "reason", "fit", "sources", "research_evidence",
        "intro_email_draft", "intro_draft_source_url", "intro_draft_claims", "intro_draft_customer_context",
    )
    for child in tasks:
        if child.get("capability") != "agency.research":
            continue
        saved = child.get("result", {})
        agency = (saved.get("agency") or {}).get("name", child.get("title"))
        allowed_count = accepted.get(agency, 0)
        for row in saved.get("rows", [])[:allowed_count]:
            if not row.get("id"):
                continue
            identity = str(row["id"])
            combined = {**row, **{key: value for key, value in drafts.get(identity, {}).items() if key.startswith("intro_")}}
            contacts[identity] = {key: combined[key] for key in fields if key in combined and key not in {"sources", "intro_draft_customer_context", "intro_draft_claims"}}
            if row.get("linkedin"):
                contacts[identity]["provider_reported_professional_profile_url"] = row["linkedin"]
                contacts[identity]["professional_profile_evidence_basis"] = "Provider-reported URL; opened or blocked observations are recorded separately in source_references."
            reason = contacts[identity].get("reason")
            if isinstance(reason, str) and len(reason) > 800:
                contacts[identity]["reason"] = reason[:800]
                contacts[identity]["reason_excerpt_truncated"] = True
            evidence = contacts[identity].get("research_evidence", [])
            selected_evidence = sorted((entry for entry in evidence if isinstance(entry, dict)), key=lambda entry: not bool(entry.get("source_url")))
            contacts[identity]["research_evidence"] = [{**entry, "quote": entry.get("quote", "")[:300]} for entry in selected_evidence[:5]]
            contacts[identity]["research_evidence_total"] = len(evidence)
            contacts[identity]["evidence_excerpts_truncated"] = len(evidence) > 5 or any(len(entry.get("quote", "")) > 300 for entry in evidence if isinstance(entry, dict))
            contacts[identity]["source_references"] = [
                {key: source[key] for key in ("url", "access", "observed_at", "content_hash") if key in source}
                for source in row.get("public_sources", []) if isinstance(source, dict)
            ][(source_page - 1) * 3:source_page * 3]
            contacts[identity]["source_references_total"] = len(row.get("public_sources", []))
            for saved_key, public_key in (("task_paid_receipt", "saved_enrichment_provenance"), ("task_saved_email_receipt", "saved_email_provenance")):
                provenance = row.get(saved_key)
                if isinstance(provenance, dict):
                    contacts[identity][public_key] = {key: provenance[key] for key in ("email", "status", "email_verified_at", "input_hash", "receipt_hash", "observed_at") if key in provenance}
            public_email = row.get("task_public_email_receipt")
            if isinstance(public_email, dict):
                contacts[identity]["public_email_provenance"] = {key: public_email[key] for key in ("email", "provider_person_id", "name", "company", "linkedin", "source_url", "observed_at", "content_hash") if key in public_email}
                quotes = {key: public_email[key][:300] for key in ("name_quote", "context_quote", "href_quote") if isinstance(public_email.get(key), str)}
                contacts[identity]["public_email_provenance"].update(quotes)
                contacts[identity]["public_email_quotes_truncated"] = any(len(public_email.get(key, "")) > 300 for key in quotes)
    result["contacts"] = list(contacts.values())[(contact_page - 1) * 5:contact_page * 5]
    result["email_gaps"] = [{key: row[key] for key in ("id", "name", "company", "email", "email_status", "email_verified_at", "email_source", "email_source_url", "email_observed_at", "email_content_hash", "provider_email_status", "enrichment_status", "enrichment_source", "enriched_on") if key in row} for row in contacts.values() if not str(row.get("email") or "").strip()][:5]
    result["public_unverified_email_examples"] = [{key: row[key] for key in ("id", "name", "company", "email", "email_status", "email_verified_at", "email_source", "email_source_url", "email_observed_at", "email_content_hash", "provider_email_status", "public_email_provenance", "public_email_quotes_truncated") if key in row} for row in contacts.values() if row.get("email_status") == "public_source_unverified"][:5]
    result["public_unverified_email_example_scope"] = "Up to five published-address examples across saved qualified contacts; not exhaustive coverage and not a fresh deliverability check."
    result["saved_email_status_counts"] = dict(Counter(str(row.get("email_status") or "not_recorded") for row in contacts.values()))
    result["saved_enrichment_status_counts"] = dict(Counter(str(row.get("enrichment_status") or "not_recorded") for row in contacts.values()))
    result["pagination"] = {"agency_page": agency_page, "agency_page_size": 5, "agencies_total": len(agencies), "contact_page": contact_page, "contact_page_size": 5, "contacts_total": len(contacts), "email_gap_preview_limit": 5, "source_page": source_page, "source_page_size": 3}
    blocker = task.get("blocker")
    result["blocker"] = ({"party": blocker.get("party"), "reason": "Research needs an internal review before completion." if blocker.get("party") == "operator" else blocker.get("reason")} if isinstance(blocker, dict) else None)
    if task.get("state") == "waiting_customer":
        result["customer_next_step"] = "Provide the agency names and their website domains when identity clarification is needed. The saved request will continue once the requested input is supplied."
    return result


def create_server(api: TaskApiClient) -> FastMCP:
    server = FastMCP(
        "ProducerSpark Tasks",
        instructions=(
            "Manage the authenticated account's durable customer research requests. Read the current task before controls; "
            "use its version and allowed_actions. For every write generate a UUID idempotency_key once and reuse it after "
            "transport failures. Only create or amend work the user requested. Research may lead to delivery through the "
            "backend's existing authorized customer channel and budget/quality gates. These tools do not authorize prospect "
            "outreach, memberships, meeting briefs, new recipients or bypassing completion checks. API keys remain pinned to their owner and organization; a token never grants extra authority. An acknowledgment is not completion. "
            "For an immediate research answer, create the request once and use wait_for_task repeatedly until completed or a real blocker appears. "
            "A wait timeout means work continues in production; reuse the same goal_id, never recreate the request. "
            "Use get_research_answer for a compact customer answer from the task's research_answer field, including its quantitative and qualitative findings. "
            "Use get_task for compact current steps and control metadata. Request include_history only for operator event identifiers/dates. "
            "Include contact counts, actual email coverage, sources and limitations; never invent prospects or claim delivery before its recorded success."
            " When quoting a saved intro_email_draft, copy its exact characters and paragraph breaks, including Unicode curly apostrophes; do not normalize punctuation or rewrite the body. "
            "A false delivery_verified value means delivery has not been verified by that answer; it does not establish that no delivery occurred. Reconcile it with the saved task fulfillment and delivery records before making delivery claims."
            " Describe retrieval side effects separately from historical delivery: say no new email was sent during this retrieval, then state the recorded customer-workbook delivery status. Never use an unqualified no email was sent when saved delivery exists. Distinguish authorized customer workbook delivery from prospect outreach. The progress counter measures worker steps, never contacts. Use research_answer.counts, saved_email_status_counts and email_gaps for actual contact/email coverage. "
            "An email_status of public_source_unverified means an address was published on the recorded official source, not verified deliverable. Report email_source_url/email_observed_at separately from saved provider enrichment and its earlier verification status; never treat the public observation date as a verification date. An unfinished overall request can contain completed saved enrichment and verification for individual contacts; report those dates/statuses without claiming the full request is finished."
        ),
    )

    @server.tool(annotations=READ)
    async def list_tasks(
        state: TaskView = "", page: Annotated[int, Field(ge=1, le=100000)] = 1
    ) -> dict:
        """List customer obligations with actual progress, blockers, next time and available actions."""
        return await api.request(
            "GET", "/api/v1/agent-tasks", params={"state": state, "page": page}
        )

    @server.tool(annotations=READ)
    async def get_task(goal_id: PositiveId, include_history: bool = False) -> dict:
        """Read compact saved results and control version/actions. Only explicitly request include_history for bounded operator event history; checkpoints and raw source pages are never returned."""
        task = await api.request("GET", f"/api/v1/agent-tasks/{goal_id}", params={} if include_history else {"view": "answer"})
        answer = compact_research_answer(task)
        steps = task.get("tasks", [])
        answer["steps"] = [{key: step[key] for key in ("id", "capability", "state", "contract_revision") if key in step} for step in steps[:20]]
        answer["steps_total"] = task.get("progress", {}).get("total", len(steps))
        answer["step_metadata_returned"] = len(answer["steps"])
        answer["step_preview_scope"] = "current research and preparation metadata" if not include_history else "bounded current task metadata"
        answer["step_preview_complete"] = len(answer["steps"]) == answer["steps_total"]
        if include_history:
            events = task.get("events", [])
            answer["operator_history"] = [{key: event[key] for key in ("id", "event_type", "created_at") if key in event} for event in events[-20:]]
            answer["operator_history_total"] = len(events)
        return answer

    @server.tool(annotations=READ)
    async def get_research_answer(
        goal_id: PositiveId,
        agency_page: Annotated[int, Field(ge=1)] = 1,
        contact_page: Annotated[int, Field(ge=1)] = 1,
        source_page: Annotated[int, Field(ge=1)] = 1,
    ) -> dict:
        """Read compact saved quantitative/qualitative results, source-backed drafts and verification dates. Copy saved draft strings character-for-character, including Unicode punctuation and paragraphs. Prefer this for customer questions; get_task includes bounded current steps and explicitly requested operator history. This never starts research or delivery."""
        task = await api.request("GET", f"/api/v1/agent-tasks/{goal_id}", params={"view": "answer"})
        return compact_research_answer(task, agency_page, contact_page, source_page)

    @server.tool(annotations=READ)
    async def wait_for_task(
        goal_id: PositiveId,
        timeout_seconds: Annotated[int, Field(ge=0, le=50)] = 45,
    ) -> dict:
        """Wait for existing background research to complete or need attention. On timeout call again with the same goal_id; work persists independently of Claude. Returns the full saved results and a truthful wait_status."""
        deadline = time.monotonic() + timeout_seconds
        stop_states = {
            "completed", "satisfied", "cancelled", "superseded", "failed",
            "needs_attention", "blocked", "waiting_customer", "waiting_external", "paused",
        }
        task = None
        while True:
            if task is not None and deadline - time.monotonic() < 1:
                return {"wait_status": "still_running", "task": compact_research_answer(task)}
            task = await api.request(
                "GET", f"/api/v1/agent-tasks/{goal_id}",
                params={"view": "answer"},
                timeout_seconds=min(10.0, max(0.1, deadline - time.monotonic())) if timeout_seconds else 10.0,
            )
            state = task.get("state")
            if state in stop_states:
                return {"wait_status": "finished" if state in {"completed", "satisfied"} else "needs_attention", "task": compact_research_answer(task)}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"wait_status": "still_running", "task": compact_research_answer(task)}
            await asyncio.sleep(min(5, remaining))

    @server.tool(annotations=WRITE)
    async def create_research_request(
        agencies: Agencies,
        idempotency_key: UUID,
        config_id: PositiveId | None = None,
        title: Annotated[str, Field(max_length=200)] | None = None,
        count: ContactCount | None = None,
    ) -> dict:
        """Create explicitly requested agency research using existing criteria, execution availability and account budget. Delivery follows backend policy."""
        body = {"agencies": agency_payload(agencies), "idempotency_key": str(idempotency_key)}
        body.update(
            {
                key: value
                for key, value in {"config_id": config_id, "title": title, "count": count}.items()
                if value is not None
            }
        )
        return compact_research_answer(await api.request("POST", "/api/v1/agent-tasks", body=body))

    @server.tool(annotations=WRITE)
    async def create_conference_research_request(
        event_id: Literal["acams-las-vegas-2026"],
        request: Annotated[str, Field(min_length=1, max_length=2000)],
        idempotency_key: UUID,
        config_id: PositiveId | None = None,
    ) -> dict:
        """Research a supported conference in production using existing account limits. Creates durable work with review and communication holds; does not send emails. Reuse the UUID on uncertain retries, then wait_for_task or get_research_answer with the returned goal ID."""
        body = {"event_id": event_id, "request": request, "idempotency_key": str(idempotency_key)}
        if config_id is not None:
            body["config_id"] = config_id
        return compact_research_answer(
            await api.request("POST", "/api/v1/agent-tasks/conferences", body=body)
        )

    async def control(goal_id, action, expected_version, idempotency_key, reason, inputs=None):
        body = {
            "action": action,
            "expected_version": expected_version,
            "idempotency_key": str(idempotency_key),
            "reason": reason,
        }
        if inputs is not None:
            body["inputs"] = inputs
        return compact_research_answer(await api.request("POST", f"/api/v1/agent-tasks/{goal_id}/actions", body=body))

    @server.tool(annotations=WRITE)
    async def supply_agency_names(
        goal_id: PositiveId,
        agencies: Agencies,
        expected_version: PositiveId,
        idempotency_key: UUID,
        reason: Reason,
        count: ContactCount | None = None,
    ) -> dict:
        """Supply customer-provided agency names to a waiting request. This can make already authorized research runnable."""
        return await control(
            goal_id,
            "supply_input",
            expected_version,
            idempotency_key,
            reason,
            {
                "agencies": agency_payload(agencies),
                **({"count": count} if count is not None else {}),
            },
        )

    @server.tool(annotations=WRITE)
    async def pause_task(
        goal_id: PositiveId, expected_version: PositiveId, idempotency_key: UUID, reason: Reason
    ) -> dict:
        """Pause future work if allowed by the current task. Existing external operations still require reconciliation."""
        return await control(goal_id, "pause", expected_version, idempotency_key, reason)

    @server.tool(annotations=WRITE)
    async def resume_task(
        goal_id: PositiveId, expected_version: PositiveId, idempotency_key: UUID, reason: Reason
    ) -> dict:
        """Resume paused, user-authorized work under the existing scope and budget."""
        return await control(goal_id, "resume", expected_version, idempotency_key, reason)

    @server.tool(annotations=CANCEL)
    async def cancel_task(
        goal_id: PositiveId, expected_version: PositiveId, idempotency_key: UUID, reason: Reason
    ) -> dict:
        """Cancel future execution at the user's request. Cannot undo an already accepted send."""
        return await control(goal_id, "cancel", expected_version, idempotency_key, reason)

    @server.tool(annotations=WRITE)
    async def retry_task(
        goal_id: PositiveId,
        task_id: PositiveId,
        expected_version: PositiveId,
        idempotency_key: UUID,
        reason: Reason,
    ) -> dict:
        """Request a safe retry; the backend reconciles uncertain paid calls/sends and checks current budget and authority."""
        return await control(
            goal_id, "retry", expected_version, idempotency_key, reason, {"task_id": task_id}
        )

    @server.tool(annotations=READ)
    async def list_schedules() -> dict:
        """List recurring research schedules with their next occurrence, timezone and enabled state."""
        return await api.request("GET", "/api/v1/agent-tasks/schedules")

    @server.tool(annotations=WRITE)
    async def create_research_schedule(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        agencies: Agencies,
        idempotency_key: UUID,
        config_id: PositiveId | None = None,
        count: ContactCount = 5,
        cadence: Literal["daily", "weekly"] = "daily",
        timezone: str = "America/Chicago",
        local_hour: Annotated[int, Field(ge=0, le=23)] = 9,
        local_minute: Annotated[int, Field(ge=0, le=59)] = 0,
        weekday: Annotated[int, Field(ge=0, le=6)] = 0,
    ) -> dict:
        """Create explicitly requested recurring research. Weekday is Monday=0; account budget, quality and delivery policy apply to every occurrence."""
        body = {
            "name": name,
            "agencies": agency_payload(agencies),
            "idempotency_key": str(idempotency_key),
            "count": count,
            "cadence": cadence,
            "timezone": timezone,
            "local_hour": local_hour,
            "local_minute": local_minute,
            "weekday": weekday,
        }
        if config_id is not None:
            body["config_id"] = config_id
        return await api.request("POST", "/api/v1/agent-tasks/schedules", body=body)

    @server.tool(annotations=WRITE)
    async def set_schedule_enabled(schedule_id: PositiveId, is_enabled: bool) -> dict:
        """Enable or pause an existing research schedule when the user requests it. This updates future occurrences only."""
        return await api.request(
            "POST", f"/api/v1/agent-tasks/schedules/{schedule_id}", body={"is_enabled": is_enabled}
        )

    @server.tool(annotations=READ, structured_output=False)
    async def get_task_artifact(goal_id: PositiveId, artifact_id: PositiveId) -> list[ResourceLink]:
        """Get a saved artifact as an authenticated MCP resource. Read the resource to obtain the workbook bytes; this never triggers delivery."""
        goal = await api.request("GET", f"/api/v1/agent-tasks/{goal_id}")
        if not any(str(row.get("id")) == str(artifact_id) for row in goal.get("artifacts", [])):
            raise TaskApiError("No accessible artifact with that ID belongs to this task.")
        return [
            ResourceLink(
                type="resource_link",
                uri=f"producerspark-task-artifact://{goal_id}/{artifact_id}",
                name=f"task-{goal_id}-artifact-{artifact_id}.xlsx",
                mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                description="Saved task deliverable; backend authorization and artifact checks apply on read.",
            )
        ]

    @server.resource(
        "producerspark-task-artifact://{goal_id}/{artifact_id}",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    async def task_artifact_resource(goal_id: str, artifact_id: str) -> bytes:
        """Read saved workbook bytes through the same authorized backend facade."""
        try:
            goal, artifact = int(goal_id), int(artifact_id)
        except ValueError:
            raise TaskApiError("Artifact and task IDs must be positive integers.") from None
        if goal <= 0 or artifact <= 0:
            raise TaskApiError("Artifact and task IDs must be positive integers.")
        return await api.request(
            "GET", f"/api/v1/agent-tasks/{goal}/artifacts/{artifact}", binary=True
        )

    return server


def main():
    try:
        api = TaskApiClient(TaskApiConfig.from_environment())
    except TaskApiError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    create_server(api).run(transport="stdio")


if __name__ == "__main__":
    main()

"""Typed MCP tools; deliberately no Django models, workers or alternate task state."""

import asyncio
import json
import re
import sys
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import ResourceLink, ToolAnnotations
from pydantic import BaseModel, Field, JsonValue

from .client import TaskApiClient, TaskApiConfig, TaskApiError
from .feedback_review import brief_feedback_projection, feedback_projection, read_feedback_attachment_resource
from .answer_navigation import bounded_examples_and_artifacts, list_task_navigation
from .current_answer import history_metadata, recorded_delivery_history, resolution_metadata, resolve_current_answer
from .business_research import BusinessResearchSpec, saved_business_answer
from .live_progress import live_update, contact_table_metadata

PositiveId = Annotated[int, Field(gt=0)]
ContactCount = Annotated[int, Field(ge=1, le=5000)]
AgencyName = Annotated[str, Field(min_length=1, max_length=200)]


class AgencyIdentity(BaseModel):
    name: AgencyName
    domain: Annotated[str, Field(min_length=1, max_length=253)] | None = None
    evidence: Annotated[str, Field(max_length=2000)] | None = None


AgencyInput = AgencyName | AgencyIdentity
Agencies = Annotated[list[AgencyInput], Field(min_length=1, max_length=200)]
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


def public_blocker(blocker):
    """Expose only canonical shared approval or historical quota diagnostics."""
    if not isinstance(blocker, dict):
        return None
    reason = blocker.get("reason")
    if blocker.get("party") == "operator" and (
        blocker.get("code") == "business_conference_human_review_required"
        or reason == "business_conference_human_review_required"
    ):
        return {"party": "operator", "code": "business_conference_human_review_required", "reason":
            "Our team is on it. Your conference speaker email request is saved for human review. "
            "You do not need to resend it."}
    if blocker.get("party") == "operator":
        approval = re.fullmatch(
            r"Apollo approval required after ([0-9]{1,10}) total attempted billable contacts\. "
            r"No provider request was sent\.",
            reason if isinstance(reason, str) else "",
        )
        matched = re.fullmatch(
            r"Prospect enrichment quota exceeded\. ([0-9]{1,10}) enrichment credits remain until "
            r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]))\.",
            reason if isinstance(reason, str) else "",
        )
        source_issue = "managed_browser_edition_unverified" in str(reason)
        reason = (
            "The publisher page was opened, but the requested event edition and dates could not be verified. "
            "Saved progress is retained; this does not establish that the browser or research service is unavailable."
            if source_issue else "Research needs an internal review before completion."
        )
        if approval:
            reason = (
                "ProducerSpark approval needed to extend the shared Apollo allowance of "
                f"{int(approval[1])} conservative billable lookup attempts. "
                "No provider request was sent."
            )
        elif matched:
            try:
                reset_at = datetime.fromisoformat(matched[2].replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                reason = (
                    f"ProducerSpark enrichment quota exceeded: {int(matched[1])} credits "
                    f"remain until {reset_at.isoformat()}."
                )
    return {"party": blocker.get("party"), "reason": reason}


def public_apollo_allowance(budget):
    """Copy canonical current lookup authority without diagnostics or arithmetic."""
    if not isinstance(budget, dict):
        return None
    result = {
        key: budget[key]
        for key in ("limit_credits", "reserved_credits", "remaining_credits", "checkpoint_size")
        if type(budget.get(key)) is int and budget[key] >= 0
    }
    if not result:
        return None
    for key, canonical in (
        ("scope", "shared Apollo account"),
        ("accounting", "conservative attempted billable lookup units, not actual invoiced credits"),
    ):
        if type(budget.get(key)) is str and budget[key] == canonical:
            result[key] = canonical
    return result


def compact_research_answer(task, agency_page=1, contact_page=1, source_page=1):
    """Project only saved, current-scope customer facts; never qualify rows locally."""
    from .provenance import bounded_email_provenance
    result = {key: task.get(key) for key in (
        "id", "title", "state", "fulfillment", "progress", "next_action", "current_answer_goal_id",
        "current_step", "running", "updated_at", "acknowledgment", "interpretation_version",
        "next_run_at", "artifacts", "version", "allowed_actions", "manual_review_required", "action_is_scheduled",
    )}
    allowance = public_apollo_allowance(task.get("apollo_credit_budget"))
    if allowance is not None:
        result["apollo_credit_budget"] = allowance
    conference = task.get("conference_answer")
    if isinstance(conference, dict):
        rows = conference.get("rows", [])
        result["conference_answer"] = {
            key: value for key, value in conference.items() if key not in {"rows", "sources"}
        }
        if isinstance(conference.get("enrichment_summary"), dict):
            result["conference_answer"]["enrichment_summary"] = bounded_email_provenance(conference["enrichment_summary"], source_page)
        event = result["conference_answer"].get("event")
        if isinstance(event, dict):
            # Declared fetch targets are not opened-source evidence. Preserve
            # observed citations separately in the actual sources projection.
            result["conference_answer"]["event"] = {
                key: value for key, value in event.items() if key != "source_urls"
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
                    key: (value[:160] if key == "quote" and isinstance(value, str) else value)
                    for key, value in ref.items()
                    if key in {"url", "source_url", "content_hash", "observed_at", "quote"}
                }
                for ref in evidence[:2]
                if isinstance(ref, dict)
            ]
            projected["evidence_refs_total"] = len(evidence)
            projected["evidence_refs_preview_scope"] = "At most two references with quote excerpts of at most 160 characters; full evidence remains in the saved artifact."
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
        current = result.get("current_answer_goal_id")
        if type(current) is int and current > 0:
            result["answer_is_current_recorded_goal"] = current == task.get("id")
        return bounded_examples_and_artifacts(result)
    business = saved_business_answer(task, result, contact_page=contact_page, source_page=source_page)
    if business is not None:
        business["blocker"] = public_blocker(task.get("blocker"))
        if (business["blocker"] or {}).get("code") == "business_conference_human_review_required":
            business["manual_review_required"] = True
            business["next_action"] = "awaiting_human_review"
        if not (business.get("business_answer") or {}).get("answer_text"):
            business.update(contact_table_metadata())
        return bounded_examples_and_artifacts(business)
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
            for row in (child.get("result") or {}).get("rows", []):
                if row.get("id"):
                    drafts[str(row["id"])] = row
    accepted = {row.get("agency"): row.get("qualified_contacts", 0) for row in agencies}
    contacts = {}
    fields = (
        "id", "name", "title", "company", "location", "city", "state", "email", "email_status",
        "email_verified_at", "email_source", "email_source_url", "email_observed_at", "email_content_hash", "provider_email_status", "enrichment_status", "enrichment_source", "enriched_on",
        "evidence_status", "reason", "fit", "sources", "research_evidence",
        "intro_email_draft", "intro_draft_source_url", "intro_draft_claims", "intro_draft_customer_context",
    )
    for child in tasks:
        if child.get("capability") != "agency.research":
            continue
        saved = child.get("result") or {}
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
    result["blocker"] = public_blocker(task.get("blocker"))
    if (result["blocker"] or {}).get("code") == "business_conference_human_review_required":
        result["manual_review_required"] = True
        result["next_action"] = "awaiting_human_review"
    if task.get("state") == "waiting_customer":
        result["customer_next_step"] = "Provide the agency names and their website domains when identity clarification is needed. The saved request will continue once the requested input is supplied."
    feedback = feedback_projection(task, contact_page=contact_page)
    if feedback is not None:
        result["feedback_review_snapshot"] = feedback
        if "answer_lineage_scope" in task:
            result["answer_lineage_scope"] = task["answer_lineage_scope"]
        result["native_contacts_basis"] = "Existing native qualified contacts; saved feedback-review selections and counts are separate."
    return result


def brief_answer(answer):
    """Keep status concise while exposing available business contacts immediately."""
    hidden = {"contacts", "sources", "pagination", "email_gaps", "public_unverified_email_examples",
              "public_unverified_email_example_scope", "steps", "step_metadata_returned",
              "step_preview_scope", "step_preview_complete", "steps_total"}
    result = {k: v for k, v in answer.items() if k not in hidden and v is not None}
    if isinstance(result.get("research_answer"), dict):
        result["research_answer"] = {k: v for k, v in result["research_answer"].items() if k != "agencies"}
    if "feedback_review_snapshot" in result:
        result["feedback_review_snapshot"] = brief_feedback_projection(result["feedback_review_snapshot"])
    result["details_available"] = "Use get_research_answer(details=true) for saved samples and provenance; read_task_spreadsheet for the full workbook."
    business = result.get("business_answer")
    if isinstance(business, dict) and not business.get("answer_text") and answer.get("contacts"):
        for field in ("contacts", "pagination", "email_gaps"):
            if field in answer:
                result[field] = answer[field]
        result["response_policy"] = (
            "Available saved contacts remain readable as Name | Company | Title | Email | LinkedIn. "
            "Preserve email verification and uncertainty; never label guessed emails verified. "
            "Use get_research_answer(details=true) with successive contact_page values to retrieve "
            "every available contact. Spreadsheet generation, export failure, unfinished research "
            "or internal delivery review must not hide contacts already returned by the backend. "
            "Describe these as saved results, not proof of full request fulfillment or file delivery."
        )
    if result.get("state") in {"pending", "running", "waiting_dependency", "queued"} and isinstance(result.get("business_answer"), dict) and not result["business_answer"].get("answer_text"):
        result["continue_watching"] = True
        result["response_policy"] = result.get("response_policy", "") + " " + (
            "Research is still active. Continue the existing watch in this response using "
            "watch_research with the same goal_id and returned cursor, or wait_for_task if "
            "watch_research is unavailable. A polling timeout is not completion. Do not "
            "ask the customer to check again or present a progress-only final answer. "
            "After completion use get_research_answer(details=true), paging contacts, "
            "or read_task_spreadsheet to return the full Name | Company | Title | Email | LinkedIn table."
        )
    if isinstance(business, dict):
        gate = live_update(answer, answer.get("id"))
        result["final_response_ready"] = gate["final_response_ready"]
        result.update(contact_table_metadata())
        if gate["final_response_ready"] and not business.get("answer_text"):
            result["response_policy"] = (
                "Return all requested current-goal saved contacts in the full Name | Company | Title | Email | LinkedIn table. "
                "Keep the Company column and repeat the recorded company on every row even for a single employer. "
                "An employer named in the introduction cannot replace that column. "
                "Preserve recorded email verification and uncertainty; never label guessed emails verified. "
                "Use get_research_answer(details=true) with successive contact_page values or read_task_spreadsheet for all rows. "
                "Use only this current goal's qualified saved rows and the recorded final fulfillment checks."
            )
        if gate["continue_watching"]:
            result["continue_watching"] = True
            result["response_policy"] = (
                "Show this current goal's qualified saved contacts immediately as Name | Company | Title | Email | LinkedIn, "
                "labelled with the saved count out of the requested count and recorded uncertainty. "
                "Then keep watching this current goal automatically until final_response_ready=true. "
                "Do not ask the customer to check again or end the response with saved preview contacts as the final result. "
                "A full count before final validation is still work in progress. "
                "Never combine contacts from historical goals to fill the count. "
                "After completion use get_research_answer(details=true) with successive contact_page values "
                "or read_task_spreadsheet and return the full Name | Company | Title | Email | LinkedIn table. "
                "Preserve recorded uncertainty; never label guessed emails verified."
            )
    return result


def research_progress_snapshot(answer):
    """Compare recorded work and coverage, excluding timestamp/version churn."""
    snapshot = {key: answer.get(key) for key in (
        "id", "state", "fulfillment", "current_step", "running", "progress",
        "next_action", "blocker", "artifacts", "manual_review_required",
    )}
    for key in ("research_answer", "business_answer", "conference_answer"):
        saved = answer.get(key)
        if isinstance(saved, dict):
            snapshot[key] = {field: saved.get(field) for field in (
                "counts", "coverage", "enrichment_summary", "full_request_fulfilled",
                "workflow", "state", "next_action", "blockers",
            )}
    return deepcopy(snapshot)


class TaskFastMCP(FastMCP):
    """Preserve each feedback attachment's recorded MIME in dynamic resources."""

    def __init__(self, api, *args, **kwargs):
        self._task_api = api
        super().__init__(*args, **kwargs)

    async def read_resource(self, uri):
        if str(uri).startswith("producerspark-feedback-attachment://"):
            content, mime = await read_feedback_attachment_resource(self._task_api, uri)
            return [ReadResourceContents(content=content, mime_type=mime)]
        return await super().read_resource(uri)


def create_server(api: TaskApiClient) -> FastMCP:
    server = TaskFastMCP(
        api,
        "ProducerSpark Tasks",
        instructions=(
            "Conference answer units: report saved name/company roster entries, not distinct people. Always distinguish email-bearing roster entries from distinct recorded email addresses. The canonical raw speaker/session appearances and the selected customer file's row_count describe different files. "
            "Missing-email causes and examples come only from the missing-email subset. A saved blocked status on an email-bearing entry is not another missing address; preserve and label it separately. Candidate evidence can remain on entries with a selected address, so candidate_people is never a count of missing or held addresses. State recorded reasons only; do not infer employer or provider causes from examples. "
            "Final research gates do not establish whether an authorized preliminary snapshot was already emailed. For past-delivery questions call get_task(include_history=true) and use recorded_delivery_history. Without that history the earlier delivery is unknown; never claim no email was authorized or sent from a false final gate. Sent-status history alone does not prove inbox delivery or which attachment bytes were sent. "
            "Use list_tasks to navigate explicitly requested existing obligations or discover account configurations, not to select a historical goal for a fresh prospect ask. Fresh prospect asks require a fresh UUID and goal with the original question verbatim; matching titles do not authorize reuse. Reuse only an explicit check/resume goal or a goal created earlier in this conversation. Do not use list_tasks to answer final research or delivery readiness. For a conference row with current_answer_goal_id, read get_research_answer for that exact goal. Listed original-goal worker progress and its review blocker are not the latest outcome's counts or all required delivery gates. Artifact row_count describes rows in that exact saved file. A canonical raw speaker/session-appearance count can differ from a customer presentation's combined roster rows; never transfer one file's count to another. Email-cell counts likewise differ from roster identity keys and distinct recorded addresses. For missing emails, use enrichment_summary.missing_email_people and missing_email_enrichment_status_counts_by_person. Global unfinished people can include people with a recorded email; use unfinished_with_recorded_email_people and unfinished_without_recorded_email_people instead of adding all global blockers to the missing-email causes. Completed enrichment without a selected address does not prove any particular provider returned empty results or that an address is genuinely unavailable. Identity unresolved does not mean unresolvable, structurally unavailable, or impossible to improve with further evidence. Use recorded_email_date_summary's separate clocks when present. A saved verified email status is not a fresh deliverability check and does not establish a recorded verification date. Missing verification/enrichment clocks stay unknown; never infer a person's review or verification date from their email observation date. Never assign a date to all saved_result_reused or completed people from an example, one source, a common timestamp, or a status label. Paginated exact/calendar groups retain their explicit omitted totals; a preview is not the entire cohort. Status counts describe a cumulative saved snapshot, not new provider calls, new sources opened, or actions performed by the current goal. A saved succeeded stage does not prove completion happened after the user's Claude session ended or support an elapsed-time calculation without the relevant recorded timestamps. conference_answer.sources describes captured organizer/program source evidence. Separately report recorded email-source observations from row provenance and public-source receipts; do not claim the organizer is the only observed source in the entire workflow while public email-source evidence is also recorded. Declared source_urls alone remain unobserved targets. Preserve the distinction between observed source access and independently proven full roster/session coverage. preliminary_review_artifact refers to the separate interim-snapshot capability. Its null value does not mean there is no saved workbook available for HERE-first inspection. A recorded canonical_review_artifact availability fact with matched scope/hash/validation can establish a validated review file; otherwise a top-level listed artifact only establishes retrieval availability. Offer or retrieve an authorized clearly labeled partial workbook here, state its actual gaps, and keep final fulfillment and sends held. A do-not-send instruction does not prohibit already-requested saved-file inspection or download here. Do not ask for additional permission to read or link the scoped saved file the user requested for review; this retrieval never authorizes email or clears send gates. Do not label an immutable saved artifact a live working document or say clearing internal review alone completes the research.  A positive current_answer_goal_id is backend-proven scoped navigation, including pending continuations without a new artifact. If answer_is_current_recorded_goal is false, follow that ID for current progress unless the user explicitly asks for the historical snapshot. saved_review_workbook.metadata_recorded establishes saved metadata and a scoped retrieval action, not verified bytes or final delivery; use its artifact_retrieval to inspect the authorized partial file here. Each email clock summary is complete for that distinct clock; missing-email status and unfinished-email-presence are separate partitions, not additive cohorts. "
            "For conference cardinality, follow enrichment_summary.identity_count_basis: unique_people and per-person totals can count saved name/company roster keys containing aliases, not independently resolved distinct individuals. Label that basis as roster entries or roster identity keys. Distinguish email-bearing roster keys, speaker/session email cells and distinct_recorded_email_addresses; use recorded_email_address_count_basis and the shared-address group counts when present. Newly filled roster keys need not add the same number of distinct addresses. Never infer identities or fresh deliverability from a shared address, or delete or merge alias rows. Preserve recorded rows, counts, clocks and every research/delivery gate. "
            "Keep user-facing replies about 80% shorter: normally at most 75 words, plus one download link. "
            "For new research submit the original request verbatim through create_business_research_request without spec or host-selected companies. The backend owns interpretation and background execution for all channels. Preserve its acknowledgment faithfully. "
            "For Email Agent status use get_email_agent_status; for list or outreach questions use ask_email_agent. "
            "Present the returned message faithfully, preserving its meaning, paragraph breaks and any clarification. Sent is a recorded provider send, never delivery or an open; queued/sending/uncertain/failed/skipped are not sent. Status reads do not send emails. "
            "Use the backend acknowledgment and concise progress wording. Use Markdown tables for contact results. Contact results use Name | Company | Title | Email | LinkedIn, one person per row. Preserve recorded uncertainty in Email status or Source columns when needed. Use Pending for unfinished or blocked lookup; Not found only after completed enrichment without a result. Progress, blockers, questions and next actions use Task | Status | Next step. Downloads use File | Status with clickable links. Honor an explicit request for another format. Omit internal steps and repeated caveats. "
            "For an ICP definition or targeting criteria use get_my_icp. For an ICP LIST, main list, golden list, prospect list or download my list use get_prospect_list. Discover all accessible lists with list_golden_lists and read every saved contact with read_golden_list. "
            "Return download_url as a clickable Markdown link with filename. Links expire after ten minutes; request a new link when expired. "
            "To analyze or clean a workbook use read_task_spreadsheet: it reads all normal-size saved rows in one call. "
            "Do not page contacts five at a time to retrieve a whole workbook. Never claim a workbook is inaccessible before trying these tools. "
            "Use the exact recorded status meaning: completed_public_email_unavailable means the completed public fallback did not record an address. Never paraphrase this as Apollo returning no result, unavailable in Apollo, or an employer/government cause. Do not add a structural public-email availability theory absent recorded evidence. Worker progress such as 135/136 counts task steps, never people or contacts. "
            "Email provenance calendar summaries group each distinct saved clock by calendar date; exact timestamp groups are paginated with source_page. Report their explicit totals and omitted counts, never treat a preview as all records. Contact pages contain five rows; use the returned pagination instead of inventing a workbook page size. "
            "Manage the authenticated account's durable customer research requests. Read the current task before controls; use its version and allowed_actions. "
            "Use create_business_research_request for new business-contact and conference questions across any industry or role. Conference speaker email requests require operator review; acknowledge that our team is on it, preserve the saved request, and do not ask the customer to retry or promise autonomous conference email completion. Explicit companies and roles govern this request; do not silently impose an older insurance ICP. For new company-contact requests, people outside golden lists are allowed. Preserve requested employer industry in industries and inclusive employee counts in employee_range with min and max; these filters never imply personal location or a saved insurance ICP. Preserve only the user's requested research fields and filters. For an unambiguous named-person email-only request, use fields=[\"email\"] and roles=[] unless the user explicitly requires a role. Do not add title, LinkedIn, current-officer proof, or deep research. A descriptive known role is an identity clue, not a required deliverable; explicit requests to identify or confirm a current role retain their role qualification. If the customer omits personal geography, leave person_locations empty with no inferred country restriction. Preserve explicit personal city/country constraints exactly; explicit worldwide/global likewise leaves person_locations empty. Employer geography stays in employer_locations and never implies personal residence. Preserve conference edition coverage without applying this people-search default. Do not change saved goals to apply a new default. Keep research_depth=standard unless the customer explicitly asks for deep research; deep mode permits broader public-source checks. Existing saved-list agency research remains a separate compatibility tool. Generic business results use business_answer with saved coverage and provenance. Future customer deliveries omit draft-email content and draft sequences. "
            "A business interim's completed/fulfilled state belongs only to that bounded result. Preserve original_goal_id and closure=interim_delivered_original_open; an open original_all_obligation is never fulfilled by child delivery. "
            "Before agency research, discover the account's enabled configurations with list_tasks and use campaign_id for the saved list/ICP the user selected. An OAuth login workspace does not hide the account's own agents. Never guess another customer's agent or switch canonical list bindings. "
            "For every write generate a UUID idempotency_key once and reuse it after "
            "transport failures. Only create or amend work the user requested. Research may lead to delivery through the "
            "backend's existing authorized customer channel and budget/quality gates. These tools do not authorize prospect "
            "outreach, memberships, meeting briefs, new recipients or bypassing completion checks. OAuth is limited to its owner's agents across active memberships; developer keys retain their organization boundary. A token never grants another customer's agent. An acknowledgment is not completion. "
            "Use watch_research as the primary live progress tool for interactive prospect finding: show saved counts, stage, evidence and meaningful changes, then suggest campaign planning after completion without sending. "
            "For an immediate research answer, create the request once and use watch_research with its returned cursor until completed or a real blocker appears. wait_for_task is the compatibility fallback only when watch_research is unavailable; never alternate polling tools for the same watch. A polling timeout or several unchanged responses never completes the customer's request. Keep watching in the active response and read the final full workbook; never finish by telling the customer to ask you to check again. For broad industry/geography requests, researched company choices are replaceable seeds: set company_scope=candidate_pool and preserve the actual customer question. "
            "Before the first wait and between waits, give a brief user-facing update using the recorded current_step, state, actual contact/email counts and blockers. "
            "Waits return within a short polling window or when saved progress changes. Never silently chain waits, invent a stage or ETA, or present worker steps as people. "
            "If no new progress is recorded, say that plainly; do not describe queued or blocked work as active research. "
            "A wait timeout means work continues in production; reuse the same goal_id, never recreate the request. "
            "Use get_research_answer for a compact customer answer from the task's research_answer field, including its quantitative and qualitative findings. "
            "watch_research, get_task, get_research_answer and wait_for_task follow the backend-recorded current answer by default. historical_snapshot=true reads the exact requested original goal. Returned id/version/allowed_actions belong together; requested_goal_controls and requested_goal_progress describe the original goal separately. Request include_history only for bounded operator event identifiers/dates; grouped history that does not identify the returned goal remains explicitly unavailable, not proof of no earlier email. "
            "apollo_credit_budget is current shared Apollo lookup authority; its numeric credit fields count conservative attempted billable lookup units, not provider balance or invoiced charges. "
            "Keep current allowance separate from recorded blocker text: an earlier monthly quota blocker does not establish today's allowance. Missing allowance fields are unknown, never zero. "
            "Available lookups do not clear review, model or source guards, authorize a retry, or prove completion. "
            "For one person's or one company's officer's email/LinkedIn lookup, submit the original request verbatim without spec; the backend determines the requested count and fields. Do not add current-role verification or public-source proof unless the customer explicitly asks for it. When business_answer includes answer_text, reply with that exact text only: email and LinkedIn, with no added commentary, table, sources, progress, task number or campaign suggestion. Otherwise include contact counts, actual email coverage, sources and limitations; never invent prospects or claim delivery before its recorded success."
            " feedback_review_snapshot is a separately authorized immutable agency pilot: use its selected_review_contact_count, recorded_business_email_count and sequence_step_count. These selected review contacts are not all native qualified_contacts; six draft steps per contact are not six people. Preserve the exact saved CSV field strings, authored draft punctuation, sources, uncertainty and missing dates. Report review_scope original/batch/remaining agency counts and remaining_work_held; a completed pilot or saved provider acceptance does not fulfill the original request or prove inbox receipt. Attachments use producerspark-feedback-attachment resources and authenticated backend paths, never TaskArtifact IDs or local paths. Retrieval does not resume research, clear feedback holds, enroll prospects or send new emails."
            " A feedback snapshot under the current no-draft policy omits draft copy and sequence counts; never reconstruct them. Its attachments identify historical original sent bytes, not a newly prepared customer deliverable. For a reviewed agency presentation continuation, the snapshot's review_scope and remaining_work_held describe the historical pilot. Current native task state/version/allowed_actions/progress and its original-goal current_answer_goal_id describe today's research separately; never turn the historical hold into a claim that current research is held or that the original ALL request was fulfilled."
            " When quoting a saved intro_email_draft, copy its exact characters and paragraph breaks, including Unicode curly apostrophes; do not normalize punctuation or rewrite the body. "
            "A false delivery_verified value means delivery has not been verified by that answer; it does not establish that no delivery occurred. Reconcile it with the saved task fulfillment and delivery records before making delivery claims."
            " Describe retrieval side effects separately from historical delivery: say no new email was sent during this retrieval, then state the recorded customer-workbook delivery status. Never use an unqualified no email was sent when saved delivery exists. Distinguish authorized customer workbook delivery from prospect outreach. The progress counter measures worker steps, never contacts. Use research_answer.counts, saved_email_status_counts and email_gaps for actual contact/email coverage. "
            "Declared supported event identity is registry metadata, not proof of an opened publisher page. Use publisher_source_observed and source_access_unproven; when research_not_started is true, say no contact research has been completed. An available operator retry is not a successful autonomous research solution. Do not promise an undeployed fallback. Use aggregate candidate_people and candidate_examples instead of walking every page to count candidates; aim for at most five tool calls for one question. For conferences, use conference_answer.enrichment_summary exact status_counts_by_person and blocker_counts_by_person; never extrapolate counts from the five-row preview. A succeeded enrichment controller or 43/44 completed worker steps does not mean enrichment is nearly complete. Foreground actual unfinished people and blockers. For conference review holds, needs_attention requires the recorded review action; do not invent a scheduled execution. For ordinary company contacts, an older internal hold is recoverable without a customer choice: read current version/steps/allowed_actions, invoke retry_task when retry is allowed, then watch the same goal. Safe retry is backend-controlled and never authorizes replaying unresolved paid operations or raising budgets. Historical failed runs do not establish that the deployed recovery will fail. Do not infer that government or any employer emails are impossible or will remain unavailable; state only recorded results and bounded research limitations. Candidate addresses remain unselected when identity is unresolved, even when shown in candidate_examples. The requested enrichment must be resolved before the review hold can be cleared for delivery. Do not treat acceptance of a subset as final fulfillment or clear the final delivery hold while requested research remains unfinished; only an explicit customer change to the final requirement can change that requirement, and that change must be persisted. Useful interim progress updates or clearly labeled partial snapshots are allowed when authorized: report actual completed counts, unresolved gaps, uncertainties and the remaining work, without claiming the full request is complete. A worker-step percentage is not a contact or research-completion percentage. Honor any show-here-first or communication hold before sending an interim deliverable; an interim update does not itself authorize customer or prospect outreach. Final delivery requires BOTH fulfillment of the requested enrichment and any required review approval; clearing one blocker does not satisfy the other. Say follow-up work remains owed, but claim a scheduled follow-up only when an actual persisted schedule is recorded. A retry action does not prove that completed research stages will rerun or that source/identity blockers will resolve. completed_public_email_unavailable means no address was recorded after the bounded completed lookup, never that no public email exists. Never attribute unavailable addresses to government/regulator or employer type, and never extrapolate an inaccessible preview page to all completed unavailable contacts. Canonical status_counts_by_person describes current person statuses; primary_enrichment_status and provider-attempt fields can describe an earlier lookup and must not replace those final status counts. Report recorded_email_enrichment_status_counts_by_person and recorded_email_provenance_groups to distinguish newly completed email records from reused records with their actual source, observation, verification and enrichment dates. An example row or common task/provider lookup clock is not the date of every email. Missing dates remain not recorded; a reused record keeps its historical date. "
            "An email_status of public_source_unverified means an address was published on the recorded official source, not verified deliverable. Report email_source_url/email_observed_at separately from saved provider enrichment and its earlier verification status; never treat the public observation date as a verification date. An unfinished overall request can contain completed saved enrichment and verification for individual contacts; report those dates/statuses without claiming the full request is finished."
        ),
    )

    @server.tool(annotations=READ)
    async def get_email_agent_status(
        config_id: PositiveId | None = None,
        campaign_id: PositiveId | None = None,
    ) -> dict:
        """Read current Email Agent on/off, enrolled, sent, queued, sending, uncertain, failed, skipped and replied counts. Uses the same saved facts and status paragraph as email replies. Select campaign_id from list_golden_lists when needed. Never sends email."""
        return await api.request(
            "GET",
            "/api/v1/agent-tasks/email-agent/status",
            params={
                key: value
                for key, value in {
                    "config_id": config_id,
                    "campaign_id": campaign_id,
                }.items()
                if value is not None
            },
        )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        )
    )
    async def ask_email_agent(
        question: Annotated[str, Field(min_length=1, max_length=2000)],
        config_id: PositiveId | None = None,
        campaign_id: PositiveId | None = None,
    ) -> dict:
        """Ask a read-only question about your list or Email Agent. The backend answers with the email agent's same model, facts and concise email reply style. Preserve the returned message and paragraph breaks. No email, list changes or research jobs are triggered. Uses saved facts, not a previous email thread; include names for follow-up questions."""
        return await api.request(
            "POST",
            "/api/v1/agent-tasks/email-agent/ask",
            body={
                "question": question,
                **{
                    key: value
                    for key, value in {
                        "config_id": config_id,
                        "campaign_id": campaign_id,
                    }.items()
                    if value is not None
                },
            },
            timeout_seconds=150.0,
        )

    @server.tool(annotations=READ)
    async def list_tasks(
        state: TaskView = "", page: Annotated[int, Field(ge=1, le=100000)] = 1
    ) -> dict:
        """Navigate explicitly requested existing obligations and account configurations. A fresh prospect ask requires a fresh UUID/goal with the original request verbatim; never substitute a historical goal solely because its title matches. Reuse only a goal explicitly requested for check/resume or created earlier in this conversation. Follow backend current_answer_goal_id with get_research_answer for current conference counts and all delivery gates. Listed progress counts original-goal worker steps."""
        response = await api.request(
            "GET", "/api/v1/agent-tasks", params={"state": state, "page": page}
        )
        return list_task_navigation(response)

    @server.tool(annotations=READ)
    async def get_task(goal_id: PositiveId, include_history: bool = False, historical_snapshot: bool = False) -> dict:
        """Read the current answer with matching version/actions; historical_snapshot=true reads the original. Conference totals count roster entries, not distinct individuals; email-bearing entries differ from distinct addresses. Use include_history=true for past-email questions: it reads bounded outgoing sent-status history and event metadata, never bodies or receipts. Final gates do not prove no earlier authorized snapshot was sent; sent status is not inbox or attachment proof."""
        resolved = await resolve_current_answer(api, goal_id, historical_snapshot=historical_snapshot)
        task = resolved.current
        answer = resolution_metadata(compact_research_answer(task), resolved)
        steps = task.get("tasks", [])
        answer["steps"] = [{key: step[key] for key in ("id", "capability", "state", "contract_revision") if key in step} for step in steps[:20]]
        answer["steps_total"] = task.get("progress", {}).get("total", len(steps))
        answer["step_metadata_returned"] = len(answer["steps"])
        answer["step_preview_scope"] = "current research and preparation metadata" if not include_history else "bounded current task metadata"
        answer["step_preview_complete"] = len(answer["steps"]) == answer["steps_total"]
        if include_history:
            history = await api.request("GET", f"/api/v1/agent-tasks/{task['id']}")
            answer["recorded_delivery_history"] = recorded_delivery_history(history)
            canonical_history = history_metadata(history, task["id"])
            answer["operator_history_available"] = canonical_history is not None
            answer["operator_history_goal_id"] = task["id"]
            answer["operator_history"] = canonical_history["events"] if canonical_history else None
            answer["operator_history_total"] = canonical_history["events_total"] if canonical_history else None
            answer["operator_history_version"] = canonical_history["version"] if canonical_history else None
            answer["operator_history_scope"] = canonical_history["scope"] if canonical_history else "Event metadata for the returned answer goal is not exposed by this grouped backend response. Use separately scoped recorded_delivery_history for saved outgoing delivery facts."
            if task["id"] != goal_id:
                original_history = history_metadata(history, goal_id)
                if original_history is None:
                    original_history = history_metadata(await api.request("GET", f"/api/v1/agent-tasks/{goal_id}"), goal_id)
                answer["requested_goal_history"] = original_history
        return answer if include_history else brief_answer(answer)

    @server.tool(annotations=READ)
    async def get_research_answer(
        goal_id: PositiveId,
        agency_page: Annotated[int, Field(ge=1)] = 1,
        contact_page: Annotated[int, Field(ge=1)] = 1,
        source_page: Annotated[int, Field(ge=1)] = 1,
        details: bool = False,
        historical_snapshot: bool = False,
    ) -> dict:
        """Read the current saved result; historical_snapshot=true preserves the original. Conference counts are roster entries with aliases, email-bearing entries and distinct addresses. Missing-email causes/examples use only the missing subset; candidate evidence can coexist with a selected address. Set details=true for samples/dates. Final gates do not prove no past send; inspect get_task(include_history=true) for that. Never starts research or delivery."""
        resolved = await resolve_current_answer(api, goal_id, historical_snapshot=historical_snapshot)
        answer = resolution_metadata(compact_research_answer(resolved.current, agency_page, contact_page, source_page), resolved)
        return answer if details or max(agency_page, contact_page, source_page) > 1 else brief_answer(answer)

    @server.tool(title="Watch live prospect research", annotations=READ)
    async def watch_research(
        goal_id: PositiveId,
        cursor: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None,
        timeout_seconds: Annotated[int, Field(ge=0, le=25)] = 25,
    ) -> dict:
        """Watch one existing research job. First call returns immediately; pass the returned cursor for subsequent bounded waits. Returns real saved prospect/email counts, stage, up to three findings with evidence, blockers and follow-up suggestions. Show qualified saved contacts from this current goal immediately, labelled with the saved count and recorded uncertainty. Keep calling automatically with the same requested goal_id and latest cursor while continue_watching=true. Only claim full completion after final_response_ready=true; do not end the response with a partial result or ask the customer to restart. Use returned next_tool_arguments, including the 25-second bounded wait, for the next watch. Ordinary queued/running stage churn is coalesced; new saved contacts, finality, actual blockers and review gates return promptly. Reading get_research_answer for saved rows does not permit a terminal response while final_response_ready=false. Stop only on the recorded terminal outcome or actual pause/blocker. Never interpret steps or preview rows as found prospects; never claim previews are newly discovered. If the user explicitly leaves or changes topics, backend work continues; this is not permission to end an active watch early. Campaign suggestions never send or enroll anyone."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            resolved = await resolve_current_answer(
                api, goal_id,
                timeout_seconds=min(10.0, max(0.1, deadline - time.monotonic())) if timeout_seconds else 10.0,
            )
            answer = resolution_metadata(compact_research_answer(resolved.current), resolved)
            update = live_update(answer, goal_id)
            changed = cursor != update["cursor"]
            remaining = deadline - time.monotonic()
            if changed or not update["continue_watching"] or remaining <= 0:
                return {**update, "changed": changed, "poll_after_seconds": 5 if update["continue_watching"] else None}
            await asyncio.sleep(min(5, remaining))
            if deadline - time.monotonic() < 0.1:
                return {**update, "changed": False, "poll_after_seconds": 5}

    @server.tool(title="Check research progress", annotations=READ)
    async def wait_for_task(
        goal_id: PositiveId,
        timeout_seconds: Annotated[int, Field(ge=0, le=50)] = 10,
        historical_snapshot: bool = False,
    ) -> dict:
        """Check existing background research, returning at a saved stage/count change or after at most a 10-second polling window (longer requested waits are capped). Give a compact user-facing status table BEFORE the first call and AFTER each response, using task.current_step/state, actual saved contact/email counts and blockers; never silently chain waits. progress_changed_during_wait compares polls within this call only: compare the returned saved fields with your previous response before saying no new progress was recorded. Do not invent activity, counts or an ETA. Worker steps are not contacts. historical_snapshot=true watches the exact requested goal. Reuse the same goal_id until complete or a real blocker; never creates work."""
        wait_seconds = min(timeout_seconds, 10)
        deadline = time.monotonic() + wait_seconds
        stop_states = {
            "completed", "satisfied", "cancelled", "superseded", "failed",
            "needs_attention", "blocked", "waiting_customer", "waiting_external", "paused",
        }
        task = None
        resolved = None
        initial_progress = None
        answer = None
        while True:
            if task is not None and deadline - time.monotonic() < 1:
                return {"wait_status": "still_running", "progress_changed_during_wait": False, "task": answer}
            resolved = await resolve_current_answer(
                api, goal_id, historical_snapshot=historical_snapshot,
                timeout_seconds=min(10.0, max(0.1, deadline - time.monotonic())) if wait_seconds else 10.0,
            )
            task = resolved.current
            answer = brief_answer(resolution_metadata(compact_research_answer(task), resolved))
            state = task.get("state")
            if state in stop_states:
                return {"wait_status": "finished" if state in {"completed", "satisfied"} else "needs_attention", "task": answer}
            current_progress = research_progress_snapshot(answer)
            if initial_progress is not None and current_progress != initial_progress:
                return {"wait_status": "still_running", "progress_changed_during_wait": True, "task": answer}
            initial_progress = current_progress
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"wait_status": "still_running", "progress_changed_during_wait": False, "task": answer}
            await asyncio.sleep(min(5, remaining))

    @server.tool(annotations=WRITE)
    async def create_business_research_request(
        request: Annotated[str, Field(min_length=1, max_length=10000)],
        idempotency_key: UUID,
        spec: BusinessResearchSpec | None = None,
        config_id: PositiveId | None = None,
        research_depth: Literal["standard", "deep"] = "standard",
        contact_provider: Literal["apollo", "treg"] | None = None,
    ) -> dict:
        """Research contacts in any industry and role, such as Dell CIO, using the same background interpretation as email. Submit the original customer request verbatim without rewriting it or preselecting companies. Omit spec for ordinary requests: the backend interprets count, roles, title equivalents, person versus employer geography, industry and size in its durable worker. Optional spec is retained as legacy client hints and never overrides the original question. The backend discovers eligible companies automatically. Use a fresh UUID for each fresh request even when a historical title matches; reuse a goal only for an explicit check/resume or work created earlier in this conversation. Reuse the exact UUID and inputs after an uncertain response. Return the backend acknowledgment faithfully and keep watching the returned goal while waiting_dependency, waiting_budget or retry_wait continues. Work persists after this connection closes. The shared qualification, artifact validation and authorized customer delivery rules apply; never invent contacts, emails, LinkedIn or completion. Specifically named people and employers cannot be replaced. No prospect outreach. For explicit provider comparisons contact_provider=treg selects TREG with Apollo excluded; contact_provider=apollo selects Apollo. Preserve explicit personal geography; when personal geography is omitted the backend uses the same account policy as email, never company headquarters as personal residence. Never add a country default or expand geography in the client. Final fulfillment requires the original requested count and criteria. For several named people, use one individual request per person with the original person-specific wording; never concatenate names or select a namesake without identity evidence. Preserve conference edition coverage. Only missing supported request details require clarification."""
        body = {
            "request": request,
            "idempotency_key": str(idempotency_key),
            "research_depth": research_depth,
            "intake_channel": "mcp_v1",
        }
        if config_id is not None:
            body["config_id"] = config_id
        if contact_provider is not None:
            body['contact_provider']=contact_provider
        if spec is not None:
            body["spec"] = spec.model_dump(exclude_none=True)
        return compact_research_answer(
            await api.request("POST", "/api/v1/agent-tasks/business-research", body=body)
        )

    @server.tool(annotations=WRITE)
    async def create_research_request(
        agencies: Agencies,
        idempotency_key: UUID,
        config_id: PositiveId | None = None,
        campaign_id: PositiveId | None = None,
        title: Annotated[str, Field(max_length=200)] | None = None,
        count: ContactCount | None = None,
        research_depth: Literal["standard", "deep"] = "standard",
    ) -> dict:
        """Create the saved-criteria agency workflow (up to 200 agencies and 5,000 contacts per agency, subject to account budget) with the account's enabled agent and budget. Research uses Apollo and opened public sources. Set research_depth=deep only when the customer explicitly requests deep research; that mode permits broader public-source checks. For broader business/industry/role research or conference questions, use create_business_research_request. Discover agents with list_tasks. Pass campaign_id from list_golden_lists for a selected customer-owned saved list without changing the default agent. Shared read access does not authorize research for another owner. Delivery follows backend policy."""
        body = {
            "agencies": agency_payload(agencies),
            "idempotency_key": str(idempotency_key),
            "research_depth": research_depth,
        }
        body.update(
            {
                key: value
                for key, value in {"config_id": config_id, "campaign_id": campaign_id, "title": title, "count": count}.items()
                if value is not None
            }
        )
        return compact_research_answer(await api.request("POST", "/api/v1/agent-tasks", body=body))

    @server.tool(annotations=WRITE)
    async def request_agency_first15_milestone(
        goal_id: PositiveId, expected_version: PositiveId, expected_revision: PositiveId, idempotency_key: UUID,
    ) -> dict:
        """Queue an optional 15-contact snapshot on an existing ALL agency task.

        Preserves original criteria and budgets. Does not resume paused work,
        create another research task, send email or fulfill the full request.
        """
        return await api.request("POST", f"/api/v1/agent-tasks/{goal_id}/milestones", body={
            "expected_version": expected_version, "expected_revision": expected_revision,
            "idempotency_key": str(idempotency_key)})

    @server.tool(annotations=READ)
    async def get_agency_milestone(goal_id: PositiveId, milestone_id: PositiveId | None = None,
                                   page: Annotated[int, Field(ge=1, le=3)] = 1) -> dict:
        """Read saved milestone metadata or a checked five-contact page.

        A milestone never establishes completion of the original ALL search.
        """
        return await api.request("GET", f"/api/v1/agent-tasks/{goal_id}/milestones",
                                 params={"page": page, **({"milestone_id": milestone_id} if milestone_id else {})})

    @server.tool(annotations=WRITE)
    async def create_conference_research_request(
        event_id: Literal["acams-las-vegas-2026", "rsa-usa-2026", "icba-live-2026", "acfe-global-2026", "aba-aml-fraud-2026", "afp-2026"],
        request: Annotated[str, Field(min_length=1, max_length=2000)],
        idempotency_key: UUID,
        config_id: PositiveId | None = None,
        research_depth: Literal["standard", "deep"] = "standard",
    ) -> dict:
        """Research a supported conference in production using existing account limits. Creates durable work with review and communication holds; does not send emails. Reuse the UUID on uncertain retries, then watch_research for live progress or get_research_answer for saved results with the returned goal ID. wait_for_task is the compatibility fallback when watch_research is unavailable."""
        body = {
            "event_id": event_id,
            "request": request,
            "idempotency_key": str(idempotency_key),
            "research_depth": research_depth,
        }
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
    async def supply_business_inputs(
        goal_id: PositiveId,
        spec: Annotated[dict[str, JsonValue], Field(description="Copy the saved partial_spec and fill only input_required.missing_fields with literal customer criteria; at most 20000 serialized JSON characters. Backend validates scope, fields and authority.")],
        expected_version: PositiveId,
        expected_revision: PositiveId,
        input_binding: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        idempotency_key: UUID,
        reason: Reason,
    ) -> dict:
        """Fill missing criteria on the existing business research request. Read its saved input_required first and preserve the returned version, revision, binding and existing criteria. Reuse the UUID if the response is uncertain. This does not authorize a new request, budget or outreach."""
        if len(json.dumps(spec)) > 20000:
            raise TaskApiError("Business input must fit within 20000 serialized JSON characters.")
        return await control(goal_id, "supply_input", expected_version, idempotency_key, reason,
            {"spec": spec, "expected_revision": expected_revision, "input_binding": input_binding})

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
        """Request a safe retry; the backend reconciles uncertain paid calls/sends and checks current budget and authority. For already-requested ordinary company-contact research, use an allowed retry to recover an internal/historical hold without asking the customer to choose a run. Read the current goal and task IDs/version first. Conference details may require clarification."""
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

    @server.tool(annotations=READ)
    async def get_task_artifact(goal_id: PositiveId, artifact_id: PositiveId) -> dict:
        """Retrieve the authorized saved Excel file for inspection here, including under a do-not-send request or held final gates. Requested review/download needs no additional read permission. Return download_url as a clickable link. Retrieval never starts research, authorizes email or clears delivery gates."""
        return await api.request("GET", f"/api/v1/agent-tasks/{goal_id}/artifacts/{artifact_id}/download-link")

    @server.tool(annotations=READ)
    async def read_task_spreadsheet(goal_id: PositiveId, artifact_id: PositiveId, sheet_name: str | None = None, include_rows: bool = True) -> dict:
        """Read the complete saved spreadsheet in one call; no five-contact pagination. Includes all sheets unless sheet_name is supplied. For sheet names, headers or counts only, set include_rows=false and quote sheets_summary exactly. Full analysis uses include_rows=true. Explicitly reports truncation; do not print rows unless asked."""
        data = await api.request("GET", f"/api/v1/agent-tasks/{goal_id}/artifacts/{artifact_id}/spreadsheet",
                                 params={"sheet_name": sheet_name} if sheet_name else None)
        summary = [{key: sheet.get(key) for key in ("name", "row_count", "truncated")}
                   for sheet in data.get("sheets", [])]
        if not include_rows:
            data = dict(data, sheets=[{key: value for key, value in sheet.items() if key != "rows"}
                                     for sheet in data.get("sheets", [])])
        return {"sheets_summary": summary, **data}

    @server.tool(annotations=READ)
    async def get_my_icp(config_id: PositiveId | None = None, campaign_id: PositiveId | None = None) -> dict:
        """Get my saved ICP (ideal customer profile), targeting criteria and account profile, plus an Excel download. Use for ICP targeting definitions. If the user means their ICP LIST or main contact/prospect list, use get_prospect_list/read_golden_list instead. Never infer an ICP from attendees. When status is not_saved, state briefly that targeting criteria are unavailable and the download contains account metadata only."""
        data = await api.request("GET", "/api/v1/agent-tasks/icp", params={k: v for k, v in {"config_id": config_id, "campaign_id": campaign_id}.items() if v is not None})
        if data.get("status") == "not_saved":
            return {"notice": "No saved ICP targeting criteria or profile are available for this account. The Excel download contains account metadata only.", **data}
        return data

    @server.tool(annotations=READ)
    async def get_prospect_list(goal_id: PositiveId | None = None, config_id: PositiveId | None = None, campaign_id: PositiveId | None = None) -> dict:
        """Download my MAIN golden/prospect/ICP list as Excel using owned or explicitly shared list access. Defaults to the canonical main golden list, never a conference workbook. Specify campaign_id to choose another accessible list. Only use goal_id for an explicitly requested research-task workbook. A saved snapshot can have unfinished enrichment; state actual coverage briefly. No research or sends."""
        return await api.request("GET", "/api/v1/agent-tasks/prospect-list",
                                 params={k: v for k, v in {"goal_id": goal_id, "config_id": config_id, "campaign_id": campaign_id}.items() if v is not None})

    @server.tool(annotations=READ)
    async def list_golden_lists(config_id: PositiveId | None = None) -> dict:
        """List my owned and explicitly shared main golden/ICP/prospect lists. Never ask for an account/config ID before checking these accessible lists. Does not expose unrelated owner accounts."""
        return await api.request("GET", "/api/v1/agent-tasks/golden-lists",
                                 params={"config_id": config_id} if config_id else None)

    @server.tool(annotations=READ)
    async def read_golden_list(campaign_id: PositiveId | None = None, config_id: PositiveId | None = None, include_rows: bool = True) -> dict:
        """Read all saved main golden-list contacts, emails, verification and enrichment fields in ONE call. This is the canonical main list, not a research-task workbook. Defaults to the main list; campaign_id chooses another accessible list. Set include_rows=false for counts only. Never starts research, enrollment or sends."""
        return await api.request("GET", "/api/v1/agent-tasks/golden-lists/read",
                                 params={k: v for k, v in {"campaign_id": campaign_id, "config_id": config_id, "include_rows": include_rows}.items() if v is not None})

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

    @server.resource(
        "producerspark-feedback-attachment://{goal_id}/{delivery_job_id}/{name_hex}/{sha256}",
        mime_type="application/octet-stream",
    )
    async def feedback_attachment_resource(goal_id: str, delivery_job_id: str, name_hex: str, sha256: str) -> bytes:
        """Read original feedback attachment bytes through the scoped backend facade."""
        uri = f"producerspark-feedback-attachment://{goal_id}/{delivery_job_id}/{name_hex}/{sha256}"
        content, _mime = await read_feedback_attachment_resource(api, uri)
        return content

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

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
from uuid import UUID, uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import ResourceLink, ToolAnnotations
from pydantic import BaseModel, Field, JsonValue

from .client import TaskApiClient, TaskApiConfig, TaskApiError, upgrade_required
from .feedback_review import brief_feedback_projection, feedback_projection, read_feedback_attachment_resource
from .answer_navigation import bounded_examples_and_artifacts, list_task_navigation
from .current_answer import history_metadata, recorded_delivery_history, resolution_metadata, resolve_current_answer
from .business_research import BusinessResearchSpec, saved_business_answer
from .live_progress import live_update, contact_table_metadata, watch_response
from .provider_disclosure import hide_data_provider_in_reply
from .agent_tools import Confirmation, INSTRUCTIONS as AGENT_TOOL_INSTRUCTIONS, register as register_agent_tools
from .people import PEOPLE_UI, register as register_people
from .recorder import register as register_recorder

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
SAVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
ACTIVATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)

CampaignName = Annotated[str, Field(min_length=1, max_length=120, description="Short campaign name. Leave out to start it from the first line of the targeting.")]
CampaignObjective = Annotated[str, Field(min_length=1, max_length=200, description="Optional: what the campaign should get done, such as Get leads or Book intro meetings.")]
CampaignTargeting = Annotated[str, Field(min_length=1, max_length=10000, description="Step 1, the prospects' industry and titles: target industry, company size, titles and places, in the customer's words.")]
CampaignSignals = Annotated[str, Field(max_length=10000, description="Step 2, target signals in the customer's words: what tells them a company is worth reaching now. Saved as written and turned into searches, at most one per source they can be checked from.")]
CampaignOffering = Annotated[str, Field(max_length=10000, description="Step 3, what they offer: the service the customer sells or provides to these people, in their words.")]
CampaignField = Annotated[str, Field(pattern="^(targeting|offering|signals)$", description="Which box to read back: targeting, offering or signals.")]
CampaignMessaging = Annotated[str, Field(max_length=10000, description="Optional: how each message should be tailored to the person: tone, what to mention, what to avoid.")]
SignalKey = Annotated[str, Field(min_length=1, max_length=120, description="The key of one signal, from signal_items on list_campaigns or get_started.")]
SignalText = Annotated[str, Field(min_length=1, max_length=5000, description="One signal in the customer's words, verbatim; it becomes one search per source it can be checked from.")]


class SignalEmail(BaseModel):
    """One email of a signal's own sequence."""

    subject: str = Field(default="", max_length=120, description="The subject line.")
    body: str = Field(min_length=1, max_length=2000, description="The email in the customer's words. {{first_name}} greets the person and {{signal_line}} becomes one sentence about what was found at their company.")


SignalEmails = Annotated[list[SignalEmail], Field(min_length=1, max_length=3, description="One to three emails. They take the place of the first emails of the campaign's main sequence, on its days; its later emails still follow. An email beyond the main sequence's length is not sent.")]


class SequenceStep(BaseModel):
    """One step of a campaign's sequence."""

    day: int = Field(ge=1, le=3650, description="The day this step goes out, counted from enrollment; day 1 is the first. Each step must be on a later day than the one before.")
    channel: Literal["email", "linkedin_connect", "linkedin_message"] = Field(default="email", description="email (the default), linkedin_connect (a connection request with an optional note) or linkedin_message (a LinkedIn message after they accept).")
    subject: str = Field(default="", max_length=255, description="The email's subject line. Leave empty for LinkedIn steps.")
    body: str = Field(max_length=10000, description="The step in the customer's words. {{first_name}} and {{company}} are filled in for each person.")


SequenceSteps = Annotated[list[SequenceStep], Field(min_length=1, max_length=20, description="The whole sequence in order, with at least one email. It replaces the saved sequence.")]


ENROLL_BATCH = 100


def campaign_page(campaign_id) -> str:
    return f"{SITE_URL}/agent/{campaign_id}"


def with_campaign_link(campaign):
    if isinstance(campaign, dict) and campaign.get("id"):
        return {"view_in_producerspark": campaign_page(campaign["id"]), **campaign}
    return campaign


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
                "ProducerSpark approval needed to extend the shared lookup allowance of "
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

    async def call_tool(self, name, arguments):
        """Every tool reply leaves without the contact-data vendor's name."""
        return hide_data_provider_in_reply(await super().call_tool(name, arguments))


FINISHED_SEARCH_STATES = {"completed", "needs_attention", "cancelled"}
SITE_URL = "https://producerspark.com"


def search_page(goal_id) -> str:
    """Where the person opens this search on the site: its results and Excel download."""
    return f"{SITE_URL}/agent?tab=tasks&task={goal_id}"


COULD_NOT_FINISH = (
    "This search could not finish on ProducerSpark's side. Tell the user plainly that it did not "
    "complete and to try again in a few minutes. Do not call the tool again for it now: every new "
    "call starts another search."
)


def with_site_link(goal_id, result: dict) -> dict:
    """Lead the result with the line the reply must end on, so the person knows where to go."""
    page = search_page(goal_id)
    if result.get("status") == "needs_attention" and not result.get("rows"):
        result = {"could_not_finish": COULD_NOT_FINISH, **result}
    return {
        "reply_must_end_with": (
            f"[Open this search in ProducerSpark]({page}) "
            "(Excel download, credits left and every earlier search)"
        ),
        "view_in_producerspark": page,
        **result,
    }


def titles_in_annotations(server: FastMCP) -> None:
    """Copy each tool's title into its own annotations.title, as directories read it there.

    Shared constants such as READ stay untouched: every tool gets its own copy with the
    same hints plus its title.
    """
    for tool in server._tool_manager.list_tools():
        if not tool.title:
            raise ValueError(f"Tool {tool.name} needs a title.")
        tool.annotations = (tool.annotations or ToolAnnotations()).model_copy(
            update={"title": tool.title}
        )


def create_server(api: TaskApiClient) -> FastMCP:
    server = TaskFastMCP(
        api,
        "ProducerSpark Tasks",
        instructions=(
            "For a new find-people or contact request (up to 200 people, any role, industry or location, including N people at each named company), call find_people first with the original question verbatim: it returns the contacts in the same call, usually within 20 to 90 seconds. Present its rows in the contact table, end the reply with the reply_must_end_with line the result gives, add Fit or Unverified notes only when a row lists them, and state coverage when fewer rows returned than requested or not_found_in_provider is non-empty; an unverified_suggestion is a name to check, never a finding. Use create_business_research_request instead only for more than 200 people, an explicitly requested emailed Excel workbook or background task, or when find_people returns nothing useful. A request for a conference's speakers or attendees also goes to find_people first, with the user's request verbatim and without looking the names up yourself: the search reads the event's own published speaker pages and adds the people it finds to any names supplied. That is deep research and can take several minutes: when a result carries a notice, tell the user what it says and keep calling with the same idempotency_key. Report coverage.event (people read on the event's pages, supplied names listed there, people not looked up) and say plainly that attendee lists are not published, so the rows are published speakers. "
            "Signals: when the user asks to research signals, buying signals or intelligence on people or companies on their Golden List, or for the custom emails written from that research, call signal_search with the request verbatim (campaign_id from list_campaigns when they name a campaign); it returns rows as they finish and keeps the same search on repeat calls with the same idempotency_key. To read research already done, call get_prospect_intelligence. "
            "When coverage.full_list.people is above coverage.returned and coverage.full_list_run is not true, the search returned only part of who matched: after the rows, ask whether they want everyone, and call full_list with that goal_id once they agree. "
            "When coverage.deep_search is present, the search left people it could not place: show the rows first, then offer a deep search in one or two sentences, giving how many people it covers (deep_search.eligible, of whom deep_search.missing_email_only are already listed and only lack an email), about how long it takes (deep_search.estimated_seconds), the most it can cost (deep_search.max_credits; say it adds no credits when that is 0) and that only new people it confirms are charged, deep_search.credits_per_find credits each. Call deep_search with that goal_id only after the user agrees, or straight away when their request already asked to find everyone or be thorough. While that offer is open the results email is held (email_delivery.status held_for_deep_search) so the user gets one email at the end with the workbook attached: say so, and if they decline the deep search call deep_search with decline=true so the current results are emailed. "
            "Conference answer units: report saved name/company roster entries, not distinct people. Always distinguish email-bearing roster entries from distinct recorded email addresses. The canonical raw speaker/session appearances and the selected customer file's row_count describe different files. "
            "Missing-email causes and examples come only from the missing-email subset. A saved blocked status on an email-bearing entry is not another missing address; preserve and label it separately. Candidate evidence can remain on entries with a selected address, so candidate_people is never a count of missing or held addresses. State recorded reasons only; do not infer employer or provider causes from examples. "
            "Final research gates do not establish whether an authorized preliminary snapshot was already emailed. For past-delivery questions call get_task(include_history=true) and use recorded_delivery_history. Without that history the earlier delivery is unknown; never claim no email was authorized or sent from a false final gate. Sent-status history alone does not prove inbox delivery or which attachment bytes were sent. "
            "Use list_tasks to navigate explicitly requested existing obligations or discover account configurations, not to select a historical goal for a fresh prospect ask. Fresh prospect asks require a fresh UUID and goal with the original question verbatim; matching titles do not authorize reuse. Reuse only an explicit check/resume goal or a goal created earlier in this conversation. Do not use list_tasks to answer final research or delivery readiness. For a conference row with current_answer_goal_id, read get_research_answer for that exact goal. Listed original-goal worker progress and its review blocker are not the latest outcome's counts or all required delivery gates. Artifact row_count describes rows in that exact saved file. A canonical raw speaker/session-appearance count can differ from a customer presentation's combined roster rows; never transfer one file's count to another. Email-cell counts likewise differ from roster identity keys and distinct recorded addresses. For missing emails, use enrichment_summary.missing_email_people and missing_email_enrichment_status_counts_by_person. Global unfinished people can include people with a recorded email; use unfinished_with_recorded_email_people and unfinished_without_recorded_email_people instead of adding all global blockers to the missing-email causes. Completed enrichment without a selected address does not prove any particular provider returned empty results or that an address is genuinely unavailable. Identity unresolved does not mean unresolvable, structurally unavailable, or impossible to improve with further evidence. Use recorded_email_date_summary's separate clocks when present. A saved verified email status is not a fresh deliverability check and does not establish a recorded verification date. Missing verification/enrichment clocks stay unknown; never infer a person's review or verification date from their email observation date. Never assign a date to all saved_result_reused or completed people from an example, one source, a common timestamp, or a status label. Paginated exact/calendar groups retain their explicit omitted totals; a preview is not the entire cohort. Status counts describe a cumulative saved snapshot, not new provider calls, new sources opened, or actions performed by the current goal. A saved succeeded stage does not prove completion happened after the user's Claude session ended or support an elapsed-time calculation without the relevant recorded timestamps. conference_answer.sources describes captured organizer/program source evidence. Separately report recorded email-source observations from row provenance and public-source receipts; do not claim the organizer is the only observed source in the entire workflow while public email-source evidence is also recorded. Declared source_urls alone remain unobserved targets. Preserve the distinction between observed source access and independently proven full roster/session coverage. preliminary_review_artifact refers to the separate interim-snapshot capability. Its null value does not mean there is no saved workbook available for HERE-first inspection. A recorded canonical_review_artifact availability fact with matched scope/hash/validation can establish a validated review file; otherwise a top-level listed artifact only establishes retrieval availability. Offer or retrieve an authorized clearly labeled partial workbook here, state its actual gaps, and keep final fulfillment and sends held. A do-not-send instruction does not prohibit already-requested saved-file inspection or download here. Do not ask for additional permission to read or link the scoped saved file the user requested for review; this retrieval never authorizes email or clears send gates. Do not label an immutable saved artifact a live working document or say clearing internal review alone completes the research.  A positive current_answer_goal_id is backend-proven scoped navigation, including pending continuations without a new artifact. If answer_is_current_recorded_goal is false, follow that ID for current progress unless the user explicitly asks for the historical snapshot. saved_review_workbook.metadata_recorded establishes saved metadata and a scoped retrieval action, not verified bytes or final delivery; use its artifact_retrieval to inspect the authorized partial file here. Each email clock summary is complete for that distinct clock; missing-email status and unfinished-email-presence are separate partitions, not additive cohorts. "
            "For conference cardinality, follow enrichment_summary.identity_count_basis: unique_people and per-person totals can count saved name/company roster keys containing aliases, not independently resolved distinct individuals. Label that basis as roster entries or roster identity keys. Distinguish email-bearing roster keys, speaker/session email cells and distinct_recorded_email_addresses; use recorded_email_address_count_basis and the shared-address group counts when present. Newly filled roster keys need not add the same number of distinct addresses. Never infer identities or fresh deliverability from a shared address, or delete or merge alias rows. Preserve recorded rows, counts, clocks and every research/delivery gate. "
            "Keep user-facing replies about 80% shorter: normally at most 75 words, plus one download link and the reply_must_end_with line. "
            "For new background research submit the original request verbatim through create_business_research_request without spec or host-selected companies. For several named people, submit one individual request per person with that person's own wording; never concatenate names or select a namesake without identity evidence. The backend owns interpretation and background execution for all channels. Preserve its acknowledgment faithfully. "
            "For Email Agent status use get_email_agent_status; for list or outreach questions use ask_email_agent. "
            "Campaigns organize the account: each starts from three steps in the customer's own words, in this order: targeting (industry and titles), signals (what makes a company worth reaching now) and offering (what they offer), plus optional objective and messaging, and holds one Golden List of prospects. When get_started shows has_campaign false, ask who to reach. Before saving any of the three, call check_campaign_item, say back its reading and questions with their options, and ask whether that is what they want; save only after a clear yes, with create_campaign for the targeting and update_campaign for the signals and the offering. The signals are saved as written and come back in signal_items as the searches they become, at most one per source (website, news, event, job postings, LinkedIn engagement with a keyword, a company page or a person's posts), each with a summary line; say the summaries back, never invent signals for the customer, and never suggest more when their words already make a complete search. To change one signal later, use update_campaign_signal with its key; to drop one, delete_campaign_signal; to add one, add_campaign_signal; update_campaign with signals replaces the whole set. A signal can carry its own one to three emails (set_signal_emails, removed with clear_signal_emails) that its people get instead of the main sequence; {{signal_line}} in them becomes one sentence about what was found at each company. Then offer to activate it. "
            "When a user states a new goal or changes who to reach, save it with create_campaign, or with update_campaign on a campaign from list_campaigns. activate_campaign starts finding prospects and signals and prepares messaging for review; it never sends. Its first call returns needs_confirmation and will_happen: show will_happen word for word, wait for a clear yes, then call again with confirmation. "
            "After activation the Email step runs in this order: get_campaign_email shows the sequence, enrollment and the checks left; save_campaign_sequence saves the main sequence after the customer says yes to every step; enroll_campaign_people enrolls the Golden List (nothing is sent while sending is off, so the campaign is then ready to send); start_campaign_sending switches sending on and emails real prospects, only after a clear yes to its will_happen. Report checks that are not ok in plain words. get_campaign_results reads sent emails, replies and website visits; get_email_sending_settings reads mailboxes, limits, days and deliverability. "
            "Present the returned message faithfully, preserving its meaning, paragraph breaks and any clarification. Sent is a recorded provider send, never delivery or an open; queued/sending/uncertain/failed/skipped are not sent. Status reads do not send emails. "
            "Use the backend acknowledgment and concise progress wording. Use Markdown tables for contact results. Contact results use Name | Company | Title | Email | LinkedIn, one person per row. Preserve recorded uncertainty in Email status or Source columns when needed. Use Pending for unfinished or blocked lookup; Not found only after completed enrichment without a result. Progress, blockers, questions and next actions use Task | Status | Next step. Downloads use File | Status with clickable links. Honor an explicit request for another format. Omit internal steps and repeated caveats. "
            "For an ICP definition or targeting criteria use get_my_icp. For an ICP LIST, main list, golden list, prospect list or download my list use get_prospect_list. Discover all accessible lists with list_golden_lists and read every saved contact with read_golden_list. "
            "Return download_url as a clickable Markdown link with filename. Links expire after ten minutes; request a new link when expired. "
            "To analyze or clean a workbook use read_task_spreadsheet: it reads all normal-size saved rows in one call. "
            "Do not page contacts five at a time to retrieve a whole workbook. Never claim a workbook is inaccessible before trying these tools. "
            "Use the exact recorded status meaning: completed_public_email_unavailable means the completed public fallback did not record an address. Never paraphrase this as a data provider returning no result or an employer/government cause. Never name a contact-data vendor to the customer; say contact database or provider. Do not add a structural public-email availability theory absent recorded evidence. Worker progress such as 135/136 counts task steps, never people or contacts. "
            "Email provenance calendar summaries group each distinct saved clock by calendar date; exact timestamp groups are paginated with source_page. Report their explicit totals and omitted counts, never treat a preview as all records. Contact pages contain five rows; use the returned pagination instead of inventing a workbook page size. "
            "Manage the authenticated account's durable customer research requests. Read the current task before controls; use its version and allowed_actions. "
            "Use create_business_research_request for new business-contact and conference questions across any industry or role. Conference speaker requests run as ordinary background research; report the saved progress and coverage as returned. Explicit companies and roles govern this request; do not silently impose an older insurance ICP. For new company-contact requests, people outside golden lists are allowed. Preserve requested employer industry in industries and inclusive employee counts in employee_range with min and max; these filters never imply personal location or a saved insurance ICP. Preserve only the user's requested research fields and filters. For an unambiguous named-person email-only request, use fields=[\"email\"] and roles=[] unless the user explicitly requires a role. Do not add title, LinkedIn, current-officer proof, or deep research. A descriptive known role is an identity clue, not a required deliverable; explicit requests to identify or confirm a current role retain their role qualification. If the customer omits personal geography, leave person_locations empty with no inferred country restriction. Preserve explicit personal city/country constraints exactly; explicit worldwide/global likewise leaves person_locations empty. Employer geography stays in employer_locations and never implies personal residence. Preserve conference edition coverage without applying this people-search default. Do not change saved goals to apply a new default. Keep research_depth=standard unless the customer explicitly asks for deep research; deep mode permits broader public-source checks. Existing saved-list agency research remains a separate compatibility tool. Generic business results use business_answer with saved coverage and provenance. Future customer deliveries omit draft-email content and draft sequences. "
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
            "provider_credit_budget is the current shared lookup authority; its numeric credit fields count conservative attempted billable lookup units, not provider balance or invoiced charges. "
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
            + AGENT_TOOL_INSTRUCTIONS
        ),
    )

    @server.tool(title="Get started with ProducerSpark", annotations=READ)
    async def get_started() -> dict:
        """What this account can do right now: the agents on the account (and those not on it), free_prospects (granted, used, remaining) on the free trial, can_search_people, example_requests, campaigns (each with its name, status, three steps and Golden List), has_campaign, next_step, first_name, results_url (the account's ProducerSpark page with every search and its Excel download) and upgrade_url (plans). Reads only; nothing is searched or charged."""
        return await api.request("GET", "/api/v1/agent-tasks/getting-started")

    @server.tool(title="Get Email Agent status", annotations=READ)
    async def get_email_agent_status(
        config_id: PositiveId | None = None,
        campaign_id: PositiveId | None = None,
    ) -> dict:
        """The Email Agent's current state for a list: on or off, and its enrolled, sent, queued, sending, uncertain, failed, skipped and replied counts, with the same status paragraph its email replies use. Reads only; sends nothing."""
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
        title="Ask the Email Agent",
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
        """Answers a question about the user's list or Email Agent from saved facts, in the Email Agent's concise reply style, and returns that message. It does not read earlier email threads. Reads only: no email, list change or research is triggered."""
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

    @server.tool(title="List tasks", annotations=READ)
    async def list_tasks(
        state: TaskView = "", page: Annotated[int, Field(ge=1, le=100000)] = 1
    ) -> dict:
        """Lists the account's existing research tasks and agent configurations, filtered by state and paged. Each row has its goal id, title, state, worker-step progress and, where a newer answer exists, current_answer_goal_id. Reads only; creates no work."""
        response = await api.request(
            "GET", "/api/v1/agent-tasks", params={"state": state, "page": page}
        )
        return list_task_navigation(response)

    @server.tool(title="Get task", annotations=READ)
    async def get_task(goal_id: PositiveId, include_history: bool = False, historical_snapshot: bool = False) -> dict:
        """One research task: its current answer with version and allowed_actions (pause, resume, cancel, retry), or the original goal when historical_snapshot=true. include_history=true adds bounded sent-status history and event metadata for past deliveries (no email bodies or receipts). Conference totals count roster entries, not distinct individuals. Sent status is not proof of inbox delivery. Reads only."""
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

    @server.tool(title="Get research answer", annotations=READ)
    async def get_research_answer(
        goal_id: PositiveId,
        agency_page: Annotated[int, Field(ge=1)] = 1,
        contact_page: Annotated[int, Field(ge=1)] = 1,
        source_page: Annotated[int, Field(ge=1)] = 1,
        details: bool = False,
        historical_snapshot: bool = False,
    ) -> dict:
        """The compact saved answer of a research task: counts, findings, coverage, agencies and contacts in pages of five, and conference counts (roster entries, email-bearing entries and distinct addresses). details=true adds samples and dates; historical_snapshot=true reads the original goal. Missing-email causes describe only the missing-email subset. Reads only; starts no research or delivery."""
        resolved = await resolve_current_answer(api, goal_id, historical_snapshot=historical_snapshot)
        answer = resolution_metadata(compact_research_answer(resolved.current, agency_page, contact_page, source_page), resolved)
        return answer if details or max(agency_page, contact_page, source_page) > 1 else brief_answer(answer)

    @server.tool(title="Watch live prospect research", annotations=READ)
    async def watch_research(
        goal_id: PositiveId,
        cursor: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None,
        timeout_seconds: Annotated[int, Field(ge=0, le=25)] = 25,
    ) -> dict:
        """Live progress of one existing research job. The first call returns at once with a cursor; later calls with that cursor wait up to about 25 seconds for a change. Returns saved prospect and email counts, stage, up to three findings with evidence, blockers, follow-up suggestions, continue_watching, final_response_ready (true only when the job is finished) and next_tool_arguments. Routine queued and running churn is coalesced. Worker steps and preview rows are not found prospects. Reads only; creates no work."""
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
                return watch_response(update, changed)
            await asyncio.sleep(min(5, remaining))
            if deadline - time.monotonic() < 0.1:
                return watch_response(update, False)

    @server.tool(title="Check research progress", annotations=READ)
    async def wait_for_task(
        goal_id: PositiveId,
        timeout_seconds: Annotated[int, Field(ge=0, le=50)] = 10,
        historical_snapshot: bool = False,
    ) -> dict:
        """Checks existing background research and returns when a saved stage or count changes, or after a polling window of at most 10 seconds. Returns task.current_step, state, saved contact and email counts, blockers and progress_changed_during_wait (which compares polls within this one call). Worker steps are not contacts. historical_snapshot=true follows the exact requested goal. Reads only; creates no work."""
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

    @server.tool(title="Find people", annotations=WRITE, meta=PEOPLE_UI)
    async def find_people(
        request: Annotated[str, Field(min_length=1, max_length=10000)],
        idempotency_key: UUID | None = None,
        config_id: PositiveId | None = None,
    ) -> dict:
        """Fast people search for any role, industry or location, taking the request in the user's own words. It runs as its own task on the account and returns the contacts in this call when ready, usually within 20 to 90 seconds; while it runs, status is still_running and a repeat call with the same idempotency_key returns the same search. An open search returns up to 200 contacts; a search that names companies covers up to 100 of them. A conference speaker or attendee request also reads the event's published pages and can take several minutes, with a notice in the result meanwhile. Rows carry name, title, company, business email with its saved verification status, LinkedIn, location, fit, why and anything unverified. coverage reports requested versus returned, named employers with no contact record, per_company_default (when the request gave no number), count_step (when the search was sized first at no charge and paused), full_list (everyone who matched, with estimated_credits) and deep_search (people it could not place). status upgrade_required, credits.exhausted or coverage.held_for_credits above 0 mean the account's free prospects or credits are used up; upgrade_url is then the plans page. could_not_finish means the search failed on ProducerSpark's side. credits.remaining gives what is left. The account owner is emailed a summary with the workbook attached. Each result has view_in_producerspark (this search's page with its Excel download) and reply_must_end_with (that link as a ready line). In supporting chats the people also appear as cards with headshots; the cards carry no emails. No prospect outreach."""
        body = {"request": request, "idempotency_key": str(idempotency_key or uuid4())}
        if config_id is not None:
            body["config_id"] = config_id
        try:
            goal = await api.request("POST", "/api/v1/agent-tasks/quick-search", body=body)
        except TaskApiError as error:
            held = upgrade_required(error)
            if held is None:
                raise
            return {**held, "idempotency_key": body["idempotency_key"], "rows": [], "coverage": {}}
        deadline = time.monotonic() + 210
        while goal.get("state") not in FINISHED_SEARCH_STATES and time.monotonic() < deadline:
            await asyncio.sleep(1)
            goal = await api.request("GET", f"/api/v1/agent-tasks/{goal['id']}")
        result = goal.get("quick_search") or {}
        blocked = next((task.get("blocked_reason") for task in goal.get("tasks") or [] if task.get("blocked_reason")), "")
        return with_site_link(goal["id"], {
            "blocked_reason": (
                "The account's contact-lookup allowance is used up. No contacts were fetched; "
                "an operator needs to approve more lookups, then run the search again."
                if "ApolloBillableBudgetExceeded" in blocked
                else blocked
            ),
            "goal_id": goal["id"],
            "idempotency_key": body["idempotency_key"],
            "status": goal["state"] if goal.get("state") in FINISHED_SEARCH_STATES else "still_running",
            "notice": result.get("notice", ""),
            "rows": result.get("rows", []),
            "coverage": result.get("coverage", {}),
            "credits": result.get("credits"),
            "email_delivery": result.get("delivery", {}),
        })

    @server.tool(title="Run deep search", annotations=WRITE, meta=PEOPLE_UI)
    async def deep_search(goal_id: PositiveId, decline: bool = False) -> dict:
        """Slower second pass over the people a find_people search (its goal_id) could not place: it retries other spellings of each name, then the web, and returns only the additions, usually within a few minutes. Each row has match: confirmed (a contact record has the requested name and employer; costs credits_per_find credits) or possible (an unconfirmed profile: free, no email, a name to check rather than a finding). A row's listed_name is the person it replaces from the first results. coverage gives confirmed, possible, not_found, still_without_email and credits_charged; credits.remaining gives what is left. One search has one deep search, so a repeat call while status is still_running never starts or charges a second. decline=true searches and charges nothing and emails the first results as they stand (status results_emailed). status upgrade_required or coverage.held_for_credits above 0 mean credits are used up; upgrade_url is the plans page. The account owner is emailed a summary with the updated workbook. Each result has view_in_producerspark and reply_must_end_with. In supporting chats the people also appear as cards with headshots; the cards carry no emails. No prospect outreach."""
        try:
            goal = await api.request(
                "POST", f"/api/v1/agent-tasks/{goal_id}/deep-search", body={"decline": True} if decline else None
            )
        except TaskApiError as error:
            held = upgrade_required(error)
            if held is None:
                raise
            return {**held, "source_goal_id": goal_id, "rows": [], "coverage": {}}
        if decline:
            return {
                "source_goal_id": goal_id,
                "status": "results_emailed",
                "rows": [],
                "coverage": {},
                "email_delivery": (goal.get("quick_search") or {}).get("delivery", {}),
            }
        deadline = time.monotonic() + 210
        while goal.get("state") not in FINISHED_SEARCH_STATES and time.monotonic() < deadline:
            await asyncio.sleep(1)
            goal = await api.request("GET", f"/api/v1/agent-tasks/{goal['id']}")
        result = goal.get("deep_search") or {}
        return with_site_link(goal["id"], {
            "goal_id": goal["id"],
            "source_goal_id": goal_id,
            "status": goal["state"] if goal.get("state") in FINISHED_SEARCH_STATES else "still_running",
            "rows": result.get("rows", []),
            "coverage": result.get("coverage", {}),
            "credits": result.get("credits"),
            "email_delivery": result.get("delivery", {}),
        })

    @server.tool(title="Get full list", annotations=WRITE, meta=PEOPLE_UI)
    async def full_list(goal_id: PositiveId) -> dict:
        """Everyone a find_people search (its goal_id) matched, when it returned only part of them: reruns the same request for everyone who matched, up to 200 people per company, and returns the complete list in the find_people shape, usually within one to three minutes. The earlier full_list figure counted matches before screening, so the result is usually somewhat smaller (coverage.returned against coverage.full_list.people). People already delivered are not charged again. One search has one full list, so a repeat call while status is still_running never starts or charges a second. status upgrade_required, credits.exhausted or coverage.held_for_credits above 0 mean credits are used up; upgrade_url is the plans page. coverage.deep_search lists people it could not place. The account owner is emailed a summary with the workbook. Each result has view_in_producerspark and reply_must_end_with. In supporting chats the people also appear as cards with headshots; the cards carry no emails. No prospect outreach."""
        try:
            goal = await api.request("POST", f"/api/v1/agent-tasks/{goal_id}/full-list")
        except TaskApiError as error:
            held = upgrade_required(error)
            if held is None:
                raise
            return {**held, "source_goal_id": goal_id, "rows": [], "coverage": {}}
        deadline = time.monotonic() + 210
        while goal.get("state") not in FINISHED_SEARCH_STATES and time.monotonic() < deadline:
            await asyncio.sleep(1)
            goal = await api.request("GET", f"/api/v1/agent-tasks/{goal['id']}")
        result = goal.get("quick_search") or {}
        return with_site_link(goal["id"], {
            "goal_id": goal["id"],
            "source_goal_id": goal_id,
            "status": goal["state"] if goal.get("state") in FINISHED_SEARCH_STATES else "still_running",
            "rows": result.get("rows", []),
            "coverage": result.get("coverage", {}),
            "credits": result.get("credits"),
            "email_delivery": result.get("delivery", {}),
        })

    @server.tool(title="Request business research", annotations=WRITE)
    async def create_business_research_request(
        request: Annotated[str, Field(min_length=1, max_length=10000)],
        idempotency_key: UUID,
        spec: BusinessResearchSpec | None = None,
        config_id: PositiveId | None = None,
        research_depth: Literal["standard", "deep"] = "standard",
        contact_provider: Literal["default", "treg"] | None = None,
    ) -> dict:
        """Starts durable background research for business contacts in any industry and role (for example a company's CIO), for conference speakers, for more than 200 people, or for an emailed Excel workbook. It takes the user's original request verbatim; the backend interprets count, roles, title equivalents, personal versus employer geography, industry and size, and discovers eligible companies itself. spec is optional legacy hints that never override the request. A company's headquarters is never treated as a person's location. The same idempotency_key returns the same request. contact_provider=treg selects TREG for an explicit provider comparison; otherwise the default contact database is used. Returns the backend acknowledgment with the goal id and state (for example waiting_dependency, waiting_budget or retry_wait); work continues after the connection closes. Named people and employers are never substituted, and contacts, emails and LinkedIn are never invented. Delivery follows the account's existing authorized channel and checks. No prospect outreach."""
        body = {
            "request": request,
            "idempotency_key": str(idempotency_key),
            "research_depth": research_depth,
            "intake_channel": "mcp_v1",
        }
        if config_id is not None:
            body["config_id"] = config_id
        if contact_provider == "treg":
            body['contact_provider']=contact_provider
        if spec is not None:
            body["spec"] = spec.model_dump(exclude_none=True)
        return compact_research_answer(
            await api.request("POST", "/api/v1/agent-tasks/business-research", body=body)
        )

    @server.tool(title="Request research", annotations=WRITE)
    async def create_research_request(
        agencies: Agencies,
        idempotency_key: UUID,
        config_id: PositiveId | None = None,
        campaign_id: PositiveId | None = None,
        title: Annotated[str, Field(max_length=200)] | None = None,
        count: ContactCount | None = None,
        research_depth: Literal["standard", "deep"] = "standard",
    ) -> dict:
        """Starts the saved-criteria agency research workflow: up to 200 agencies and 5,000 contacts per agency, within the account's budget, using the account's enabled agent and an optional campaign_id for one of the user's own saved lists. Research uses the contact database and opened public sources; research_depth=deep adds broader public-source checks. Shared read access to another owner's list does not allow research on it. Returns the backend acknowledgment with the goal id. Delivery follows backend policy."""
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

    @server.tool(title="Request agency milestone", annotations=WRITE)
    async def request_agency_first15_milestone(
        goal_id: PositiveId, expected_version: PositiveId, expected_revision: PositiveId, idempotency_key: UUID,
    ) -> dict:
        """Queues an optional 15-contact snapshot on an existing agency task that covers all contacts. It keeps the original criteria and budget. It does not resume paused work, create another research task, send email or fulfill the full request."""
        return await api.request("POST", f"/api/v1/agent-tasks/{goal_id}/milestones", body={
            "expected_version": expected_version, "expected_revision": expected_revision,
            "idempotency_key": str(idempotency_key)})

    @server.tool(title="Get agency milestone", annotations=READ)
    async def get_agency_milestone(goal_id: PositiveId, milestone_id: PositiveId | None = None,
                                   page: Annotated[int, Field(ge=1, le=3)] = 1) -> dict:
        """Saved milestone metadata, or one checked five-contact page of it. A milestone does not mean the original full search is complete. Reads only."""
        return await api.request("GET", f"/api/v1/agent-tasks/{goal_id}/milestones",
                                 params={"page": page, **({"milestone_id": milestone_id} if milestone_id else {})})

    @server.tool(title="Request conference research", annotations=WRITE)
    async def create_conference_research_request(
        event_id: Literal["acams-las-vegas-2026", "rsa-usa-2026", "icba-live-2026", "acfe-global-2026", "aba-aml-fraud-2026", "afp-2026"],
        request: Annotated[str, Field(min_length=1, max_length=2000)],
        idempotency_key: UUID,
        config_id: PositiveId | None = None,
        research_depth: Literal["standard", "deep"] = "standard",
    ) -> dict:
        """Starts research on a supported conference within the account's existing limits. It creates durable work with review and communication holds and sends no email. The same idempotency_key returns the same request. Returns the goal id and state."""
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

    @server.tool(title="Supply business inputs", annotations=WRITE)
    async def supply_business_inputs(
        goal_id: PositiveId,
        spec: Annotated[dict[str, JsonValue], Field(description="The saved partial_spec with input_required.missing_fields filled in from the customer's criteria; at most 20000 serialized JSON characters. The backend validates scope, fields and authority.")],
        expected_version: PositiveId,
        expected_revision: PositiveId,
        input_binding: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        idempotency_key: UUID,
        reason: Reason,
    ) -> dict:
        """Fills missing criteria on an existing business research request that is waiting for input (its input_required), keeping the saved version, revision, binding and existing criteria. The same idempotency_key returns the same update. It does not create a new request, add budget or start outreach."""
        if len(json.dumps(spec)) > 20000:
            raise TaskApiError("Business input must fit within 20000 serialized JSON characters.")
        return await control(goal_id, "supply_input", expected_version, idempotency_key, reason,
            {"spec": spec, "expected_revision": expected_revision, "input_binding": input_binding})

    @server.tool(title="Supply agency names", annotations=WRITE)
    async def supply_agency_names(
        goal_id: PositiveId,
        agencies: Agencies,
        expected_version: PositiveId,
        idempotency_key: UUID,
        reason: Reason,
        count: ContactCount | None = None,
    ) -> dict:
        """Adds agency names the customer supplied to a request waiting for them, which can let already authorized research run."""
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

    @server.tool(title="Pause task", annotations=WRITE)
    async def pause_task(
        goal_id: PositiveId, expected_version: PositiveId, idempotency_key: UUID, reason: Reason
    ) -> dict:
        """Pauses future work on a task when its allowed_actions permit. External operations already started still need reconciliation."""
        return await control(goal_id, "pause", expected_version, idempotency_key, reason)

    @server.tool(title="Resume task", annotations=WRITE)
    async def resume_task(
        goal_id: PositiveId, expected_version: PositiveId, idempotency_key: UUID, reason: Reason
    ) -> dict:
        """Resumes paused, user-authorized work within its existing scope and budget."""
        return await control(goal_id, "resume", expected_version, idempotency_key, reason)

    @server.tool(title="Cancel task", annotations=CANCEL)
    async def cancel_task(
        goal_id: PositiveId, expected_version: PositiveId, idempotency_key: UUID, reason: Reason
    ) -> dict:
        """Cancels a task's future execution. An email already accepted for sending cannot be undone."""
        return await control(goal_id, "cancel", expected_version, idempotency_key, reason)

    @server.tool(title="Retry task", annotations=WRITE)
    async def retry_task(
        goal_id: PositiveId,
        task_id: PositiveId,
        expected_version: PositiveId,
        idempotency_key: UUID,
        reason: Reason,
    ) -> dict:
        """Requests a safe retry of a task, using its current goal id, task id and version. The backend reconciles uncertain paid calls and sends and checks current budget and authority first. Conference tasks may need more detail."""
        return await control(
            goal_id, "retry", expected_version, idempotency_key, reason, {"task_id": task_id}
        )

    @server.tool(title="List research schedules", annotations=READ)
    async def list_schedules() -> dict:
        """Recurring research schedules with their next occurrence, timezone and enabled state. Reads only."""
        return await api.request("GET", "/api/v1/agent-tasks/schedules")

    @server.tool(title="Create research schedule", annotations=WRITE)
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
        """Creates recurring agency research. weekday is Monday=0. The account's budget, quality and delivery policy apply to every occurrence."""
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

    @server.tool(title="Turn schedule on or off", annotations=WRITE)
    async def set_schedule_enabled(schedule_id: PositiveId, is_enabled: bool) -> dict:
        """Turns an existing research schedule on or off. Future occurrences change; past ones do not."""
        return await api.request(
            "POST", f"/api/v1/agent-tasks/schedules/{schedule_id}", body={"is_enabled": is_enabled}
        )

    @server.tool(title="Get task file", annotations=READ)
    async def get_task_artifact(goal_id: PositiveId, artifact_id: PositiveId) -> dict:
        """A saved Excel file from a research task, with a download_url (a link valid for about ten minutes). Retrieval works even when sending is held, and never starts research, sends email or clears delivery checks."""
        return await api.request("GET", f"/api/v1/agent-tasks/{goal_id}/artifacts/{artifact_id}/download-link")

    @server.tool(title="Read task spreadsheet", annotations=READ)
    async def read_task_spreadsheet(goal_id: PositiveId, artifact_id: PositiveId, sheet_name: str | None = None, include_rows: bool = True) -> dict:
        """The complete saved spreadsheet of a research task in one call: every sheet, or one sheet_name. include_rows=false returns only sheets_summary (sheet names, headers and row counts). The result says when rows were truncated. Reads only."""
        data = await api.request("GET", f"/api/v1/agent-tasks/{goal_id}/artifacts/{artifact_id}/spreadsheet",
                                 params={"sheet_name": sheet_name} if sheet_name else None)
        summary = [{key: sheet.get(key) for key in ("name", "row_count", "truncated")}
                   for sheet in data.get("sheets", [])]
        if not include_rows:
            data = dict(data, sheets=[{key: value for key, value in sheet.items() if key != "rows"}
                                     for sheet in data.get("sheets", [])])
        return {"sheets_summary": summary, **data}

    @server.tool(title="Get my ICP", annotations=READ)
    async def get_my_icp(config_id: PositiveId | None = None, campaign_id: PositiveId | None = None) -> dict:
        """The account's saved ICP (ideal customer profile): targeting criteria and account profile, with an Excel download. status not_saved means no targeting criteria are saved and the download holds account metadata only. This is the targeting definition, not the contact list. Reads only."""
        data = await api.request("GET", "/api/v1/agent-tasks/icp", params={k: v for k, v in {"config_id": config_id, "campaign_id": campaign_id}.items() if v is not None})
        if data.get("status") == "not_saved":
            return {"notice": "No saved ICP targeting criteria or profile are available for this account. The Excel download contains account metadata only.", **data}
        return data

    @server.tool(title="Get prospect list", annotations=READ)
    async def get_prospect_list(goal_id: PositiveId | None = None, config_id: PositiveId | None = None, campaign_id: PositiveId | None = None) -> dict:
        """An Excel download of the user's main Golden List (prospect or ICP list), or another list they own or that was shared with them (campaign_id), or one research-task workbook (goal_id). A saved snapshot can include contacts whose enrichment is unfinished; the result gives actual coverage. Starts no research and sends nothing."""
        return await api.request("GET", "/api/v1/agent-tasks/prospect-list",
                                 params={k: v for k, v in {"goal_id": goal_id, "config_id": config_id, "campaign_id": campaign_id}.items() if v is not None})

    @server.tool(title="List Golden Lists", annotations=READ)
    async def list_golden_lists(config_id: PositiveId | None = None) -> dict:
        """The main Golden Lists (prospect or ICP lists) the user owns or that were explicitly shared with them, with their campaign ids. Lists of unrelated accounts are not included. Reads only."""
        return await api.request("GET", "/api/v1/agent-tasks/golden-lists",
                                 params={"config_id": config_id} if config_id else None)

    @server.tool(title="Read Golden List", annotations=READ)
    async def read_golden_list(campaign_id: PositiveId | None = None, config_id: PositiveId | None = None, include_rows: bool = True) -> dict:
        """Every saved contact on the main Golden List, or another accessible list (campaign_id), in one call: emails, verification and enrichment fields. include_rows=false returns counts only. Starts no research, enrollment or sending."""
        return await api.request("GET", "/api/v1/agent-tasks/golden-lists/read",
                                 params={k: v for k, v in {"campaign_id": campaign_id, "config_id": config_id, "include_rows": include_rows}.items() if v is not None})

    @server.tool(title="List campaigns", annotations=READ)
    async def list_campaigns() -> dict:
        """The account's campaigns. Each has its three steps (targeting: the prospects' industry and titles; signals: target signals in the customer's words, with signal_items splitting them one per entry; offering: what they offer), optional objective and messaging, name, status (draft, active or paused), is_default, its Golden List, results (prospects, signals, messages prepared) and signal research counts (ready, no_signal, queued, running, held, failed, pending), and view_in_producerspark. Reads only."""
        data = await api.request("GET", "/api/v1/agent-tasks/campaigns")
        if isinstance(data, dict) and isinstance(data.get("campaigns"), list):
            data = {**data, "campaigns": [with_campaign_link(item) for item in data["campaigns"]]}
        return data

    @server.tool(title="Create campaign", annotations=SAVE)
    async def create_campaign(
        idempotency_key: UUID,
        targeting: CampaignTargeting,
        signals: CampaignSignals | None = None,
        offering: CampaignOffering | None = None,
        objective: CampaignObjective | None = None,
        messaging: CampaignMessaging | None = None,
        name: CampaignName | None = None,
    ) -> dict:
        """Saves a new campaign as a draft, with its own Golden List. targeting (the prospects' industry and titles) is required; signals and offering are optional here and required before activation; objective and messaging are optional. The same idempotency_key returns the campaign already saved instead of creating another. A 400 error carries a plain reason. Returns the campaign with view_in_producerspark. Saves only; nothing is searched, sent or charged."""
        body = {key: value for key, value in {
            "name": name, "objective": objective, "targeting": targeting,
            "signals": signals, "offering": offering, "messaging": messaging,
        }.items() if value is not None}
        body["creation_key"] = str(idempotency_key)
        return with_campaign_link(await api.request("POST", "/api/v1/agent-tasks/campaigns", body=body))

    @server.tool(title="Update campaign", annotations=SAVE)
    async def update_campaign(
        campaign_id: PositiveId,
        name: CampaignName | None = None,
        objective: CampaignObjective | None = None,
        targeting: CampaignTargeting | None = None,
        signals: CampaignSignals | None = None,
        offering: CampaignOffering | None = None,
        messaging: CampaignMessaging | None = None,
        review_mode: Annotated[bool | None, Field(description="True: each researched prospect waits on the Golden List until approved with review_prospect before any outreach. False: enrollment needs no approval (the default).")] = None,
        min_score: Annotated[float | None, Field(ge=0, le=4, description="Fit (0 to 1) plus intent (0 to 3) a prospect needs before enrollment; 0 switches it off. Anyone below it stays on the list but is not enrolled.")] = None,
        website_visitors: Annotated[bool | None, Field(description="True: the campaign gets a visitor key and a script (visitor_snippet in the result) for the customer's own website; the companies visiting it are then sourced as prospects, one search per sweep, one credit per person found. False switches it off; the key is kept.")] = None,
        ai_first_email: Annotated[bool | None, Field(description="True: for each prospect with a signal found, a whole first email is written from their evidence, the offering and the messaging instructions, and takes Email 1 when their signal has no emails of its own. When false (the default), Email 1 stays as authored and the {{signal_line}} placeholder carries the per-person text.")] = None,
        webhook_url: Annotated[str | None, Field(max_length=500, description="An https address that receives signed JSON events (prospect.researched, prospect.signal, prospect.added); the result's research_options.webhook_secret verifies the X-ProducerSpark-Signature header (sha256 HMAC of '<timestamp>.<body>'). Empty switches it off.")] = None,
    ) -> dict:
        """Changes a campaign's name, targeting, signals, offering, objective or messaging, or its review_mode, min_score, website_visitors, ai_first_email and webhook_url; fields left out stay as they are. signals replaces the whole set, and saved signals come back in signal_items as the searches they become (one per source, each with a summary line). Returns research_options (review_mode, min_score) and waiting_review (how many prospects wait for approval). A 400 error carries a plain reason; 404 means the campaign is unknown or archived. Returns the campaign with view_in_producerspark. Saves only; nothing is searched, sent or charged."""
        body = {key: value for key, value in {
            "name": name, "objective": objective, "targeting": targeting,
            "signals": signals, "offering": offering, "messaging": messaging,
        }.items() if value is not None}
        options = {key: value for key, value in {"review_mode": review_mode, "min_score": min_score, "website_visitors": website_visitors, "ai_first_email": ai_first_email, "webhook_url": webhook_url}.items() if value is not None}
        if options:
            body["research_options"] = options
        return with_campaign_link(
            await api.request("PATCH", f"/api/v1/agent-tasks/campaigns/{campaign_id}", body=body)
        )

    @server.tool(title="Add campaign signal", annotations=SAVE)
    async def add_campaign_signal(campaign_id: PositiveId, text: SignalText) -> dict:
        """Adds one target signal, in the customer's words, to a campaign and keeps its other signals. The backend turns it into one search per source it can be checked from (website, news, event, job postings, LinkedIn engagement: who posted, commented or reacted about a keyword, on a company page's posts or on a person's posts, read from public posts); a line nothing can check comes back declined with the reason. Returns the campaign with signal_items, each with its summary (what will be searched), check_from and checkable, and view_in_producerspark. On an active campaign, a changed set of signals makes every prospect due for research again (three first); nobody is charged twice for a person. 404 means the campaign is unknown or archived."""
        return with_campaign_link(
            await api.request("POST", f"/api/v1/agent-tasks/campaigns/{campaign_id}/signals", body={"text": text})
        )

    @server.tool(title="Update campaign signal", annotations=SAVE)
    async def update_campaign_signal(campaign_id: PositiveId, signal_key: SignalKey, text: SignalText) -> dict:
        """Replaces the wording of one target signal (by its signal_key) and keeps the campaign's other signals. Returns the campaign with signal_items, including the changed signal's summary, and view_in_producerspark. On an active campaign, a changed signal makes every prospect due for research again (three first); nobody is charged twice for a person. 404 means the campaign or signal key is unknown."""
        return with_campaign_link(
            await api.request(
                "PATCH", f"/api/v1/agent-tasks/campaigns/{campaign_id}/signals/{signal_key}", body={"text": text}
            )
        )

    @server.tool(title="Delete campaign signal", annotations=CANCEL)
    async def delete_campaign_signal(campaign_id: PositiveId, signal_key: SignalKey) -> dict:
        """Removes one target signal (by its signal_key) and its Golden List filter from a campaign. Research already done for it stays on the prospects. Returns the campaign with the remaining signal_items and view_in_producerspark. 404 means the campaign or signal key is unknown."""
        return with_campaign_link(
            await api.request("DELETE", f"/api/v1/agent-tasks/campaigns/{campaign_id}/signals/{signal_key}")
        )

    @server.tool(title="Set signal emails", annotations=SAVE)
    async def set_signal_emails(campaign_id: PositiveId, signal_key: SignalKey, emails: SignalEmails) -> dict:
        """Gives one target signal (by its signal_key) its own short email sequence: people found under that signal get these emails in place of the first emails of the campaign's main sequence (someone found under two signals gets the first signal's). {{first_name}} and {{signal_line}} are filled per person; {{signal_line}} becomes one sentence about what was found at their company. Returns the campaign with signal_emails by key and view_in_producerspark. Saves only; sending follows the Email Agent's own schedule. A 400 error carries a plain reason; 404 means the campaign or signal key is unknown."""
        return with_campaign_link(
            await api.request(
                "PUT",
                f"/api/v1/agent-tasks/campaigns/{campaign_id}/signals/{signal_key}/emails",
                body={"emails": [email.model_dump() for email in emails]},
            )
        )

    @server.tool(title="Clear signal emails", annotations=CANCEL)
    async def clear_signal_emails(campaign_id: PositiveId, signal_key: SignalKey) -> dict:
        """Removes one signal's own emails (by its signal_key); its people go back to the campaign's main sequence. Returns the campaign with the remaining signal_emails and view_in_producerspark. 404 means the campaign is unknown or archived."""
        return with_campaign_link(
            await api.request("DELETE", f"/api/v1/agent-tasks/campaigns/{campaign_id}/signals/{signal_key}/emails")
        )

    @server.tool(title="Check campaign step", annotations=READ)
    async def check_campaign_item(
        field: CampaignField,
        text: Annotated[str, Field(min_length=1, max_length=5000, description="What the customer wrote for that box, verbatim.")],
        targeting: CampaignTargeting | None = None,
        offering: CampaignOffering | None = None,
    ) -> dict:
        """Reads back what a customer wrote for one campaign step (targeting, signals or offering), using the other steps as context when given. Returns reading (what was understood), issues (typos, vague or missing parts), questions (at most two worth answering), suggested_text (a cleaned wording, empty when the text is fine) and confident. Reads only; nothing is saved."""
        body = {key: value for key, value in {
            "field": field, "text": text, "targeting": targeting, "offering": offering,
        }.items() if value is not None}
        return await api.request("POST", "/api/v1/agent-tasks/campaigns/assist/check", body=body)

    @server.tool(title="Activate campaign", annotations=ACTIVATE)
    async def activate_campaign(campaign_id: PositiveId, confirmation: Confirmation | None = None) -> dict:
        """Activates a campaign: finds its first 100 prospects and adds them to its Golden List, then researches each against the campaign's target signals and writes a custom first email for the customer to review, which replaces Email 1 when that person is enrolled. Costs one credit per prospect found and, per prospect researched, 1 credit per signal (research_credits_per_prospect), charged only when every signal was answered. Nothing is sent and nobody is enrolled. Needs targeting, signals and offering; a 400 error names the missing step. The first call activates nothing and returns needs_confirmation, will_happen (a plain description of what will start) and confirmation; activation happens only on a second call with the same campaign_id and that confirmation. 404 means the campaign is unknown or archived. Returns view_in_producerspark."""
        body = {"confirmation": confirmation} if confirmation is not None else {}
        return with_campaign_link(
            await api.request("POST", f"/api/v1/agent-tasks/campaigns/{campaign_id}/activate", body=body)
        )

    @server.tool(title="Search for signals", annotations=WRITE)
    async def signal_search(
        request: Annotated[str, Field(min_length=1, max_length=10000)],
        campaign_id: PositiveId | None = None,
        idempotency_key: UUID | None = None,
    ) -> dict:
        """Researches people on a campaign's Golden List against its target signals and writes each one's custom first email, taking the request in the user's own words. People or companies it names are researched; otherwise the next N without results (default 10, at most 100). Uses the default campaign unless campaign_id is given; the campaign must be active. Each person costs 1 credit per signal (research_credits_per_prospect, in the result), charged only when every signal was answered, and takes about a minute. The call returns what is ready so far; while status is still_running a repeat call with the same idempotency_key and campaign_id returns the rest and never starts or charges a second search. Rows carry name, title, company, email, status (ready, no_signal, queued, running, held or failed; held means credits are used up), summary, strongest_signal, signals (fired, evidence, sources), facts with sources and, when a signal fired, the custom email (subject and body), which replaces Email 1 when the person is enrolled. Nothing is sent. Each result has view_in_producerspark and reply_must_end_with. No prospect outreach."""
        body = {"request": request, "idempotency_key": str(idempotency_key or uuid4())}
        if campaign_id is not None:
            body["campaign_id"] = campaign_id
        goal = await api.request("POST", "/api/v1/agent-tasks/signal-search", body=body)
        deadline = time.monotonic() + 150
        while goal.get("state") not in FINISHED_SEARCH_STATES and time.monotonic() < deadline:
            await asyncio.sleep(2)
            goal = await api.request("GET", f"/api/v1/agent-tasks/{goal['id']}")
        result = goal.get("prospect_intelligence") or {}
        done = goal.get("state") in FINISHED_SEARCH_STATES
        return with_site_link(goal["id"], {
            "goal_id": goal["id"],
            "idempotency_key": body["idempotency_key"],
            "status": goal["state"] if done else "still_running",
            "notice": "" if done else (
                "Signal research takes about a minute per person. Rows marked ready are done; "
                "call again with the same idempotency_key to collect the rest."
            ),
            "rows": result.get("rows", []),
            "coverage": result.get("coverage", {}),
        })

    @server.tool(title="Review prospect", annotations=SAVE)
    async def review_prospect(
        campaign_id: PositiveId,
        candidate_id: Annotated[str, Field(min_length=1, max_length=255, description="The prospect's candidate_id from get_prospect_intelligence or signal_search rows.")],
        decision: Annotated[Literal["approve", "reject"], Field(description="approve releases a prospect waiting for review for enrollment; reject removes them from the Golden List.")],
    ) -> dict:
        """Decides one prospect held by a campaign's review mode: approve lets them be enrolled, reject removes them from the Golden List. Returns the campaign with waiting_review and view_in_producerspark. Enrolls and sends nothing itself. 400 carries a plain reason; 404 means the campaign is unknown or archived."""
        return with_campaign_link(
            await api.request(
                "POST",
                f"/api/v1/agent-tasks/campaigns/{campaign_id}/prospects/{candidate_id}/review",
                body={"decision": decision},
            )
        )

    @server.tool(title="Add prospect signal", annotations=SAVE)
    async def add_prospect_signal(
        campaign_id: PositiveId,
        candidate_id: Annotated[str, Field(min_length=1, max_length=255, description="The prospect's candidate_id from get_prospect_intelligence or signal_search rows.")],
        intent: Annotated[str, Field(min_length=1, max_length=600, description="What happened, in a sentence, for example 'Booked a demo from the pricing page'.")],
        keyword: Annotated[str, Field(min_length=1, max_length=60, description="A short label for the signal, for example demo_booked; one signal per person and keyword.")],
        source_url: Annotated[str, Field(max_length=500, description="A link to the record or page behind it, when there is one.")] = "",
    ) -> dict:
        """Records a first-party signal the customer's own system knows about a Golden List person: it joins their research as a found signal with the intent as evidence, becomes their strongest signal, reworks their scores, writes their {{signal_line}} for it, and the campaign's webhook hears prospect.signal. Review mode applies. Returns created (false when that keyword was already recorded for the person) and the person's research row. Sends nothing. 400 carries a plain reason; 404 means the campaign is unknown or archived."""
        return await api.request(
            "POST",
            f"/api/v1/agent-tasks/campaigns/{campaign_id}/prospects/{candidate_id}/signals",
            body={"intent": intent, "keyword": keyword, "source_url": source_url},
        )

    @server.tool(title="Find lookalikes", annotations=SAVE)
    async def find_lookalikes(
        campaign_id: PositiveId,
        seeds: Annotated[str, Field(min_length=1, max_length=2000, description="The customer's best customers in their own words: company names, or people and their companies.")],
        count: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> dict:
        """Keeps the customer's best customers on the campaign (lookalike_seeds in research_options) and starts one people search for people like them who match the targeting: the same kinds of roles at the same kinds of companies, skipping the customers named. The people found with an email join the Golden List and are researched like everyone else; the search charges one credit per person delivered. Returns the campaign with view_in_producerspark. 400 carries a plain reason; 404 means the campaign is unknown or archived."""
        return with_campaign_link(
            await api.request(
                "POST",
                f"/api/v1/agent-tasks/campaigns/{campaign_id}/lookalikes",
                body={"seeds": seeds, "count": count},
            )
        )

    @server.tool(title="Get prospect intelligence", annotations=READ)
    async def get_prospect_intelligence(
        campaign_id: PositiveId,
        status: Annotated[str | None, Field(description="Filters rows to one state: ready, no_signal, queued, running, held, failed or could_not_check. Omitted: all rows.")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> dict:
        """The signal research and custom emails already on a campaign's Golden List, newest first, with counts (ready, no_signal, queued, running, held, failed, could_not_check, and pending prospects not yet researched), optionally filtered by status. Rows have the same shape as signal_search rows and carry scores (fit 0 to 1, intent 0 to 3, total, each with its reason), review (waiting or approved under review mode), fresh (false once older than 90 days) and enrollment_block (why the person is not enrolled now, or null). Reads only; starts no research."""
        params = {"status": status, "limit": limit}
        data = await api.request(
            "GET", f"/api/v1/agent-tasks/campaigns/{campaign_id}/intelligence",
            params={key: value for key, value in params.items() if value is not None},
        )
        if isinstance(data, dict):
            data = {**data, "view_in_producerspark": campaign_page(campaign_id)}
        return data

    @server.tool(title="Get campaign email step", annotations=READ)
    async def get_campaign_email(campaign_id: PositiveId) -> dict:
        """A campaign's Email step: its saved sequence (steps and revision), how many people are enrolled and not yet enrolled, sent, queued and replied counts, whether it is sending, and checks listing what is still missing before sending can start (email_agent, sequence, mailbox, people), with ready_to_send. Reads only; nothing is sent."""
        return with_campaign_link({"id": campaign_id, **await api.request("GET", f"/api/v1/agent-tasks/campaigns/{campaign_id}/email")})

    @server.tool(title="Save campaign sequence", annotations=SAVE)
    async def save_campaign_sequence(campaign_id: PositiveId, steps: SequenceSteps, revision: Annotated[str | None, Field(description="The sequence.revision the edit was based on. When omitted, whatever is saved now is replaced.")] = None) -> dict:
        """Saves a campaign's main sequence: the emails (and any LinkedIn steps) every enrolled person gets, on their days, as on the campaign's Messaging tab. steps replaces the saved sequence. A signal's own emails take the place of the first emails for that signal's people. Saves only: nothing is enrolled or sent. A campaign already sending uses the new wording on its live emails; changing the number or days of steps needs sending paused. 409 means the sequence changed since revision was read. Returns the same shape as get_campaign_email."""
        body = {"steps": [step.model_dump() for step in steps]}
        if revision is not None:
            body["revision"] = revision
        return with_campaign_link({"id": campaign_id, **await api.request("PUT", f"/api/v1/agent-tasks/campaigns/{campaign_id}/email/sequence", body=body)})

    @server.tool(title="Enroll campaign people", annotations=SAVE)
    async def enroll_campaign_people(campaign_id: PositiveId, limit: Annotated[int, Field(ge=1, le=1000, description="Most people to enroll in this call. Omitted: everyone not enrolled yet, up to 1,000.")] = 1000) -> dict:
        """Enrolls people on a campaign's Golden List in its email sequence: everyone not yet enrolled, or up to limit. Excluded people and restricted companies are never enrolled; anyone the sequence cannot email is listed in skipped with the reason. While sending is off, enrolling sends nothing and people wait, ready. While sending is on, their first email goes out about fifteen minutes later. A person's custom first email from signal research replaces Email 1 for them. Returns enrolled_count, skipped, remaining and the get_campaign_email shape."""
        enrolled, skipped, result = 0, [], {}
        while enrolled < limit:
            result = await api.request("POST", f"/api/v1/agent-tasks/campaigns/{campaign_id}/email/enroll",
                                       body={"limit": min(ENROLL_BATCH, limit - enrolled)}, timeout_seconds=120.0)
            enrolled += result.get("enrolled_count", 0)
            skipped += result.get("skipped", [])
            if not result.get("enrolled_count") or not result.get("remaining"):
                break
        return with_campaign_link({"id": campaign_id, **result, "enrolled_count": enrolled, "skipped": skipped})

    @server.tool(title="Start campaign sending", annotations=ACTIVATE)
    async def start_campaign_sending(campaign_id: PositiveId, confirmation: Confirmation | None = None) -> dict:
        """Switches a campaign's sending on: enrolled people start getting its sequence from the account's mailboxes on its sending days and hours. This emails real prospects. The first call sends nothing and returns needs_confirmation, will_happen (the sequence, how many people, which mailboxes and days) and confirmation; sending starts only on a second call with the same campaign_id and that confirmation. A 400 error names what is missing (sequence, mailbox or enrolled people). Returns the get_campaign_email shape with sending true."""
        body = {"confirmation": confirmation} if confirmation is not None else {}
        return with_campaign_link({"id": campaign_id, **await api.request("POST", f"/api/v1/agent-tasks/campaigns/{campaign_id}/email/start", body=body)})

    @server.tool(title="Pause campaign sending", annotations=SAVE)
    async def pause_campaign_sending(campaign_id: PositiveId) -> dict:
        """Stops a campaign's sending, email and LinkedIn steps both. Enrolled people keep their place until sending is switched back on. Nothing is sent or deleted. Returns the get_campaign_email shape."""
        return with_campaign_link({"id": campaign_id, **await api.request("POST", f"/api/v1/agent-tasks/campaigns/{campaign_id}/email/pause")})

    @server.tool(title="Get campaign results", annotations=READ)
    async def get_campaign_results(campaign_id: PositiveId) -> dict:
        """A campaign's results, as its Results tab shows them: prospects found and reviewed, signals, emails prepared, enrolled, sent and replied (personal and automatic replies apart), sends per day, every sent email with its person, mailbox, step and outcome and any visit to the user's website from that company, every reply with its sender, subject and kind, research progress and the last search. Sent is a recorded provider send, not an open or inbox receipt. Reads only."""
        return with_campaign_link({"id": campaign_id, **await api.request("GET", f"/api/v1/agent-tasks/campaigns/{campaign_id}/results")})

    @server.tool(title="Get email sending settings", annotations=READ)
    async def get_email_sending_settings() -> dict:
        """The account's sending settings: whether sending is on, send_on_weekends, the daily limit and each mailbox's limit, minutes between sends, sending hours, stop on reply, and each connected mailbox with whether it is ready (and why not), in rotation, SPF/DKIM/DMARC, queued sends and deliverability over 7 and 30 days with inbox placement. Reads only."""
        return await api.request("GET", "/api/v1/agent-tasks/email/sending-settings")

    @server.tool(title="Update email sending settings", annotations=SAVE)
    async def update_email_sending_settings(send_on_weekends: Annotated[bool | None, Field(description="true to send every day, false for Monday to Friday only.")] = None, daily_limit: Annotated[int | None, Field(ge=1, description="Most emails the account sends in a day.")] = None) -> dict:
        """Changes the account's sending days or daily limit for every campaign. Waiting first emails move onto the new days. Returns the settings in the get_email_sending_settings shape. A 400 error names a value outside what the account allows."""
        body = {key: value for key, value in {"send_on_weekends": send_on_weekends, "daily_limit": daily_limit}.items() if value is not None}
        return await api.request("PATCH", "/api/v1/agent-tasks/email/sending-settings", body=body)

    register_agent_tools(server, api)
    register_recorder(server, api)
    register_people(server)
    titles_in_annotations(server)

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

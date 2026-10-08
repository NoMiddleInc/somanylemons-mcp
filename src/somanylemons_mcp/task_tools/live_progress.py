"""Stateless presentation of canonical research facts. Never executes research."""
import hashlib
import json

FINISHED = {"completed", "satisfied"}
ATTENTION = {"cancelled", "superseded", "failed", "needs_attention", "blocked",
             "waiting_customer", "waiting_external", "paused"}


def number(*values):
    return next((value for value in values if type(value) is int and value >= 0), None)


def live_update(answer, requested_goal_id):
    business = answer.get("business_answer") or {}
    research = answer.get("research_answer") or {}
    conference = answer.get("conference_answer") or {}
    counts = business.get("counts") or research.get("counts") or {}
    coverage = business.get("coverage") or {}
    enrichment = conference.get("enrichment_summary") or {}
    request = answer.get("request") or {}
    spec = business.get("spec") or request.get("spec") or {}
    qualified = number(coverage.get("qualified_rows"), counts.get("qualified_contacts"))
    found = number(qualified, counts.get("contacts"), counts.get("returned"))
    target = number(coverage.get("requested_count"), counts.get("requested"), spec.get("count"), request.get("count"))
    emails = number(counts.get("qualified_contacts_with_recorded_email"), counts.get("recorded_emails"), enrichment.get("recorded_email_people"))
    # Never count preview rows, provider candidates or worker steps as prospects.
    unit = "qualified prospects" if qualified is not None else "saved contacts"
    state = answer.get("state")
    fulfilled = business.get("full_request_fulfilled")
    all_requested = coverage.get("all_requested") is True or spec.get("all") is True
    coverage_complete = not all_requested or coverage.get("full_coverage_verified") is True
    blocker = answer.get("blocker")
    blockers = business.get("blockers")
    manual_review = answer.get("manual_review_required")
    # Completed, accepted current evidence outranks retained retry diagnostics.
    # A bounded count does not claim independently verified whole-market coverage.
    delivery = business.get("delivery") or {}
    current_completion = (
        state in FINISHED and answer.get("fulfillment") == "fulfilled"
        and business.get("fulfillment") == "fulfilled" and business.get("closure") == "fulfilled"
        and (business.get("fulfillment_review") or {}).get("passed") is True
        and business.get("review_scope_current") is not False
        and delivery.get("status") == "provider_accepted" and bool(delivery.get("receipt"))
        and fulfilled is not False and coverage_complete and coverage.get("interim") is not True
        and found is not None and (target is None or found >= target)
    )
    historical_blockers = []
    if current_completion:
        historical_blockers = ([blocker] if blocker else []) + (blockers if isinstance(blockers, list) else [blockers] if blockers else [])
        blocker, blockers, manual_review = None, [], False
    held = bool(blocker or manual_review or blockers)
    finished = (state in FINISHED and fulfilled is not False and not held
                and answer.get("fulfillment") not in {"partial", "unfulfilled", "pending"}
                and coverage_complete
                and not (found is not None and target is not None and found < target))
    stop = state in ATTENTION or state in FINISHED or held
    samples = []
    for row in (answer.get("contacts") or [])[:3]:
        if not isinstance(row, dict):
            continue
        samples.append({key: row[key] for key in (
            "name", "company", "title", "role", "location", "fit", "reason",
            "email", "linkedin", "linkedin_url", "email_status", "enrichment_status", "source_references", "evidence_refs",
        ) if key in row})
    sources = (answer.get("sources") or [])[:3]
    facts = {
        "goal_id": answer.get("id"), "requested_goal_id": requested_goal_id,
        "state": state, "stage": answer.get("current_step"),
        "running": answer.get("running"), "found": found, "target": target,
        "count_basis": unit if found is not None else "Contact count not yet recorded",
        "recorded_emails": emails, "blocker": blocker,
        "next_action": answer.get("next_action"), "findings": samples, "sources": sources,
        "coverage": coverage, "enrichment": enrichment,
        "email_status_counts": answer.get("saved_email_status_counts"),
        "enrichment_status_counts": answer.get("saved_enrichment_status_counts"),
        "limitations": business.get("limitations"), "blockers": blockers,
        "historical_blockers": historical_blockers,
        "fulfillment": answer.get("fulfillment"), "full_request_fulfilled": fulfilled,
        "manual_review_required": manual_review,
        "artifacts": answer.get("artifacts"),
        **{key: business[key] for key in ("answer_text", "response_policy") if key in business},
    }
    cursor = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
    if found is None:
        summary = "Prospect count not yet recorded."
    elif target is not None:
        summary = f"{found} of {target} {unit} found."
    else:
        summary = f"{found} {unit} found."
    if emails is not None:
        summary += f" {emails} with recorded business emails."
    if answer.get("current_step"):
        summary += f" Saved stage: {str(answer['current_step'])[:240]}."
    summary += f" Status: {state or 'not recorded'}."
    conference_review = isinstance(blocker, dict) and blocker.get("party") == "operator" and (
        blocker.get("reason") == "business_conference_human_review_required"
        or blocker.get("code") == "business_conference_human_review_required"
        or str(blocker.get("reason") or "").startswith("Our team is on it. Your conference speaker email request")
    )
    if conference_review:
        summary = ("Our team is on it. Your conference speaker email request is saved for human review. "
                   "You do not need to resend it.")
    suggestions = []
    if finished:
        suggestions.append("Review or download the saved prospect list.")
        suggestions.append("Would you like to plan an email campaign for these prospects?")
    elif conference_review:
        suggestions.append("Our team will review the saved conference speaker email request; no customer retry is needed.")
    elif stop:
        suggestions.append("Review the saved partial results and their remaining gaps.")
        suggestions.append("Explain the specific recorded blocker and the smallest next step; reuse this research task.")
    contact_ready = isinstance(business.get("answer_text"), str) and bool(business["answer_text"])
    if contact_ready:
        summary, suggestions, stop = business["answer_text"], [], True
    return {
        **facts, "cursor": cursor, "summary": summary,
        "final_response_ready": bool(finished or contact_ready),
        "contact_table_policy": "Show qualified saved contacts from this current goal immediately, labelled with the saved count and recorded uncertainty, and continue recovery automatically. Claim the request is complete only after final_response_ready. Never combine old partial, rejected or ineligible rows from other goals to fill the requested count.",
        "monitor_status": "answer_available" if contact_ready else "finished" if finished else "needs_attention" if stop else "watching",
        "continue_watching": not stop, "suggestions": suggestions,
        "observed_at": answer.get("updated_at"),
        "email_note": "Recorded email and verification statuses are saved evidence, not a fresh deliverability check.",
        "findings_scope": "Up to three saved preview contacts and sources; not newly discovered contacts or a complete cohort.",
        "campaign_action": "suggest_only",
        "completion_instruction": (
            "Continue watching in this response until final_response_ready; a poll timeout or unchanged cursor is not a final answer. Do not ask the customer to check again, present a partial table as fulfillment, or combine contacts from historical goals to fill the count."
            if not stop else
            "Read get_research_answer(details=true) with all contact pages or read_task_spreadsheet and return the full requested contact table."
            if finished else "Explain the actual saved outcome without inventing missing data."
        ),
    }

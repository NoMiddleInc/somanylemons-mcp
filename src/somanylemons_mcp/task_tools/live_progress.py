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
    held = bool(answer.get("blocker") or answer.get("manual_review_required") or business.get("blockers"))
    finished = (state in FINISHED and fulfilled is not False and not held
                and answer.get("fulfillment") not in {"partial", "unfulfilled", "pending"}
                and coverage.get("full_coverage_verified") is not False
                and not (found is not None and target is not None and found < target))
    stop = state in ATTENTION or state in FINISHED or held
    samples = []
    for row in (answer.get("contacts") or [])[:3]:
        if not isinstance(row, dict):
            continue
        samples.append({key: row[key] for key in (
            "name", "company", "title", "role", "location", "fit", "reason",
            "email_status", "enrichment_status", "source_references", "evidence_refs",
        ) if key in row})
    sources = (answer.get("sources") or [])[:3]
    facts = {
        "goal_id": answer.get("id"), "requested_goal_id": requested_goal_id,
        "state": state, "stage": answer.get("current_step"),
        "running": answer.get("running"), "found": found, "target": target,
        "count_basis": unit if found is not None else "Contact count not yet recorded",
        "recorded_emails": emails, "blocker": answer.get("blocker"),
        "next_action": answer.get("next_action"), "findings": samples, "sources": sources,
        "coverage": coverage, "enrichment": enrichment,
        "email_status_counts": answer.get("saved_email_status_counts"),
        "enrichment_status_counts": answer.get("saved_enrichment_status_counts"),
        "limitations": business.get("limitations"), "blockers": business.get("blockers"),
        "fulfillment": answer.get("fulfillment"), "full_request_fulfilled": fulfilled,
        "manual_review_required": answer.get("manual_review_required"),
        "artifacts": answer.get("artifacts"),
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
    suggestions = []
    if finished:
        suggestions.append("Review or download the saved prospect list.")
        suggestions.append("Would you like to plan an email campaign for these prospects?")
    elif stop:
        suggestions.append("Resolve the recorded blocker or review the saved partial results.")
    return {
        **facts, "cursor": cursor, "summary": summary,
        "monitor_status": "finished" if finished else "needs_attention" if stop else "watching",
        "continue_watching": not stop, "suggestions": suggestions,
        "observed_at": answer.get("updated_at"),
        "email_note": "Recorded email and verification statuses are saved evidence, not a fresh deliverability check.",
        "findings_scope": "Up to three saved preview contacts and sources; not newly discovered contacts or a complete cohort.",
        "campaign_action": "suggest_only",
    }

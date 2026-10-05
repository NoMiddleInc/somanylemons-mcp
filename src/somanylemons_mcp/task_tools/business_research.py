"""Typed business intake and projections of saved backend evidence only."""

from collections import Counter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class BusinessCompany(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=200)]
    domain: Annotated[str, Field(max_length=253)] | None = None


class BusinessEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=200)]
    year: Annotated[int, Field(ge=2000, le=2100)]
    source_urls: Annotated[list[Annotated[str, Field(pattern=r"^https://", max_length=2000)]], Field(min_length=1, max_length=10)]
    profile_id: Annotated[str, Field(max_length=200)] | None = None
    start_date: Annotated[str, Field(max_length=10)] | None = None
    end_date: Annotated[str, Field(max_length=10)] | None = None


class BusinessResearchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["company_contacts", "conference_speakers"]
    companies: Annotated[list[BusinessCompany], Field(max_length=200)] | None = None
    roles: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=30)] | None = None
    person_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    event: BusinessEvent | None = None
    fields: Annotated[list[Literal["name", "company", "title", "role", "email", "business_email", "linkedin", "linkedin_url", "session_title", "session", "location", "industry", "website_url", "company_website", "notes"]], Field(min_length=1, max_length=20)] | None = None
    count: Annotated[int, Field(ge=1, le=500)] | None = None
    all: bool | None = None


def saved_business_answer(task, result, *, contact_page, source_page):
    """Read current saved rows without qualifying, researching or declaring success."""
    contract = task.get("contract") or {}
    canonical = task.get("business_answer")
    revision = task.get("contract_revision") or contract.get("task_manager", {}).get("revision", 1)
    steps = [step for step in task.get("tasks", []) if step.get("contract_revision", 1) == revision]
    if not isinstance(canonical, dict) and contract.get("workflow") != "business_research" and not any(
        step.get("capability", "").startswith("business.") for step in steps
    ):
        return None
    if isinstance(canonical, dict):
        saved = canonical
    else:
        research = next((step.get("result") or {} for step in steps if step.get("capability") == "business.research"), {})
        prepared = next((step.get("result") or {} for step in steps if step.get("capability") == "business.artifact_prepare"), {})
        saved = prepared if isinstance(prepared.get("rows"), list) else research
    rows = saved.get("rows") if isinstance(saved.get("rows"), list) else saved.get("contacts") or []
    sources = saved.get("sources") or []
    total = saved.get("contacts_total", len(rows))
    if type(total) is not int or total < len(rows):
        total = len(rows)
    result["business_answer"] = {key: value for key, value in saved.items() if key in {
        "counts", "coverage", "spec", "requested", "delivery", "citation_basis", "limitations", "full_request_fulfilled",
    }}
    result["request"] = {"request": contract.get("request"), "spec": contract.get("spec")}
    fields = {
        "id", "name", "company", "title", "role", "email", "linkedin", "linkedin_status", "location", "industry",
        "website_url", "company_website", "session_title", "notes", "email_status", "email_verified_at",
        "email_source", "email_source_url", "email_observed_at", "enrichment_status",
        "enrichment_source", "enriched_on", "fit", "reason", "uncertainty",
    }
    result["contacts"] = []
    for row in rows[(contact_page - 1) * 5:contact_page * 5]:
        if not isinstance(row, dict):
            continue
        projected = {key: value for key, value in row.items() if key in fields}
        evidence = row.get("evidence_refs") or []
        projected["evidence_refs"] = [{key: str(value)[:300] if key == "quote" else value for key, value in ref.items() if key in {
            "url", "source_url", "content_hash", "observed_at", "quote", "field", "provider",
            "enrichment_source", "source_operation_id",
        }} for ref in evidence[:3] if isinstance(ref, dict)]
        projected["evidence_refs_total"] = len(evidence)
        projected["evidence_excerpts_truncated"] = len(evidence) > 3 or any(
            isinstance(ref, dict) and len(str(ref.get("quote") or "")) > 300 for ref in evidence
        )
        projected["source_references"] = [{key: source[key] for key in ("url", "access", "observed_at", "content_hash") if key in source}
            for source in (row.get("public_sources") or [])[:3] if isinstance(source, dict)]
        projected["source_references_total"] = len(row.get("public_sources") or [])
        provenance = row.get("field_provenance") or {}
        if isinstance(provenance, dict):
            projected["field_provenance"] = {field: {
                key: str(value)[:300] for key, value in detail.items()
                if key in {"status", "source", "observed_at", "uncertainty"}
            } for field, detail in list(provenance.items())[:20] if isinstance(detail, dict)}
            projected["field_provenance_total"] = len(provenance)
        result["contacts"].append(projected)
    result["sources"] = [{key: value for key, value in source.items() if key in {
        "url", "source_url", "access", "observed_at", "content_hash", "name", "title", "source_type",
    }} for source in sources[(source_page - 1) * 3:source_page * 3] if isinstance(source, dict)]
    result["pagination"] = {
        "contact_page": contact_page, "contact_page_size": 5, "contacts_total": total,
        "contacts_available": len(rows), "contacts_truncated": total > len(rows),
        "source_page": source_page, "source_page_size": 3, "sources_total": len(sources),
    }
    result["saved_email_status_counts"] = dict(Counter(str(row.get("email_status") or "not_recorded") for row in rows if isinstance(row, dict)))
    result["saved_enrichment_status_counts"] = dict(Counter(str(row.get("enrichment_status") or "not_recorded") for row in rows if isinstance(row, dict)))
    result["saved_status_count_scope"] = {
        "contacts_counted": len(rows), "contacts_total": total, "complete": len(rows) == total,
    }
    result["email_gaps"] = [{key: row[key] for key in fields if key in row} for row in rows if isinstance(row, dict) and not str(row.get("email") or "").strip()][:5]
    result["business_evidence_scope"] = "Saved current-revision rows and checks; source targets alone are not observed evidence, and recorded verification is not a fresh deliverability check."
    return result

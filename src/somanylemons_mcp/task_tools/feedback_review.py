"""Read only a backend-validated feedback snapshot and its immutable attachments.

Selected review rows are never promoted to native qualified contacts. Exact saved
CSV field strings, authored draft bodies and separate source/date/uncertainty
fields remain unchanged. This consumer neither reads local files nor starts work.
"""

from collections import Counter
from copy import deepcopy
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from .agency_identity import positive_id, validated_agency_scope
from .client import TaskApiError

VERSION = "agency.feedback_review_snapshot.v2"
# This version publishes one reviewed saved delivery. Its validation revisions
# are current read-time checks, never inferred delivery-time revisions. A future
# amendment requires a new reviewed publication binding rather than relabeling
# these immutable files from an arbitrary current task revision.
PUBLICATION_BINDING = {
    "config_id": 8, "client_id": 79, "organization_id": 67, "campaign_id": 45,
    "original_goal_id": 24, "pilot_goal_id": 52, "delivery_job_id": 251, "source_job_id": 219,
    "current_original_contract_revision": 2, "current_pilot_contract_revision": 1,
    "validation_revision_basis": "reviewed_post_delivery_current_revisions",
    "validation_receipt_sha256": "7bc84eb88107e9c4255ea92f8a2f6454bfeb83495799c7a08f32a0c5dc82aa60",
}
# The backend proves this presentation-only continuation against its saved
# event/manifest. It does not amend the historical 2/1 publication or establish
# delivery-time revisions. Accept no arbitrary current-revision relabeling.
CONTINUATION_BINDING = {
    "approval_key": "0b8fada3-dfb3-40dc-a6e2-1cae86227e2a",
    "current_native_revision": 3,
    "validation_revision": 2,
    "manifest_hash": "623845cbdbf928c325d745e70b7691d1947c186dd5c4332784a059d1d9932258",
}
MAX_CONTACTS = 1000
STEPS_PER_CONTACT = 6
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}\Z")
RESOURCE = re.compile(
    r"producerspark-feedback-attachment://([1-9][0-9]{0,19})/([1-9][0-9]{0,19})/([0-9a-f]{2,800})/([0-9a-f]{64})\Z"
)
DTO_KEYS = frozenset({
    "version", "requested_goal_id", "source", "answer_lineage_scope", "current_answer_goal_id",
    "lineage_basis", "review_scope", "saved_delivery", "counting_basis", "native_research_counts_replaced",
    "recorded_verification_is_fresh", "retrieval_effects", "counts", "selected_review_contacts",
    "draft_sequences", "csv_headers", "csv_attachment_roles", "attachments",
})
FACTS_DTO_KEYS = DTO_KEYS - {"draft_sequences"}
DRAFT_CONTACT_FIELD = "Draft email for you to send"
CONTACT_FIELDS = frozenset({
    "Name", "Why selected", "LinkedIn profile", "Business email", "Draft email for you to send",
    "Stable prospect ID", "Proposed action", "Membership change applied", "Agency",
    "Email verification", "Enrichment status", "Enrichment source", "Enriched on (UTC)",
    "Evidence sources", "Uncertainty / review notes", "Qualification note", "Review batch/version",
})
SEQUENCE_FIELDS = frozenset({
    "Name", "Why selected", "LinkedIn profile", "Business email", "Draft email for you to send",
    "Prospect ID", "Step", "Body", "Subject", "Status", "Outreach enrolled",
    "Membership change applied", "Agency", "Review batch/version", "Sequence version", "Source sequence version",
})


def _require(condition):
    if not condition:
        # Never echo customer field strings, filenames or attachment content in an error.
        raise TaskApiError("The saved feedback snapshot scope or integrity is invalid; no fallback was substituted.")


def _record(value):
    _require(isinstance(value, dict))
    return value


def _fields(row, required):
    fields = _record(row.get("fields"))
    _require(required.issubset(fields) and len(fields) <= 100)
    _require(all(isinstance(key, str) and isinstance(value, str) and len(value) <= 100000
                 for key, value in fields.items()))
    return fields


def _prospect_draft_field(name):
    separated = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name or ""))
    key = re.sub(r"[^a-z0-9]+", " ", separated.casefold()).strip()
    return bool(
        re.search(r"\b(?:draft|drafts|sequence|sequences)\b", key)
        or key.startswith("intro copy") or key in {"prospect intro", "intro overrides"}
        or (key.startswith("intro ") and re.search(r"\b(?:email|subject|body|source)\b", key))
        or key in {"prospect email body", "prospect email subject", "prospect email copy",
                   "email body", "email subject", "email copy"}
    )


def _attachment_path(task, snapshot, descriptor):
    source = snapshot["source"]
    _require(set(descriptor) == {
        "name", "mime", "bytes", "sha256", "source_delivery_job_id", "source_goal_id",
        "validation_contract_revision", "delivered_contract_revision", "delivered_revision_status", "attachment_retrieval",
    })
    name = descriptor.get("name")
    _require(isinstance(name, str) and 0 < len(name) <= 200
             and re.fullmatch(r"[A-Za-z0-9_. -]+", name) is not None)
    _require(isinstance(descriptor.get("sha256"), str) and HASH.fullmatch(descriptor["sha256"]))
    _require(positive_id(descriptor.get("bytes")) and descriptor["bytes"] <= MAX_ATTACHMENT_BYTES)
    _require(descriptor.get("mime") in {"text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
    _require(descriptor.get("source_delivery_job_id") == source["delivery_job_id"]
             and descriptor.get("source_goal_id") == source["pilot_goal_id"]
             and positive_id(descriptor.get("validation_contract_revision"))
             and descriptor["validation_contract_revision"] == source["current_pilot_contract_revision"]
             and descriptor.get("delivered_contract_revision") is None
             and descriptor.get("delivered_revision_status") == "not_recorded")
    retrieval = _record(descriptor.get("attachment_retrieval"))
    _require(retrieval.get("method") == "GET" and retrieval.get("authentication_required") is True
             and retrieval.get("original_sent_bytes") is True and retrieval.get("new_research_or_delivery") is False)
    url = retrieval.get("api_url")
    _require(isinstance(url, str) and len(url) <= 2000)
    parsed = urlsplit(url)
    expected_path = f"/api/v1/agent-tasks/{task['id']}/feedback-snapshots/{source['delivery_job_id']}/attachments"
    params = {"name": name, "sha256": descriptor["sha256"]}
    _require(not parsed.scheme and not parsed.netloc and not parsed.fragment and parsed.path == expected_path)
    _require(parse_qs(parsed.query, keep_blank_values=True) == {key: [value] for key, value in params.items()})
    browser_url = retrieval.get("browser_url")
    _require(isinstance(browser_url, str) and len(browser_url) <= 2000)
    browser = urlsplit(browser_url)
    _require(not browser.scheme and not browser.netloc and not browser.fragment
             and browser.path == expected_path.replace("/api/v1/", "/api/", 1)
             and parse_qs(browser.query, keep_blank_values=True) == {key: [value] for key, value in params.items()})
    return expected_path, params


def _validate_feedback_snapshot(task):
    """Return a validated saved DTO, None when absent; malformed/foreign DTOs fail closed."""
    snapshot = task.get("feedback_review_snapshot")
    if snapshot is None:
        return None
    snapshot = _record(snapshot)
    if snapshot.get("status") == "unavailable":
        _require(set(snapshot) == {"status", "reason", "read_only", "new_research_or_delivery"}
                 and snapshot["reason"] == "saved_feedback_binding_or_integrity_invalid"
                 and snapshot["read_only"] is True and snapshot["new_research_or_delivery"] is False)
        return snapshot
    # customer_research_facts applies the mandatory no-draft policy recursively
    # to this otherwise unchanged v2 DTO. This is an exact second wire shape,
    # not a missing-field fallback or a new historical publication version.
    facts_only = set(snapshot) == FACTS_DTO_KEYS
    _require((facts_only or set(snapshot) == DTO_KEYS) and snapshot.get("version") == VERSION)
    _require(all(isinstance(snapshot.get(key), str) and 0 < len(snapshot[key]) <= 2000
                 for key in ("lineage_basis", "counting_basis")))
    _require(positive_id(task.get("id")) and isinstance(task.get("research_answer"), dict)
             and not isinstance(task.get("conference_answer"), dict))
    scope = validated_agency_scope(snapshot.get("answer_lineage_scope"))
    # Feedback lineage is independently server-owned. Its nested pointer does not
    # create a native pointer; native navigation requires a task-level scope.
    if "answer_lineage_scope" in task:
        _require(validated_agency_scope(task["answer_lineage_scope"]) == scope)
    customer = _record(task.get("customer"))
    _require(positive_id(customer.get("id")) and customer["id"] == scope["client_id"])
    source = _record(snapshot.get("source"))
    source_keys = set(PUBLICATION_BINDING) | {
        "manifest_sha256", "original_scope_sha256", "pilot_scope_sha256",
        "delivered_original_contract_revision", "delivered_pilot_contract_revision", "delivered_revision_status",
        "publication_validation",
    }
    continuation = source.get("continuation_provenance")
    if continuation is not None:
        continuation = _record(continuation)
        _require(set(source) == source_keys | {"validation_original_contract_revision", "continuation_provenance"}
                 and set(continuation) == set(CONTINUATION_BINDING)
                 and all(continuation.get(key) == value for key, value in CONTINUATION_BINDING.items())
                 and positive_id(continuation.get("current_native_revision"))
                 and positive_id(continuation.get("validation_revision"))
                 and isinstance(continuation.get("approval_key"), str)
                 and 0 < len(continuation["approval_key"]) <= 140
                 and source.get("validation_original_contract_revision") == PUBLICATION_BINDING["current_original_contract_revision"]
                 and positive_id(source.get("validation_original_contract_revision")))
    else:
        _require(set(source) == source_keys)
    for key in ("config_id", "client_id", "organization_id", "campaign_id", "original_goal_id"):
        _require(positive_id(source.get(key)) and source[key] == scope[key])
    for key in ("source_job_id", "delivery_job_id", "pilot_goal_id", "current_original_contract_revision", "current_pilot_contract_revision"):
        _require(positive_id(source.get(key)))
    for key, value in PUBLICATION_BINDING.items():
        if continuation is not None and key == "current_original_contract_revision":
            value = continuation["current_native_revision"]
        elif continuation is not None and key == "validation_revision_basis":
            value = "reviewed_immutable_pre_continuation_snapshot"
        _require(source.get(key) == value)
    publication = _record(source["publication_validation"])
    _require(positive_id(publication.get("original_contract_revision"))
             and positive_id(publication.get("pilot_contract_revision")))
    _require(publication == {
        "original_contract_revision": PUBLICATION_BINDING["current_original_contract_revision"],
        "pilot_contract_revision": PUBLICATION_BINDING["current_pilot_contract_revision"],
        "basis": PUBLICATION_BINDING["validation_revision_basis"],
        "receipt_sha256": PUBLICATION_BINDING["validation_receipt_sha256"],
    })
    _require(source.get("delivered_original_contract_revision") is None
             and source.get("delivered_pilot_contract_revision") is None
             and source.get("delivered_revision_status") == "not_recorded")
    _require("original_contract_revision" not in source and "pilot_contract_revision" not in source)
    for key in ("manifest_sha256", "original_scope_sha256", "pilot_scope_sha256"):
        _require(isinstance(source.get(key), str) and HASH.fullmatch(source[key]))
    _require(source["pilot_goal_id"] != source["original_goal_id"]
             and positive_id(snapshot.get("requested_goal_id")) and snapshot["requested_goal_id"] == task["id"]
             and task["id"] in {source["original_goal_id"], source["pilot_goal_id"]}
             and positive_id(snapshot.get("current_answer_goal_id")) and snapshot["current_answer_goal_id"] == source["pilot_goal_id"])
    # Snapshot metadata alone cannot establish a native navigation pointer.
    pointer = task.get("current_answer_goal_id")
    # Historical snapshot navigation remains nested. A proven continuation's
    # native pointer targets the original ALL goal, never the old paused pilot.
    expected_pointer = (source["original_goal_id"]
                        if continuation is not None and task["id"] == source["original_goal_id"]
                        else source["pilot_goal_id"])
    _require(pointer is None or (positive_id(pointer) and pointer == expected_pointer))
    revision = task.get("contract_revision")
    if revision is None:
        revision = (task.get("contract") or {}).get("task_manager", {}).get("revision")
    expected_revision = source["current_original_contract_revision"] if task["id"] == source["original_goal_id"] else source["current_pilot_contract_revision"]
    _require(positive_id(revision) and revision == expected_revision)
    review = _record(snapshot.get("review_scope"))
    for key in ("requested_original_agency_count", "approved_batch_agency_count", "remaining_original_agency_count"):
        _require(positive_id(review.get(key)))
    _require(review["approved_batch_agency_count"] + review["remaining_original_agency_count"] == review["requested_original_agency_count"])
    _require(review.get("partial_roster") is True and review.get("roster_complete") is False
             and review.get("original_request_fulfilled") is False and review.get("remaining_work_held") is True
             and review.get("feedback_state") == "awaiting_client_feedback")
    if continuation is None or task["id"] == source["pilot_goal_id"]:
        _require(task.get("state") == "paused" and task.get("action_is_scheduled") is not True)
    # review_scope describes the immutable pilot's historical hold. The active
    # original goal's current state, controls and progress are independent.
    _require(snapshot.get("native_research_counts_replaced") is False
             and snapshot.get("recorded_verification_is_fresh") is False)
    effects = _record(snapshot.get("retrieval_effects"))
    _require(set(effects) == {"enrichment_started", "customer_email_sent", "prospect_outreach", "membership_changed", "holds_cleared"}
             and all(value is False for value in effects.values()))
    delivery = _record(snapshot.get("saved_delivery"))
    _require(delivery.get("status") == "provider_accepted_saved_and_verified"
             and delivery.get("fresh_provider_readback") is False and delivery.get("inbox_receipt_proven") is False
             and isinstance(delivery.get("verification_recorded_at"), str) and 0 < len(delivery["verification_recorded_at"]) <= 100
             and isinstance(delivery.get("provider_receipt_sha256"), str) and HASH.fullmatch(delivery["provider_receipt_sha256"]))
    counts = _record(snapshot.get("counts"))
    count = counts.get("selected_review_contact_count")
    _require(positive_id(count) and count <= MAX_CONTACTS)
    email_count = counts.get("recorded_business_email_count")
    _require(type(email_count) is int and 0 <= email_count <= count)
    if facts_only:
        _require(set(counts) == {"selected_review_contact_count", "recorded_business_email_count"})
    else:
        _require(type(counts.get("sequence_step_count")) is int and counts["sequence_step_count"] == count * STEPS_PER_CONTACT)
    contacts = snapshot.get("selected_review_contacts")
    sequences = snapshot.get("draft_sequences")
    _require(isinstance(contacts, list) and len(contacts) == count)
    if not facts_only:
        _require(isinstance(sequences, list) and len(sequences) == counts["sequence_step_count"])
    by_id = {}
    for contact in contacts:
        contact = _record(contact)
        identity = contact.get("stable_prospect_id")
        fields = _fields(contact, CONTACT_FIELDS - {DRAFT_CONTACT_FIELD} if facts_only else CONTACT_FIELDS)
        if facts_only:
            _require(not any(_prospect_draft_field(key) for key in fields))
        _require(isinstance(identity, str) and 0 < len(identity) <= 200 and identity not in by_id
                 and fields["Stable prospect ID"] == identity
                 and fields["Membership change applied"] == "No" and fields["Proposed action"] in {"KEEP", "REVIEW"}
                 and fields["Enrichment status"].startswith("completed") and bool(fields["Enrichment source"]))
        by_id[identity] = fields
    _require(sum(bool(fields["Business email"].strip()) for fields in by_id.values()) == email_count)
    seen = set()
    for sequence in sequences or []:
        sequence = _record(sequence)
        identity, step = sequence.get("stable_prospect_id"), sequence.get("step_number")
        fields = _fields(sequence, SEQUENCE_FIELDS)
        _require(identity in by_id and type(step) is int and 1 <= step <= STEPS_PER_CONTACT
                 and (identity, step) not in seen and fields["Prospect ID"] == identity and fields["Step"] == str(step))
        seen.add((identity, step))
        _require(fields["Status"] == "DRAFT" and fields["Outreach enrolled"] == fields["Membership change applied"] == "No"
                 and fields["Body"] == fields["Draft email for you to send"] and bool(fields["Subject"]))
        contact = by_id[identity]
        _require(all(fields[key] == contact[key] for key in (
            "Name", "Why selected", "LinkedIn profile", "Business email", "Agency", "Review batch/version",
        )))
        _require(step != 1 or fields["Body"] == contact["Draft email for you to send"])
    if not facts_only:
        _require(Counter(identity for identity, _ in seen) == {identity: STEPS_PER_CONTACT for identity in by_id})
    attachments = snapshot.get("attachments")
    _require(isinstance(attachments, list) and len(attachments) == 3)
    names = []
    for descriptor in attachments:
        descriptor = _record(descriptor)
        _attachment_path(task, snapshot, descriptor)
        names.append(descriptor["name"])
    _require(len(set(names)) == 3)
    roles = _record(snapshot.get("csv_attachment_roles"))
    expected_roles = {"workbook", "selected_review_contacts"} if facts_only else {"workbook", "selected_review_contacts", "draft_sequences"}
    _require(set(roles) == expected_roles and len(set(roles.values())) == len(expected_roles)
             and set(roles.values()).issubset(names)
             and (facts_only or set(roles.values()) == set(names)))
    _require(roles["workbook"].endswith(".xlsx")
             and all(roles[key].endswith(".csv") for key in expected_roles - {"workbook"}))
    for descriptor in attachments:
        expected_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if descriptor["name"] == roles["workbook"] else "text/csv"
        _require(descriptor["mime"] == expected_mime)
    headers = _record(snapshot.get("csv_headers"))
    _require(set(headers) == expected_roles - {"workbook"})
    first_five = ["Name", "Why selected", "LinkedIn profile", "Business email", "Draft email for you to send"]
    for value in headers.values():
        _require(isinstance(value, list) and 5 <= len(value) <= 100
                 and all(isinstance(key, str) for key in value)
                 and len(value) == len(set(value)) and value[:5] == first_five)
    return snapshot


def validate_feedback_snapshot(task):
    try:
        return _validate_feedback_snapshot(task)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise TaskApiError("The saved feedback snapshot scope or integrity is invalid; no fallback was substituted.") from None


def resource_uri(task, descriptor):
    return (f"producerspark-feedback-attachment://{task['id']}/{descriptor['source_delivery_job_id']}/"
            f"{descriptor['name'].encode('utf-8').hex()}/{descriptor['sha256']}")


def feedback_projection(task, *, contact_page=1, details=True):
    snapshot = validate_feedback_snapshot(task)
    if snapshot is None:
        return None
    if snapshot.get("status") == "unavailable":
        return deepcopy(snapshot)
    _require(positive_id(contact_page))
    result = deepcopy({key: value for key, value in snapshot.items()
                       if key not in {"selected_review_contacts", "draft_sequences"}})
    for descriptor in result["attachments"]:
        descriptor["mcp_resource_uri"] = resource_uri(task, descriptor)
    result["row_projection_basis"] = (
        "Saved feedback-review contacts and exact draft copy. Separate from native qualified_contacts; "
        "no fresh verification, complete staff roster or fulfillment of the original request is asserted."
    )
    facts_only = "draft_sequences" not in snapshot
    if facts_only:
        result["row_projection_basis"] = (
            "Saved feedback-review factual fields under the current no-draft policy. "
            "Historical attachment descriptors still identify original sent bytes, not a new customer deliverable. "
            "Native qualification, current controls and original fulfillment remain separate."
        )
        # These are the original CSV's header strings, including its historical
        # draft column; do not present them as this facts-only answer's columns.
        result.pop("csv_headers", None)
    if details:
        contacts = snapshot["selected_review_contacts"][(contact_page - 1) * 5:contact_page * 5]
        identities = {contact["stable_prospect_id"] for contact in contacts}
        sequences = [step for step in snapshot.get("draft_sequences", []) if step["stable_prospect_id"] in identities]
        result["selected_review_contacts"] = deepcopy(contacts)
        # Detailed contact answers expose the exact intro in selected contact
        # fields. Monthly bodies remain in the authenticated immutable CSV;
        # returning every body here would obscure the requested compact answer.
        if not facts_only:
            result["draft_sequences"] = [{
                "stable_prospect_id": step["stable_prospect_id"],
                "step_number": step["step_number"],
                "fields": {key: step["fields"][key] for key in (
                    "Step", "Subject", "Status", "Sequence version", "Source sequence version",
                    "Membership change applied", "Outreach enrolled",
                )},
                "exact_body_retrieval": "Read the recorded draft_sequences CSV feedback attachment; bodies are not rewritten here.",
                "body_omitted_from_compact_projection": True,
            } for step in sequences]
        result["pagination"] = {
            "contact_page": contact_page, "contact_page_size": 5,
            "selected_review_contacts_total": snapshot["counts"]["selected_review_contact_count"],
            "selected_review_contacts_returned": len(contacts),
        }
        if not facts_only:
            result["pagination"].update(
                draft_sequence_steps_total=snapshot["counts"]["sequence_step_count"],
                draft_sequence_steps_returned=len(sequences),
                draft_sequence_scope="Metadata for all six saved steps per contact on this page; steps are not people. Exact monthly bodies remain in the authenticated draft sequence CSV.",
            )
    else:
        result.pop("csv_headers", None)
    return result


def brief_feedback_projection(snapshot):
    if not isinstance(snapshot, dict):
        return snapshot
    return deepcopy({key: value for key, value in snapshot.items()
                     if key not in {"selected_review_contacts", "draft_sequences", "pagination", "csv_headers"}})


async def read_feedback_attachment_resource(api, uri):
    """Retrieve original server-owned bytes, scoped to the exact goal; no local-file fallback."""
    matched = RESOURCE.fullmatch(str(uri))
    _require(matched is not None)
    goal_text, job_text, name_hex, digest = matched.groups()
    try:
        name = bytes.fromhex(name_hex).decode("utf-8")
    except (ValueError, UnicodeError):
        raise TaskApiError("The saved feedback attachment URI is invalid.") from None
    task = await api.request("GET", f"/api/v1/agent-tasks/{int(goal_text)}", params={"view": "answer"})
    _require(isinstance(task, dict) and task.get("id") == int(goal_text))
    snapshot = validate_feedback_snapshot(task)
    _require(snapshot is not None and snapshot.get("status") != "unavailable")
    descriptors = [row for row in snapshot["attachments"] if row["name"] == name
                   and row["sha256"] == digest and row["source_delivery_job_id"] == int(job_text)]
    _require(len(descriptors) == 1)
    descriptor = descriptors[0]
    path, params = _attachment_path(task, snapshot, descriptor)
    content = await api.request("GET", path, params=params, binary=True)
    _require(isinstance(content, bytes) and len(content) == descriptor["bytes"]
             and hashlib.sha256(content).hexdigest() == descriptor["sha256"])
    return content, descriptor["mime"]

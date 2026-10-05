"""Synthetic backend feedback DTO and deterministic exact attachment bytes.

The cardinalities match the reviewed contract; all people, addresses, sources,
copy and provider receipt hashes are fictional. No delivery files or backend
modules are imported, and no filesystem/network access is needed.
"""

from copy import deepcopy
import csv
import hashlib
import io
from urllib.parse import urlencode
from xml.sax.saxutils import escape
import zipfile

FIRST_FIVE = ["Name", "Why selected", "LinkedIn profile", "Business email", "Draft email for you to send"]
REVIEW_HEADERS = FIRST_FIVE + [
    "Stable prospect ID", "Proposed action", "Membership change applied", "Agency", "Email verification",
    "Enrichment status", "Enrichment source", "Enriched on (UTC)", "Evidence sources",
    "Uncertainty / review notes", "Qualification note", "Review batch/version",
]
SEQUENCE_HEADERS = FIRST_FIVE + [
    "Prospect ID", "Step", "Body", "Subject", "Status", "Outreach enrolled", "Membership change applied",
    "Agency", "Review batch/version", "Sequence version", "Source sequence version",
]
WORKBOOK_NAME = "fixture-feedback-review.xlsx"
REVIEW_NAME = "fixture-feedback-review.csv"
SEQUENCE_NAME = "fixture-feedback-draft-sequences.csv"


def review_rows():
    rows = []
    for index in range(1, 40):
        name = f"Synthetic reviewer {index:02d}"
        intro = f"Hi {name},\n\nI’m sharing our saved introduction. It’s authored copy—keep its punctuation.\n\nThanks,\nFixture team"
        has_email = index <= 35
        verification = "Saved provider verified; not a fresh deliverability check" if index <= 32 else (
            "Published employer address; unverified" if has_email else "Unavailable after completed bounded enrichment")
        rows.append(dict(zip(REVIEW_HEADERS, [
            name, "Recorded commercial insurance responsibility; selection needs customer review.",
            f"https://profiles.example.invalid/person-{index}" if index <= 32 else "",
            f"person{index}@agency.example.invalid" if has_email else "", intro,
            f"synthetic-review-{index:02d}", "KEEP" if index <= 4 else "REVIEW", "No", f"Synthetic agency {(index - 1) % 10 + 1}",
            verification, "completed_saved_result_reused" if index <= 26 else "completed_bounded_enrichment",
            "Synthetic saved provider receipt" if index <= 32 else "Synthetic employer page after completed provider lookup",
            "" if index == 1 else "2026-07-10T12:00:00+00:00" if index <= 26 else "2026-10-05T06:00:00+00:00",
            f"https://agency.example.invalid/team/person-{index} | observed 2026-10-05; hash " + "a" * 64,
            "Current title needs review; a missing verification date remains unknown." if index <= 35 else "No selected address recorded after completed bounded lookup; do not invent one.",
            "Selected for feedback; native qualification remains separate.", "synthetic-feedback-v1",
        ])))
    return rows


def sequence_rows():
    rows = []
    for contact in review_rows():
        for step in range(1, 7):
            body = contact["Draft email for you to send"] if step == 1 else (
                f"Hi {contact['Name']},\n\nThis is the exact saved monthly follow-up {step}.\nI’d welcome your thoughts—no rewrite.\n\nThanks,\nFixture team")
            fields = {key: contact[key] for key in FIRST_FIVE}
            fields["Draft email for you to send"] = body
            fields.update({
                "Prospect ID": contact["Stable prospect ID"], "Step": str(step), "Body": body,
                "Subject": "A saved introduction" if step == 1 else f"Saved monthly follow-up {step}",
                "Status": "DRAFT", "Outreach enrolled": "No", "Membership change applied": "No",
                "Agency": contact["Agency"], "Review batch/version": "synthetic-feedback-v1",
                "Sequence version": "synthetic-sequence-v1", "Source sequence version": "saved-synthetic-copy-v1",
            })
            rows.append(fields)
    return rows


def _csv_bytes(headers, rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _workbook_bytes():
    """A deterministic minimal valid XLSX containing the review's first five fields."""
    rows = [FIRST_FIVE] + [[row[key] for key in FIRST_FIVE] for row in review_rows()]
    sheet_rows = []
    for row_number, row in enumerate(rows, 1):
        cells = "".join(f'<c r="{chr(65 + index)}{row_number}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>' for index, value in enumerate(row))
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Contacts" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(sheet_rows) + '</sheetData></worksheet>',
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            workbook.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()


def attachment_bytes():
    return {
        WORKBOOK_NAME: _workbook_bytes(),
        REVIEW_NAME: _csv_bytes(REVIEW_HEADERS, review_rows()),
        SEQUENCE_NAME: _csv_bytes(SEQUENCE_HEADERS, sequence_rows()),
    }


def snapshot_fixture(goal_id=52):
    raw = attachment_bytes()
    attachments = []
    for name, content in raw.items():
        digest = hashlib.sha256(content).hexdigest()
        query = urlencode({"name": name, "sha256": digest})
        suffix = f"{goal_id}/feedback-snapshots/251/attachments?{query}"
        attachments.append({
            "name": name, "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if name == WORKBOOK_NAME else "text/csv",
            "bytes": len(content), "sha256": digest, "source_delivery_job_id": 251,
            "source_goal_id": 52, "validation_contract_revision": 1,
            "delivered_contract_revision": None, "delivered_revision_status": "not_recorded",
            "attachment_retrieval": {"method": "GET", "authentication_required": True,
                "api_url": "/api/v1/agent-tasks/" + suffix, "browser_url": "/api/agent-tasks/" + suffix,
                "original_sent_bytes": True, "new_research_or_delivery": False},
        })
    return {
        "version": "agency.feedback_review_snapshot.v2", "requested_goal_id": goal_id,
        "answer_lineage_scope": {"workflow": "agency_research", "config_id": 8, "client_id": 79,
            "organization_id": 67, "campaign_id": 45, "original_goal_id": 24},
        "current_answer_goal_id": 52,
        "source": {"config_id": 8, "client_id": 79, "organization_id": 67, "campaign_id": 45,
            "source_job_id": 219, "delivery_job_id": 251, "original_goal_id": 24, "current_original_contract_revision": 2,
            "pilot_goal_id": 52, "current_pilot_contract_revision": 1, "manifest_sha256": "a" * 64,
            "original_scope_sha256": "b" * 64, "pilot_scope_sha256": "c" * 64,
            "delivered_original_contract_revision": None, "delivered_pilot_contract_revision": None,
            "delivered_revision_status": "not_recorded", "validation_revision_basis": "reviewed_post_delivery_current_revisions",
            "validation_receipt_sha256": "7bc84eb88107e9c4255ea92f8a2f6454bfeb83495799c7a08f32a0c5dc82aa60",
            "publication_validation": {"original_contract_revision": 2, "pilot_contract_revision": 1,
                "basis": "reviewed_post_delivery_current_revisions", "receipt_sha256": "7bc84eb88107e9c4255ea92f8a2f6454bfeb83495799c7a08f32a0c5dc82aa60"}},
        "lineage_basis": "Synthetic server-validated saved delivery and grant lineage; feedback-only pointer.",
        "review_scope": {"requested_original_agency_count": 110, "approved_batch_agency_count": 10,
            "remaining_original_agency_count": 100, "partial_roster": True, "roster_complete": False,
            "original_request_fulfilled": False, "feedback_state": "awaiting_client_feedback", "remaining_work_held": True},
        "saved_delivery": {"status": "provider_accepted_saved_and_verified", "verification_recorded_at": "2026-10-05T07:00:00+00:00",
            "provider_receipt_sha256": "d" * 64, "fresh_provider_readback": False, "inbox_receipt_proven": False},
        "counting_basis": "Synthetic selected review contacts; not native qualified contacts or complete staff coverage. Six saved draft steps per contact.",
        "native_research_counts_replaced": False, "recorded_verification_is_fresh": False,
        "retrieval_effects": {"enrichment_started": False, "customer_email_sent": False, "prospect_outreach": False,
            "membership_changed": False, "holds_cleared": False},
        "counts": {"selected_review_contact_count": 39, "recorded_business_email_count": 35, "sequence_step_count": 234},
        "selected_review_contacts": [{"stable_prospect_id": row["Stable prospect ID"], "fields": deepcopy(row)} for row in review_rows()],
        "draft_sequences": [{"stable_prospect_id": row["Prospect ID"], "step_number": int(row["Step"]), "fields": deepcopy(row)} for row in sequence_rows()],
        "csv_headers": {"selected_review_contacts": list(REVIEW_HEADERS), "draft_sequences": list(SEQUENCE_HEADERS)},
        "csv_attachment_roles": {"workbook": WORKBOOK_NAME, "selected_review_contacts": REVIEW_NAME, "draft_sequences": SEQUENCE_NAME},
        "attachments": attachments,
    }

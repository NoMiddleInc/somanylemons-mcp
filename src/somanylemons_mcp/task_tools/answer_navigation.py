"""Display current answer navigation without deriving state or authorization."""

from copy import deepcopy


def positive_id(value):
    return type(value) is int and value > 0


def conference_artifacts(row):
    artifacts = row.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    return [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and isinstance(artifact.get("schema"), dict)
        and artifact["schema"].get("version") == "conference.speakers.v1"
    ]


def list_task_navigation(response):
    result = deepcopy(response)
    if not isinstance(result, dict) or not isinstance(result.get("tasks"), list):
        return result
    for row in result["tasks"]:
        if not isinstance(row, dict) or not positive_id(row.get("id")):
            continue
        artifacts = conference_artifacts(row)
        current = row.get("current_answer_goal_id")
        if not artifacts and not positive_id(current):
            continue
        listed = row["id"]
        row["listed_goal_id"] = listed
        row["list_is_navigation_only"] = True
        progress = row.pop("progress", None)
        row["listed_goal_progress"] = {
            **(progress if isinstance(progress, dict) else {}),
            "unit": "worker_steps",
            "scope_goal_id": listed,
        }
        for field in ("blocker", "next_action", "next_run_at"):
            row["listed_goal_" + field] = row.pop(field, None)
        if positive_id(current):
            row["current_answer_goal_id_basis"] = (
                "Backend-proven same-scope conference source lineage; includes unfinished continuations without artifacts."
            )
            row["canonical_answer_action"] = {
                "tool": "get_research_answer",
                "goal_id": current,
            }
            row["outcome_summary"] = (
                f"Read get_research_answer(goal_id={current}) for current roster identity counts and their counting basis, recorded email clocks, "
                "research gaps and all final-delivery requirements. This row is navigation metadata; listed progress "
                "counts worker steps for the original goal. Artifact row_count describes rows in that exact saved file; "
                "canonical raw appearance totals can differ from combined customer-presentation rows. Never transfer one file's totals to another. A review blocker or worker-stage completion alone never establishes final readiness."
            )
        else:
            row["current_answer_goal_id"] = None
            row["canonical_answer_action"] = None
            origins = {
                artifact.get("origin_goal_id")
                for artifact in artifacts
                if positive_id(artifact.get("origin_goal_id"))
            }
            lineage = row.get("outcome_goal_ids")
            saved = (
                next((pk for pk in lineage if positive_id(pk) and pk in origins), None)
                if isinstance(lineage, list)
                else None
            )
            row["saved_artifact_answer_goal_id_hint"] = saved
            row["saved_artifact_hint_is_current_answer"] = False
            row["outcome_summary"] = (
                "Current answer lineage is not recorded by this response. Saved artifact metadata may identify a historical "
                "snapshot; it never proves current research state or readiness. Read the requested task before drawing conclusions. "
                "Listed progress counts original-goal worker steps; artifact row_count describes that exact saved file, which can differ from canonical raw appearance or roster-key totals."
            )
    return result


def bounded_examples_and_artifacts(answer):
    """Bound optional examples; preserve exact canonical gates, counts and clocks."""
    result = deepcopy(answer)
    if not isinstance(result, dict) or not isinstance(
        result.get("conference_answer"), dict
    ):
        return result
    conference = result["conference_answer"]
    summary = conference.get("enrichment_summary")
    if isinstance(summary, dict):
        examples = summary.get("candidate_examples")
        if isinstance(examples, list):
            summary["candidate_examples_source_preview_was_complete"] = summary.get(
                "candidate_examples_are_complete"
            )
            summary["candidate_examples_returned"] = min(len(examples), 2)
            summary["candidate_examples_saved_preview_total"] = len(examples)
            summary["candidate_examples_preview_omitted"] = max(0, len(examples) - 2)
            summary["candidate_examples"] = examples[:2]
            if len(examples) > 2:
                summary["candidate_examples_are_complete"] = False
            summary["candidate_examples_preview_scope"] = (
                "At most two saved examples; candidate_people is the authoritative saved identity count, with identity_count_basis when recorded. Saved preview totals are not whole-cohort counts."
            )
        groups = summary.get("status_examples_by_person")
        if isinstance(groups, dict):
            summary["status_examples_preview_counts"] = {
                key: {
                    "returned": min(len(value), 2),
                    "saved_preview_total": len(value),
                    "omitted_from_saved_preview": max(0, len(value) - 2),
                }
                for key, value in groups.items()
                if isinstance(value, list)
            }
            summary["status_examples_by_person"] = {
                key: value[:2] if isinstance(value, list) else value
                for key, value in groups.items()
            }
    artifacts = result.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, list) else []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        schema = artifact.get("schema")
        if isinstance(schema, dict) and isinstance(schema.get("fields"), list):
            fields = schema["fields"]
            artifact["schema"] = {
                key: value for key, value in schema.items() if key != "fields"
            }
            artifact["schema_field_count"] = len(fields)
            artifact["schema_field_names_preview"] = fields[:5]
            artifact["schema_field_names_preview_omitted"] = max(0, len(fields) - 5)
            artifact["schema_details_available"] = (
                "Exact headers remain in the saved artifact. Identity, hash, row counts and dates are unchanged."
            )
    recorded = next(
        (
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict)
            and positive_id(artifact.get("id"))
            and positive_id(artifact.get("origin_goal_id"))
            and isinstance(artifact.get("hash"), str)
            and len(artifact["hash"]) == 64
        ),
        None,
    )
    conference["saved_review_workbook"] = {
        "metadata_recorded": recorded is not None,
        "artifact_retrieval": (
            {
                "tool": "get_task_artifact",
                "goal_id": recorded["origin_goal_id"],
                "artifact_id": recorded["id"],
            }
            if recorded
            else None
        ),
        "content_hash": recorded["hash"] if recorded else None,
        "basis": "Saved scoped artifact metadata. An authorized saved file may be inspected or linked here while final send gates remain held; a do-not-send instruction does not prohibit already-requested review/download inspection. Retrieve the artifact to verify available bytes; this read asserts no validation or final delivery clearance and requires no additional permission for the requested inspection.",
        "is_live_working_document": False,
        "reading_authorizes_send": False,
    }
    return result

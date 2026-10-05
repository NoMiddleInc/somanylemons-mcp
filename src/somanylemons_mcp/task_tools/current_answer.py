"""Bounded saved-answer navigation; authority and source lineage stay in the backend."""

from copy import deepcopy
from dataclasses import dataclass
import re
import time

from .client import TaskApiError

MAX_POINTER_HOPS = 3


def positive_id(value):
    return type(value) is int and value > 0


def _identity(task):
    customer = task.get("customer")
    conference = task.get("conference_answer")
    event = conference.get("event") if isinstance(conference, dict) else None
    if (
        not isinstance(customer, dict)
        or not positive_id(customer.get("id"))
        or not isinstance(event, dict)
        or not isinstance(event.get("name"), str)
        or not event["name"].strip()
        or not positive_id(event.get("year"))
    ):
        raise TaskApiError("Current answer customer or conference identity is not recorded safely.")
    return customer["id"], event["name"], event["year"]


@dataclass(frozen=True)
class ResolvedAnswer:
    requested: dict
    current: dict
    goal_ids: tuple[int, ...]
    historical_snapshot: bool


async def resolve_current_answer(api, goal_id, *, historical_snapshot=False, timeout_seconds=30.0):
    """Follow only backend-proven pointers; never return old facts after a failed follow."""
    deadline = time.monotonic() + timeout_seconds
    requested = None
    current_id = goal_id
    visited = []
    identity = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TaskApiError("Current saved answer resolution timed out; no historical result substituted.")
        task = await api.request(
            "GET", f"/api/v1/agent-tasks/{current_id}", params={"view": "answer"},
            timeout_seconds=min(30.0, remaining),
        )
        if not isinstance(task, dict) or task.get("id") != current_id or not positive_id(task.get("id")):
            raise TaskApiError("The saved answer does not match the requested goal.")
        visited.append(current_id)
        if requested is None:
            requested = task
        elif _identity(task) != identity:
            raise TaskApiError("Current answer customer or conference identity changed; no historical result substituted.")
        if historical_snapshot:
            return ResolvedAnswer(requested, task, tuple(visited), True)
        pointer = task.get("current_answer_goal_id")
        if pointer is None:
            if len(visited) > 1:
                raise TaskApiError("The followed answer has no authoritative current pointer.")
            return ResolvedAnswer(requested, task, tuple(visited), False)
        if not positive_id(pointer):
            raise TaskApiError("The authoritative current answer pointer is invalid.")
        if pointer == current_id:
            return ResolvedAnswer(requested, task, tuple(visited), False)
        if identity is None:
            identity = _identity(requested)
        if pointer in visited:
            raise TaskApiError("The authoritative current answer pointers contain a cycle.")
        if len(visited) > MAX_POINTER_HOPS:
            raise TaskApiError("The authoritative current answer exceeds the bounded pointer limit.")
        current_id = pointer


def resolution_metadata(answer, resolved):
    """Keep returned controls together and label original controls/progress separately."""
    result = dict(answer)
    original = resolved.requested
    result["answer_resolution"] = {
        "requested_goal_id": original["id"],
        "returned_goal_id": resolved.current["id"],
        "historical_snapshot": resolved.historical_snapshot,
        "followed_current_answer": len(resolved.goal_ids) > 1,
        "pointer_hops": len(resolved.goal_ids) - 1,
        "validation_basis": (
            "Backend-authoritative same-scope current_answer_goal_id; followed responses must match the authenticated customer and conference name/year. Exact config/organization/campaign scope is enforced by the backend, not inferred from artifacts."
            if len(resolved.goal_ids) > 1 else
            "Exact requested answer view; historical_snapshot bypasses current-answer navigation only when explicitly requested."
        ),
    }
    if len(resolved.goal_ids) > 1:
        result["requested_goal_controls"] = {
            "goal_id": original["id"],
            **{key: deepcopy(original[key]) for key in ("version", "allowed_actions") if key in original},
        }
        if isinstance(original.get("progress"), dict):
            result["requested_goal_progress"] = {
                **deepcopy(original["progress"]), "unit": "worker_steps", "scope_goal_id": original["id"],
            }
    return result


def history_metadata(history, goal_id):
    """Event metadata only; bodies, receipt payloads and checkpoints are never copied."""
    if not isinstance(history, dict) or not positive_id(history.get("id")) or history["id"] != goal_id:
        return None
    events = history.get("events")
    if not isinstance(events, list):
        return None
    return {
        "goal_id": goal_id,
        "events": [
            {key: event[key] for key in ("id", "event_type", "event", "created_at")
             if (key == "id" and positive_id(event.get(key)))
             or (key != "id" and isinstance(event.get(key), str) and len(event[key]) <= 100)}
            for event in events[-20:] if isinstance(event, dict)
        ],
        "events_total": len(events),
        "version": history.get("version") if positive_id(history.get("version")) else None,
        "scope": "Bounded event identifiers/types/dates for this returned goal; bodies and provider receipts are not exposed. Answer state, controls, requirements and scheduling come from the answer view.",
    }


def recorded_delivery_history(history):
    """Bound saved sent-turn metadata; a provider-recorded status is not inbox proof."""
    conversation = history.get("conversation") if isinstance(history, dict) else None
    if not isinstance(conversation, list):
        return {
            "available": False,
            "sent_turns": None,
            "basis": "Saved outgoing delivery history is not exposed here; prior delivery remains unknown from this read.",
        }
    sent = [turn for turn in conversation if isinstance(turn, dict)
            and turn.get("role") in ("team", "agent") and turn.get("status") == "sent"
            and isinstance(turn.get("id"), str) and re.fullmatch(r"job:[1-9][0-9]{0,19}:reply", turn["id"])]
    selected = []
    for turn in sent[-10:]:
        item = {"id": turn["id"], "role": turn["role"], "status": "sent"}
        if isinstance(turn.get("subject"), str):
            item["subject"] = turn["subject"][:300]
            if len(turn["subject"]) > 300:
                item["subject_truncated"] = True
        if isinstance(turn.get("created_at"), str) and len(turn["created_at"]) <= 100:
            item["created_at"] = turn["created_at"]
        selected.append(item)
    total = history.get("conversation_total")
    return {
        "available": True,
        "grouped_outcome_goal_id": history.get("id") if positive_id(history.get("id")) else None,
        "sent_turns": selected,
        "sent_turns_total": len(sent),
        "sent_turns_returned": len(selected),
        "sent_turns_omitted": len(sent) - len(selected),
        "sent_turns_total_basis": "Completed reply turns in the returned grouped outcome conversation; excludes progress/blocker-notice metadata and does not count every email ever sent to this customer. Zero here does not establish that no prior delivery occurred.",
        "source_conversation_complete": history.get("conversation_truncated") is False and type(total) is int and total == len(conversation),
        "basis": "Backend-recorded grouped outcome conversation. Sent status records a completed outgoing job with a provider receipt; it does not establish inbox delivery, exact attachment bytes, recipient identity or final research fulfillment. created_at is the saved turn creation time, not a proven provider-acceptance time. No receipt payload or body is returned.",
    }

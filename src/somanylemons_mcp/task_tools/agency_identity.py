"""Typed backend-owned agency lineage; no name, artifact or local-file inference."""

from .client import TaskApiError


SCOPE_KEYS = frozenset({
    "workflow", "config_id", "client_id", "organization_id", "campaign_id", "original_goal_id",
})


def positive_id(value):
    return type(value) is int and value > 0


def validated_agency_scope(scope):
    if (not isinstance(scope, dict) or set(scope) != SCOPE_KEYS
            or scope.get("workflow") != "agency_research"
            or any(not positive_id(scope.get(key)) for key in SCOPE_KEYS - {"workflow"})):
        raise TaskApiError("The backend agency answer lineage scope is missing or invalid.")
    return dict(scope)


def agency_lineage_identity(task):
    scope = validated_agency_scope(task.get("answer_lineage_scope"))
    customer = task.get("customer")
    if (not isinstance(customer, dict) or not positive_id(customer.get("id"))
            or customer["id"] != scope["client_id"]
            or not isinstance(task.get("research_answer"), dict)
            or isinstance(task.get("conference_answer"), dict)):
        raise TaskApiError("The saved agency answer does not match its backend lineage scope.")
    return tuple(scope[key] for key in (
        "workflow", "config_id", "client_id", "organization_id", "campaign_id", "original_goal_id",
    ))

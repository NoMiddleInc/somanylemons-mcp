"""Per-request task tools; no shared customer credentials or research execution."""
from .task_tools.client import TaskApiClient, TaskApiConfig, TaskApiError
from .task_tools.server import create_server

TASK_TOOL_NAMES = frozenset({"list_tasks", "get_task", "get_research_answer", "wait_for_task", "create_research_request", "create_conference_research_request", "supply_agency_names", "pause_task", "resume_task", "cancel_task", "retry_task", "list_schedules", "create_research_schedule", "set_schedule_enabled", "get_task_artifact", "read_task_spreadsheet", "get_my_icp", "get_prospect_list", "list_golden_lists", "read_golden_list"})

SCHEMA_SERVER = create_server(TaskApiClient(TaskApiConfig("https://schema.invalid", "schema-only")))

async def task_schemas():
    return await SCHEMA_SERVER.list_tools()

async def invoke_task(name, arguments, *, api_url, api_key, transport=None):
    if not api_key:
        raise TaskApiError("An account-scoped API key is required.")
    api = TaskApiClient(TaskApiConfig(api_url, api_key, auth_scheme="X-API-Key"), transport=transport)
    return await create_server(api).call_tool(name, arguments)

async def read_task_artifact(uri, *, api_url, api_key, transport=None):
    import re
    matched = re.fullmatch(r"producerspark-task-artifact://([1-9][0-9]*)/([1-9][0-9]*)", str(uri))
    if not matched:
        raise TaskApiError("Artifact URI must identify one task and artifact with positive IDs.")
    api = TaskApiClient(TaskApiConfig(api_url, api_key, auth_scheme="X-API-Key"), transport=transport)
    goal, artifact = matched.groups()
    return await api.request("GET", f"/api/v1/agent-tasks/{goal}/artifacts/{artifact}", binary=True)

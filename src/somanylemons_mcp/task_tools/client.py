"""HTTP-only task client. All authority and lifecycle decisions stay in Django."""

import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx


class TaskApiError(RuntimeError):
    """A safe error suitable for an MCP tool response."""


@dataclass(frozen=True)
class TaskApiConfig:
    origin: str
    token: str = field(repr=False)
    auth_scheme: str = "Bearer"

    def __post_init__(self):
        try:
            parsed = urlsplit(self.origin)
        except ValueError:
            raise TaskApiError("PRODUCERSPARK_API_URL is not a valid origin.") from None
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or not (parsed.scheme == "https" or (parsed.scheme == "http" and local))
        ):
            raise TaskApiError(
                "PRODUCERSPARK_API_URL must be an HTTPS origin; HTTP is allowed only on localhost for development."
            )
        try:
            parsed.port
        except ValueError as exc:
            raise TaskApiError("PRODUCERSPARK_API_URL has an invalid port.") from exc
        if not self.token or any(char.isspace() for char in self.token):
            raise TaskApiError(
                "Set a valid PRODUCERSPARK_API_TOKEN in the MCP process environment."
            )
        if self.auth_scheme not in {"Bearer", "X-API-Key"}:
            raise TaskApiError("PRODUCERSPARK_AUTH_SCHEME must be Bearer or X-API-Key.")

    @classmethod
    def from_environment(cls):
        return cls(
            origin=os.environ.get("PRODUCERSPARK_API_URL", "").strip().rstrip("/"),
            token=os.environ.get("PRODUCERSPARK_API_TOKEN", "").strip(),
            auth_scheme=os.environ.get("PRODUCERSPARK_AUTH_SCHEME", "Bearer"),
        )


class TaskApiClient:
    def __init__(self, config: TaskApiConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
        binary: bool = False,
        timeout_seconds: float = 30.0,
    ) -> Any:
        if (
            not (path == "/api/v1/agent-tasks" or path.startswith("/api/v1/agent-tasks/"))
            or ".." in path
            or "?" in path
            or "#" in path
        ):
            raise TaskApiError("Only the authenticated Tasks API is available through this server.")
        try:
            async with httpx.AsyncClient(
                base_url=self.config.origin.rstrip("/"),
                headers={
                    **(
                        {"X-API-Key": self.config.token}
                        if self.config.auth_scheme == "X-API-Key"
                        else {"Authorization": f"Bearer {self.config.token}"}
                    ),
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds)),
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.request(method, path, params=params, json=body)
        except httpx.RequestError:
            # No implicit mutation retry: the caller must reuse its idempotency key.
            raise TaskApiError(
                "Tasks API could not be reached. For a write, retry with the same idempotency_key; do not create another request."
            ) from None
        if not 200 <= response.status_code < 300:
            try:
                data = response.json()
                detail = (
                    data.get("detail") or data.get("message") or data
                    if isinstance(data, dict)
                    else data
                )
            except ValueError:
                detail = ""
            if isinstance(detail, (dict, list)):
                detail = json.dumps(detail, ensure_ascii=False)
            elif not isinstance(detail, str):
                detail = ""
            detail = detail.replace(self.config.token, "[redacted]")[:1000]
            guidance = (
                " Refresh the task and use its current version before retrying."
                if response.status_code == 409
                else ""
            )
            raise TaskApiError(
                f"Tasks API returned HTTP {response.status_code}: {detail or 'Request was not accepted.'}{guidance}"
            )
        if binary:
            if len(response.content) > 10 * 1024 * 1024:
                raise TaskApiError(
                    "This artifact exceeds the 10 MiB MCP resource limit. Download it from the Tasks dashboard."
                )
            return response.content
        try:
            data = response.json()
        except ValueError:
            raise TaskApiError(
                "Tasks API returned an invalid response. Reconcile writes with the same idempotency_key."
            ) from None
        payload = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(payload, dict):
            url = payload.get("download_url", "")
            prefix = "https://api.producerspark.com/api/v1/agent-tasks/download/"
            if isinstance(url, str) and url.startswith(prefix):
                payload["download_url"] = "https://producerspark.com/api/v1/agent-tasks/download/" + url[len(prefix):]
        return payload

import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class RemoteMcpSmokeClient:
    def __init__(self, server_url: str, api_key: str):
        self.server_url = server_url
        self.api_key = api_key
        self._transport_cm = None
        self._session_cm = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "RemoteMcpSmokeClient":
        self._transport_cm = streamablehttp_client(
            self.server_url,
            headers={"X-API-Key": self.api_key},
        )
        streams = await self._transport_cm.__aenter__()
        read_stream, write_stream = streams[0], streams[1]
        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)
        if self._transport_cm is not None:
            await self._transport_cm.__aexit__(exc_type, exc, tb)

    async def list_tool_names(self) -> list[str]:
        assert self._session is not None
        result = await self._session.list_tools()
        return [tool.name for tool in result.tools]

    async def call_tool_json(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        assert self._session is not None
        result = await self._session.call_tool(name, arguments or {})
        if result.isError:
            raise AssertionError(f"Tool {name} returned an error: {result.content}")
        if not result.content:
            return None

        first_content = result.content[0]
        text = getattr(first_content, "text", "")
        return json.loads(text) if text else None

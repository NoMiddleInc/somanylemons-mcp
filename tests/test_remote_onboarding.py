import os
import unittest

from tests.remote_client import RemoteMcpSmokeClient


class RemoteOnboardingTest(unittest.IsolatedAsyncioTestCase):
    def build_client(self) -> RemoteMcpSmokeClient:
        api_key = os.environ.get("SML_REMOTE_TEST_API_KEY")
        if not api_key:
            self.skipTest("Set SML_REMOTE_TEST_API_KEY to run hosted SoManyLemons MCP smoke tests.")
        return RemoteMcpSmokeClient(
            server_url=os.environ.get("SML_REMOTE_TEST_URL", "https://mcp.somanylemons.com/mcp"),
            api_key=api_key,
        )

    async def test_remote_server_exposes_expected_tools_for_new_user(self) -> None:
        async with self.build_client() as client:
            tools = await client.list_tool_names()

        self.assertIn("list_plans", tools)
        self.assertIn("get_usage", tools)
        self.assertIn("generate_content", tools)

    async def test_remote_server_supports_safe_first_run_calls(self) -> None:
        async with self.build_client() as client:
            plans = await client.call_tool_json("list_plans")
            usage = await client.call_tool_json("get_usage")

        self.assertTrue(plans, "Expected remote list_plans response to include plan data")
        self.assertIn("renders", str(plans).lower())
        self.assertTrue(
            isinstance(usage, dict),
            f"Expected get_usage to return a JSON object, got: {type(usage).__name__}",
        )

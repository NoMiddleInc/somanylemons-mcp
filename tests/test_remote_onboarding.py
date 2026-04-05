import os
import unittest

from tests.remote_client import RemoteMcpSmokeClient


class RemoteOnboardingTest(unittest.IsolatedAsyncioTestCase):
    def build_client(self) -> RemoteMcpSmokeClient:
        api_key = os.environ.get("SML_REMOTE_TEST_API_KEY")
        if not api_key:
            self.skipTest("Set SML_REMOTE_TEST_API_KEY to run hosted SoManyLemons MCP smoke tests.")
        return RemoteMcpSmokeClient(
            server_url=os.environ.get("SML_REMOTE_TEST_URL", "https://mcp.somanylemons.com/sse"),
            api_key=api_key,
        )

    async def test_remote_new_user_can_connect_and_take_first_action(self) -> None:
        async with self.build_client() as client:
            tools = await client.list_tool_names()
            plans = await client.call_tool_json("list_plans")
            usage = await client.call_tool_json("get_usage")
            brands = await client.call_tool_json("list_brands")
            drafts = await client.call_tool_json("list_drafts")
            content = await client.call_tool_json(
                "generate_content",
                {"topic": "how founders can use short-form video to build trust"},
            )

        self.assertIn("list_plans", tools)
        self.assertIn("get_usage", tools)
        self.assertIn("generate_content", tools)

        self.assertTrue(plans, "Expected remote list_plans response to include plan data")
        self.assertIn("renders", str(plans).lower())
        self.assertIsInstance(usage, dict)

        self.assertEqual(brands.get("profiles"), [])
        self.assertEqual(drafts.get("drafts"), [])
        self.assertEqual(drafts.get("count"), 0)

        self.assertTrue(content.get("success"))
        self.assertIsInstance(content.get("post_text"), str)
        self.assertGreater(len(content["post_text"].strip()), 100)

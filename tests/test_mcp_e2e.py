#!/usr/bin/env python3
"""
End-to-end MCP tool tests that mirror the backend API tests in scripts/test_api_e2e.py.

Each test calls MCP tools the same way a user would through /lemons prompts.
Every test includes a PROMPT field showing the plain English a user would type
to exercise the same functionality.

Usage:
    # Requires SML_REMOTE_TEST_API_KEY env var (or .env.test)
    SML_REMOTE_TEST_API_KEY=sml_xxx python -m pytest tests/test_mcp_e2e.py -v

    # Target QAS
    SML_REMOTE_TEST_URL=https://mcp.somanylemons.com/mcp \
    SML_REMOTE_TEST_API_KEY=sml_xxx python -m pytest tests/test_mcp_e2e.py -v

Mapping to backend tests (scripts/test_api_e2e.py):
    Backend Test                              | MCP Test                              | User Prompt
    ------------------------------------------|---------------------------------------|------------------------------------------
    TestAccount::test_get_account             | test_who_am_i                         | "who am i"
    TestUsage::test_get_usage                 | test_check_usage                      | "check my usage"
    TestBrands::test_list_brands              | test_list_brands                      | "show me my brands"
    TestBrands::test_brand_crud               | test_brand_create_update_delete        | "create a brand called Test, then delete it"
    TestDrafts::test_list_drafts              | test_show_queue                       | "what's in my queue"
    TestDrafts::test_draft_crud               | test_write_save_delete_draft          | "write a post about X, save as draft, delete it"
    TestDrafts::test_draft_schedule           | test_schedule_draft                   | "write a post and schedule it for Dec 31"
    TestJobs::test_list_jobs                  | test_show_recordings                  | "show me my recordings"
    TestContentWriting::test_generate         | test_write_post                       | "write a post about delegation"
    TestContentWriting::test_score            | test_score_post                       | "score this post: [text]"
    TestContentWriting::test_rewrite          | test_rewrite_post                     | "rewrite this post with a stronger hook"
    TestContentWriting::test_pipeline         | test_write_score_rewrite_pipeline     | "write a post, score it, rewrite based on feedback"
    TestQuoteExtraction::test_extract         | test_extract_quotes                   | "extract quotes from this transcript"
    TestUpload::test_upload_png               | test_upload_file                      | "upload this image"
    TestVideogramRendering::test_submit       | test_submit_reel                      | "make a reel from [url]"
    TestVideogramRendering::test_completed    | test_completed_job_has_clips          | "show me my latest recording clips"
    TestImageQuote::test_create               | test_create_image_quote               | "make an image quote: [text]"
    TestTranscriptSearch::test_search         | test_search_transcripts               | "search my transcripts for [topic]"
    TestTemplates::test_list                  | test_list_templates                   | "what templates are available"
    TestPlans::test_list                      | test_list_plans                       | "what plans do you have"
    TestCrossEndpoint::test_pipeline          | test_full_content_pipeline            | "write a post about X, score it, save to queue"
    TestCrossEndpoint::test_quotes_to_posts   | test_quotes_to_post                   | "extract quotes from this, turn the best into a post"
"""

import os
import time
import unittest

from tests.remote_client import RemoteMcpSmokeClient

# ---------------------------------------------------------------------------
# Env setup
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(SCRIPT_DIR), ".env.test")

def _load_env():
    for path in [ENV_FILE, os.path.join(SCRIPT_DIR, "..", "..", "scripts", ".env.test")]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key, _, value = line.partition("=")
                    if key and value and key not in os.environ:
                        os.environ[key] = value
            break

_load_env()


SAMPLE_TRANSCRIPT = """
The biggest lesson I've learned building a startup is that speed matters more than
perfection. We spent three months polishing our first feature when we should have
shipped in three weeks. Your users will tell you what to fix. They'll tell you what
matters. But they can only do that if you give them something to react to. Ship early,
ship often, and listen. The market doesn't care about your architecture diagrams.
It cares about whether you solve a real problem. And you won't know if you do until
you put it in front of real people. Every day you spend building in isolation is a
day you're guessing instead of learning. I'd rather have an ugly product that users
love than a beautiful product that nobody's seen. Perfection is the enemy of progress.
"""


class MCPE2ETest(unittest.IsolatedAsyncioTestCase):
    """End-to-end MCP tool tests that mirror backend API tests."""

    def _client(self) -> RemoteMcpSmokeClient:
        api_key = (
            os.environ.get("SML_REMOTE_TEST_API_KEY")
            or os.environ.get("SML_QAS_API_KEY")
        )
        if not api_key:
            self.skipTest("Set SML_REMOTE_TEST_API_KEY to run MCP E2E tests.")
        url = os.environ.get("SML_REMOTE_TEST_URL", "https://mcp.somanylemons.com/mcp")
        return RemoteMcpSmokeClient(server_url=url, api_key=api_key)

    # ===================================================================
    # 1. ACCOUNT & IDENTITY
    # Prompt: "who am i" / "which account am i on"
    # Backend: TestAccount::test_get_account_returns_identity
    # ===================================================================

    async def test_who_am_i(self):
        """PROMPT: 'who am i'"""
        async with self._client() as c:
            data = await c.call_tool_json("get_account")

        self.assertIn("email", data)
        self.assertIn("@", data["email"])
        self.assertIn("tier", data)
        self.assertIn("key_prefix", data)
        self.assertTrue(data["key_prefix"].startswith("sml_"))

    # ===================================================================
    # 2. USAGE
    # Prompt: "check my usage" / "how many renders do i have left"
    # Backend: TestUsage::test_get_usage_returns_quota
    # ===================================================================

    async def test_check_usage(self):
        """PROMPT: 'check my usage'"""
        async with self._client() as c:
            data = await c.call_tool_json("get_usage")

        self.assertIn("render_limit", data)
        self.assertIn("renders_used", data)
        self.assertIn("renders_remaining", data)
        self.assertGreaterEqual(data["renders_used"], 0)

    # ===================================================================
    # 3. BRANDS - LIST
    # Prompt: "show me my brands" / "what brands do i have"
    # Backend: TestBrands::test_list_brands_returns_profiles
    # ===================================================================

    async def test_list_brands(self):
        """PROMPT: 'show me my brands'"""
        async with self._client() as c:
            data = await c.call_tool_json("list_brands")

        self.assertIn("profiles", data)
        self.assertIsInstance(data["profiles"], list)
        # Should have at least one brand profile
        self.assertGreater(len(data["profiles"]), 0,
            "list_brands returned empty. Org fallback may not be working.")

        profile = data["profiles"][0]
        self.assertIn("id", profile)
        self.assertIn("name", profile)
        self.assertIn("primary_color", profile)

    # ===================================================================
    # 4. BRANDS - CRUD
    # Prompt: "create a brand called E2E Test with red and white colors, then delete it"
    # Backend: TestBrands::test_brand_crud_lifecycle
    # ===================================================================

    async def test_brand_create_update_delete(self):
        """PROMPT: 'create a brand called E2E Test with red and white colors'"""
        async with self._client() as c:
            # Check room
            brands = await c.call_tool_json("list_brands")
            if len(brands.get("profiles", [])) >= 5:
                # Delete one to make room
                deletable = [p for p in brands["profiles"] if not p.get("is_default")]
                if deletable:
                    await c.call_tool_json("delete_brand", {"brand_id": deletable[-1]["id"]})

            # Create
            created = await c.call_tool_json("create_brand", {
                "name": "MCP E2E Test Brand",
                "primary_color": "#e63946",
                "secondary_color": "#f1faee",
            })
            self.assertIn("profile", created)
            brand_id = created["profile"]["id"]
            self.assertEqual(created["profile"]["name"], "MCP E2E Test Brand")

            try:
                # Update
                updated = await c.call_tool_json("update_brand", {
                    "brand_id": brand_id,
                    "name": "MCP E2E Updated",
                })
                self.assertEqual(updated["profile"]["name"], "MCP E2E Updated")
            finally:
                # Delete
                await c.call_tool_json("delete_brand", {"brand_id": brand_id})

            # Verify gone
            after = await c.call_tool_json("list_brands")
            ids = [p["id"] for p in after["profiles"]]
            self.assertNotIn(brand_id, ids)

    # ===================================================================
    # 5. DRAFTS - LIST
    # Prompt: "what's in my queue" / "show me my drafts"
    # Backend: TestDrafts::test_list_drafts_returns_data
    # ===================================================================

    async def test_show_queue(self):
        """PROMPT: 'what's in my queue'"""
        async with self._client() as c:
            data = await c.call_tool_json("list_drafts", {"limit": 5})

        self.assertIn("drafts", data)
        self.assertIn("count", data)
        self.assertIsInstance(data["drafts"], list)
        self.assertGreater(len(data["drafts"]), 0,
            "list_drafts returned empty. Org fallback may not be working.")

    # ===================================================================
    # 6. DRAFTS - CRUD
    # Prompt: "write a post about leadership, save it as a draft, then delete it"
    # Backend: TestDrafts::test_draft_crud_lifecycle
    # ===================================================================

    async def test_write_save_delete_draft(self):
        """PROMPT: 'save this as a draft: [text]'"""
        caption = f"MCP E2E test draft {int(time.time())}"

        async with self._client() as c:
            # Create
            created = await c.call_tool_json("create_draft", {"caption": caption})
            self.assertIn("id", created)
            draft_id = created["id"]
            self.assertEqual(created["caption"], caption)

            try:
                # Update
                new_caption = f"MCP E2E updated {int(time.time())}"
                updated = await c.call_tool_json("update_draft", {
                    "draft_id": draft_id,
                    "caption": new_caption,
                })
                self.assertEqual(updated["caption"], new_caption)

                # Duplicate
                duped = await c.call_tool_json("duplicate_draft", {"draft_id": draft_id})
                self.assertNotEqual(duped["id"], draft_id)
                await c.call_tool_json("delete_draft", {"draft_id": duped["id"]})
            finally:
                # Delete
                await c.call_tool_json("delete_draft", {"draft_id": draft_id})

    # ===================================================================
    # 7. DRAFTS - SCHEDULE
    # Prompt: "write a post and schedule it for December 31st"
    # Backend: TestDrafts::test_draft_schedule
    # ===================================================================

    async def test_schedule_draft(self):
        """PROMPT: 'schedule this draft for December 31st at noon'"""
        async with self._client() as c:
            created = await c.call_tool_json("create_draft", {
                "caption": f"MCP schedule test {int(time.time())}",
            })
            draft_id = created["id"]

            try:
                scheduled = await c.call_tool_json("schedule_draft", {
                    "draft_id": draft_id,
                    "scheduled_at": "2026-12-31T12:00:00Z",
                })
                self.assertEqual(scheduled["status"], "scheduled")
                self.assertIn("2026-12-31", scheduled.get("scheduled_for", ""))
            finally:
                await c.call_tool_json("delete_draft", {"draft_id": draft_id})

    # ===================================================================
    # 8. JOBS - LIST
    # Prompt: "show me my recordings" / "what do i have"
    # Backend: TestJobs::test_list_jobs_returns_data
    # ===================================================================

    async def test_show_recordings(self):
        """PROMPT: 'show me my recordings'"""
        async with self._client() as c:
            data = await c.call_tool_json("list_jobs", {"limit": 5})

        self.assertIn("jobs", data)
        self.assertIsInstance(data["jobs"], list)
        # Jobs may be empty on QAS test account

    # ===================================================================
    # 9. CONTENT - GENERATE
    # Prompt: "write a post about delegation"
    # Backend: TestContentWriting::test_generate_content_produces_post
    # ===================================================================

    async def test_write_post(self):
        """PROMPT: 'write a post about why founders should delegate more'"""
        async with self._client() as c:
            data = await c.call_tool_json("generate_content", {
                "topic": "why founders should share their failures publicly",
            })

        self.assertTrue(data.get("success"), f"Generate failed: {data}")
        post = data.get("post_text", "")
        self.assertGreater(len(post), 100,
            f"Generated post too short ({len(post)} chars)")
        # Real post should have multiple paragraphs
        lines = [l for l in post.strip().split("\n") if l.strip()]
        self.assertGreaterEqual(len(lines), 3)

    # ===================================================================
    # 10. CONTENT - SCORE
    # Prompt: "score this post: [text]"
    # Backend: TestContentWriting::test_score_content_returns_numeric_score
    # ===================================================================

    async def test_score_post(self):
        """PROMPT: 'score this post: I quit my job 3 years ago...'"""
        post_text = (
            "I quit my corporate job 3 years ago to build a startup.\n\n"
            "Everyone told me I was crazy. My parents. My friends.\n\n"
            "Here's what happened:\n\n"
            "Month 1: Zero revenue. Pure panic.\n"
            "Month 6: First paying customer. Pure joy.\n"
            "Month 12: Broke even. Pure relief.\n\n"
            "The risk of staying is often greater than the risk of going."
        )
        async with self._client() as c:
            data = await c.call_tool_json("score_content", {"post_text": post_text})

        score = data.get("overall_score") or data.get("score") or data.get("squeeze_total")
        self.assertIsNotNone(score, f"No score in response: {list(data.keys())}")
        self.assertGreaterEqual(score, 0)
        self.assertIn("feedback", data)
        self.assertIn("strengths", data)

    # ===================================================================
    # 11. CONTENT - REWRITE
    # Prompt: "rewrite this post with a stronger hook"
    # Backend: TestContentWriting::test_rewrite_content_produces_different_text
    # ===================================================================

    async def test_rewrite_post(self):
        """PROMPT: 'rewrite this post with a stronger hook and a specific example'"""
        original = (
            "Had a meeting today about AI. It was pretty interesting. "
            "We talked about how AI can help businesses. There were some good points. "
            "I think AI is going to change a lot of things."
        )
        async with self._client() as c:
            data = await c.call_tool_json("rewrite_content", {
                "post_text": original,
                "feedback": ["Make the hook stronger", "Add a specific example"],
            })

        rewritten = data.get("post") or data.get("post_text") or data.get("rewritten_text", "")
        self.assertGreater(len(rewritten), 50,
            f"Rewrite too short. Keys: {list(data.keys())}")
        self.assertNotEqual(rewritten.strip(), original.strip())

    # ===================================================================
    # 12. CONTENT - FULL PIPELINE
    # Prompt: "write a post about remote work, score it, rewrite based on feedback"
    # Backend: TestContentWriting::test_generate_score_rewrite_pipeline
    # ===================================================================

    async def test_write_score_rewrite_pipeline(self):
        """PROMPT: 'write a post about remote work, score it, then improve it'"""
        async with self._client() as c:
            # Generate
            gen = await c.call_tool_json("generate_content", {
                "topic": "the hidden cost of not delegating as a founder",
            })
            post = gen.get("post_text", "")
            self.assertGreater(len(post), 100)

            # Score
            scored = await c.call_tool_json("score_content", {"post_text": post})
            initial_score = scored.get("overall_score") or scored.get("score", 0)
            self.assertGreaterEqual(initial_score, 0)

            # Rewrite
            feedback = scored.get("feedback", "Improve the hook")
            rewritten_data = await c.call_tool_json("rewrite_content", {
                "post_text": post,
                "feedback": [feedback] if isinstance(feedback, str) else feedback,
            })
            rewritten = (rewritten_data.get("post")
                         or rewritten_data.get("post_text")
                         or rewritten_data.get("rewritten_text", ""))
            self.assertGreater(len(rewritten), 50)

            # Re-score
            rescored = await c.call_tool_json("score_content", {"post_text": rewritten})
            final_score = rescored.get("overall_score") or rescored.get("score", 0)
            self.assertGreaterEqual(final_score, 0)

    # ===================================================================
    # 13. QUOTE EXTRACTION
    # Prompt: "extract quotes from this transcript"
    # Backend: TestQuoteExtraction::test_extract_quotes_from_transcript
    # ===================================================================

    async def test_extract_quotes(self):
        """PROMPT: 'extract the best quotes from this: [transcript]'"""
        async with self._client() as c:
            data = await c.call_tool_json("extract_quotes", {
                "text": SAMPLE_TRANSCRIPT,
                "count": 5,
            })

        quotes = data.get("quotes", [])
        self.assertGreaterEqual(len(quotes), 1)

        q = quotes[0]
        self.assertIn("text", q)
        self.assertGreater(len(q["text"]), 10)
        score = q.get("squeeze_total") or q.get("score")
        self.assertIsNotNone(score)
        self.assertIn("voice", q)
        self.assertIn("substance", q)
        self.assertIn("completeness", q)

    # ===================================================================
    # 14. UPLOAD
    # Prompt: "upload this file" (user provides a file path)
    # Backend: TestUpload::test_upload_png_returns_url
    # ===================================================================

    async def test_upload_file(self):
        """PROMPT: 'upload ~/test-image.png'"""
        # upload_file requires a local file path; create a temp file
        import tempfile
        from PIL import Image as PILImage

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img = PILImage.new("RGB", (100, 100), (255, 0, 0))
            img.save(f, format="PNG")
            tmp_path = f.name

        try:
            async with self._client() as c:
                data = await c.call_tool_json("upload_file", {"file_path": tmp_path})

            url = data.get("url", "")
            self.assertTrue(url.startswith("http"), f"Upload URL invalid: {url}")
            self.assertGreater(data.get("size", 0), 0)
        finally:
            os.unlink(tmp_path)

    # ===================================================================
    # 15. VIDEOGRAM - SUBMIT
    # Prompt: "make a reel from [url]"
    # Backend: TestVideogramRendering::test_submit_clip_job_returns_job_id
    # ===================================================================

    async def test_submit_reel(self):
        """PROMPT: 'make a reel from this file'"""
        # Upload a test file first, then submit for rendering
        import tempfile
        import struct

        # Create minimal WAV
        sample_rate = 16000
        num_samples = sample_rate * 3  # 3 seconds
        audio_data = b"\x00\x00" * num_samples
        data_size = len(audio_data)
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE",
            b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
            b"data", data_size,
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(header + audio_data)
            tmp_path = f.name

        try:
            async with self._client() as c:
                uploaded = await c.call_tool_json("upload_file", {"file_path": tmp_path})
                url = uploaded["url"]

                data = await c.call_tool_json("create_reels", {"url": url})

            self.assertIn("id", data)
            self.assertEqual(data.get("status"), "pending")
        finally:
            os.unlink(tmp_path)

    # ===================================================================
    # 16. VIDEOGRAM - COMPLETED JOB HAS CLIPS
    # Prompt: "show me my latest recording and give me the download links"
    # Backend: TestVideogramRendering::test_existing_completed_job_has_clips
    # ===================================================================

    async def test_completed_job_has_clips(self):
        """PROMPT: 'show me my latest recording clips'"""
        async with self._client() as c:
            jobs = await c.call_tool_json("list_jobs", {"status": "completed", "limit": 1})

        job_list = jobs.get("jobs", [])
        if not job_list:
            self.skipTest("No completed jobs to verify")

        job = job_list[0]
        self.assertEqual(job["status"], "completed")

        async with self._client() as c:
            detail = await c.call_tool_json("check_job_status", {"job_id": job["id"]})

        clips = detail.get("clips", [])
        self.assertGreater(len(clips), 0, f"Completed job has no clips")
        self.assertTrue(clips[0].get("url", "").startswith("http"))

    # ===================================================================
    # 17. IMAGE QUOTE
    # Prompt: "make an image quote: Ship early, ship often"
    # Backend: TestImageQuote::test_create_image_quote_returns_image_url
    # ===================================================================

    async def test_create_image_quote(self):
        """PROMPT: 'make an image quote: Ship early, ship often, and listen.'"""
        async with self._client() as c:
            data = await c.call_tool_json("create_image_quote", {
                "quote_text": "Ship early, ship often, and listen. The market doesn't care about your architecture diagrams.",
                "speaker_name": "MCP E2E Test",
                "size": "square",
            })

        self.assertTrue(data.get("success"), f"Image quote failed: {data}")
        url = data.get("image_url", "")
        self.assertTrue(url.startswith("http"), f"Invalid image URL: {url}")

    # ===================================================================
    # 18. TRANSCRIPT SEARCH
    # Prompt: "search my transcripts for leadership"
    # Backend: TestTranscriptSearch::test_search_transcripts_returns_results
    # ===================================================================

    async def test_search_transcripts(self):
        """PROMPT: 'search my transcripts for the word leadership'"""
        async with self._client() as c:
            data = await c.call_tool_json("search_transcripts", {
                "query": "the",
                "limit": 5,
            })

        self.assertIn("results", data)
        # May be empty on QAS test account

    # ===================================================================
    # 19. TEMPLATES
    # Prompt: "what templates are available"
    # Backend: TestTemplates::test_list_templates_returns_data
    # ===================================================================

    async def test_list_templates(self):
        """PROMPT: 'what templates are available'"""
        async with self._client() as c:
            data = await c.call_tool_json("list_templates")

        self.assertIn("templates", data)
        self.assertIsInstance(data["templates"], list)
        self.assertGreater(len(data["templates"]), 0)

        tmpl = data["templates"][0]
        self.assertIn("id", tmpl)
        self.assertIn("name", tmpl)

    # ===================================================================
    # 20. PLANS
    # Prompt: "what plans do you have" / "show me pricing"
    # Backend: TestPlans::test_list_plans_returns_tiers
    # ===================================================================

    async def test_list_plans(self):
        """PROMPT: 'what plans do you have'"""
        async with self._client() as c:
            data = await c.call_tool_json("list_plans")

        plans = data.get("plans", [])
        self.assertGreaterEqual(len(plans), 2)
        tier_names = [p.get("tier") for p in plans]
        self.assertIn("free", tier_names)

    # ===================================================================
    # 21. FULL CONTENT PIPELINE
    # Prompt: "write a post about async communication, score it, save to my queue"
    # Backend: TestCrossEndpointIntegration::test_generate_score_draft_pipeline
    # ===================================================================

    async def test_full_content_pipeline(self):
        """PROMPT: 'write a post about async communication, score it, save to my queue'"""
        async with self._client() as c:
            # Generate
            gen = await c.call_tool_json("generate_content", {
                "topic": "why remote teams need async communication rituals",
            })
            post = gen.get("post_text", "")
            self.assertGreater(len(post), 100)

            # Score
            scored = await c.call_tool_json("score_content", {"post_text": post})
            score = scored.get("overall_score") or scored.get("score", 0)
            self.assertGreaterEqual(score, 0)

            # Save as draft
            draft = await c.call_tool_json("create_draft", {"caption": post})
            draft_id = draft["id"]

            try:
                # Verify in queue
                queue = await c.call_tool_json("list_drafts", {"limit": 100})
                ids = [d["id"] for d in queue["drafts"]]
                self.assertIn(draft_id, ids)
            finally:
                await c.call_tool_json("delete_draft", {"draft_id": draft_id})

    # ===================================================================
    # 22. QUOTES TO POST
    # Prompt: "extract quotes from this, turn the best one into a post"
    # Backend: TestCrossEndpointIntegration::test_extract_quotes_then_generate_posts
    # ===================================================================

    async def test_quotes_to_post(self):
        """PROMPT: 'extract quotes from this transcript, turn the best into a post'"""
        async with self._client() as c:
            # Extract
            extracted = await c.call_tool_json("extract_quotes", {
                "text": SAMPLE_TRANSCRIPT,
                "count": 3,
            })
            quotes = extracted.get("quotes", [])
            self.assertGreaterEqual(len(quotes), 1)

            # Pick best
            best = max(quotes, key=lambda q: q.get("squeeze_total", 0))

            # Generate post from it
            gen = await c.call_tool_json("generate_content", {
                "topic": f"Expand this into a LinkedIn post: {best['text']}",
            })
            post = gen.get("post_text", "")
            self.assertGreater(len(post), 100)


if __name__ == "__main__":
    unittest.main()

import copy
import json
import unittest

from somanylemons_mcp.task_tools.live_progress import live_update, watch_response


class WatchDeltaTests(unittest.TestCase):
    def update(self):
        return live_update({
            "id": 289, "state": "queued",
            "business_answer": {"counts": {"contacts": 4, "requested": 5}},
            "contacts": [{
                "name": "Synthetic Sales VP", "company": "Synthetic Agency",
                "title": "VP Sales", "email": "synthetic@example.com",
                "linkedin": "https://linkedin.com/in/synthetic",
                "evidence_refs": [{"source_url": "https://example.com/team",
                                   "quote": "Recorded evidence. " * 50}],
            } for _ in range(3)],
            "sources": [{"url": "https://example.com/team"}],
        }, 280)

    def test_quiet_delta_preserves_every_control_and_original_alias(self):
        update = self.update()
        original = copy.deepcopy(update)
        delta = watch_response(update, False)
        for key, value in update.items():
            if key not in {"findings", "sources", "contact_table_policy", "findings_scope"}:
                self.assertEqual(delta[key], value, key)
        self.assertEqual(update, original)
        self.assertEqual(delta["goal_id"], 289)
        self.assertEqual(delta["next_tool_arguments"]["goal_id"], 280)
        self.assertTrue(delta["findings_unchanged"])
        self.assertNotIn("findings", delta)
        self.assertIn("does not mean contacts are missing", delta["findings_scope"])
        self.assertFalse(delta["final_response_ready"])
        self.assertTrue(delta["continue_watching"])

    def test_first_and_changed_responses_keep_full_evidence(self):
        update = self.update()
        result = watch_response(update, True)
        self.assertEqual(result["findings"], update["findings"])
        self.assertEqual(result["sources"], update["sources"])
        self.assertEqual(result["contact_table_policy"], update["contact_table_policy"])
        self.assertNotIn("payload_mode", result)

    def test_unchanged_terminal_and_blocker_keep_full_evidence(self):
        for state in ("completed", "needs_attention"):
            update = self.update()
            update.update(state=state, continue_watching=False,
                          final_response_ready=state == "completed")
            result = watch_response(update, False)
            self.assertEqual(result["findings"], update["findings"])
            self.assertEqual(result["contact_table_policy"], update["contact_table_policy"])
            self.assertIsNone(result["poll_after_seconds"])
            self.assertNotIn("findings_unchanged", result)

    def test_source_rich_quiet_heartbeat_reduces_repeated_payload(self):
        # Synthetic source-rich preview follows the recorded goal 289 shape.
        update = self.update()
        for row in update["findings"]:
            row["employer_sector_proof"] = {
                "company": row["company"], "domain": "example.com",
                "quote": "Synthetic bounded official agency evidence. " * 20,
                "source_url": "https://example.com/about",
                "source_hash": "a" * 64,
            }
        full = json.dumps(watch_response(update, True))
        delta = json.dumps(watch_response(update, False))
        self.assertLess(len(delta), len(full) * 0.5)

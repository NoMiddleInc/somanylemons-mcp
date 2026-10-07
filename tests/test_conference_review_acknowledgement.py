import unittest
from somanylemons_mcp.task_tools.server import public_blocker
from somanylemons_mcp.task_tools import live_progress


class ConferenceReviewAcknowledgementTests(unittest.TestCase):
    def test_exact_operator_code_maps_to_customer_acknowledgement(self):
        result = public_blocker({'party': 'operator', 'reason': 'business_conference_human_review_required'})
        self.assertEqual(result['party'], 'operator')
        self.assertIn('Our team is on it.', result['reason'])
        self.assertIn('saved for human review', result['reason'])

    def test_other_operator_holds_do_not_claim_team_acknowledgement(self):
        result = public_blocker({'party': 'operator', 'reason': 'other_hold'})
        self.assertNotIn('Our team is on it.', result['reason'])

    def test_watch_stops_with_acknowledgement_and_no_retry_suggestion(self):
        blocker = public_blocker({'party': 'operator', 'reason': 'business_conference_human_review_required'})
        answer = {'id': 104, 'state': 'needs_attention', 'blocker': blocker,
                  'business_answer': {'full_request_fulfilled': False}}
        result = live_progress.live_update(answer, 104)
        self.assertFalse(result['continue_watching'])
        self.assertIn('Our team is on it.', result['summary'])
        self.assertEqual(result['blocker']['code'], 'business_conference_human_review_required')
        self.assertFalse(any('reuse this research task' in text for text in result['suggestions']))

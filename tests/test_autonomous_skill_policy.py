"""Deployment skill copies must not ask users to finish ordinary recovery."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class AutonomousSkillPolicyTests(unittest.TestCase):
    def test_every_deployed_skill_has_one_consistent_continuation_policy(self):
        paths = ['skills/producerspark/SKILL.md',
                 'plugins/producerspark/skills/producerspark/SKILL.md',
                 'src/somanylemons_mcp/skills/producerspark/SKILL.md']
        copies = [(ROOT / path).read_text() for path in paths]
        self.assertEqual(len(set(copies)), 1)
        for text in copies:
            self.assertIn('show qualified saved contacts immediately', text)
            self.assertIn('continue automatically in the same response', text)
            self.assertIn('fulfillment requires the original qualification criteria and every requested field', text)
            self.assertIn('Specifically named people and fixed employers cannot be substituted', text)
            self.assertNotIn('work through the research quietly and present the complete', text)
            self.assertNotIn('Stop watching on completion, pause, cancellation or an actual blocker', text)
            self.assertNotIn('Would you like to plan an email campaign', text)

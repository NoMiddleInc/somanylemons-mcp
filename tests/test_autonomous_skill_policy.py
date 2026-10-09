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

class FreshProspectRequestPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_and_all_skill_copies_disallow_historical_title_substitution(self):
        from somanylemons_mcp.task_bridge import task_schemas
        tools = {tool.name: tool for tool in await task_schemas()}
        for name in ('list_tasks', 'create_business_research_request'):
            description = tools[name].description
            self.assertIn('fresh UUID', description)
            self.assertIn('historical', description)
            self.assertIn('verbatim', description)
            self.assertIn('earlier in this conversation', description)
        for path in ('skills/producerspark/SKILL.md', 'plugins/producerspark/skills/producerspark/SKILL.md',
                     'src/somanylemons_mcp/skills/producerspark/SKILL.md'):
            text = (ROOT / path).read_text()
            self.assertIn('create a fresh UUID and goal', text)
            self.assertIn('original request verbatim', text)
            self.assertIn('Reconcile uncertain creation with the same UUID', text)
            self.assertNotIn('Start with list_tasks to find existing work', text)
            self.assertNotIn('If no matching active request exists', text)

    async def test_fresh_creation_preserves_verbatim_criteria_without_reading_old_tasks(self):
        import json
        import httpx
        from somanylemons_mcp.task_bridge import invoke_task
        question = 'Give me 5 CFOs of healthtech startups in Boston, with titles, emails and LinkedIn.'
        calls = []
        async def handler(request):
            calls.append(request)
            self.assertEqual(request.method, 'POST')
            return httpx.Response(200, json={'data': {'id': 300, 'state': 'queued'}})
        await invoke_task('create_business_research_request', {
            'request': question, 'idempotency_key': '0189f26c-1cdb-4088-9c84-b729dd8d3f37',
            'spec': {'kind': 'company_contacts', 'companies': [], 'company_scope': 'candidate_pool',
                     'company_stage': 'startup', 'industries': ['healthtech'],
                     'employer_locations': ['Boston'], 'roles': ['CFO'], 'count': 5,
                     'fields': ['title', 'email', 'linkedin']}},
            api_url='https://example.com', api_key='test-only', transport=httpx.MockTransport(handler))
        self.assertEqual(len(calls), 1)
        body = json.loads(calls[0].content)
        self.assertEqual(body['request'], question)
        self.assertEqual(body['spec']['company_stage'], 'startup')
        self.assertEqual(body['spec']['industries'], ['healthtech'])

    async def test_omitted_personal_location_and_explicit_locations_remain_separate(self):
        import json
        import httpx
        from somanylemons_mcp.task_bridge import invoke_task, task_schemas
        tools = {tool.name: tool for tool in await task_schemas()}
        self.assertNotIn('assume United States', tools['create_business_research_request'].description)
        for personal, employer in [([], []), (['Paris'], []), ([], ['Boston'])]:
            calls = []
            async def handler(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200, json={'data': {'id': 301, 'state': 'queued'}})
            await invoke_task('create_business_research_request', {
                'request': 'Find 5 CFOs with email and LinkedIn.',
                'idempotency_key': '0189f26c-1cdb-4088-9c84-b729dd8d3f38',
                'spec': {'kind': 'company_contacts', 'companies': [], 'company_scope': 'candidate_pool',
                         'roles': ['CFO'], 'count': 5, 'fields': ['title', 'email', 'linkedin'],
                         'person_locations': personal, 'employer_locations': employer}},
                api_url='https://example.com', api_key='test-only', transport=httpx.MockTransport(handler))
            self.assertEqual(calls[0]['spec']['person_locations'], personal)
            self.assertEqual(calls[0]['spec']['employer_locations'], employer)
        for path in ('skills/producerspark/SKILL.md', 'plugins/producerspark/skills/producerspark/SKILL.md',
                     'src/somanylemons_mcp/skills/producerspark/SKILL.md'):
            text = (ROOT / path).read_text()
            self.assertIn('otherwise leave person_locations empty', text)
            self.assertNotIn('United States default', text)


class HostedContactRoutingTests(unittest.TestCase):
    def test_general_contact_triggers_route_before_web_only_discovery(self):
        for path in ('skills/producerspark/SKILL.md',
                     'plugins/producerspark/skills/producerspark/SKILL.md',
                     'src/somanylemons_mcp/skills/producerspark/SKILL.md'):
            text = (ROOT / path).read_text()
            metadata, body = text.split('---', 2)[1:]
            for trigger in ('professional contacts', 'business emails', 'LinkedIn profiles',
                            'any industry or location', 'customer success', 'HR'):
                self.assertIn(trigger, metadata)
            self.assertIn('call find_people first with the original question verbatim', body.split('\n\n')[1])
            self.assertIn('submit the original question before manual web discovery', body.split('\n\n')[2])
            self.assertIn('Public web search alone does not establish', body)
            self.assertIn('recorded uncertainty', body)
            self.assertIn('without spec', body)
            self.assertIn('Requested counts never expand configured spending authority', body)
            self.assertIn('Unfinished work and uncertain provider effects remain open', body)

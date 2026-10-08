import copy
import json
import unittest
import httpx
from somanylemons_mcp.task_tools.client import TaskApiClient, TaskApiConfig
from somanylemons_mcp.task_tools.live_progress import live_update
from somanylemons_mcp.task_tools.server import brief_answer, compact_research_answer, create_server

COLUMNS=['Name','Company','Title','Email','LinkedIn']


class ContactTableColumnsTests(unittest.IsolatedAsyncioTestCase):
    def task(self, completed=True):
        rows=[{'name':f'Person {i}','company':'Salesforce','title':'Sales Director, Cloud',
               'email':f'p{i}@salesforce.com','linkedin':f'https://linkedin.com/in/person{i}','email_status':'guessed' if i==0 else 'verified'} for i in range(6)]
        return {'id':255,'state':'completed' if completed else 'running','fulfillment':'fulfilled' if completed else 'unknown',
            'contract':{'workflow':'business_research','request':'Find6cloudsalesdirectors at Salesforce.','spec':{'kind':'company_contacts','count':6}},
            'business_answer':{'rows':rows,'fulfillment':'fulfilled' if completed else 'unknown','closure':'fulfilled' if completed else 'unknown',
                'counts':{'contacts':6,'requested':6},'coverage':{'requested_count':6,'qualified_rows':6},
                'fulfillment_review':{'passed':completed},'review_scope_current':True,
                'delivery':{'status':'provider_accepted','receipt':{'provider_id':'saved'}} if completed else {},
                'full_request_fulfilled':completed}}

    def assert_columns(self, result):
        self.assertEqual(result['required_contact_columns'],COLUMNS)
        self.assertIn('repeat each contact\'s recorded company on every row',result['contact_table_policy'])
        self.assertIn('introduction does not replace the Company column',result['contact_table_policy'])

    def test_completed_single_employer_keeps_company_column_and_uncertainty(self):
        answer=compact_research_answer(self.task())
        brief=brief_answer(answer)
        self.assert_columns(answer);self.assert_columns(brief)
        self.assertTrue(brief['final_response_ready'])
        self.assertIn('Keep the Company column',brief['response_policy'])
        self.assertEqual([row['company'] for row in brief['contacts']],['Salesforce']*5)
        self.assertEqual(brief['contacts'][0]['email_status'],'guessed')

    def test_progress_full_count_and_blocker_cannot_become_final_through_format_policy(self):
        for completed,held in [(False,False),(True,True)]:
            task=self.task(completed)
            if held:task['blocker']={'party':'external','reason':'external_operation_uncertain'};task['business_answer']['fulfillment_review']['passed']=False
            answer=compact_research_answer(task);progress=live_update(answer,255);brief=brief_answer(answer)
            self.assert_columns(progress);self.assert_columns(brief)
            self.assertFalse(progress['final_response_ready']);self.assertFalse(brief['final_response_ready'])
            self.assertEqual(progress['findings'][0]['company'],'Salesforce')
            self.assertEqual(progress['findings'][0]['email_status'],'guessed')

    async def test_real_details_and_successive_contact_pages_retain_required_columns(self):
        task=self.task();calls=[]
        def handler(request):
            calls.append(request)
            self.assertEqual(request.method,'GET')
            return httpx.Response(200,json={'data':copy.deepcopy(task)})
        api=TaskApiClient(TaskApiConfig('https://example.com','mock-key'),transport=httpx.MockTransport(handler))
        server=create_server(api)
        for page in [1,2]:
            result=await server.call_tool('get_research_answer',{'goal_id':255,'details':True,'contact_page':page})
            content=result[0] if isinstance(result,tuple) else result
            answer=json.loads(content[0].text)
            self.assert_columns(answer)
            self.assertEqual([row['company'] for row in answer['contacts']],['Salesforce']*(5 if page==1 else 1))
        self.assertEqual(len(calls),2)

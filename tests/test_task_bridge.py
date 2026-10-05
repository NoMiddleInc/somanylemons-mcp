import asyncio,json,unittest
import httpx
from somanylemons_mcp.task_bridge import invoke_task,task_schemas,read_task_artifact
from somanylemons_mcp.task_tools.client import TaskApiError, TaskApiClient, TaskApiConfig
from somanylemons_mcp.remote import SessionKeyBindings

class BridgeTests(unittest.IsolatedAsyncioTestCase):
 async def test_declared_targets_not_exposed_as_observed_citations(self):
  from somanylemons_mcp.task_tools.server import compact_research_answer
  task={'conference_answer':{'event':{'name':'Event','source_urls':['https://publisher.example/unopened.pdf']},'rows':[],'sources':[{'url':'https://publisher.example/observed','content_hash':'a'*64}],'citation_basis':'observed sources only'}}
  answer=compact_research_answer(task)
  self.assertNotIn('unopened.pdf',json.dumps(answer))
  self.assertEqual(answer['sources'][0]['url'],'https://publisher.example/observed')
  self.assertEqual(answer['conference_answer']['citation_basis'],'observed sources only')
  self.assertIn('source_urls',task['conference_answer']['event'])
 async def test_task_schema_keeps_typed_conference_and_authority(self):
  tools={t.name:t for t in await task_schemas()}
  self.assertEqual(len(tools),20)
  self.assertIn('create_research_request',tools)
  self.assertIn('create_conference_research_request',tools)
  self.assertEqual(set(tools['create_conference_research_request'].inputSchema['properties']['event_id']['enum']), {'acams-las-vegas-2026','rsa-usa-2026','icba-live-2026','acfe-global-2026','aba-aml-fraud-2026','afp-2026'})
  self.assertNotIn('production_capture',tools['create_conference_research_request'].inputSchema['properties'])
 async def test_concurrent_clients_never_share_keys(self):
  calls=[]
  async def handler(request):
   calls.append(request.headers['x-api-key']);await asyncio.sleep(.01)
   return httpx.Response(200,json={'data':{'id':1,'state':'queued'}})
  transport=httpx.MockTransport(handler)
  await asyncio.gather(*(invoke_task('get_task',{'goal_id':1},api_url='https://example.com',api_key=key,transport=transport) for key in ('tenant-A','tenant-B')))
  self.assertCountEqual(calls,['tenant-A','tenant-B'])

 async def test_research_request_forwards_selected_list_without_extra_calls(self):
  calls=[]
  def handler(request):
   calls.append(request)
   return httpx.Response(200,json={'data':{'id':100,'state':'queued','contract':{'count':10}}})
  result=await invoke_task('create_research_request',{'agencies':['Lockton'],'campaign_id':51,'count':10,'idempotency_key':'22222222-2222-4222-8222-222222222222'},api_url='https://example.com',api_key='owner',transport=httpx.MockTransport(handler))
  self.assertEqual(len(calls),1)
  self.assertEqual(calls[0].url.path,'/api/v1/agent-tasks')
  body=json.loads(calls[0].content)
  self.assertEqual(body['campaign_id'],51)
  self.assertEqual(body['count'],10)
  self.assertEqual(body['agencies'],['Lockton'])
  self.assertNotIn('config_id',body)
  self.assertIn('queued',str(result))
 async def test_artifact_resource_uses_same_owner_and_rejects_injection(self):
  calls=[]
  def handler(request):
   calls.append(request);return httpx.Response(200,content=b"saved-workbook")
  blob=await read_task_artifact('producerspark-task-artifact://31/42',api_url='https://example.com',api_key='owner',transport=httpx.MockTransport(handler))
  self.assertEqual(blob,b'saved-workbook');self.assertEqual(calls[0].headers['x-api-key'],'owner')
  with self.assertRaises(TaskApiError):await read_task_artifact('producerspark-task-artifact://31/42?other',api_url='https://example.com',api_key='owner')

 async def test_missing_key_rejected_without_fallback(self):
  with self.assertRaises(TaskApiError):await invoke_task('list_tasks',{},api_url='https://example.com',api_key='')

 async def test_full_spreadsheet_and_download_are_single_scoped_calls(self):
  calls=[]
  def handler(request):
   calls.append(request)
   if request.url.path.endswith('/download-link'):
    return httpx.Response(200,json={'data':{'filename':'237-prospects.xlsx','download_url':'https://api.producerspark.com/api/v1/agent-tasks/download/signed','sha256':'saved-hash'}})
   return httpx.Response(200,json={'data':{'sheets':[{'name':'Prospects','headers':['Name'],'rows':[[f'Person {i}'] for i in range(237)],'row_count':237,'truncated':False}]}})
  transport=httpx.MockTransport(handler)
  result=await invoke_task('get_task_artifact',{'goal_id':34,'artifact_id':7},api_url='https://example.com',api_key='owner',transport=transport)
  self.assertIn('download_url',str(result))
  result=await invoke_task('read_task_spreadsheet',{'goal_id':34,'artifact_id':7},api_url='https://example.com',api_key='owner',transport=transport)
  self.assertIn('Person 236',str(result));self.assertEqual(len(calls),2)
  self.assertTrue(all(r.headers['x-api-key']=='owner' for r in calls))
  self.assertTrue(calls[0].url.path.endswith('/34/artifacts/7/download-link'))

 async def test_sheet_summary_preserves_all_sheet_names_without_large_rows(self):
  calls=[]
  def handler(request):
   calls.append(request)
   return httpx.Response(200,json={"data":{"sheets":[
    {"name":"Speakers and sessions","row_count":237,"headers":["Name"],"rows":[["person"]]*237,"truncated":False},
    {"name":"Coverage and review","row_count":20,"headers":["Field","Value"],"rows":[["coverage","saved"]]*20,"truncated":False}]}})
  result=await invoke_task("read_task_spreadsheet",{"goal_id":34,"artifact_id":7,"include_rows":False},api_url="https://example.com",api_key="owner",transport=httpx.MockTransport(handler))
  rendered=str(result)
  self.assertIn("sheets_summary",rendered);self.assertIn("Speakers and sessions",rendered);self.assertIn("Coverage and review",rendered)
  self.assertNotIn("person",rendered);self.assertEqual(len(calls),1)

 async def test_saved_icp_tool_uses_scoped_facade_and_concise_guidance(self):
  from somanylemons_mcp.task_bridge import SCHEMA_SERVER
  calls=[]
  def handler(request):
   calls.append(request);return httpx.Response(200,json={'data':{'criteria':{'target':'Saved target'},'download_url':'https://example.com/file'}})
  result=await invoke_task('get_my_icp',{},api_url='https://example.com',api_key='owner',transport=httpx.MockTransport(handler))
  self.assertIn('Saved target',str(result));self.assertEqual(calls[0].url.path,'/api/v1/agent-tasks/icp')
  self.assertIn('75 words',SCHEMA_SERVER.instructions)

class ResearchBlockerTests(unittest.IsolatedAsyncioTestCase):
    async def read_public_task(self, name, reason):
        calls = []
        # Same public goal/blocker shape as the backend's goal_detail facade.
        task = {
            "id": 43,
            "title": "Research Lockton producers",
            "state": "needs_attention",
            "version": 1,
            "allowed_actions": ["pause", "cancel", "retry"],
            "progress": {"completed": 0, "total": 4},
            "contract": {"count": 10, "agencies": [{"name": "Lockton"}]},
            "blocker": {"party": "operator", "reason": reason},
            "research_answer": {
                "counts": {"qualified_contacts": 0, "pending_contacts": 51},
                "agencies": [{"agency": "Lockton", "qualified_contacts": 0}],
            },
            "tasks": [{
                "id": "491", "capability": "agency.research",
                "state": "needs_attention", "result": {"blocker": reason},
            }],
        }

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"data": task})

        arguments = {"goal_id": 43}
        if name == "wait_for_task":
            arguments["timeout_seconds"] = 0
        result = await invoke_task(
            name, arguments, api_url="https://producerspark.com", api_key="owner",
            transport=httpx.MockTransport(handler),
        )
        content = result[0] if isinstance(result, tuple) else result
        answer = json.loads(content[0].text)
        if isinstance(result, tuple):
            self.assertEqual(answer, result[1])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, "/api/v1/agent-tasks/43")
        self.assertEqual(calls[0].url.params["view"], "answer")
        self.assertEqual(calls[0].headers["x-api-key"], "owner")
        if name == "wait_for_task":
            self.assertEqual(answer["wait_status"], "needs_attention")
            answer = answer["task"]
        return answer

    async def test_canonical_quota_reset_is_exposed_across_saved_task_reads(self):
        reason = (
            "Prospect enrichment quota exceeded. 0 enrichment credits remain until "
            "2026-10-18T21:20:00.655157+00:00."
        )
        for name in ("get_task", "get_research_answer", "wait_for_task"):
            with self.subTest(tool=name):
                answer = await self.read_public_task(name, reason)
                self.assertEqual(answer["blocker"], {
                    "party": "operator",
                    "reason": "ProducerSpark enrichment quota exceeded: 0 credits remain until 2026-10-18T21:20:00.655157+00:00.",
                })
                self.assertEqual(answer["state"], "needs_attention")
                self.assertEqual(answer["research_answer"]["counts"]["qualified_contacts"], 0)
                self.assertIn("retry", answer["allowed_actions"])
                self.assertNotIn("http", answer["blocker"]["reason"])

    async def test_unknown_or_extended_operator_diagnostics_stay_redacted(self):
        canonical = (
            "Prospect enrichment quota exceeded. 0 enrichment credits remain until "
            "2026-10-18T21:20:00.655157+00:00."
        )
        reasons = [
            "Internal provider failure: https://internal.example/debug?token=private",
            canonical + " https://internal.example/debug?token=private",
            "Internal details: " + canonical,
            canonical + "\n",
            canonical.replace("2026-10-18", "2026-99-18"),
            canonical.replace("+00:00", "+24:00"),
            canonical.replace("+00:00", "+00:60"),
            canonical.replace("+00:00", ""),
            canonical.replace("0 enrichment", "٠ enrichment"),
            {"internal": "private"},
        ]
        for name in ("get_task", "get_research_answer", "wait_for_task"):
            for reason in reasons:
                with self.subTest(tool=name, reason=reason):
                    answer = await self.read_public_task(name, reason)
                    self.assertEqual(answer["blocker"], {
                        "party": "operator",
                        "reason": "Research needs an internal review before completion.",
                    })
                    self.assertNotIn("internal.example", json.dumps(answer))
                    self.assertNotIn("private", json.dumps(answer))


class SessionBindingTests(unittest.TestCase):
 def test_foreign_key_and_unknown_session_rejected(self):
  b=SessionKeyBindings();self.assertTrue(b.check('', 'owner'))
  b.bind('known','owner');self.assertTrue(b.check('known','owner'))
  self.assertFalse(b.check('known','other'));self.assertFalse(b.check('unknown','owner'))
  self.assertNotIn('owner',str(b.owners))
 def test_session_capacity_is_bounded(self):
  b=SessionKeyBindings(limit=1);b.bind('one','owner')
  self.assertFalse(b.check('', 'other'));self.assertTrue(b.check('one','owner'))

 def test_expired_session_requires_initialize_and_never_rebinds(self):
  from unittest.mock import patch
  with patch('somanylemons_mcp.remote.time.monotonic',return_value=100):
   b=SessionKeyBindings(ttl=10);b.bind('old-sdk-session','owner')
  with patch('somanylemons_mcp.remote.time.monotonic',return_value=111):
   self.assertFalse(b.check('old-sdk-session','owner'))
   self.assertFalse(b.check('old-sdk-session','new-key'))
   self.assertTrue(b.check('', 'new-key'))
   self.assertNotIn('old-sdk-session',b.owners)
 def test_evicted_or_rotated_key_cannot_claim_existing_sdk_session(self):
  b=SessionKeyBindings();b.bind('live-sdk-session','original-key')
  self.assertFalse(b.check('live-sdk-session','rotated-key'))
  b.owners.pop('live-sdk-session')
  self.assertFalse(b.check('live-sdk-session','original-key'))
  self.assertFalse(b.check('live-sdk-session','other-key'))

class RemoteIsolationTests(unittest.IsolatedAsyncioTestCase):
 async def test_http_session_cannot_be_reused_with_another_key_and_context_resets(self):
  from unittest.mock import patch
  import somanylemons_mcp.remote as remote
  import somanylemons_mcp.server as server
  calls=[]
  class Manager:
   def __init__(self,**kwargs):pass
   async def handle_request(self,scope,receive,send):
    calls.append(server._session_api_key.get())
    await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json'),(b'mcp-session-id',b'session-one')]})
    await send({'type':'http.response.body','body':b'{}'})
  with patch.object(remote,'StreamableHTTPSessionManager',Manager):
   app=remote._create_app()
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://mcp.example') as client:
    first=await client.post('/mcp',headers={'x-api-key':'sml_owner_abcdefghijklmnopqrstuvwxyz'})
    self.assertEqual(first.status_code,200)
    attack=await client.post('/mcp',headers={'x-api-key':'sml_other_abcdefghijklmnopqrstuvwxyz','mcp-session-id':'session-one'})
    self.assertEqual(attack.status_code,403)
    stale=await client.post('/mcp',headers={'x-api-key':'sml_owner_abcdefghijklmnopqrstuvwxyz','mcp-session-id':'previous-deploy-session'})
    self.assertEqual(stale.status_code,404)
    accepted=await client.post('/mcp',headers={'x-api-key':'sml_owner_abcdefghijklmnopqrstuvwxyz','mcp-session-id':'session-one'})
    self.assertEqual(accepted.status_code,200)
  self.assertEqual(calls,['sml_owner_abcdefghijklmnopqrstuvwxyz','sml_owner_abcdefghijklmnopqrstuvwxyz'])
  self.assertEqual(server._session_api_key.get(),'')

class InitializeInstructionsTests(unittest.TestCase):
 def test_outer_server_exposes_research_rules_without_customer_credentials(self):
  from somanylemons_mcp.server import server
  instructions=server.create_initialization_options().instructions
  self.assertIn('status_counts_by_person',instructions)
  self.assertIn('Do not infer that government',instructions)
  self.assertIn('must be resolved before the review hold can be cleared for delivery',instructions)
  self.assertIn('clearly labeled partial snapshots are allowed when authorized',instructions)
  self.assertIn('Honor any show-here-first or communication hold',instructions)
  self.assertIn('Final delivery requires BOTH',instructions)
  self.assertIn('scheduled follow-up only when an actual persisted schedule',instructions)
  self.assertIn('never that no public email exists',instructions)
  self.assertIn('recorded_email_provenance_groups',instructions)
  self.assertIn('common task/provider lookup clock is not the date of every email',instructions)
  self.assertIn('must not replace those final status counts',instructions)
  self.assertIn('content tools according to their schemas',instructions)
  self.assertNotIn('schema-only',instructions)
  self.assertNotIn('schema.invalid',instructions)

class DownloadOriginTests(unittest.IsolatedAsyncioTestCase):
 async def test_download_capability_uses_public_site_without_changing_token(self):
  token = "signed-token:timestamp:signature"
  def handler(request):
   return httpx.Response(200,json={"data":{"download_url":"https://api.producerspark.com/api/v1/agent-tasks/download/"+token,"filename":"prospects.xlsx"}})
  client = TaskApiClient(TaskApiConfig("https://api.producerspark.com","test-key"),transport=httpx.MockTransport(handler))
  result = await client.request("GET","/api/v1/agent-tasks/34/artifacts/7/download-link")
  self.assertEqual(result["download_url"],"https://producerspark.com/api/v1/agent-tasks/download/"+token)
  self.assertEqual(result["filename"],"prospects.xlsx")

 async def test_main_list_tools_use_canonical_routes_and_list_ids(self):
  calls=[]
  def handler(request):
   calls.append(request)
   return httpx.Response(200,json={'data':{'source':'main golden list','campaign_id':44}})
  transport=httpx.MockTransport(handler)
  for name,args in [('list_golden_lists',{}),('read_golden_list',{'campaign_id':44,'include_rows':False}),('get_prospect_list',{'campaign_id':44}),('get_my_icp',{'campaign_id':44})]:
   await invoke_task(name,args,api_url='https://example.com',api_key='owner',transport=transport)
  self.assertEqual([r.url.path for r in calls],['/api/v1/agent-tasks/golden-lists','/api/v1/agent-tasks/golden-lists/read','/api/v1/agent-tasks/prospect-list','/api/v1/agent-tasks/icp'])
  self.assertEqual(calls[-1].url.params['campaign_id'],'44')
  self.assertEqual(calls[1].url.params['include_rows'],'false')

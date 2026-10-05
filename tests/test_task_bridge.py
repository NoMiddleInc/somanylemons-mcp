import asyncio,json,unittest
import httpx
from somanylemons_mcp.task_bridge import invoke_task,task_schemas,read_task_artifact
from somanylemons_mcp.task_tools.client import TaskApiError
from somanylemons_mcp.remote import SessionKeyBindings

class BridgeTests(unittest.IsolatedAsyncioTestCase):
 async def test_task_schema_keeps_typed_conference_and_authority(self):
  tools={t.name:t for t in await task_schemas()}
  self.assertEqual(len(tools),15)
  self.assertIn('create_research_request',tools)
  self.assertIn('create_conference_research_request',tools)
  self.assertNotIn('production_capture',tools['create_conference_research_request'].inputSchema['properties'])
 async def test_concurrent_clients_never_share_keys(self):
  calls=[]
  async def handler(request):
   calls.append(request.headers['x-api-key']);await asyncio.sleep(.01)
   return httpx.Response(200,json={'data':{'id':1,'state':'queued'}})
  transport=httpx.MockTransport(handler)
  await asyncio.gather(*(invoke_task('get_task',{'goal_id':1},api_url='https://example.com',api_key=key,transport=transport) for key in ('tenant-A','tenant-B')))
  self.assertCountEqual(calls,['tenant-A','tenant-B'])
 async def test_artifact_resource_uses_same_owner_and_rejects_injection(self):
  calls=[]
  def handler(request):
   calls.append(request);return httpx.Response(200,content=b"saved-workbook")
  blob=await read_task_artifact('producerspark-task-artifact://31/42',api_url='https://example.com',api_key='owner',transport=httpx.MockTransport(handler))
  self.assertEqual(blob,b'saved-workbook');self.assertEqual(calls[0].headers['x-api-key'],'owner')
  with self.assertRaises(TaskApiError):await read_task_artifact('producerspark-task-artifact://31/42?other',api_url='https://example.com',api_key='owner')

 async def test_missing_key_rejected_without_fallback(self):
  with self.assertRaises(TaskApiError):await invoke_task('list_tasks',{},api_url='https://example.com',api_key='')

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
    accepted=await client.post('/mcp',headers={'x-api-key':'sml_owner_abcdefghijklmnopqrstuvwxyz','mcp-session-id':'session-one'})
    self.assertEqual(accepted.status_code,200)
  self.assertEqual(calls,['sml_owner_abcdefghijklmnopqrstuvwxyz','sml_owner_abcdefghijklmnopqrstuvwxyz'])
  self.assertEqual(server._session_api_key.get(),'')

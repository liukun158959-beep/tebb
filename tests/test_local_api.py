"""真实本地 WebSocket 与无头对话链路；模型和用户文件全部隔离。"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from atlas.testing import FakeLLM

from desk_companion import assistant, memory, state, usage
from desk_companion.local_api import server
from desk_companion.local_api.host import HeadlessApp


class LocalApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"DESK_COMPANION_DATA_DIR": str(root)}).start()
        patch.object(memory, "memory_path", return_value=root / "memory" / "chat.jsonl").start()
        patch.object(state, "state_path", return_value=root / "state.json").start()
        patch.object(usage, "usage_path", return_value=root / "usage.jsonl").start()
        self.host = HeadlessApp()
        from desk_companion.tasks import TaskManager
        self.host._tasks = TaskManager(self.host, command=[sys.executable, "-u", "-c", "exec(open('tests/worker_fixture.py', encoding='utf-8').read())"])
        self.addCleanup(self.host._tasks.shutdown)
        with patch.object(assistant, "require_llm_env", return_value={
            "ATLAS_API_KEY": "test-only", "ATLAS_BASE_URL": "http://example.invalid/v1", "ATLAS_MODEL": "fake",
        }), patch('atlas.LLM', return_value=FakeLLM(["本地流式回归正常。"])), \
                patch('desk_companion.model_catalog.bind_llm'):
            self.host.agent = assistant.build_agent(self.host)
        self.host.agent.llm.sampling = None
        patch.object(self.host, "engage_model").start()
        patch.object(server, "HOST", self.host).start()
        patch.object(server, "TOKEN", "test-token").start()
        self.service = await serve(server._handler, "127.0.0.1", 0, process_request=server._health)
        self.addAsyncCleanup(self.stop_service)
        self.port = self.service.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/ws?token=test-token"

    async def stop_service(self):
        self.service.close()
        await self.service.wait_closed()

    async def notebook_rpc(self, method, _timeout=5, **args):
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args)))
            frame = json.loads(await asyncio.wait_for(ws.recv(), _timeout))
        self.assertEqual(frame['id'], method)
        self.assertTrue(frame['result']['ok'], frame)
        return frame['result']['result']

    async def notebook_question(self, sid, tokens=None):
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps(dict(type='notebook', session_id=sid, text='question', doc_ids=['doc-1'],
                sampling=dict(reasoning_effort='low', temperature=.5, top_p=1))))
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if frame['type'] == 'token' and tokens is not None:
                    tokens.append(frame['data'])
                if frame['type'] in ('done', 'error'):
                    return frame

    async def test_search_settings_and_probe_are_authenticated_and_skip_host_lock(self):
        import httpx
        from desk_companion import search_service
        from tests.test_web_search import exa
        self.host.conversation.running=True
        with self.host.turn_lock:
            loaded=await self.notebook_rpc('load_search_settings')
            self.assertEqual(loaded['provider'],'exa')
            saved=await self.notebook_rpc('save_search_settings',payload=dict(provider='exa'))
            self.assertTrue(saved['ok'])
            with patch.object(search_service.model_network,'http_client',side_effect=lambda *a,**k:httpx.Client(transport=httpx.MockTransport(exa))):
                result=await self.notebook_rpc('test_search',query='TaskGroup')
            self.assertTrue(result['ok'],result);self.assertEqual(result['provider'],'exa')
            self.assertNotIn('api_key',json.dumps(result))
        from websockets.exceptions import ConnectionClosed
        with self.assertRaises(ConnectionClosed):
            async with connect(self.url.replace('test-token','bad-token'),proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc',method='test_search')));await ws.recv()

    async def test_tebb_worker_uses_authenticated_shared_calendar_and_automation_services(self):
        from desk_companion.collaboration import Collaboration
        from desk_companion.collaboration_worker import execute
        from tests.test_collaboration import Queue, MODEL
        from tests.schedule_fixture import TimeCalendar, DAY, START, END
        patch('desk_companion.model_catalog.require_active',return_value=MODEL).start()
        patch('desk_companion.model_catalog.require_item',return_value=MODEL).start()
        self.host._collaboration=Collaboration(self.host.work_board.path,managers=dict(work=Queue(),chat=Queue()),start=False)
        self.addCleanup(self.host._collaboration.shutdown)
        self.addCleanup(lambda:self.host.automation.stop(0))
        self.host.scheduling.calendar=TimeCalendar()
        patch.dict(os.environ,DESK_TERMINAL_PORT=str(self.port),DESK_TERMINAL_TOKEN='test-token').start()
        accepted=self.host.collaboration.submit_execution('tebb','创建明天学习日程','',dict(kind='test',id='rpc'),'tebb-rpc-one')
        self.host.collaboration.tick()
        root=self.host.collaboration.execution_status(accepted['run_id'])
        work=self.host.collaboration.managers['work']
        planning=work.requests[root['task']]
        planned=[]
        class Planner:
            def chat(_,messages,tools,on_delta):
                self.assertEqual([t['function']['name'] for t in tools],['submit_plan'])
                return dict(message=dict(role='assistant',tool_calls=[dict(id='self-plan',type='function',function=dict(name='submit_plan',arguments=json.dumps(dict(tasks=[dict(agent_id='tebb',title='安排学习',instructions='创建本地学习安排，开始结束时间已明确',depends_on=None)]))))]),usage={})
        await asyncio.to_thread(execute,planning,lambda k,d:planned.append((k,d)),Planner())
        work.complete(root['task'],next(d for k,d in planned if k=='result'))
        self.host.collaboration.tick();self.host.collaboration.tick()
        root=next(r for r in self.host.collaboration.snapshot()['state']['runs'] if r.get('parent')==root['id'] and r.get('stage')=='collect')
        root=self.host.collaboration.execution_status(root['id'])
        events=[]
        class Model:
            calls=0
            def chat(model,messages,tools,on_delta):
                model.calls+=1
                if model.calls==1:
                    return dict(message=dict(role='assistant',content='',tool_calls=[dict(id='calendar-call',type='function',function=dict(name='calendar_change',arguments=json.dumps(dict(action=dict(type='create',summary='学习安排',start=START,end=END,item_id='')))))]),usage={})
                receipt=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
                if not receipt.get('ok'):raise AssertionError(receipt)
                return dict(message=dict(content='日程已保存到本地。'),usage={})
        request=dict(kind='task',role=root['agent_config'],collab_run=root['id'],collab_path=str(self.host.collaboration.path),
            text=root['input'],context=[],workspace=root['workspace'],model_id=MODEL['id'],budget=3)
        with self.host.turn_lock:
            await asyncio.wait_for(asyncio.to_thread(execute,request,lambda k,d:events.append((k,d)),Model()),15)
        self.assertTrue(any(k=='tool_end' and d.get('status')=='success' for k,d in events))
        page=await self.notebook_rpc('load_schedule_month',day=DAY,refresh=True)
        event=next(e for e in page['events'] if e['summary']=='学习安排')
        self.assertEqual(event['sync_state'],'local');self.assertEqual(self.host.scheduling.calendar.creates,[])
        result=await self.notebook_rpc('tebb_operation',run_id=root['id'],tool='automation_save',args=dict(_call_id='auto-one',payload=dict(name='每日学习',action='tebb_task',goal='整理学习计划',cadence='daily',weekdays=[],hour=9,minute=0,enabled=False)))
        self.assertTrue(result['ok'],result)
        refused=await self.notebook_rpc('tebb_operation',run_id='missing',tool='calendar_change',args=dict(_call_id='nope',action=dict(type='delete')))
        self.assertFalse(refused['ok'])

    async def test_automation_four_routes_skip_host_busy_and_keep_active_guards_and_auth(self):
        from unittest.mock import Mock
        from desk_companion import automation as a
        from tests.test_automation_service import payload
        from websockets.exceptions import ConnectionClosed
        self.addCleanup(lambda: self.host.automation.stop(0))
        for name in ('list_automation_jobs','save_automation_job','delete_automation_job','run_automation_job'):
            patch.object(self.host, name, side_effect=AssertionError('legacy automation'), create=True).start()
        self.host.conversation.running = True
        with self.host.turn_lock:
            saved = await self.notebook_rpc('save_automation_job', payload=payload())
            self.assertTrue(saved['ok'], saved)
            jid = saved['job_id']
            self.assertTrue((await self.notebook_rpc('list_automation_jobs'))['ok'])
            with patch.object(self.host.automation, '_kick'):
                accepted = await self.notebook_rpc('run_automation_job', job_id=jid)
            self.assertTrue(accepted['ok'], accepted)
            self.assertTrue(accepted['execution']['id'])
            for method, args in [('save_automation_job', dict(payload=dict(payload(), id=jid))),
                                 ('delete_automation_job', dict(job_id=jid)),
                                 ('run_automation_job', dict(job_id=jid))]:
                self.assertFalse((await self.notebook_rpc(method, **args))['ok'])
        self.host.conversation.running = False
        review = Mock(); review.generate_week_review.return_value = dict(ok=True, summary='rpc review')
        self.host.automation.review = review
        await asyncio.to_thread(self.host.automation._loop)
        page = await self.notebook_rpc('list_automation_jobs', seen=True)
        self.assertEqual(page['items'][0]['execution']['result']['summary'], 'rpc review')
        self.assertFalse(page['items'][0]['active'])
        before = a.jobs_path().read_bytes()
        with self.assertRaises(ConnectionClosed):
            async with connect(self.url.replace('test-token', 'bad-token'), proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', method='delete_automation_job', args=dict(job_id=jid))))
                await ws.recv()
        self.assertEqual(a.jobs_path().read_bytes(), before)
        self.assertTrue((await self.notebook_rpc('delete_automation_job', job_id=jid))['ok'])

    async def test_automation_disconnect_keeps_receipt_other_config_and_worker_available(self):
        import threading
        from unittest.mock import Mock
        from tests.test_automation_service import payload
        from desk_companion import automation as a
        self.addCleanup(lambda: self.host.automation.stop(0))
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        review = Mock()
        def publish(doc):
            entered.set()
            if not release.wait(40):
                raise RuntimeError('not released')
            return dict(ok=False, stage='remote', remote_state='unconfirmed', target=doc,
                        summary='retained body', error='远端结果未确认，请核对。')
        review.write_scheduled_review.side_effect = publish
        self.host.automation.review = review
        original = self.host.automation._execute
        def tracked(jid):
            try:
                return original(jid)
            finally:
                finished.set()
        saved = await self.notebook_rpc('save_automation_job', payload=payload(action='retro_write'))
        jid = saved['job_id']
        with patch.object(self.host.automation, '_execute', side_effect=tracked):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', method='run_automation_job', args=dict(job_id=jid))))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    page = await self.notebook_rpc('list_automation_jobs')
                    identity = page['items'][0]['execution']['id']
                    self.assertTrue(page['items'][0]['active'])
                    self.assertFalse((await self.notebook_rpc('delete_automation_job', job_id=jid))['ok'])
                    self.assertTrue((await self.notebook_rpc('save_automation_job', payload=payload(name='other')))['ok'])
                    def write():
                        from desk_companion.resource_lock import WRITES
                        with WRITES.hold(blocking=False), a.transaction():
                            return a.load_store()
                    await asyncio.wait_for(asyncio.to_thread(write), 5)
                    task_id = await asyncio.to_thread(self.host.tasks.submit, '写入测试', 'other')
                    result = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task_id), 20)
                    self.assertEqual(result['state'], 'succeeded', result)
                    phases = [e['data'] for e in result['events'] if e['kind'] == 'worker_phase']
                    self.assertEqual([p['stage'] for p in phases],
                                     ['request_received', 'building_agent', 'agent_ready'])
                    self.assertGreaterEqual(phases[-1]['duration_ms'], 0)
                    await ws.close()
                finally:
                    release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 5))
        page = await self.notebook_rpc('list_automation_jobs')
        row = next(j for j in page['items'] if j['id']==jid)
        self.assertEqual(row['execution']['id'], identity)
        self.assertEqual(row['execution']['result']['remote_state'], 'unconfirmed')
        self.assertEqual(row['execution']['result']['summary'], 'retained body')
        review.write_scheduled_review.assert_called_once_with('token')

    def review_setup(self):
        from contextlib import ExitStack
        from desk_companion import board_data as b
        from tests.test_weekly_review import MONDAY, NOW, DAY, SECTIONS, URL
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(b, 'week_range', return_value=(MONDAY, NOW)))
        stack.enter_context(patch.object(b, 'today_date', return_value=DAY))
        stack.enter_context(patch.object(b, 'fetch_board', return_value=SECTIONS))
        stack.enter_context(patch.object(b, 'fetch_week_feishu', return_value=SECTIONS))
        stack.enter_context(patch('desk_companion.github.format_week_review_text', return_value='output'))
        stack.enter_context(patch('desk_companion.skill_catalog.read_skill', return_value=dict(body='rules')))
        create = stack.enter_context(patch('desk_companion.feishu_auth.create_markdown_doc',
            return_value=dict(ok=True, url=URL, document_id='created')))
        stack.enter_context(patch('desk_companion.feishu_docs.overwrite_markdown_doc', return_value=dict(ok=True, url=URL)))
        opened = stack.enter_context(patch.object(self.host.weekly_review, '_open_document'))
        for name in ('generate_week_review', 'write_today_summary_doc', 'write_week_review_doc'):
            stack.enter_context(patch.object(self.host, name, side_effect=AssertionError('legacy review call'), create=True))
        b.load_today_snapshot(refresh=False)
        b.save_today_fields(summary='old review', summary_at=NOW.isoformat(timespec='seconds'), doc_url='old url')
        return create, opened

    async def test_review_three_rpc_routes_authentication_and_failure_facts(self):
        from desk_companion import board_data as b
        from websockets.exceptions import ConnectionClosed
        from tests.test_weekly_review import URL
        create, opened = self.review_setup()
        self.host.agent.llm = FakeLLM(['new review'])
        generated = await self.notebook_rpc('generate_week_review')
        self.assertTrue(generated['ok'], generated)
        self.assertEqual((generated['summary'], generated['doc_url']), ('new review', ''))
        self.assertTrue((await self.notebook_rpc('write_today_summary_doc'))['ok'])
        opened.assert_called_once_with(URL)
        opened.reset_mock()
        self.assertTrue((await self.notebook_rpc('write_week_review_doc', payload=dict(doc='token')))['ok'])
        opened.assert_not_called()
        opened.side_effect = OSError('shell failure')
        result = await self.notebook_rpc('write_week_review_doc', payload=dict(title='title'))
        self.assertEqual((result['stage'], result['remote_state'], result['url']), ('open', 'confirmed', URL), result)
        self.assertEqual(b._read_today_file()['doc_url'], URL)
        create.side_effect = RuntimeError('timeout')
        result = await self.notebook_rpc('write_today_summary_doc')
        self.assertEqual((result['stage'], result['remote_state']), ('remote', 'unconfirmed'), result)
        before = b.today_path().read_bytes(); count = create.call_count
        with self.assertRaises(ConnectionClosed):
            async with connect(self.url.replace('test-token', 'wrong'), proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', method='write_today_summary_doc')))
                await ws.recv()
        self.assertEqual(b.today_path().read_bytes(), before)
        self.assertEqual(create.call_count, count)

    async def test_review_model_and_publication_disconnect_allow_reads_board_and_workers(self):
        import threading
        from unittest.mock import Mock
        from desk_companion import board_data as b
        from desk_companion.resource_lock import WRITES
        from tests.test_weekly_review import NOW, URL
        create, opened = self.review_setup()
        for method in ('generate_week_review', 'write_today_summary_doc'):
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            self.addCleanup(release.set)
            def slow(*args, **kwargs):
                entered.set()
                if not release.wait(45):
                    raise RuntimeError('not released')
                return dict(message=dict(content='after disconnect')) if method=='generate_week_review' else dict(ok=True, url=URL)
            self.host.agent.llm = Mock(model='fake')
            if method == 'generate_week_review':
                self.host.agent.llm.chat.side_effect = slow
            else:
                create.side_effect = slow
                b.save_today_fields(summary='to publish', summary_at=NOW.isoformat(timespec='seconds'))
            service = self.host.weekly_review
            operation = getattr(service, method)
            def tracked():
                result = operation()
                if not result.get('retryable_busy'):
                    finished.set()
                return result
            with patch.object(service, method, side_effect=tracked):
                async with connect(self.url, proxy=None) as ws:
                    await ws.send(json.dumps(dict(type='rpc', method=method)))
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                        self.assertTrue(self.host.conversation.running)
                        self.assertTrue((await self.notebook_rpc('load_board', refresh=True))['ok'])
                        self.assertTrue((await self.notebook_rpc('load_notebook'))['ok'])
                        for name, args in [('generate_week_review', {}), ('write_today_summary_doc', {}),
                                           ('write_week_review_doc', dict(payload=dict(title='no'))), ('new_notebook', {})]:
                            rejected = await self.notebook_rpc(name, **args)
                            self.assertFalse(rejected['ok'], rejected)
                        acquired = self.host.turn_lock.acquire(blocking=False)
                        self.assertTrue(acquired)
                        if acquired:
                            self.host.turn_lock.release()
                        def write():
                            with WRITES.hold(blocking=False):
                                b.save_today_fields(doc_url='parallel')
                        await asyncio.wait_for(asyncio.to_thread(write), 5)
                        task = await asyncio.to_thread(self.host.tasks.submit, '写入测试', 'other')
                        result = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task), 20)
                        self.assertEqual(result['state'], 'succeeded', result)
                        await ws.close()
                    finally:
                        release.set()
                    self.assertTrue(await asyncio.to_thread(finished.wait, 5))
            self.assertFalse(self.host.conversation.running)
            saved = await self.notebook_rpc('load_board')
            self.assertEqual(saved['summary'], 'after disconnect' if method=='generate_week_review' else 'to publish')
            self.assertEqual(saved['doc_url'], '' if method=='generate_week_review' else URL)
        create.assert_called_once()
        opened.assert_called_once_with(URL)

    async def test_review_host_lock_and_shared_assistant_busy_refuse_immediately(self):
        create, opened = self.review_setup()
        with self.host.turn_lock:
            self.assertTrue((await self.notebook_rpc('generate_week_review'))['retryable_busy'])
            self.assertTrue((await self.notebook_rpc('write_today_summary_doc'))['retryable_busy'])
            self.assertTrue((await self.notebook_rpc('load_board'))['ok'])
        self.host.conversation.running = True
        self.assertTrue((await self.notebook_rpc('write_today_summary_doc'))['retryable_busy'])
        self.assertTrue(self.host.conversation.running)
        self.host.conversation.running = False
        create.assert_not_called(); opened.assert_not_called()

    async def test_board_four_rpc_routes_keep_contracts_and_failure_stages(self):
        from desk_companion import board_data as b
        from tests.test_board_service import DAY, START, END, AGENDA, TASKS
        from websockets.exceptions import ConnectionClosed
        with patch.object(b, 'today_date', return_value=DAY), \
                patch.object(b, 'fetch_board', return_value=dict(agenda=AGENDA, tasks=TASKS)), \
                patch.object(b, '_open_tasks_due_today', return_value=TASKS), \
                patch.object(self.host, 'load_today_board', side_effect=AssertionError('legacy board'), create=True):
            def remote(args):
                if args[:2] == ['calendar', '+agenda']:
                    return json.dumps(dict(ok=True, data=[]))
                return json.dumps(dict(ok=True, data=dict(event_id='created')))
            with patch.object(b, '_run_lark', side_effect=remote) as lark:
                loaded = await self.notebook_rpc('load_board')
                self.assertTrue(loaded['ok'], loaded)
                self.assertTrue((await self.notebook_rpc('create_agenda', summary='title', start=START, end=END))['ok'])
                self.assertTrue((await self.notebook_rpc('delete_agenda', event_id='event'))['ok'])
                self.assertTrue((await self.notebook_rpc('delete_task', guid='task'))['ok'])
                with patch('desk_companion.video.atomic_write', side_effect=OSError('disk')):
                    failed = await self.notebook_rpc('create_agenda', summary='retained', start=START, end=END)
                self.assertEqual((failed['remote_state'], failed['cache_state']), ('confirmed', 'unconfirmed'), failed)
                self.assertEqual(failed['summary'], 'retained')
                self.assertEqual(failed['stage'], 'cache_save')
                lark.reset_mock()
                lark.side_effect = RuntimeError('timeout')
                failed = await self.notebook_rpc('create_agenda', summary='title', start=START, end=END)
                self.assertEqual(failed['remote_state'], 'unconfirmed')
                self.assertEqual(lark.call_count, 1)
                with self.assertRaises(ConnectionClosed):
                    async with connect(self.url.replace('test-token', 'wrong'), proxy=None) as ws:
                        await ws.send(json.dumps(dict(type='rpc', method='delete_task', args=dict(guid='task'))))
                        await ws.recv()
                self.assertEqual(lark.call_count, 1)

    async def test_board_network_disconnect_keeps_cache_models_and_workers_independent(self):
        import threading
        from desk_companion import board_data as b
        from tests.test_board_service import DAY, START, END, AGENDA, TASKS
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        with patch.object(b, 'today_date', return_value=DAY), \
                patch.object(b, 'fetch_board', return_value=dict(agenda=AGENDA, tasks=TASKS)):
            self.assertTrue((await self.notebook_rpc('load_board'))['ok'])
            b.save_today_fields(summary='old', doc_url='old url')
            calls = []
            def remote(args):
                calls.append(args)
                if args[:2] == ['calendar', '+create']:
                    entered.set()
                    if not release.wait(45):
                        raise RuntimeError('not released')
                    return json.dumps(dict(ok=True, data=dict(event_id='new-id')))
                return json.dumps(dict(ok=True, data=[]))
            create = b.create_board_event
            def tracked(*args):
                try:
                    return create(*args)
                finally:
                    finished.set()
            self.host.conversation.running = True
            with patch.object(b, '_run_lark', side_effect=remote), patch.object(b, 'create_board_event', side_effect=tracked):
                async with connect(self.url, proxy=None) as ws:
                    await ws.send(json.dumps(dict(type='rpc', method='create_agenda',
                        args=dict(summary='title', start=START, end=END))))
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                        self.assertTrue((await self.notebook_rpc('load_board'))['ok'])
                        for method, args in [('load_board', dict(refresh=True)),
                                ('delete_agenda', dict(event_id='event')), ('delete_task', dict(guid='task'))]:
                            self.assertEqual((await self.notebook_rpc(method, **args))['stage'], 'admission')
                        acquired = self.host.turn_lock.acquire(blocking=False)
                        self.assertTrue(acquired)
                        if acquired:
                            self.host.turn_lock.release()
                        await asyncio.wait_for(asyncio.to_thread(b.save_today_fields,
                            summary='concurrent summary', doc_url='concurrent url'), 5)
                        task = await asyncio.to_thread(self.host.tasks.submit, '写入测试', 'other')
                        done = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task), 20)
                        self.assertEqual(done['state'], 'succeeded', done)
                        await ws.close()
                    finally:
                        release.set()
                    self.assertTrue(await asyncio.to_thread(finished.wait, 5))
                self.assertTrue(self.host.conversation.running)  # Never took ownership of model busy state.
                self.assertEqual(sum(a[:2] == ['calendar', '+create'] for a in calls), 1)
                loaded = await self.notebook_rpc('load_board')
                self.assertEqual((loaded['summary'], loaded['doc_url']), ('concurrent summary', 'concurrent url'))
                self.assertEqual(loaded['agenda']['items'], [])
            self.host.conversation.running = False

    async def test_knowledge_eight_service_routes_and_authenticated_failure_receipts(self):
        from contextlib import ExitStack
        import io
        from desk_companion import knowledge as k, local_sources
        from websockets.exceptions import ConnectionClosed
        source_path = Path(self.temp.name) / 'source.md'
        source_path.write_text('original body', encoding='utf-8')
        source = local_sources.stage([str(source_path)])['sources'][0]
        doc_id = 'local:' + source['id']
        repo = k.EMBED_MODELS[0]['repo']
        with ExitStack() as stack:
            for name in ('board_knowledge', 'board_save_knowledge', 'board_download_knowledge',
                    'board_delete_model', 'board_add_knowledge', 'board_delete_knowledge',
                    'board_rebuild_knowledge', 'board_ask_knowledge'):
                stack.enter_context(patch.object(self.host, name, side_effect=AssertionError('legacy route'), create=True))
            stack.enter_context(patch.object(k, '_JOBS', {}))
            stack.enter_context(patch.object(k, '_require_models'))
            stack.enter_context(patch.object(k, '_embed', side_effect=lambda texts: [[1., 0.] for _ in texts]))
            stack.enter_context(patch.object(k, '_rerank', side_effect=lambda q, rows: rows))
            stack.enter_context(patch.object(self.host.conversation, 'engage_model'))
            self.assertTrue((await self.notebook_rpc('load_knowledge'))['ok'])
            self.assertTrue((await self.notebook_rpc('save_knowledge', payload=k.default_settings()))['ok'])
            self.assertTrue((await self.notebook_rpc('add_knowledge', doc_id=doc_id, label='source'))['ok'])
            self.assertTrue((await self.notebook_rpc('rebuild_knowledge'))['ok'])
            before = memory.list_chat(self.host.state.session_id)
            self.host.agent.llm = FakeLLM(['strict answer'])
            result = await self.notebook_rpc('ask_knowledge', text='q')
            self.assertEqual(result['trace']['answer'], 'strict answer')
            self.assertEqual(memory.list_chat(self.host.state.session_id), before)
            self.host.agent.llm = FakeLLM([dict(role='assistant', content='invalid', tool_calls=[dict(id='tool')])])
            result = await self.notebook_rpc('ask_knowledge', text='q')
            self.assertEqual(result['stage'], 'validation')
            self.assertEqual(result['answer'], 'invalid')
            with patch('desk_companion.video.atomic_write', side_effect=OSError('disk')):
                result = await self.notebook_rpc('delete_knowledge', doc_id=doc_id)
            self.assertEqual(result['change_state'], 'unconfirmed')
            self.assertTrue((await self.notebook_rpc('delete_knowledge', doc_id=doc_id))['ok'])
            self.assertTrue(source_path.exists())
            class Response(io.BytesIO):
                status = 200
                headers = {}
            with patch.object(k, '_ms_files', return_value=[dict(path='config.json', size=2),
                        dict(path='model.safetensors', size=2)]), \
                    patch('urllib.request.urlopen', side_effect=lambda *a, **kw: Response(b'{}')):
                result = await self.notebook_rpc('download_knowledge', repo=repo)
                self.assertTrue(result['ok'], result)
                for _ in range(200):
                    page = await self.notebook_rpc('load_knowledge')
                    if not page['downloads'][0]['active']:
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(page['downloads'][0]['phase'], 'done')
            # Wait for the worker's lease release after its final progress publication.
            for _ in range(200):
                result = await self.notebook_rpc('delete_model', repo=repo)
                if result['ok']:
                    break
                await asyncio.sleep(.01)
            self.assertTrue(result['ok'], result)
            with self.assertRaises(ConnectionClosed):
                async with connect(self.url.replace('test-token', 'wrong'), proxy=None) as ws:
                    await ws.send(json.dumps(dict(type='rpc', method='save_knowledge',
                        args=dict(payload=k.default_settings()))))
                    await ws.recv()

    async def test_knowledge_compute_disconnect_keeps_reads_workers_and_busy_guards(self):
        import threading
        from desk_companion import knowledge as k, local_sources
        source_path = Path(self.temp.name) / 'source.md'
        source_path.write_text('original body', encoding='utf-8')
        source = local_sources.stage([str(source_path)])['sources'][0]
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def embed(texts):
            entered.set(); calls.append(texts)
            if not release.wait(45):
                raise RuntimeError('not released')
            return [[1., 0.] for _ in texts]
        original = k.add_doc
        def add(*args):
            try:
                return original(*args)
            finally:
                finished.set()
        with patch.object(k, '_require_models'), patch.object(k, '_embed', side_effect=embed), \
                patch.object(k, 'add_doc', side_effect=add):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', method='add_knowledge',
                    args=dict(doc_id='local:'+source['id'], label='source'))))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    self.assertTrue((await self.notebook_rpc('load_knowledge'))['ok'])
                    for method, args in [('save_knowledge', dict(payload=k.default_settings())),
                            ('add_knowledge', dict(doc_id='x', label='x')), ('delete_knowledge', dict(doc_id='x')),
                            ('rebuild_knowledge', {}), ('ask_knowledge', dict(text='q')),
                            ('download_knowledge', dict(repo=k.EMBED_MODELS[0]['repo'])),
                            ('delete_model', dict(repo=k.EMBED_MODELS[0]['repo']))]:
                        self.assertEqual((await self.notebook_rpc(method, **args))['change_state'], 'not_changed')
                    acquired = self.host.turn_lock.acquire(blocking=False)
                    self.assertTrue(acquired)
                    if acquired:
                        self.host.turn_lock.release()
                    task = await asyncio.to_thread(self.host.tasks.submit, '写入测试', 'other')
                    done = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task), 20)
                    self.assertEqual(done['state'], 'succeeded', done)
                    # A separate storage writer can progress; add must reject its stale snapshot.
                    await asyncio.wait_for(asyncio.to_thread(k.save_settings,
                        dict(k.default_settings(), retrieve_k=21)), 5)
                    await ws.close()
                finally:
                    release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 5))
            for _ in range(100):
                if not self.host.conversation.running:
                    break
                await asyncio.sleep(.01)
            self.assertFalse(self.host.conversation.running)
            self.assertEqual(len(calls), 1)
            self.assertEqual((await self.notebook_rpc('load_knowledge'))['docs'], [])

    async def test_knowledge_host_lock_and_pet_busy_immediately_refuse_admission(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        def hold():
            with self.host.turn_lock:
                entered.set(); release.wait(10)
        thread = threading.Thread(target=hold); thread.start()
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            self.assertTrue((await self.notebook_rpc('load_knowledge'))['ok'])
            self.assertFalse((await self.notebook_rpc('rebuild_knowledge'))['ok'])
        finally:
            release.set(); await asyncio.to_thread(thread.join, 5)
        self.host.conversation.running = True
        self.assertFalse((await self.notebook_rpc('rebuild_knowledge'))['ok'])
        self.host.conversation.running = False

    async def test_notebook_generation_service_rpc_preserves_protocol_and_failure_phases(self):
        from contextlib import ExitStack
        from desk_companion import knowledge, notebook
        from websockets.exceptions import ConnectionClosed
        cites = [dict(n=1, doc='source', title='section', text='original', doc_id='doc-1')]
        with ExitStack() as stack:
            stack.enter_context(patch.object(knowledge, 'public_view', return_value=dict(docs=[])))
            stack.enter_context(patch.object(knowledge, 'ask_sources', side_effect=lambda q, ids, complete:
                dict(answer=complete('system', q), cites=cites)))
            stack.enter_context(patch.object(self.host.conversation, 'engage_model'))
            launch = stack.enter_context(patch('os.startfile', side_effect=OSError('no handler')))
            stack.enter_context(patch.object(self.host, 'run_notebook', side_effect=AssertionError('legacy generation'), create=True))
            stack.enter_context(patch.object(self.host, 'board_summarize_notebook', side_effect=AssertionError('legacy summary'), create=True))
            sid = (await self.notebook_rpc('new_notebook'))['session_id']
            self.host.agent.llm = FakeLLM(['complete answer [1]'])
            tokens = []
            result = await self.notebook_question(sid, tokens)
            self.assertEqual(result['type'], 'done', result)
            self.assertEqual(json.loads(result['data']), dict(answer='complete answer [1]', cites=cites))
            self.assertEqual(''.join(tokens), 'complete answer [1]')
            nid = (await self.notebook_rpc('save_notebook_note', session_id=sid,
                question='question', answer='answer [1]', cites=cites))['note_id']
            self.host.agent.llm = FakeLLM(['summary [1]'])
            result = await self.notebook_rpc('summarize_notebook', session_id=sid, note_ids=[nid], sampling={})
            self.assertEqual(result['stage'], 'open', result)
            self.assertEqual(result['answer'], 'summary [1]')
            self.assertTrue(Path(result['path']).is_file())
            self.assertTrue(result['note_id'])
            launch.assert_called_once()
            self.host.agent.llm = FakeLLM([dict(role='assistant', content='wrong tool body',
                tool_calls=[dict(id='tool')])])
            result = await self.notebook_question(sid)
            self.assertEqual(result['type'], 'error')
            self.assertIsInstance(result['data'], str)
            self.assertEqual(result['result']['stage'], 'validation')
            self.assertFalse(result['result']['answer_complete'])
            self.host.agent.llm = FakeLLM(['full answer [1]'])
            original = notebook._write
            writes = 0
            def write(book):
                nonlocal writes
                writes += 1
                original(book)
                if writes == 2:
                    raise OSError('answer ack lost')
            with patch.object(notebook, '_write', side_effect=write):
                result = await self.notebook_question(sid)
            self.assertEqual(result['result']['stage'], 'answer_save', result)
            self.assertEqual(result['result']['change_state'], 'unconfirmed')
            self.assertEqual(result['result']['answer'], 'full answer [1]')
            self.assertFalse(self.host.conversation.running)
            before = notebook._path().read_bytes()
            with self.assertRaises(ConnectionClosed):
                async with connect(f'ws://127.0.0.1:{self.port}/ws?token=wrong', proxy=None) as ws:
                    await ws.send(json.dumps(dict(type='notebook', session_id=sid, text='no', doc_ids=['doc-1'],
                        sampling=dict(reasoning_effort='low', temperature=.5, top_p=1))))
                    await ws.recv()
            self.assertEqual(notebook._path().read_bytes(), before)

    async def test_notebook_generation_disconnect_finishes_once_without_blocking_reads_or_workers(self):
        import threading
        from contextlib import ExitStack
        from desk_companion import knowledge, notebook
        cites = [dict(n=1, doc='source', title='section', text='original', doc_id='doc-1')]
        with ExitStack() as stack:
            stack.enter_context(patch.object(knowledge, 'public_view', return_value=dict(docs=[])))
            stack.enter_context(patch.object(knowledge, 'ask_sources', side_effect=lambda q, ids, complete:
                dict(answer=complete('system', q), cites=cites)))
            stack.enter_context(patch.object(self.host.conversation, 'engage_model'))
            launch = stack.enter_context(patch('os.startfile'))
            sid = (await self.notebook_rpc('new_notebook'))['session_id']
            nid = (await self.notebook_rpc('save_notebook_note', session_id=sid,
                question='question', answer='answer [1]', cites=cites))['note_id']
            for mode in ('question', 'summary'):
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                def model(*args, **kwargs):
                    if kwargs.get('on_delta'):
                        kwargs['on_delta']('partial ')
                    entered.set()
                    if not release.wait(45):
                        raise RuntimeError('fixture not released')
                    return dict(message=dict(role='assistant', content='final body [1]'), usage={})
                with patch.object(self.host.agent.llm, 'chat', side_effect=model) as call:
                    async with connect(self.url, proxy=None) as ws:
                        request = (dict(type='notebook', session_id=sid, text='question', doc_ids=['doc-1'],
                            sampling=dict(reasoning_effort='low', temperature=.5, top_p=1)) if mode == 'question' else
                            dict(type='rpc', method='summarize_notebook', args=dict(session_id=sid, note_ids=[nid], sampling={})))
                        await ws.send(json.dumps(request))
                        try:
                            self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                            self.assertTrue((await self.notebook_rpc('load_notebook'))['ok'])
                            rejected_question = await self.notebook_question(sid)
                            self.assertEqual(rejected_question['type'], 'error')
                            self.assertEqual(rejected_question['result']['stage'], 'admission')
                            rejected = await self.notebook_rpc('summarize_notebook', session_id=sid, note_ids=[nid], sampling={})
                            self.assertEqual(rejected['change_state'], 'not_changed')
                            self.assertEqual(rejected['stage'], 'admission')
                            acquired = self.host.turn_lock.acquire(blocking=False)
                            self.assertTrue(acquired)
                            if acquired:
                                self.host.turn_lock.release()
                            task = await asyncio.to_thread(self.host.tasks.submit, '写入测试', 'other')
                            done = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task), 20)
                            self.assertEqual(done['state'], 'succeeded', done)
                            await asyncio.wait_for(asyncio.to_thread(notebook.begin_notebook_turn, sid, 'concurrent'), 5)
                            await ws.close()
                            self.assertTrue(self.host.conversation.running)
                        finally:
                            release.set()
                    for _ in range(100):
                        if not self.host.conversation.running:
                            break
                        await asyncio.sleep(.05)
                    self.assertFalse(self.host.conversation.running)
                    self.assertEqual(call.call_count, 1)
            self.assertEqual(launch.call_count, 1)
            book = notebook._find(notebook._read(), sid)
            self.assertEqual(len(book['notes']), 2)
            self.assertEqual(sum(t['role'] == 'pet' for t in book['turns']), 1)
            self.assertEqual(Path(self.temp.name, 'write_count').read_text(), '2')
            self.assertTrue((await self.notebook_rpc('load_notebook'))['ok'])

    async def test_notebook_reads_and_busy_writes_during_question_summary_and_compression(self):
        import threading
        from contextlib import ExitStack
        from desk_companion import knowledge, notebook
        cites = [dict(n=1, doc='source', title='section', text='original', doc_id='doc-1')]
        original_session = self.host.state.session_id
        for role, text in (('user', 'old question'), ('pet', 'old answer'),
                           ('user', 'latest question'), ('pet', 'latest answer')):
            memory.append_chat(role, text, original_session)

        async def question(sid):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='notebook', session_id=sid, text='question',
                                             doc_ids=['doc-1'], sampling=dict(reasoning_effort='low', temperature=.5, top_p=1))))
                while True:
                    frame = json.loads(await asyncio.wait_for(ws.recv(), 45))
                    if frame['type'] in ('done', 'error'):
                        return frame

        with ExitStack() as stack:
            stack.enter_context(patch.object(knowledge, 'public_view', return_value=dict(docs=[])))
            stack.enter_context(patch.object(knowledge, 'ask_sources', side_effect=lambda q, ids, complete:
                                           dict(answer=complete('system', q), cites=cites)))
            stack.enter_context(patch.object(self.host.conversation, 'engage_model'))
            stack.enter_context(patch('os.startfile'))
            for name in ('board_load_notebook', 'board_new_notebook', 'board_save_notebook_note',
                         'board_delete_notebook_note'):
                stack.enter_context(patch.object(self.host, name, side_effect=AssertionError('legacy notebook call'), create=True))
            sid = (await self.notebook_rpc('new_notebook'))['session_id']
            nid = (await self.notebook_rpc('save_notebook_note', session_id=sid,
                                         question='saved question', answer='saved answer [1]', cites=cites))['note_id']
            for mode in ('question', 'summary', 'compression'):
                with self.subTest(mode=mode):
                    entered, release = threading.Event(), threading.Event()
                    self.addCleanup(release.set)
                    def model(*args, **kwargs):
                        entered.set()
                        if not release.wait(45):
                            raise RuntimeError('fixture model was not released')
                        return dict(message=dict(role='assistant', content='new answer [1]'), usage={})
                    with patch.object(self.host.agent.llm, 'chat', side_effect=model) as call:
                        if mode == 'question':
                            pending = asyncio.create_task(question(sid))
                        elif mode == 'summary':
                            # This call legitimately waits for the fake model; other RPCs must not.
                            async def summarize():
                                async with connect(self.url, proxy=None) as ws:
                                    await ws.send(json.dumps(dict(type='rpc', id='summary', method='summarize_notebook',
                                        args=dict(session_id=sid, note_ids=[nid], sampling={})) ))
                                    frame = json.loads(await asyncio.wait_for(ws.recv(), 45))
                                    return frame['result']['result']
                            pending = asyncio.create_task(summarize())
                        else:
                            async def compress():
                                async with connect(self.url, proxy=None) as ws:
                                    await ws.send(json.dumps(dict(type='rpc', id='compress', method='compress_context')))
                                    frame = json.loads(await asyncio.wait_for(ws.recv(), 45))
                                    return frame['result']['result']
                            pending = asyncio.create_task(compress())
                        try:
                            if not await asyncio.to_thread(entered.wait, 5):
                                self.fail('model was not reached: ' + str(await pending))
                            before = notebook._path().read_bytes()
                            self.assertTrue((await self.notebook_rpc('load_notebook'))['ok'])
                            for method, args in [('new_notebook', {}),
                                    ('save_notebook_note', dict(session_id=sid, question='no', answer='no', cites=cites)),
                                    ('delete_notebook_note', dict(session_id=sid, note_id=nid)),
                                    ('export_notebook_markdown', dict(session_id=sid, note_id=nid)),
                                    ('export_notebook_feishu', dict(session_id=sid, note_id=nid)),
                                    ('open_notebook_file', dict(session_id=sid, note_id=nid, name='unused.md')),
                                    ('reveal_notebook_file', dict(session_id=sid, note_id=nid, name='unused.md')),
                                    ('delete_notebook_file', dict(session_id=sid, note_id=nid, name='unused.md'))]:
                                result = await self.notebook_rpc(method, **args)
                                self.assertFalse(result['ok'], result)
                                self.assertEqual(result['change_state'], 'not_changed')
                            self.assertEqual(notebook._path().read_bytes(), before)
                            self.assertTrue(self.host.conversation.running)
                            if mode == 'question':
                                task = await asyncio.to_thread(self.host.tasks.submit, '写入测试', 'other')
                                done = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task), 20)
                                self.assertEqual(done['state'], 'succeeded', done)
                                self.assertEqual(Path(self.temp.name, 'write_count').read_text(), '1')
                                self.assertTrue((await self.notebook_rpc('load_notebook'))['ok'])
                        finally:
                            release.set()
                        result = await pending
                        if mode == 'question':
                            self.assertEqual(result['type'], 'done', result)
                        else:
                            self.assertTrue(result['ok'], result)
                        self.assertEqual(call.call_count, 1)
                    self.assertFalse(self.host.conversation.running)
            # Rejected operations did not queue behind the models and execute later.
            book = notebook._read()
            self.assertEqual(len(book['sessions']), 1)
            self.assertEqual(len(book['sessions'][0]['notes']), 2)  # Saved note and summary only.
            self.assertEqual(len(book['sessions'][0]['turns']), 2)
            self.assertEqual(self.host.state.session_id, original_session)

    async def test_notebook_output_rpc_success_partial_failure_and_authentication(self):
        from contextlib import ExitStack
        from desk_companion import knowledge, notebook
        from websockets.exceptions import ConnectionClosed
        cites = [dict(n=1, doc='source', title='section', text='original', doc_id='doc-1')]
        with ExitStack() as stack:
            stack.enter_context(patch.object(knowledge, 'public_view', return_value=dict(docs=[])))
            launch = stack.enter_context(patch('os.startfile'))
            reveal = stack.enter_context(patch('subprocess.Popen'))
            create = stack.enter_context(patch('desk_companion.feishu_auth.create_markdown_doc',
                return_value=dict(ok=True, url='https://example.invalid/doc', document_id='cloud-1')))
            sid = (await self.notebook_rpc('new_notebook'))['session_id']
            nid = (await self.notebook_rpc('save_notebook_note', session_id=sid,
                question='question', answer='answer', cites=cites))['note_id']
            args = dict(session_id=sid, note_id=nid)
            # Bridge resolves services directly; inherited facades remain compatibility only.
            for method in ('export_notebook_markdown', 'export_notebook_feishu', 'open_notebook_file',
                           'reveal_notebook_file', 'delete_notebook_file'):
                stack.enter_context(patch.object(self.host, 'board_' + method, side_effect=AssertionError('legacy call'), create=True))
            saved = await self.notebook_rpc('export_notebook_markdown', **args)
            path = Path(saved['path'])
            self.assertTrue(saved['ok'], saved)
            self.assertIn('original', path.read_text(encoding='utf-8'))
            for method in ('open_notebook_file', 'reveal_notebook_file'):
                result = await self.notebook_rpc(method, **args, name=path.name)
                self.assertTrue(result['ok'], result)
            reveal.assert_called_once_with(['explorer', f'/select,{path}'])
            launch.side_effect = OSError('no handler')
            result = await self.notebook_rpc('export_notebook_feishu', **args)
            self.assertEqual(result['stage'], 'open')
            self.assertEqual(result['document_id'], 'cloud-1')
            self.assertIn(result['url'], result['error'])
            create.side_effect = TimeoutError('cloud timeout')
            result = await self.notebook_rpc('export_notebook_feishu', **args)
            self.assertEqual(result['change_state'], 'unconfirmed')
            self.assertEqual(create.call_count, 2)
            with self.host.turn_lock:
                for method in ('export_notebook_markdown', 'export_notebook_feishu', 'open_notebook_file',
                               'reveal_notebook_file', 'delete_notebook_file'):
                    kw = dict(args, name=path.name) if method.endswith('_file') else args
                    result = await self.notebook_rpc(method, **kw)
                    self.assertEqual(result['change_state'], 'not_changed', result)
            with patch.object(notebook, '_write', side_effect=OSError('registry failed')):
                result = await self.notebook_rpc('delete_notebook_file', **args, name=path.name)
            self.assertEqual(result['stage'], 'registration')
            self.assertFalse(path.exists())
            with patch.object(knowledge, 'public_view', side_effect=RuntimeError('refresh failed')):
                result = await self.notebook_rpc('delete_notebook_file', **args, name=path.name)
            self.assertEqual(result['change_state'], 'committed', result)
            self.assertEqual(result['steps']['deletion'], 'already_missing')
            self.assertEqual(notebook._find(notebook._read(), sid)['notes'][0]['files'], [])
            self.assertFalse(self.host.conversation.running)
            before = notebook._path().read_bytes()
            with self.assertRaises(ConnectionClosed):
                async with connect(f'ws://127.0.0.1:{self.port}/ws?token=invalid', proxy=None) as ws:
                    await ws.send(json.dumps(dict(type='rpc', method='export_notebook_feishu', args=args)))
                    await ws.recv()
            self.assertEqual(create.call_count, 2)
            self.assertEqual(notebook._path().read_bytes(), before)

    async def test_notebook_external_wait_does_not_hold_host_storage_or_global_write_lock(self):
        import threading
        from desk_companion import knowledge, notebook
        cites = [dict(n=1, doc='source', title='section', text='original', doc_id='doc-1')]
        with patch.object(knowledge, 'public_view', return_value=dict(docs=[])), patch('os.startfile'):
            sid = (await self.notebook_rpc('new_notebook'))['session_id']
            nid = (await self.notebook_rpc('save_notebook_note', session_id=sid,
                question='question', answer='answer', cites=cites))['note_id']
            entered, release = threading.Event(), threading.Event()
            self.addCleanup(release.set)
            def create(*args):
                entered.set()
                if not release.wait(45):
                    raise RuntimeError('fixture not released')
                return dict(ok=True, url='https://example.invalid/doc')
            with patch('desk_companion.feishu_auth.create_markdown_doc', side_effect=create):
                pending = asyncio.create_task(self.notebook_rpc('export_notebook_feishu',
                    _timeout=45, session_id=sid, note_id=nid))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    self.assertTrue((await self.notebook_rpc('load_notebook'))['ok'])
                    self.assertEqual((await self.notebook_rpc('new_notebook'))['change_state'], 'not_changed')
                    acquired = self.host.turn_lock.acquire(blocking=False)
                    self.assertTrue(acquired)
                    if acquired:
                        self.host.turn_lock.release()
                    # Separate real worker needs WRITES for its tool, while notebook writer needs its own lock.
                    task = await asyncio.to_thread(self.host.tasks.submit, '写入测试', 'other')
                    done = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task), 20)
                    self.assertEqual(done['state'], 'succeeded', done)
                    self.assertEqual(Path(self.temp.name, 'write_count').read_text(), '1')
                    await asyncio.wait_for(asyncio.to_thread(notebook.begin_notebook_turn, sid, 'concurrent turn'), 5)
                    self.assertTrue(self.host.conversation.running)
                finally:
                    release.set()
                result = await pending
            self.assertTrue(result['ok'], result)
            self.assertFalse(self.host.conversation.running)

    async def test_notebook_rpc_failure_phases_host_admission_and_authentication(self):
        from desk_companion import knowledge, notebook
        from websockets.exceptions import ConnectionClosed
        cites = [dict(n=1, doc='source', title='section', text='original', doc_id='doc-1')]
        with patch.object(knowledge, 'public_view', return_value=dict(docs=[])):
            sid = (await self.notebook_rpc('new_notebook'))['session_id']
            other = (await self.notebook_rpc('new_notebook'))['session_id']
            with self.host.turn_lock:
                result = await self.notebook_rpc('new_notebook')
                self.assertEqual(result['change_state'], 'not_changed')
                self.assertTrue((await self.notebook_rpc('load_notebook'))['ok'])
            self.assertFalse(self.host.conversation.running)
            with patch.object(notebook, '_write', side_effect=OSError('write failed')):
                result = await self.notebook_rpc('save_notebook_note', session_id=sid,
                    question='question', answer='answer', cites=cites)
            self.assertEqual(result['change_state'], 'unconfirmed', result)
            with patch.object(knowledge, 'public_view', side_effect=RuntimeError('refresh failed')):
                result = await self.notebook_rpc('save_notebook_note', session_id=sid,
                    question='question', answer='answer', cites=cites)
            self.assertEqual(result['change_state'], 'committed', result)
            nid = result['note_id']
            missing = await self.notebook_rpc('delete_notebook_note', session_id=other, note_id=nid)
            self.assertEqual(missing['change_state'], 'not_changed')
            with patch.object(knowledge, 'public_view', side_effect=RuntimeError('refresh failed')):
                result = await self.notebook_rpc('delete_notebook_note', session_id=sid, note_id=nid)
            self.assertEqual(result['change_state'], 'committed', result)
            self.assertEqual(notebook._find(notebook._read(), sid)['notes'], [])
            self.assertTrue((await self.notebook_rpc('new_notebook'))['ok'])
        with self.assertRaises(ConnectionClosed):
            async with connect(f'ws://127.0.0.1:{self.port}/ws?token=invalid', proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', method='load_notebook')))
                await ws.recv()

    async def test_compression_rpc_keeps_reads_other_workers_and_cancellation_available(self):
        from contextlib import ExitStack
        import threading
        sid = self.host.state.session_id
        for role, text in (('user', 'old question'), ('pet', 'old answer'),
                           ('user', 'latest question'), ('pet', 'latest answer')):
            memory.append_chat(role, text, sid)
        before = memory.list_chat(sid)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def model(*args, **kwargs):
            entered.set()
            if not release.wait(40):
                raise RuntimeError('test did not release compression')
            return dict(message=dict(role='assistant', content='RPC summary'),
                        usage=dict(prompt_tokens=10, completion_tokens=5))

        async def rpc(method, **args):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args)))
                envelope = json.loads(await asyncio.wait_for(ws.recv(), 30 if method == 'compress_context' else 5))['result']
            self.assertTrue(envelope['ok'], envelope)
            return envelope['result']

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.host.conversation, 'engage_model'))
            stack.enter_context(patch.object(self.host.agent.llm, 'chat', side_effect=model))
            for method in ('board_compress_context', 'prepare_context', 'board_memory'):
                stack.enter_context(patch.object(self.host, method, side_effect=AssertionError('inherited method'), create=True))
            pending = asyncio.create_task(rpc('compress_context'))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self.assertTrue((await rpc('load_memory'))['ok'])
                self.assertEqual((await rpc('load_chat_log'))['items'], before)
                self.assertFalse((await rpc('compress_context'))['ok'])
                for method, args in (('new_chat_session', {}), ('switch_chat_session', dict(session_id='other')),
                                     ('clear_chat', {}), ('add_fact', dict(text='busy')),
                                     ('update_fact', dict(fact_id='missing', text='busy')),
                                     ('delete_fact', dict(fact_id='missing')),
                                     ('delete_memory_turn', dict(role='user', text='old question')),
                                     ('save_persona', dict(payload=dict(persona='new', max_steps=5, nudge_enabled=True))),
                                     ('use_model', dict(model_id='other')),
                                     ('save_model_network', dict(payload=dict(mode='direct')))):
                    self.assertFalse((await rpc(method, **args))['ok'], method)
                async with connect(self.url, proxy=None) as ws:
                    await ws.send(json.dumps(dict(type='chat', text='must not queue', chips={},
                        sampling=dict(reasoning_effort='low', temperature=.5, top_p=1))))
                    frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
                self.assertEqual(frame['type'], 'error')
                self.assertIn('整理上下文', frame['data'])
                # A real independent worker executes a WRITES-protected tool while compression waits.
                task = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.submit, '写入测试', 'other'), 5)
                await rpc('list_agent_tasks')
                done = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task), 20)
                self.assertEqual(done['state'], 'succeeded', done)
                self.assertEqual(Path(self.temp.name, 'write_count').read_text(), '1')
                self.assertTrue(memory.list_chat('other'))
                # Cancellation must acquire the same task condition lock, not wait for the model.
                cancel_task = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.submit, 'cancel me', 'third'), 5)
                cancelled = await rpc('cancel_agent_task', task_id=cancel_task)
                self.assertTrue(cancelled['ok'], cancelled)
                self.assertEqual(memory.list_chat(sid), before)
            finally:
                release.set()
            result = await pending
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['context']['summary'], 'RPC summary')
        self.assertFalse(self.host.conversation.running)
        self.assertEqual(memory.list_chat(sid), before)

    async def test_compression_disconnect_finishes_once_and_can_be_read_after_reconnect(self):
        import threading
        from unittest.mock import Mock
        sid = self.host.state.session_id
        for role, text in (('user', 'old question'), ('pet', 'old answer'),
                           ('user', 'latest question'), ('pet', 'latest answer')):
            memory.append_chat(role, text, sid)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def model(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError('test did not release disconnected compression')
            return dict(message=dict(content='saved after disconnect'), usage={})
        call = Mock(side_effect=model)
        with patch.object(self.host.conversation, 'engage_model'), patch.object(self.host.agent.llm, 'chat', call):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id='compress', method='compress_context')))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    await ws.close()
                finally:
                    release.set()
            for _ in range(100):
                if not self.host.conversation.running:
                    break
                await asyncio.sleep(.05)
            self.assertFalse(self.host.conversation.running)
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id='read', method='load_memory')))
                envelope = json.loads(await asyncio.wait_for(ws.recv(), 5))['result']
            self.assertEqual(envelope['result']['context']['summary'], 'saved after disconnect')
            self.assertEqual(call.call_count, 1)
        with self.host.tasks.reserve_session(sid):
            pass  # Disconnected caller did not leak the session reservation.

    async def test_compression_rpc_distinguishes_uncertain_save_and_committed_refresh_failure(self):
        from desk_companion import context_compression, context_pack
        sid = self.host.state.session_id
        for role, text in (('user', 'old question'), ('pet', 'old answer'),
                           ('user', 'latest question'), ('pet', 'latest answer')):
            memory.append_chat(role, text, sid)
        async def compress():
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id='compress', method='compress_context')))
                envelope = json.loads(await asyncio.wait_for(ws.recv(), 5))['result']
            self.assertTrue(envelope['ok'], envelope)
            return envelope['result']
        with patch.object(self.host.conversation, 'engage_model'), patch.object(self.host.agent.llm, 'chat',
                return_value=dict(message=dict(content='saved summary'), usage={} )):
            with patch.object(context_compression, 'save_pack', side_effect=OSError('save failed')):
                result = await compress()
            self.assertEqual(result['change_state'], 'unconfirmed')
            self.assertEqual(context_pack.context_view(sid, 'system')['summary'], '')
            with patch.object(self.host.chat_sessions, 'load_memory', return_value=dict(ok=False, error='refresh failed')):
                result = await compress()
            self.assertEqual(result['change_state'], 'committed')
            self.assertEqual(context_pack.context_view(sid, 'system')['summary'], 'saved summary')
        self.assertFalse(self.host.conversation.running)

    async def test_compression_rejects_occupied_host_without_waiting_or_calling_model(self):
        from unittest.mock import Mock
        call = Mock()
        self.host.agent.llm.chat = call
        with self.host.turn_lock:
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id='compress', method='compress_context')))
                envelope = json.loads(await asyncio.wait_for(ws.recv(), 5))['result']
        self.assertTrue(envelope['ok'], envelope)
        self.assertEqual(envelope['result']['change_state'], 'not_changed')
        call.assert_not_called()
        self.assertFalse(self.host.conversation.running)

    async def test_compression_rejects_current_queue_without_calling_model(self):
        from unittest.mock import Mock
        from desk_companion.tasks import TaskManager
        self.host.tasks.shutdown()
        with patch.object(TaskManager, '_schedule', return_value=None):
            manager = TaskManager(self.host)
        self.host._tasks = manager
        self.addCleanup(manager.shutdown)
        sid = self.host.state.session_id
        for role, text in (('user', 'old question'), ('pet', 'old answer'),
                           ('user', 'latest question'), ('pet', 'latest answer')):
            memory.append_chat(role, text, sid)
        task = manager.submit('pending request', sid)
        model = Mock(return_value=dict(message=dict(content='summary'), usage={}))
        self.host.agent.llm.chat = model
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps(dict(type='rpc', id='compress', method='compress_context')))
            envelope = json.loads(await asyncio.wait_for(ws.recv(), 10))['result']
        self.assertTrue(envelope['ok'], envelope)
        self.assertFalse(envelope['result']['ok'], envelope)
        model.assert_not_called()
        self.assertEqual(manager.get(task)['state'], 'queued')

    async def test_memory_editing_rpc_uses_service_and_preserves_task_boundaries(self):
        from contextlib import ExitStack
        import threading
        from desk_companion import context_pack, facts
        from desk_companion.tasks import TaskManager
        self.host.tasks.shutdown()
        with patch.object(TaskManager, '_schedule', return_value=None):
            manager = TaskManager(self.host)
        self.host._tasks = manager
        self.addCleanup(manager.shutdown)
        sid = self.host.state.session_id
        for role, text in (('user', 'remove me'), ('pet', 'old answer'),
                           ('user', 'keep me'), ('pet', 'latest answer')):
            memory.append_chat(role, text, sid)
        memory.append_chat('user', 'remove me', 'other')
        context_pack.prepare_injection(sid, 'system', '', exclude_user=None, manual=True,
                                       complete=lambda _: 'summary contains remove me')

        async def rpc(method, **args):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args)))
                envelope = json.loads(await asyncio.wait_for(ws.recv(), 10))['result']
            self.assertTrue(envelope['ok'], envelope)
            return envelope['result']

        with ExitStack() as stack:
            for method in ('board_add_fact', 'board_update_fact', 'board_delete_fact',
                           'board_drop_memory_turn', 'board_memory'):
                stack.enter_context(patch.object(self.host, method, side_effect=AssertionError('inherited method'), create=True))
            result = await rpc('add_fact', text='global fact')
            self.assertTrue(result['ok'], result)
            self.assertEqual(set(result), {'ok', 'total', 'fact_limit', 'facts', 'context', 'items'})
            fact_id = result['facts'][0]['id']
            task = manager.submit('pending', sid)
            for status in ('queued', 'running', 'cancelling'):
                manager.update(task, state=status)
                rejected = await rpc('delete_memory_turn', role='user', text='remove me')
                self.assertEqual(rejected['change_state'], 'not_changed')
                self.assertTrue((await rpc('update_fact', fact_id=fact_id, text=status))['ok'])
                self.assertEqual(manager.get(task)['state'], status)
            manager.update(task, state='failed')
            manager.active[task] = dict(session=sid, cancel=threading.Event())
            try:
                self.assertFalse((await rpc('delete_memory_turn', role='user', text='remove me'))['ok'])
                self.assertTrue((await rpc('delete_fact', fact_id=fact_id))['ok'])
            finally:
                manager.active.pop(task)
            other = manager.submit('unrelated', 'other')
            self.host.conversation.running = True
            for method, args in (('add_fact', dict(text='busy')), ('update_fact', dict(fact_id=fact_id, text='busy')),
                                 ('delete_fact', dict(fact_id=fact_id)),
                                 ('delete_memory_turn', dict(role='user', text='remove me'))):
                self.assertFalse((await rpc(method, **args))['ok'])
            self.host.conversation.running = False
            result = await rpc('delete_memory_turn', role='user', text='remove me')
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['total'], 3)
            self.assertEqual(result['context']['summary'], '')
            self.assertEqual(memory.list_chat('other')[0]['text'], 'remove me')
            self.assertEqual(manager.get(other)['state'], 'queued')
            self.assertEqual(len(manager.list()['items']), 2)
            with patch.object(self.host.chat_sessions, 'load_memory', return_value=dict(ok=False, error='snapshot failed')):
                result = await rpc('add_fact', text='saved despite snapshot failure')
            self.assertEqual(result['change_state'], 'committed')
            self.assertEqual(facts.read_facts()[0]['text'], 'saved despite snapshot failure')

    async def test_memory_delete_preserves_history_when_summary_invalidation_fails(self):
        from desk_companion import context_pack
        sid = self.host.state.session_id
        for role, text in (('user', 'remove me'), ('pet', 'old answer'),
                           ('user', 'latest question'), ('pet', 'latest answer')):
            memory.append_chat(role, text, sid)
        context_pack.prepare_injection(sid, 'system', '', exclude_user=None, manual=True,
                                       complete=lambda _: 'summary contains remove me')
        before = memory.list_chat(sid)
        with patch.object(context_pack, 'drop_pack_if_covered', side_effect=OSError('summary write failed')):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id='delete', method='delete_memory_turn',
                                              args=dict(role='user', text='remove me'))))
                result = json.loads(await asyncio.wait_for(ws.recv(), 10))['result']['result']
        self.assertFalse(result['ok'])
        self.assertEqual(memory.list_chat(sid), before)
        self.assertEqual(result['change_state'], 'unconfirmed')

    async def test_first_access_initializes_tasks_for_rpc_and_chat(self):
        from desk_companion.tasks import TaskManager
        command = [sys.executable, '-u', '-c', "exec(open('tests/worker_fixture.py', encoding='utf-8').read())"]

        async def rpc(method):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method)))
                envelope = json.loads(await asyncio.wait_for(ws.recv(), 10))['result']
            self.assertTrue(envelope['ok'], envelope)
            self.assertTrue(envelope['result']['ok'], envelope)
            return envelope['result']

        def create_manager():
            manager = TaskManager(command=command)
            self.addCleanup(manager.shutdown)
            return manager

        for first in ('clear_chat', 'chat'):
            with self.subTest(first=first):
                if self.host._tasks is not None:
                    self.host._tasks.shutdown()
                memory.clear_session(self.host.state.session_id)
                self.host._tasks = None
                with patch('desk_companion.tasks.TaskManager', side_effect=create_manager) as constructor:
                    if first == 'clear_chat':
                        await rpc('clear_chat')
                    async with connect(self.url, proxy=None) as ws:
                        await ws.send(json.dumps(dict(type='chat', text='first request', chips={},
                            sampling=dict(reasoning_effort='low', temperature=.5, top_p=1))))
                        while True:
                            frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
                            if frame['type'] in ('done', 'error'):
                                break
                    self.assertEqual(frame, dict(type='done', data='本地流式回归正常。'))
                    self.assertEqual(len(memory.list_chat(self.host.state.session_id)), 2)
                    self.assertEqual(len((await rpc('list_agent_tasks'))['items']), 1 + (first == 'chat'))
                    self.assertIs(self.host.tasks, self.host._tasks)
                    constructor.assert_called_once()

    async def test_health_and_readonly_rpc(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        await writer.wait_closed()
        self.assertIn(b"200 OK", response)
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps({"type": "rpc", "id": "1", "method": "load_chat_log", "args": {}}))
            message = json.loads(await asyncio.wait_for(ws.recv(), 5))
        self.assertTrue(message["result"]["ok"])
        self.assertTrue(message["result"]["result"]["ok"])

    async def test_work_board_authenticated_contract_dedup_and_busy_independence(self):
        import uuid
        from websockets.exceptions import ConnectionClosed
        action=dict(type='create',request_id=uuid.uuid4().hex,fields=dict(title='RPC work item'))
        self.host.conversation.running=True
        with self.host.turn_lock:
            first=await self.notebook_rpc('change_work_item',action=action)
            second=await self.notebook_rpc('change_work_item',action=action)
            self.assertEqual(first['receipt'],second['receipt'])
            self.assertEqual(len(second['items']),1)
        item=second['items'][0]
        moved=await self.notebook_rpc('change_work_item',action=dict(type='move',request_id=uuid.uuid4().hex,
            id=item['id'],version=item['version'],state='review'))
        self.assertTrue(moved['ok']);self.assertEqual(moved['items'][0]['state'],'review')
        self.assertIsNone(self.host._collaboration)  # Read/write did not start collaboration queues.
        recovered=await self.notebook_rpc('load_work_board',request_id=action['request_id'])
        self.assertEqual(recovered['receipt']['item_id'],item['id'])
        with self.assertRaises(ConnectionClosed):
            async with connect(self.url.replace('test-token','wrong'),proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc',method='change_work_item',args=dict(action=action))))
                await ws.recv()
        self.assertEqual(len((await self.notebook_rpc('load_work_board'))['items']),1)
        self.host.conversation.running=False

    async def test_work_board_rpc_preserves_conflicts_and_committed_refresh_failure(self):
        import uuid
        result=await self.notebook_rpc('change_work_item',action=dict(type='create',request_id=uuid.uuid4().hex,fields=dict(title='versioned')))
        item=result['items'][0]
        args=dict(type='move',id=item['id'],version=item['version'],state='doing')
        self.assertTrue((await self.notebook_rpc('change_work_item',action={**args,'request_id':uuid.uuid4().hex}))['ok'])
        conflict=await self.notebook_rpc('change_work_item',action={**args,'state':'done','request_id':uuid.uuid4().hex})
        self.assertFalse(conflict['ok']);self.assertTrue(conflict['conflict'])
        latest=(await self.notebook_rpc('load_work_board'))['items'][0]
        request=dict(type='archive',id=item['id'],version=latest['version'],archived=True,request_id=uuid.uuid4().hex)
        with patch.object(self.host.work_board,'load',return_value=dict(ok=False,error='refresh failed')):
            failure=await self.notebook_rpc('change_work_item',action=request)
        self.assertEqual(failure['change_state'],'committed')
        checked=await self.notebook_rpc('load_work_board',request_id=request['request_id'])
        self.assertTrue(checked['items'][0]['archived']);self.assertIsNotNone(checked['receipt'])

    async def test_scheduling_authenticated_rpc_and_explicit_remote_create_recovery(self):
        from tests.schedule_fixture import FakeCalendar, DAY, START, END
        import uuid
        calendar = FakeCalendar(); self.host.scheduling.calendar = calendar
        item = (await self.notebook_rpc('change_work_item',action=dict(type='create',request_id=uuid.uuid4().hex,fields=dict(title='RPC schedule'))))['items'][0]
        request=dict(type='create',request_id=uuid.uuid4().hex,item_id=item['id'],version=item['version'],day=DAY,
                     summary='RPC arrangement',start=START,end=END)
        original=calendar.create
        def lose(*args): original(*args); raise TimeoutError('response lost')
        calendar.create=lose
        failure=await self.notebook_rpc('change_schedule',action=request)
        self.assertEqual(failure['change_state'],'unconfirmed')
        self.assertFalse((await self.notebook_rpc('change_schedule',action=request))['ok'])
        self.assertEqual(len(calendar.creates),1)
        page=await self.notebook_rpc('load_schedule',day=DAY,item_id=item['id'],refresh=True,request_id=request['request_id'])
        self.assertTrue(page['events_valid']);self.assertEqual(page['operation']['state'],'unconfirmed')
        repaired=await self.notebook_rpc('change_schedule',action=dict(type='resolve',request_id=uuid.uuid4().hex,
            operation_id=request['request_id'],resolution='link',calendar_id='calendar-one',event_id='created-1_0',
            item_id=item['id'],version=item['version'],day=DAY))
        self.assertTrue(repaired['ok'],repaired);self.assertEqual(len(calendar.creates),1)
        self.assertIsNone(self.host._collaboration)

    async def test_local_calendar_authenticated_rpc_offline_sync_and_retime(self):
        import uuid
        from websockets.exceptions import ConnectionClosed
        from tests.schedule_fixture import TimeCalendar, DAY, START, END
        from desk_companion.local_calendar import LOCAL
        calendar = TimeCalendar(); self.host.scheduling.calendar = calendar
        self.host.conversation.running = True
        try:
            with patch.object(calendar, 'primary', side_effect=RuntimeError('offline')):
                made = await self.notebook_rpc('change_local_calendar', action=dict(type='create', request_id=uuid.uuid4().hex,
                    summary='本地RPC', start=START, end=END, item_id=''))
                self.assertTrue(made['ok'], made); event = made['event']
                loaded = await self.notebook_rpc('load_schedule_week', day=DAY, refresh=True)
                self.assertFalse(loaded['events_valid']); self.assertEqual(loaded['events'][0]['event_id'], event['event_id'])
            month = await self.notebook_rpc('load_schedule_month', day=DAY)
            self.assertTrue(month['month']); self.assertEqual(month['events'][0]['event_id'], event['event_id'])
            local = await self.notebook_rpc('load_local_calendar')
            self.assertEqual(len(local['events']), 1)
            batch = await self.notebook_rpc('change_local_calendar', action=dict(type='prepare_sync', request_id=uuid.uuid4().hex,
                events=[dict(local_id=event['local_id'], version=event['local_version'])]))
            synced = await self.notebook_rpc('calendar_sync_step', batch_id=batch['batch']['id'], local_id=event['local_id'])
            self.assertEqual(synced['batch']['entries'][0]['state'], 'synced', synced)
            inspected = await self.notebook_rpc('inspect_schedule_event', calendar_id=LOCAL, event_id=event['event_id'])
            self.assertTrue(inspected['event']['editable'], inspected)
            moved = await self.notebook_rpc('change_schedule', action=dict(type='retime', request_id=uuid.uuid4().hex,
                calendar_id=LOCAL, event_id=event['event_id'], day=DAY, original_start=START, original_end=END,
                start=DAY+'T11:00:00+08:00', end=DAY+'T12:00:00+08:00'))
            self.assertTrue(moved['ok'], moved); self.assertEqual(len(calendar.creates), 1); self.assertEqual(len(calendar.patches), 1)
            self.assertIsNone(self.host._collaboration)
            with self.assertRaises(ConnectionClosed):
                async with connect(self.url.replace('test-token','bad-token'), proxy=None) as ws:
                    await ws.send(json.dumps(dict(type='rpc',method='load_local_calendar',args={})))
                    await ws.recv()
        finally:
            self.host.conversation.running = False

    async def test_week_retime_auth_disconnect_and_reconcile_do_not_replay_external_edit(self):
        import uuid, threading
        from tests.schedule_fixture import TimeCalendar, DAY, START, END
        from websockets.exceptions import ConnectionClosed
        calendar=TimeCalendar();self.host.scheduling.calendar=calendar
        self.host.conversation.running=True
        self.assertTrue((await self.notebook_rpc('load_schedule_week',day=DAY,refresh=True))['events_valid'])
        inspected=await self.notebook_rpc('inspect_schedule_event',calendar_id='calendar-one',event_id='existing_0')
        self.assertTrue(inspected['event']['editable'])
        entered,release=threading.Event(),threading.Event();original=calendar.patch_time
        self.addCleanup(release.set)
        request=dict(type='retime',request_id=uuid.uuid4().hex,calendar_id='calendar-one',event_id='existing_0',day=DAY,
                     original_start=START,original_end=END,start=DAY+'T11:00:00+08:00',end=DAY+'T12:00:00+08:00')
        def wait(*args):entered.set();release.wait(10);original(*args);raise TimeoutError('lost remote ack')
        with patch.object(calendar,'patch_time',side_effect=wait):
            async with connect(self.url,proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc',id='edit',method='change_schedule',args=dict(action=request))))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,5))
                    self.assertTrue((await self.notebook_rpc('load_schedule_week',day=DAY))['ok'])
                    self.assertFalse((await self.notebook_rpc('change_schedule',action={**request,'request_id':uuid.uuid4().hex}))['ok'])
                    await ws.close()
                finally:release.set()
            for _ in range(100):
                page=await self.notebook_rpc('load_schedule_week',day=DAY,request_id=request['request_id'])
                if page['operation'] and page['operation']['state']=='unconfirmed':break
                await asyncio.sleep(.02)
        self.assertEqual(page['operation']['state'],'unconfirmed')
        self.assertFalse((await self.notebook_rpc('change_schedule',action=request))['ok'])
        checked=await self.notebook_rpc('inspect_schedule_event',calendar_id='calendar-one',event_id='existing_0')
        event=checked['event']
        result=await self.notebook_rpc('change_schedule',action=dict(type='reconcile_time',request_id=uuid.uuid4().hex,
            operation_id=request['request_id'],calendar_id='calendar-one',event_id='existing_0',day=DAY,confirmed=True,
            original_start=event['start'],original_end=event['end']))
        self.assertTrue(result['ok'],result);self.assertEqual(len(calendar.patches),1)
        with self.assertRaises(ConnectionClosed):
            async with connect(self.url.replace('test-token','bad-token'),proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc',method='load_schedule_week',args=dict(day=DAY))))
                await ws.recv()
        self.host.conversation.running=False

    async def test_scheduling_rpc_wait_allows_other_items_reads_and_global_write_lock(self):
        import uuid, threading
        from tests.schedule_fixture import FakeCalendar, DAY, START, END
        from desk_companion.resource_lock import WRITES
        calendar=FakeCalendar();self.host.scheduling.calendar=calendar
        items=[]
        for title in ('reserved','other'):
            result=await self.notebook_rpc('change_work_item',action=dict(type='create',request_id=uuid.uuid4().hex,fields=dict(title=title)))
            items.append(next(i for i in result['items'] if i['id']==result['receipt']['item_id']))
        entered,release=threading.Event(),threading.Event();original=calendar.create
        def wait(*args): entered.set();release.wait(10);return original(*args)
        calendar.create=wait
        self.host.conversation.running=True
        pending=asyncio.create_task(self.notebook_rpc('change_schedule',_timeout=15,action=dict(type='create',
            request_id=uuid.uuid4().hex,item_id=items[0]['id'],version=items[0]['version'],day=DAY,
            summary='wait',start=START,end=END)))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait,5))
            self.assertTrue((await self.notebook_rpc('load_schedule',day=DAY))['ok'])
            for item,expected in ((items[0],False),(items[1],True)):
                moved=await self.notebook_rpc('change_work_item',action=dict(type='move',request_id=uuid.uuid4().hex,id=item['id'],version=item['version'],state='doing'))
                self.assertEqual(moved['ok'],expected,moved)
            with WRITES.hold(blocking=False): pass
        finally:
            release.set();self.host.conversation.running=False
        self.assertTrue((await pending)['ok'])

    async def test_headless_sessions_clear_and_isolate_data_without_gui_methods(self):
        from contextlib import ExitStack
        memory.append_chat('user', 'selected question', self.host.state.session_id)
        memory.append_chat('user', 'other question', 'other-session')
        original = self.host.state.session_id

        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args or {})))
                message = json.loads(await asyncio.wait_for(ws.recv(), 10))
            self.assertTrue(message['result']['ok'], message)
            return message['result']['result']

        # The backend has no legacy pet/window state.
        self.assertFalse(hasattr(self.host, "pet"))
        self.assertTrue((await rpc('clear_chat'))['ok'])
        self.assertEqual(memory.list_chat(original), [])
        self.assertEqual(len(memory.list_chat('other-session')), 1)
        with ExitStack() as stack:
            for method in ('clear_chat_history', 'new_chat_session', 'switch_chat_session', 'board_chat', 'board_memory'):
                stack.enter_context(patch.object(self.host, method, side_effect=AssertionError('inherited GUI method'), create=True))
            self.assertEqual((await rpc('new_chat_session'))['session_id'], original)
            self.assertEqual((await rpc('switch_chat_session', {'session_id': 'other-session'}))['session_id'], 'other-session')
            loaded = await rpc('load_chat_log')
            self.assertEqual(loaded['items'][0]['text'], 'other question')
            self.assertEqual((await rpc('load_memory'))['total'], 1)
            self.host.conversation.running = True
            for method in ('clear_chat', 'new_chat_session', 'switch_chat_session'):
                result = await rpc(method, {'session_id': original} if method == 'switch_chat_session' else None)
                self.assertFalse(result['ok'])
            self.assertTrue((await rpc('load_chat_log'))['ok'])
            self.host.conversation.running = False
            self.assertTrue((await rpc('clear_chat'))['ok'])
            self.assertEqual(self.host.state.session_id, 'other-session')

    async def test_session_rpc_blocks_pending_and_tail_tasks_but_keeps_reads_available(self):
        import threading
        from desk_companion.tasks import TaskManager
        previous = self.host.tasks
        previous.shutdown()
        with patch.object(TaskManager, '_schedule', return_value=None):
            manager = TaskManager(self.host, path=previous.path)
        self.host._tasks = manager
        self.addCleanup(manager.shutdown)
        sid = self.host.state.session_id
        memory.append_chat('user', 'keep current history', sid)
        memory.append_chat('user', 'other history', 'other-session')
        task = manager.submit('pending request', sid)
        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args or {})))
                value = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertTrue(value['result']['ok'], value)
            return value['result']['result']
        for method in ('new_chat_session', 'switch_chat_session', 'clear_chat'):
            value = await rpc(method, {'session_id': 'other-session'} if method == 'switch_chat_session' else None)
            self.assertFalse(value['ok'], value)
        self.assertEqual(manager.get(task)['state'], 'queued')
        with self.host.turn_lock:
            self.assertTrue((await rpc('load_chat_log'))['ok'])
            self.assertTrue((await rpc('load_memory'))['ok'])
        manager.update(task, state='cancelled')
        manager.active[task] = dict(session=sid, cancel=threading.Event())
        try:
            self.assertFalse((await rpc('clear_chat'))['ok'])
        finally:
            manager.active.pop(task)
        other = manager.submit('other pending request', 'other-session')
        self.assertTrue((await rpc('clear_chat'))['ok'])
        self.assertEqual(self.host.state.session_id, sid)
        self.assertEqual(manager.get(other)['state'], 'queued')
        self.assertEqual(manager.get(task)['state'], 'cancelled')

    async def test_feishu_settings_rpc_reports_invalid_secret_without_echoing_it(self):
        with patch("desk_companion.feishu_agent._run_lark", side_effect=RuntimeError("invalid_client test-only-secret")):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "rpc", "id": "feishu-check",
                    "method": "check_feishu_agent_connection", "args": {"profile": "test-profile"}}))
                raw = await asyncio.wait_for(ws.recv(), 5)
                message = json.loads(raw)
        self.assertTrue(message["result"]["ok"])
        self.assertFalse(message["result"]["result"]["ok"])
        self.assertIn("App Secret 无效", message["result"]["result"]["error"])
        self.assertNotIn("test-only-secret", raw)

    async def test_settings_rpc_calls_independent_service_and_preserves_contracts(self):
        import httpx
        from desk_companion import envconf, model_network
        from contextlib import ExitStack

        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id=method, method=method, args=args or {})))
                message = json.loads(await asyncio.wait_for(ws.recv(), 10))
            self.assertEqual(message['id'], method)
            self.assertTrue(message['result']['ok'], message)
            self.assertNotIn('PRIVATE_RPC_KEY', json.dumps(message))
            self.assertNotIn('PRIVATE_PROXY_KEY', json.dumps(message))
            return message['result']['result']

        service = self.host.assistant_settings
        self.assertIs(service.conversation, self.host.conversation)
        with ExitStack() as stack:
            # The data RPC must no longer depend on inherited App configuration methods.
            for method in ('board_persona', 'save_persona', 'board_model', 'board_models',
                           'save_model_entry', 'delete_model_entry', 'use_model', 'save_model', 'test_model'):
                stack.enter_context(patch.object(self.host, method, side_effect=AssertionError('inherited GUI method'), create=True))
            rebuild = stack.enter_context(patch.object(self.host.conversation, 'rebuild_agent'))
            binding = stack.enter_context(patch.object(self.host.conversation, 'bind_active_llm'))
            self.assertTrue((await rpc('load_persona'))['ok'])
            self.assertTrue((await rpc('save_persona', {'payload': dict(persona='RPC persona', max_steps=7, nudge_enabled=False)}))['ok'])
            self.assertEqual((await rpc('load_persona'))['persona'], 'RPC persona')
            self.assertEqual(state.load_state(0, 0).persona, 'RPC persona')
            first = await rpc('save_model_entry', {'payload': dict(base_url='https://example.invalid/v1', model='one', api_key='PRIVATE_RPC_KEY')})
            one = first['active']
            self.assertEqual((await rpc('load_model'))['model'], 'one')
            self.assertTrue((await rpc('load_model'))['has_key'])
            second = await rpc('save_model_entry', {'payload': dict(base_url='https://example.invalid/v1', model='two', copy_key_from=one)})
            two = second['items'][1]['id']
            self.assertEqual(len((await rpc('load_models'))['items']), 2)
            self.assertEqual((await rpc('use_model', {'model_id': two}))['active'], two)
            self.assertEqual((await rpc('delete_model_entry', {'model_id': one}))['active'], two)
            self.assertFalse((await rpc('delete_model_entry', {'model_id': two}))['ok'])
            self.assertTrue((await rpc('save_model', {'payload': dict(base_url='https://example.invalid/v1', model='legacy',
                input_cny_per_mtok=1, output_cny_per_mtok=2)}))['ok'])
            self.assertEqual((await rpc('load_model'))['output_cny_per_mtok'], 2.0)
            self.assertEqual(envconf.parse_env_file()['ATLAS_API_KEY'], 'PRIVATE_RPC_KEY')
            def transport(timeout):
                return httpx.Client(timeout=timeout, transport=httpx.MockTransport(lambda _: httpx.Response(200,
                    json=dict(choices=[dict(index=0, finish_reason='stop', message=dict(role='assistant', content='pong'))]))))
            with patch.object(model_network, 'http_client', side_effect=transport):
                result = await rpc('test_model', {'payload': dict(base_url='https://example.invalid/v1', model='probe', id=two)})
            self.assertEqual(result, dict(ok=True, message='连通正常。 回复：pong'))
            self.assertEqual(rebuild.call_count, 2)
            self.assertEqual(binding.call_count, 4)
            self.host.conversation.running = True
            saved = state.state_path().read_bytes()
            self.assertFalse((await rpc('save_persona', {'payload': dict(persona='rejected', max_steps=3, nudge_enabled=True)}))['ok'])
            self.assertEqual(state.state_path().read_bytes(), saved)
            self.host.conversation.running = False
            self.assertEqual((await rpc('load_model_network'))['mode'], 'system')
            result = await rpc('save_model_network', {'payload': dict(mode='custom', proxy_url='http://localhost:7890', password='PRIVATE_PROXY_KEY')})
            self.assertTrue(result['has_password'])
            self.assertEqual((await rpc('load_model_network'))['proxy_url'], 'http://localhost:7890')

    async def test_legacy_save_rpc_preserves_invalid_config_and_reports_saved_initialization_failure(self):
        from desk_companion import envconf
        envconf.write_llm_env(api_key='PRIVATE_RPC_KEY', base_url='https://example.invalid/v1', model='before')
        before_env = envconf.env_path().read_bytes()
        before_state = state.state_path().read_bytes()

        async def save(payload):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps(dict(type='rpc', id='save', method='save_model', args={'payload': payload})))
                message = json.loads(await asyncio.wait_for(ws.recv(), 10))
            self.assertTrue(message['result']['ok'], message)
            self.assertNotIn('PRIVATE_RPC_KEY', json.dumps(message))
            return message['result']['result']

        payload = dict(base_url='https://example.invalid/v1', model='after',
                       input_cny_per_mtok=1, output_cny_per_mtok=2)
        with patch.object(self.host.conversation, 'rebuild_agent', side_effect=RuntimeError('fixture init error')) as rebuild:
            for change in ({'output_cny_per_mtok': None}, {'input_cny_per_mtok': float('nan')}):
                result = await save({**payload, **change})
                self.assertFalse(result['ok'])
                self.assertEqual(result['save_state'], 'not_saved')
                self.assertEqual(envconf.env_path().read_bytes(), before_env)
                self.assertEqual(state.state_path().read_bytes(), before_state)
                self.assertEqual(self.host.state.model_prices, {})
            rebuild.assert_not_called()
            result = await save(payload)
        self.assertFalse(result['ok'])
        self.assertEqual(result['save_state'], 'saved')
        self.assertIn('已保存', result['error'])
        self.assertIn('初始化失败', result['error'])
        self.assertEqual(envconf.public_llm_env()['model'], 'after')
        self.assertEqual(state.load_state(0, 0).model_prices['after']['output_cny_per_mtok'], 2.0)

    async def test_manual_update_check_rpc_is_available_while_chat_lock_is_held(self):
        with patch("desk_companion.release_info.check_updates", return_value={"ok": True, "latest_version": "0.3.0"}):
            with self.host.turn_lock:
                async with connect(self.url, proxy=None) as ws:
                    await ws.send(json.dumps({"type": "rpc", "id": "update", "method": "check_updates", "args": {}}))
                    message = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertTrue(message["result"]["ok"])
            self.assertEqual(message["result"]["result"]["latest_version"], "0.3.0")

    async def test_terminal_worker_uses_shared_rpc_without_waiting_for_agent_turn_lock(self):
        from desk_companion import terminal, agent_debug
        from unittest.mock import Mock
        import base64
        controller = Mock()
        controller.start.return_value = {"ok": True, "job": {"id": "j1"}}
        controller.read.return_value = {"ok": True, "job": {"id": "j1", "state": "succeeded", "output_bytes": 6, "exit_code": 0},
                                        "data": base64.b64encode(b"hello\n").decode(), "offset": 6}
        tool = next(s for s in terminal.tool_specs(lambda: "worker-session") if s["name"] == "execute_command")
        self.assertEqual(tool["retry_max"], 0)
        self.assertFalse(self.host.agent.tools.get("execute_command").isIdempotent)
        self.assertEqual(self.host.agent.tools.get("execute_command").retry_max, 0)
        with patch.object(terminal, "_manager", controller), patch.dict(os.environ, {
                "DESK_TERMINAL_PORT": str(self.port), "DESK_TERMINAL_TOKEN": "test-token"}):
            with self.host.turn_lock, agent_debug.context(task_id="worker-task"):
                result = await asyncio.wait_for(asyncio.to_thread(tool["func"], {"command": "echo hello"}), 5)
        self.assertEqual(json.loads(result)["output"], "hello\n")
        self.assertEqual(controller.start.call_args.args[1], "worker-session")
        self.assertEqual(controller.start.call_args.args[4:6], ("agent", "worker-task"))

    async def test_attachment_cancel_and_picker_are_available_while_agent_lock_is_held(self):
        import uuid
        ident = uuid.uuid4().hex
        with self.host.turn_lock:
            for method in ("cancel_local_source_request", "pick_local_sources"):
                async with connect(self.url, proxy=None) as ws:
                    await ws.send(json.dumps({"type": "rpc", "id": method, "method": method,
                                              "args": {"request_id": ident}}))
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 5))["result"]
                    self.assertTrue(reply["ok"], reply)
                    if method == "pick_local_sources":
                        self.assertTrue(reply["result"]["cancelled"])

    async def test_video_settings_rpc_does_not_block_on_desktop_turn_and_rejects_bad_values(self):
        with self.host.turn_lock:
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "rpc", "id": "video", "method": "save_video_settings",
                                         "args": {"proxy": "http://127.0.0.1:7890", "cookie_file": ""}}))
                saved = json.loads(await asyncio.wait_for(ws.recv(), 5))
                self.assertTrue(saved["result"]["ok"])
                await ws.send(json.dumps({"type": "rpc", "id": "video", "method": "load_video_settings"}))
                loaded = json.loads(await asyncio.wait_for(ws.recv(), 5))
                self.assertEqual(loaded["result"]["result"]["settings"]["proxy"], "http://127.0.0.1:7890")
                await ws.send(json.dumps({"type": "rpc", "id": "video", "method": "save_video_settings",
                                         "args": {"proxy": "http://user:secret@proxy:80", "cookie_file": ""}}))
                invalid = await asyncio.wait_for(ws.recv(), 5)
                self.assertFalse(json.loads(invalid)["result"]["ok"])
                self.assertNotIn("secret@proxy", invalid)

    async def test_shutdown_requires_exact_authorization_and_only_signals_the_server(self):
        shutdown = asyncio.Event()
        async def request(auth):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            writer.write(("GET /shutdown HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n" + auth + "\r\n").encode())
            await writer.drain()
            result = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            await writer.wait_closed()
            return result
        with patch.object(server, "SHUTDOWN", shutdown):
            self.assertIn(b"401", await request(""))
            self.assertIn(b"401", await request("Authorization: Bearer test-token-suffix\r\n"))
            self.assertFalse(shutdown.is_set())
            self.assertIn(b"200", await request("Authorization: Bearer test-token\r\n"))
            self.assertTrue(shutdown.is_set())

    async def test_chat_stream_persists_metadata_and_releases_busy_state(self):
        frames = []
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps({"type": "chat", "text": "打个招呼", "chips": {},
                                     "sampling": {"reasoning_effort": "low", "temperature": 0.5, "top_p": 1}}))
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
                frames.append(frame)
                if frame["type"] in ("done", "error"):
                    break
        self.assertEqual(frames[-1], {"type": "done", "data": "本地流式回归正常。"})
        self.assertTrue(any(frame["type"] == "token" for frame in frames))
        rows = memory.list_chat(self.host.state.session_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["react_loops"], 2)
        task = self.host.tasks.list()["items"][0]
        events = self.host.tasks.get(task["id"])["events"]
        tool = next(e["data"] for e in events if e["kind"] == "tool_end")
        self.assertIn("input", tool)
        self.assertIn("离线工具结果", json.dumps(tool["result"], ensure_ascii=False))
        self.assertGreater(rows[-1]["total_tokens"], 0)
        self.assertFalse(self.host._agent_running)
        self.assertIsNone(self.host.agent.llm.sampling)

    async def test_unknown_rpc_and_missing_sampling_report_errors(self):
        async with connect(self.url, proxy=None) as ws:
            await ws.send(json.dumps({"type": "rpc", "id": "bad", "method": "open_url"}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertFalse(reply["result"]["ok"])
            await ws.send(json.dumps({"type": "chat", "text": "缺参数"}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
            self.assertEqual(reply["type"], "error")
        self.assertEqual(memory.list_chat(self.host.state.session_id), [])


    async def test_video_page_rpc_runs_independent_tasks_and_continues_after_disconnect(self):
        original = self.host.state.session_id
        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "rpc", "id": method, "method": method, "args": args or {}}))
                envelope = json.loads(await asyncio.wait_for(ws.recv(), 30))["result"]
                self.assertTrue(envelope["ok"], envelope)
                return envelope["result"]
        started = await rpc("start_video_task", {"url": "https://youtu.be/abcdefghijk"})
        task_id = started["task_id"]
        # 提交连接关闭后，独立 worker 仍执行；原桌面会话不被切换。
        finished = await asyncio.wait_for(asyncio.to_thread(self.host.tasks.wait, task_id), 30)
        self.assertEqual(finished["state"], "succeeded")
        self.assertEqual(self.host.state.session_id, original)
        self.assertEqual([row["id"] for row in (await rpc("list_video_tasks"))["items"]], [task_id])
        followup = await rpc("continue_video_task", {"task_id": task_id, "text": "继续解释"})
        child = self.host.tasks.get(followup["task_id"])
        self.assertEqual(child["session"], finished["session"])
        self.assertEqual(child["source"]["workflow"], "video")
        self.assertFalse(child["source"]["send_back"])

    async def test_video_login_rpc_roundtrip_returns_only_safe_status_and_validates_platform(self):
        async def rpc(method, args=None):
            async with connect(self.url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "rpc", "id": method, "method": method, "args": args or {}}))
                return json.loads(await asyncio.wait_for(ws.recv(), 10))["result"]
        from tests.test_video_login import cookie
        saved = await rpc("save_video_login", {"platform": "Bilibili", "cookies": [cookie()]})
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["result"]["items"][0]["state"], "saved")
        self.assertNotIn("test-private-cookie", json.dumps(saved))
        loaded = await rpc("load_video_login")
        self.assertEqual(loaded["result"]["items"][0]["state"], "saved")
        self.assertNotIn("encrypted", json.dumps(loaded))
        invalid = await rpc("save_video_login", {"platform": "Bilibili", "cookies": [cookie("evil.test")]})
        self.assertFalse(invalid["ok"])
        self.assertNotIn("test-private-cookie", json.dumps(invalid))
        cleared = await rpc("clear_video_login", {"platform": "Bilibili"})
        self.assertEqual(cleared["result"]["items"][0]["state"], "missing")


if __name__ == "__main__":
    unittest.main()

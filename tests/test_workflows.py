"""Workflow behavior: immutable runs, branch execution, approval and unknown writes."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from desk_companion import automation
from desk_companion.automation_service import AutomationService
from desk_companion.workflow_graph import resolve, validate
from desk_companion.workflows import WorkflowService
from desk_companion.workflow_worker import execute
from desk_companion import workflow_nodes


def node(ident, kind, **config):
    return dict(id=ident, kind=kind, config=config, position=dict(x=0, y=0))


def graph(*nodes):
    return dict(nodes=list(nodes), edges=[dict(source=a['id'], target=b['id'], branch='') for a, b in zip(nodes, nodes[1:])])


def document():
    return node('doc', 'document', title='结果', content='正文')


class FakeTasks:
    def __init__(self):
        self.tasks = {}
        self.requests = []

    def submit(self, text, session, submission_key='', **request):
        from desk_companion.tasks import submission_task_id
        ident = submission_task_id(submission_key)
        self.tasks.setdefault(ident, dict(id=ident, state='queued'))
        self.requests.append({**request, 'session_id': session, 'task_id': ident, 'limits': request['task_limits']})
        return ident

    def get(self, ident, events=False):
        if ident not in self.tasks:
            raise ValueError('任务不存在。')
        return self.tasks[ident]

    def cancel(self, ident):
        self.tasks[ident]['state'] = 'cancelled'


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, DESK_COMPANION_DATA_DIR=temp.name).start()
        self.tasks = FakeTasks()
        self.service = WorkflowService(lambda: self.tasks)
        self.store = self.service.store
        self.events = []

    def save(self, value, **fields):
        return self.store.save(dict(name='样例', graph=value, **fields))

    def start(self, definition, **fields):
        result = self.service.operation('start', {**dict(id=definition['id'], version=definition['version'], request_id='request', mode='full'), **fields})
        self.assertTrue(result['ok'], result)
        return result['run']

    def drive(self, adapter=None, check=None):
        request = self.tasks.requests[-1]
        execute(request, lambda *event: self.events.append(event), store=self.store,
                adapter=adapter or (lambda kind, config, key, emit, request: {'text': config.get('text', '')}),
                check_target=check or (lambda *args: None))
        self.tasks.tasks[request['task_id']]['state'] = 'succeeded'

    def test_graph_rejects_cycles_unconnected_nodes_dynamic_targets_and_wrong_references(self):
        valid = graph(node('trigger', 'trigger'), node('read', 'read', url='{{trigger.text}}'))
        validate(valid)
        bad = []
        disconnected = copy.deepcopy(valid); disconnected['edges'] = []; bad.append(disconnected)
        cyclic = copy.deepcopy(valid); cyclic['edges'].append(dict(source='read', target='trigger', branch='')); bad.append(cyclic)
        missing = copy.deepcopy(valid); missing['nodes'][1]['config']['url'] = '{{trigger.invented}}'; bad.append(missing)
        downstream = graph(node('trigger', 'trigger', text='{{read.text}}'), node('read', 'read', url='https://example.org')); bad.append(downstream)
        target = graph(node('trigger', 'trigger'), node('send', 'message', chat_id='{{trigger.text}}', text='hello')); bad.append(target)
        duplicate = copy.deepcopy(valid); duplicate['edges'] *= 2; bad.append(duplicate)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(value)

    def test_same_request_is_idempotent_and_active_definition_cannot_spawn_second_run(self):
        definition = self.save(graph(node('trigger', 'trigger')))
        run = self.start(definition)
        self.assertEqual(self.start(definition)['id'], run['id'])
        other = self.service.operation('start', dict(id=definition['id'], version=definition['version'], request_id='second', mode='full'))
        self.assertFalse(other['ok'])
        self.assertEqual(len(self.store.runs()), 1)

    def test_definition_update_keeps_original_run_snapshot_and_conflicting_name_does_not_delete(self):
        definition = self.save(graph(node('trigger', 'trigger', text='original')))
        run = self.start(definition)
        changed = self.save(graph(node('trigger', 'trigger', text='new')), id=definition['id'], revision=definition['revision'])
        self.assertNotEqual(changed['version'], run['version'])
        self.assertEqual(self.store.run(run['id'])['graph']['nodes'][0]['config']['text'], 'original')
        with self.assertRaises(ValueError):
            self.save(changed['graph'], id=definition['id'], revision=1)
        other = self.store.save(dict(name='another', graph=changed['graph']))
        with self.assertRaises(ValueError):
            self.store.save(dict(id=other['id'], revision=other['revision'], name='样例', graph=other['graph']))
        self.assertEqual(len(self.store.definitions()), 2)

    def test_condition_runs_selected_branch_and_skips_other_path(self):
        value = graph(node('trigger', 'trigger', text='yes'), node('check', 'condition', left='{{trigger.text}}', operator='equals', right='yes'))
        value['nodes'] += [node('yes', 'ai', prompt='yes'), node('no', 'ai', prompt='no')]
        value['edges'] += [dict(source='check', target='yes', branch='true'), dict(source='check', target='no', branch='false')]
        definition = self.save(value)
        run = self.start(definition)
        called = []
        def adapter(kind, config, *args):
            called.append((kind, config))
            return {'result': True} if kind == 'condition' else {'text': config.get('text', config.get('prompt', ''))}
        self.drive(adapter)
        result = self.store.run(run['id'])
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['receipts']['no']['state'], 'skipped')
        self.assertEqual(len(called), 3)

    def test_step_requires_explicit_write_confirmation_and_keeps_same_run(self):
        definition = self.save(graph(node('trigger', 'trigger', text='content'), document()))
        run = self.start(definition, mode='step')
        self.drive()
        self.assertEqual(self.store.run(run['id'])['state'], 'paused')
        result = self.service.operation('advance', dict(run_id=run['id']))
        self.assertTrue(result['ok'], result)
        self.drive()
        self.assertEqual(self.store.run(run['id'])['state'], 'waiting_approval')
        self.assertNotIn('doc', self.store.run(run['id'])['receipts'])
        approved = self.service.operation('advance', dict(run_id=run['id'], confirm_node='doc'))
        self.assertTrue(approved['ok'], approved)
        self.drive(lambda *args: dict(document_id='d12345', url='https://example/docx/d12345'))
        result = self.store.run(run['id'])
        self.assertEqual(result['id'], run['id'])
        self.assertEqual(result['receipts']['doc']['state'], 'confirmed')

    def test_resume_after_read_failure_does_not_repeat_confirmed_document(self):
        definition = self.save(graph(node('trigger', 'trigger'), document(), node('read', 'read', url='https://example.org')))
        run = self.start(definition, confirm_writes=True)
        calls = []
        def adapter(kind, *args):
            calls.append(kind)
            if kind == 'document': return dict(document_id='d12345', url='https://example/docx/d12345')
            if kind == 'read': raise RuntimeError('temporary')
            return dict(text='')
        self.drive(adapter)
        self.assertEqual(self.store.run(run['id'])['state'], 'failed')
        result = self.service.operation('advance', dict(run_id=run['id']))
        self.assertTrue(result['ok'], result)
        self.drive(lambda kind, *args: (calls.append(kind) or dict(text='body', url='https://example.org', title='Example', truncated=False)))
        self.assertEqual(self.store.run(run['id'])['state'], 'completed')
        self.assertEqual(calls.count('document'), 1)

    def test_unknown_write_blocks_retry_until_read_only_reconciliation(self):
        definition = self.save(graph(node('trigger', 'trigger'), node('base', 'base', base_token='base', table_id='table', key_field='回执', fields={'内容':'value'})))
        run = self.start(definition, confirm_writes=True)
        def adapter(kind, *args):
            if kind == 'base': raise TimeoutError()
            return dict(text='')
        self.drive(adapter)
        self.assertEqual(self.store.run(run['id'])['state'], 'unknown')
        self.assertFalse(self.service.operation('advance', dict(run_id=run['id']))['ok'])
        with patch.object(workflow_nodes, 'reconcile', return_value=dict(record_id='rec123')) as check:
            result = self.service.operation('reconcile', dict(run_id=run['id'], node_id='base'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(check.call_count, 1)
        self.assertEqual(result['run']['receipts']['base']['state'], 'confirmed')
        self.assertTrue(self.service.operation('advance', dict(run_id=run['id']))['ok'])
        self.drive(lambda *args: self.fail('已确认的节点不能再次调用'))
        self.assertEqual(self.store.run(run['id'])['state'], 'completed')

    def test_process_termination_converts_pending_write_to_unknown(self):
        definition = self.save(graph(node('trigger', 'trigger'), document()))
        run = self.start(definition, confirm_writes=True)
        self.store.claim(run['id'], 0)
        self.store.receipt(run['id'], definition['graph']['nodes'][0], 'confirmed', output=dict(text=''))
        self.store.receipt(run['id'], definition['graph']['nodes'][1], 'pending', inputs=definition['graph']['nodes'][1]['config'])
        self.tasks.tasks[run['task_id']]['state'] = 'interrupted'
        current = self.service.observe(run['id'])
        self.assertEqual(current['state'], 'unknown')
        self.assertEqual(current['receipts']['doc']['state'], 'unknown')
        self.assertFalse(self.service.operation('advance', dict(run_id=run['id']))['ok'])

    def test_failed_success_receipt_persistence_never_repeats_write(self):
        definition = self.save(graph(node('trigger', 'trigger'), document()))
        run = self.start(definition, confirm_writes=True)
        original = self.store.receipt
        def receipt(ident, node, state, **fields):
            if node['kind'] == 'document' and state == 'confirmed': raise OSError('disk')
            return original(ident, node, state, **fields)
        with patch.object(self.store, 'receipt', side_effect=receipt):
            self.drive(lambda kind, *args: dict(document_id='doc123', url='https://example/docx/doc123') if kind == 'document' else dict(text=''))
        self.assertEqual(self.store.run(run['id'])['receipts']['doc']['state'], 'unknown')
        self.assertFalse(self.service.operation('advance', dict(run_id=run['id']))['ok'])

    def test_network_preflight_does_not_hold_store_transaction(self):
        definition = self.save(graph(node('trigger', 'trigger'), document()))
        self.start(definition, confirm_writes=True)
        def check(*args):
            with self.store.db(True) as db:
                db.execute('UPDATE definitions SET updated=updated WHERE id=?', (definition['id'],))
            raise workflow_nodes.WriteRejected('目标拒绝')
        self.drive(check=check)
        run = self.store.runs()[0]
        self.assertEqual(run['state'], 'failed')
        self.assertNotIn('doc', self.store.run(run['id'])['receipts'])

    def test_legacy_jobs_and_fixed_version_schedule_coexist(self):
        definition = self.save(graph(node('trigger', 'trigger')))
        service = AutomationService(tasks=lambda:self.tasks)
        self.addCleanup(lambda: service.stop(0))
        old = service.save_automation_job(dict(name='legacy',action='retro_gen',enabled=False,cadence='daily',weekdays=[],hour=9,minute=0,doc=''))
        new = service.save_automation_job(dict(name='workflow',action='workflow',enabled=False,cadence='daily',weekdays=[],hour=9,minute=0,doc='',workflow_id=definition['id'],workflow_version=definition['version'],workflow_write_version=''))
        self.assertTrue(old['ok'],old); self.assertTrue(new['ok'],new)
        stored = automation.load_store()['jobs']
        self.assertEqual({j['action'] for j in stored}, {'retro_gen','workflow'})
        with patch.object(service,'_kick'):
            result=service.run_automation_job(new['job_id'])
        self.assertEqual(result['execution']['workflow_version'],definition['version'])

    def test_late_concurrent_reconcile_cannot_pause_new_attempt(self):
        definition = self.save(graph(node('trigger','trigger'),document()))
        run = self.start(definition,confirm_writes=True)
        self.store.claim(run['id'],0)
        self.store.receipt(run['id'],definition['graph']['nodes'][0],'confirmed',output=dict(text=''))
        self.store.receipt(run['id'],definition['graph']['nodes'][1],'unknown',inputs=definition['graph']['nodes'][1]['config'])
        self.store.set_run(run['id'],state='unknown')
        entered,release=threading.Event(),threading.Event()
        results=[]
        def read(*args):
            if threading.current_thread().name=='late-check':
                entered.set();self.assertTrue(release.wait(5))
            return dict(document_id='doc123',url='https://example/docx/doc123')
        with patch.object(workflow_nodes,'reconcile',side_effect=read):
            late=threading.Thread(name='late-check',target=lambda:results.append(self.service.operation('reconcile',dict(run_id=run['id'],node_id='doc',remote_id='doc123'))))
            late.start();self.assertTrue(entered.wait(5))
            try:
                self.assertTrue(self.service.operation('reconcile',dict(run_id=run['id'],node_id='doc',remote_id='doc123'))['ok'])
                self.assertTrue(self.service.operation('advance',dict(run_id=run['id']))['ok'])
            finally:
                release.set();late.join(5)
        current=self.store.run(run['id'])
        self.assertFalse(results[0]['ok'])
        self.assertEqual((current['state'],current['attempt']),('queued',1))
        self.assertFalse(self.service.operation('advance',dict(run_id=run['id']))['ok'])
        with self.assertRaises(RuntimeError):
            self.store.receipt(run['id'],definition['graph']['nodes'][1],'pending',attempt=0)
        self.store.observe_terminal(run['id'],0,'interrupted')
        self.assertEqual(self.store.run(run['id'])['state'],'queued')

    def test_missing_version_is_rejected_without_creating_run(self):
        definition=self.save(graph(node('trigger','trigger')))
        result=self.service.operation('start',dict(id=definition['id'],request_id='r',mode='full'))
        self.assertFalse(result['ok'])
        self.assertEqual(self.store.runs(),[])


class WorkflowAdapterTests(unittest.TestCase):
    def test_base_v3_id_list_and_exact_filter_use_one_create(self):
        config=dict(base_token='b',table_id='t',key_field='回执',fields={'内容':'真实正文'},profile='')
        values=[dict(record_id_list=[],has_more=False),dict(record=dict(record_id_list=['rec123']))]
        with patch.object(workflow_nodes,'cli',side_effect=values) as cli:
            value=workflow_nodes.execute('base',config,'key',lambda *args:None,{})
        self.assertEqual(value,dict(record_id='rec123'))
        self.assertIn('--filter-json',cli.call_args_list[0].args[1])
        self.assertEqual(cli.call_count,2)
        payload=json.loads(cli.call_args_list[1].args[1][-1])
        self.assertEqual(payload,{'内容':'真实正文','回执':'key'})

    def test_base_unknown_reconciliation_never_creates_when_no_match(self):
        with patch.object(workflow_nodes,'cli',return_value=dict(record_id_list=[],has_more=False)) as cli:
            with self.assertRaises(ValueError):
                workflow_nodes.reconcile('base',dict(base_token='b',table_id='t',key_field='回执'),'key')
        self.assertEqual(cli.call_count,1)
        self.assertNotIn('+record-upsert',cli.call_args.args[1])

    def test_message_same_key_and_exact_remote_content_and_chat(self):
        config=dict(chat_id='oc_target',text='正文',profile='')
        with patch.object(workflow_nodes,'cli',return_value=dict(message_id='om_id')) as cli:
            self.assertEqual(workflow_nodes.execute('message',config,'key',lambda *args:None,{}),dict(message_id='om_id'))
        self.assertIn('tebb-workflow-key',cli.call_args.args[1])
        response=dict(items=[dict(chat_id='oc_other',body=dict(content=json.dumps(dict(text='正文\n\n自动化回执：key'))))])
        with patch.object(workflow_nodes,'cli',return_value=response):
            with self.assertRaises(ValueError): workflow_nodes.reconcile('message',config,'key','om_12345')

    def test_invalid_record_receipts_do_not_become_success(self):
        from desk_companion.lark_receipts import single_record_id
        for value in ({'record_id_list':['a','b']},{'record_id_list':[None]},{'record_id_list':'a'},{'id':3}):
            with self.subTest(value=value),self.assertRaises(ValueError): single_record_id(value)

    def test_resolve_never_evaluates_scripts_or_missing_branch_outputs(self):
        self.assertEqual(resolve('{{a.text}}',{'a':{'text':'$(echo unsafe)'}}),'$(echo unsafe)')
        with self.assertRaises(ValueError): resolve('{{missing.text}}',{})


if __name__ == '__main__':
    unittest.main()

"""Durable business state, project ownership and execution isolation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from desk_companion.collaboration import Collaboration, transaction
from desk_companion.work_board import WorkBoardService


class WorkBoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scope = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=str(self.root)); scope.start(); self.addCleanup(scope.stop)
        self.service = WorkBoardService()
        self.assertTrue(self.service.load()['ok'])
        with transaction(self.service.path) as (state, _):
            state['projects'] = [dict(id='p1', name='Project one'), dict(id='p2', name='Project two')]
            state['conversations'][0]['project_id'] = 'p1'
            state['runs'] = [dict(id='r1', title='work', conversation=state['conversations'][0]['id'],
                agent='tebb', state='running', kind='task', answer='', workspace=dict(project_id='p1'),
                task='PRIVATE_TASK', input='PRIVATE_INPUT', agent_config=dict(api_key='PRIVATE_KEY')),
                dict(id='r2', title='delegated', conversation=state['conversations'][0]['id'],
                agent='tebb', state='succeeded', delivery_state='running', kind='plan', answer='delegated',
                workspace=dict(project_id='p2'))]

    def action(self, kind='create', **fields):
        return dict(type=kind, request_id=uuid.uuid4().hex, **fields)

    def create(self, **fields):
        result = self.service.change(self.action(fields=dict(title='item', **fields)))
        self.assertTrue(result['ok'], result)
        return next(i for i in result['items'] if i['id'] == result['receipt']['item_id'])

    def change(self, item, kind, **kwargs):
        return self.service.change(self.action(kind, id=item['id'], version=item['version'], **kwargs))

    def test_create_dedup_versions_validation_and_reopen_are_durable(self):
        request = self.action(fields=dict(title=' first ', project_id='p1', priority='high', due='2026-10-22'))
        first = self.service.change(request); second = self.service.change(request)
        self.assertTrue(first['ok']); self.assertEqual(first['receipt'], second['receipt'])
        self.assertEqual(len(second['items']), 1)
        item = first['items'][0]; self.assertEqual(item['title'], 'first')
        for state in ('doing','review','done','todo'):
            result = self.change(item, 'move', state=state); self.assertTrue(result['ok'], result)
            item = result['items'][0]; self.assertEqual(item['state'], state)
        self.assertEqual(WorkBoardService().load()['items'][0], item)
        conflict = self.service.change({**request, 'fields':dict(title='different')})
        self.assertFalse(conflict['ok']); self.assertEqual(len(self.service.load()['items']),1)
        for payload in (dict(title=''),dict(title='x',due='2026-02-30'),dict(title='x',project_id='missing'),dict(title='x',unknown=True)):
            self.assertFalse(self.service.change(self.action(fields=payload))['ok'])

    def test_project_deletion_detaches_cards_atomically_and_preserves_active_runs(self):
        item = self.create(project_id='p1')
        linked = self.change(item, 'link', run_id='r1'); item = linked['items'][0]
        collab = Collaboration(managers=dict(work=Mock(settings={}),chat=Mock(settings={})), start=False)
        self.addCleanup(collab.shutdown)
        with transaction(self.service.path) as (state, _): before = copy.deepcopy(state['runs'])
        snapshot = collab.action(dict(type='delete_project', id='p1', request_id=uuid.uuid4().hex))
        self.assertNotIn('work_items', snapshot['state'])
        result = self.service.load(); saved = result['items'][0]
        self.assertEqual((saved['project_id'], saved['removed_project']), ('','Project one'))
        self.assertGreater(saved['version'], item['version']); self.assertEqual(saved['links'][0]['id'], 'r1')
        with transaction(self.service.path) as (state, _): self.assertEqual(state['runs'], before)
        self.assertTrue(self.change(item, 'move', state='done')['conflict'])

    def test_archive_link_unlink_and_edit_do_not_execute_or_change_runs(self):
        item = self.create(project_id='p1')
        with transaction(self.service.path) as (state, _): before = copy.deepcopy(state['runs'])
        for rid in ('r1','r2'):
            result = self.change(item, 'link', run_id=rid); self.assertTrue(result['ok']); item=result['items'][0]
        self.assertEqual(len(item['links']),2)
        result=self.change(item,'archive',archived=True); item=result['items'][0]
        self.assertTrue(item['archived']); self.assertFalse(self.change(item,'move',state='done')['ok'])
        item=self.change(item,'archive',archived=False)['items'][0]
        item=self.change(item,'move',state='done')['items'][0]
        item=self.change(item,'save',fields=dict(title='moved project',project_id='p2'))['items'][0]
        self.assertEqual(item['links'][0]['project_name'],'Project one')
        item=self.change(item,'unlink',run_id='r1')['items'][0]
        self.assertEqual([l['id'] for l in item['links']],['r2'])
        with transaction(self.service.path) as (state, _): self.assertEqual(state['runs'],before)

    def test_catalog_hides_internal_execution_data_and_handles_removed_records(self):
        item=self.create()
        item=self.change(item,'link',run_id='r1')['items'][0]
        with transaction(self.service.path) as (state, _):
            state['runs'].append(dict(id='chat',kind='chat',conversation='dm-tebb',agent='tebb',title='chat',state='succeeded'))
        page=self.service.load()
        self.assertNotIn('PRIVATE_',json.dumps(page))
        self.assertEqual(page['runs'][1]['delivery_state'],'running')
        self.assertFalse(self.change(item,'link',run_id='chat')['ok'])
        with transaction(self.service.path) as (state,_): state['runs']=[]
        page=self.service.load(); self.assertEqual(page['runs'],[])
        self.assertEqual(page['items'][0]['links'][0]['id'],'r1')

    def test_concurrent_edits_use_compare_and_swap_and_do_not_lose_other_cards(self):
        first=self.create(); second=self.create()
        def update(state):
            return WorkBoardService().change(self.action('move',id=first['id'],version=first['version'],state=state))
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(update,('doing','done')))
        self.assertEqual(sum(r['ok'] for r in results),1)
        self.assertTrue(next(r for r in results if not r['ok'])['conflict'])
        self.assertTrue(self.change(second,'archive',archived=True)['ok'])
        self.assertEqual(len(self.service.load()['items']),2)

    def test_commit_ack_loss_is_recoverable_by_same_request_without_duplicate(self):
        request=self.action(fields=dict(title='uncertain'))
        original=transaction
        @contextmanager
        def lose_ack(path):
            with original(path) as result: yield result
            raise OSError('commit acknowledgement lost')
        with patch('desk_companion.collaboration.transaction',lose_ack): result=self.service.change(request)
        self.assertEqual(result['change_state'],'unconfirmed')
        recovered=self.service.load(request['request_id']); self.assertIsNotNone(recovered['receipt'])
        replay=self.service.change(request); self.assertTrue(replay['ok']); self.assertEqual(len(replay['items']),1)

    def test_write_rollback_and_committed_refresh_failure_have_distinct_receipts(self):
        item=self.create()
        with patch('desk_companion.work_items.apply_change',side_effect=OSError('before write')):
            failure=self.change(item,'move',state='done')
        self.assertEqual(failure['change_state'],'not_changed')
        with patch.object(self.service,'load',return_value=dict(ok=False,error='read fail')):
            failure=self.change(item,'archive',archived=True)
        self.assertEqual(failure['change_state'],'committed')
        self.assertTrue(self.service.load(failure['request_id'])['items'][0]['archived'])

    def test_service_operations_work_without_gui_or_task_manager_construction(self):
        code='''
import sys
from unittest.mock import patch
from tests.gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0,NoGui())
from desk_companion.work_board import WorkBoardService
with patch('desk_companion.tasks.TaskManager',side_effect=AssertionError('must remain lazy')):
    service=WorkBoardService()
    page=service.change(dict(type='create',request_id='no-gui-create',fields=dict(title='isolated')))
    assert page['ok'],page
    item=next(i for i in page['items'] if i['id']==page['receipt']['item_id'])
    assert service.change(dict(type='move',request_id='no-gui-move',id=item['id'],version=item['version'],state='review'))['ok']
assert_no_gui()
'''
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,encoding='utf-8',
                              timeout=30,env={**os.environ,'PYTHONUTF8':'1'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

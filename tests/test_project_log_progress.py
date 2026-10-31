import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from desk_companion.project_environments import ProjectEnvironmentService
from desk_companion.task_budgets import Ledger,installed,record_progress


class ProjectLogProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        environment=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=str(self.root/'profile'))
        environment.start();self.addCleanup(environment.stop)
        self.service=ProjectEnvironmentService(self.root/'projects',client=object())
        self.ident=uuid.uuid4().hex;self.operation=uuid.uuid4().hex;self.scope='scope'
        self.service.store.begin(self.ident,'prepare',self.operation,{},initial=dict(id=self.ident,scope=self.scope,
            state='creating',sandbox_id='',service=None,prepare_operation=self.operation))
        self.service.store.finish(self.operation,'unknown',error='original receipt unknown')
        self.ledger=Ledger();self.budget=self.ledger.create('diagnosis','coding');self.ledger.bind(self.budget,'worker',root_member=True)
        self.arguments=dict(environment=self.ident,operation=self.operation,offset=0)

    def log(self,offset=0):return self.service.log(self.ident,self.scope,self.operation,offset)

    def no_progress_call(self):
        call,_=self.ledger.reserve_call('worker',10);self.ledger.settle('worker',call,dict(prompt_tokens=10,completion_tokens=5))

    def test_actual_log_bytes_are_readable_and_do_not_resolve_unknown_creation(self):
        body='诊断：file unavailable\n'.encode('utf-8');self.service._append(self.operation,body)
        value=self.log()
        self.assertEqual(value['text'],body.decode());self.assertEqual(value['sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual(value['start_offset'],0);self.assertEqual(value['offset'],len(body));self.assertEqual(value['bytes'],len(body))
        self.assertEqual(base64.b64decode(value['base64']),body);self.assertFalse(value['done'])
        self.assertTrue(value['creation_outcome_unknown']);self.assertEqual(self.service.store.operation(self.operation)['state'],'unknown')
        partial=self.log(1);self.assertEqual(partial['start_offset'],1);self.assertEqual(partial['sha256'],hashlib.sha256(body[1:]).hexdigest())
        self.assertEqual(partial['encoding'],'utf-8-replace')

    def test_new_log_range_resets_no_progress_but_replay_empty_and_status_do_not(self):
        self.service._append(self.operation,'new diagnostic')
        self.no_progress_call();self.no_progress_call()
        with installed('worker'):
            record_progress('project_log',self.arguments,self.log())
            self.assertEqual(self.ledger.view(self.budget)['no_progress_calls'],0)
            self.no_progress_call();record_progress('project_log',self.arguments,self.log())
            self.assertEqual(self.ledger.view(self.budget)['no_progress_calls'],1)
            for offset in (1,5):record_progress('project_log',{**self.arguments,'offset':offset},self.log(offset))
            self.assertEqual(self.ledger.view(self.budget)['no_progress_calls'],1)
            end=self.log()['offset'];record_progress('project_log',{**self.arguments,'offset':end},self.log(end))
            record_progress('project_status',self.arguments,dict(ok=True,text='status only',sha256='a'*64))
            self.assertEqual(self.ledger.view(self.budget)['no_progress_calls'],1)
            self.service._append(self.operation,' more bytes')
            record_progress('project_log',{**self.arguments,'offset':end},self.log(end))
            self.assertEqual(self.ledger.view(self.budget)['no_progress_calls'],0)
        self.assertEqual(self.ledger.view(self.budget)['consumed']['model_calls'],3)
        self.assertEqual(self.ledger.view(self.budget)['extensions'],[])

    def test_wrong_scope_range_or_digest_cannot_claim_new_progress(self):
        self.service._append(self.operation,'diagnostic');value=self.log();self.no_progress_call()
        with installed('worker'):
            for changes in [dict(environment='other'),dict(operation='other'),dict(start_offset=1),dict(bytes=0),dict(sha256='not-a-digest')]:
                record_progress('project_log',self.arguments,{**value,**changes})
        self.assertEqual(self.ledger.view(self.budget)['no_progress_calls'],1)
        with self.assertRaises(ValueError):self.service.log(self.ident,'wrong-scope',self.operation)

    def test_model_log_projection_keeps_text_and_digest_without_duplicate_base64(self):
        from desk_companion.project_tools import specs
        self.service._append(self.operation,'readable error')
        tool=next(item for item in specs(dict(collab_run='run',task_id='worker'))if item['function']['name']=='project_log')
        with patch('desk_companion.project_tools.call',return_value=self.log()):value=tool['implementation'](self.arguments)
        self.assertEqual(value['text'],'readable error');self.assertNotIn('base64',value)
        self.assertEqual(value['sha256'],hashlib.sha256(b'readable error').hexdigest())

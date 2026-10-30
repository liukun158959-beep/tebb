"""Local-only calendar capability and model tool permission boundaries."""
from datetime import datetime, timedelta
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from desk_companion.calendar_time import TZ
from desk_companion.scheduling import SchedulingService
from desk_companion.work_board import WorkBoardService


class NoRemoteCalendar:
    def __getattr__(self, name):
        raise AssertionError('Local read must not access remote adapter: '+name)


class LocalCalendarToolTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=temp.name);env.start();self.addCleanup(env.stop)
        self.board=WorkBoardService(path=Path(temp.name)/'workspace.sqlite3')
        self.local=SchedulingService(self.board,NoRemoteCalendar()).local
        self.day=(datetime.now(TZ)+timedelta(days=3)).date().isoformat()

    def create(self, title, start, end):
        result=self.local.change(dict(type='create',request_id=uuid.uuid4().hex,
            summary=title,start=start,end=end,item_id=''))
        self.assertTrue(result['ok'],result)
        return result['event']

    def test_query_filters_midnight_overlap_sorts_and_preserves_facts_without_cli(self):
        before_day=(datetime.fromisoformat(self.day)-timedelta(days=1)).date().isoformat()
        self.create('later',self.day+'T10:00:00+08:00',self.day+'T11:00:00+08:00')
        self.create('overlap',before_day+'T23:30:00+08:00',self.day+'T00:30:00+08:00')
        self.create('outside',before_day+'T08:00:00+08:00',before_day+'T09:00:00+08:00')
        before=copy.deepcopy(self.local.load()['events'])
        with patch('subprocess.run',side_effect=AssertionError('No CLI permitted')):
            result=self.local.query(self.day)
        self.assertTrue(result['ok'],result)
        self.assertEqual([e['summary'] for e in result['events']],['overlap','later'])
        self.assertEqual(result['scope'],'local_snapshot');self.assertFalse(result['remote_refreshed'])
        self.assertEqual(result['total'],2);self.assertFalse(result['truncated'])
        self.assertEqual(self.local.load()['events'],before)

    def test_invalid_day_does_not_read_and_empty_day_is_not_a_failure(self):
        with patch.object(self.local,'load',side_effect=AssertionError('invalid day must fail before reading')):
            for day in ('today','2040-99-02',None):
                self.assertFalse(self.local.query(day)['ok'])
        result=self.local.query(self.day)
        self.assertTrue(result['ok']);self.assertEqual(result['events'],[])
        self.assertEqual(result['total'],0)

    def test_overflow_is_explicit_and_retains_real_total(self):
        row=dict(event_id='e',summary='sample',start=self.day+'T10:00:00+08:00',
                 end=self.day+'T11:00:00+08:00',sync_state='local',verification='confirmed',checked_at='saved')
        with patch.object(self.local,'load',return_value=dict(ok=True,events=[{**row,'event_id':str(i)} for i in range(101)])):
            result=self.local.query(self.day)
        self.assertEqual(len(result['events']),100);self.assertEqual(result['total'],101)
        self.assertTrue(result['truncated'])

    def worker(self, permissions, force_call=False):
        from desk_companion.collaboration_worker import execute
        events=[];offered=[]
        day=self.day
        class LLM:
            def chat(_, messages, tools, on_delta):
                offered.append([t['function']['name'] for t in tools])
                if len(offered)==1 and (force_call or 'read_local_calendar' in offered[-1]):
                    return dict(message=dict(tool_calls=[dict(id='calendar-read',type='function',
                        function=dict(name='read_local_calendar',arguments=json.dumps(dict(day=day))))]),usage={})
                result=json.loads(next((m['content'] for m in reversed(messages) if m['role']=='tool'),'{}'))
                return dict(message=dict(content=json.dumps(dict(count=len(result.get('events',[])),
                    titles=[e['summary'] for e in result.get('events',[])]))),usage={})
        request=dict(kind='task',text='Read the specified local day only',context=[],
            role=dict(id='research',name='Read-only reviewer',role='Read facts only',tools=permissions),
            collab_path=str(self.board.path),collab_run=uuid.uuid4().hex,model_id='fixture',
            limits=dict(call_timeout=10,task_timeout=30),sampling=dict(reasoning_effort='low',temperature=0.1,top_p=1),
            applied=0,budget=4,search_budget=0,fetch_budget=0)
        with (patch('desk_companion.model_catalog.require_item',return_value=dict(id='fixture',model='fixture',api_key='synthetic-key')),
              patch('subprocess.run',side_effect=AssertionError('No external CLI permitted'))):
            execute(request,lambda kind,data:events.append((kind,data)),llm=LLM())
        return events,offered

    def test_authorized_worker_reads_real_local_service_with_only_readonly_tool(self):
        self.create('synthetic event',self.day+'T10:00:00+08:00',self.day+'T11:00:00+08:00')
        before=copy.deepcopy(self.local.load()['events'])
        events,offered=self.worker(['本地日程读取'])
        self.assertEqual(offered[0],['read_local_calendar'])
        self.assertEqual(json.loads(events[-1][1]),dict(count=1,titles=['synthetic event']))
        self.assertTrue(any(kind=='tool_start' and data.get('read_only') is True for kind,data in events))
        self.assertTrue(any(kind=='tool_end' and data.get('status')=='success' for kind,data in events))
        self.assertEqual(self.local.load()['events'],before)

    def test_ungranted_tool_is_unavailable_and_cannot_be_called_by_a_model(self):
        # Initialize the shared workspace without configuring any model or OS CLI.
        self.local.load()
        events,offered=self.worker([])
        self.assertEqual(offered[0],[])
        self.assertFalse(any(kind=='tool_start' for kind,data in events))
        with self.assertRaisesRegex(ValueError,'白名单'):
            self.worker([],force_call=True)

    def test_shipped_partners_do_not_gain_the_new_permission_on_migration(self):
        from desk_companion.collaboration import seed,migrate_companions
        state=seed()
        self.assertTrue(all('本地日程读取' not in a['tools'] for a in state['agents']))
        before=copy.deepcopy(state['agents']);migrate_companions(state)
        self.assertEqual(state['agents'],before)

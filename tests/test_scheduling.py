"""Scheduling state, durable external receipts, isolated IO, and actual worker-free boundaries."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timedelta
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import uuid

from desk_companion import schedule_domain
from desk_companion.board_data import calendar_operation
from desk_companion.calendar_adapter import CalendarAdapter, CalendarError, day_range, event_view
from desk_companion.calendar_time import TZ
from desk_companion.resource_lock import WRITES
from desk_companion.scheduling import SchedulingService
from desk_companion.work_board import WorkBoardService
from tests.schedule_fixture import DAY, START, END, FakeCalendar, event


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        scope = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=self.temp.name); scope.start(); self.addCleanup(scope.stop)
        self.board = WorkBoardService()
        self.calendar = FakeCalendar()
        self.service = SchedulingService(self.board, self.calendar)
        self.item = self.new_item()

    def new_item(self):
        page = self.board.change(dict(type='create', request_id=uuid.uuid4().hex, fields=dict(title='安排工作')))
        self.assertTrue(page['ok'], page)
        return next(i for i in page['items'] if i['id'] == page['receipt']['item_id'])

    def item_now(self, item=None):
        return next(i for i in self.board.load()['items'] if i['id'] == (item or self.item)['id'])

    def action(self, kind='create', item=None, **fields):
        item = self.item_now(item)
        return dict(type=kind, request_id=uuid.uuid4().hex, item_id=item['id'], version=item['version'], day=DAY,
                    **(dict(summary='独立安排', start=START, end=END) if kind == 'create' else {}), **fields)

    def test_native_adapter_preserves_absolute_identity_and_only_explicit_creation_fields(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[1:3] == ['calendars','primary']:
                data = dict(calendars=[dict(calendar=dict(calendar_id='calendar-one', type='primary', role='owner'))])
            elif args[2] == 'instance_view': data = dict(items=[])
            else:
                data = dict(event=dict(event_id='native_0', summary='Native', recurrence='', status='confirmed',
                    start_time=dict(timestamp=str(int(datetime.fromisoformat(START).timestamp()))),
                    end_time=dict(timestamp=str(int(datetime.fromisoformat(END).timestamp())))))
            return json.dumps(dict(ok=True, data=data))
        adapter = CalendarAdapter(run)
        self.assertEqual(adapter.primary()['id'], 'calendar-one')
        self.assertEqual(adapter.day('calendar-one', DAY), [])
        a, b = day_range(DAY); self.assertEqual(a.utcoffset(), timedelta(hours=8)); self.assertEqual(b-a, timedelta(days=1))
        created = adapter.create('calendar-one', 'stable-key', dict(summary='Native', start=START, end=END))
        self.assertTrue(created['eligible']); self.assertTrue(created['start'].endswith('+08:00'))
        command = calls[-1]; body = json.loads(command[command.index('--data')+1])
        self.assertEqual(body['vchat'], dict(vc_type='no_meeting')); self.assertEqual(body['reminders'], [])
        self.assertFalse(body['need_notification']); self.assertNotIn('attendees', body)
        self.assertIn('stable-key', command); self.assertIn('user', command)

    def test_error_codes_and_event_types_never_turn_into_false_empty_or_editable_events(self):
        for code, status in ((193003,'deleted'),(193001,'missing'),(191002,'unavailable'),(190004,'read_failed')):
            def run(*args, **kwargs): raise RuntimeError(json.dumps(dict(error=dict(code=code), secret='PRIVATE_KEY')))
            with self.assertRaises(CalendarError) as caught: CalendarAdapter(run).get('calendar-one','x_0')
            self.assertEqual(caught.exception.status,status); self.assertNotIn('PRIVATE_KEY',str(caught.exception))
        for raw in (dict(recurrence='FREQ=DAILY'), dict(status='cancelled'), dict(is_exception=True)):
            self.assertFalse(event(**raw)['eligible'])
        self.assertFalse(event('series_1720000000')['eligible'])
        all_day = event_view(dict(event_id='all_0',summary='all',start_time=dict(date=DAY),end_time=dict(date=DAY)), 'calendar-one')
        self.assertFalse(all_day['eligible']); self.assertEqual(all_day['start'],DAY)

    def test_create_once_then_link_unlink_and_archive_preserve_remote_and_ai_state(self):
        request = self.action()
        first = self.service.change(request); self.assertTrue(first['ok'],first)
        self.assertEqual(first['operation']['state'],'linked'); self.assertEqual(len(self.calendar.creates),1)
        self.assertTrue(self.service.change(request)['ok']); self.assertEqual(len(self.calendar.creates),1)
        self.assertFalse(self.service.change({**request,'summary':'different'})['ok'])
        existing = self.service.change(self.action('link',calendar_id='calendar-one',event_id='existing_0'))
        self.assertTrue(existing['ok'],existing); self.assertEqual(len(existing['links']),2)
        item = self.item_now()
        self.assertEqual(item['state'],'todo'); self.assertEqual(item['links'],[]); self.assertEqual(len(item['schedules']['events']),2)
        self.assertTrue(self.board.change(dict(type='archive',request_id=uuid.uuid4().hex,id=item['id'],version=item['version'],archived=True))['ok'])
        self.assertFalse(self.service.change(self.action('unlink',calendar_id='calendar-one',event_id='existing_0'))['ok'])
        self.assertEqual(len(self.calendar.events),2)

    def test_single_event_owner_and_stale_versions_are_enforced_atomically(self):
        other = self.new_item()
        requests = [self.action('link', item=i, calendar_id='calendar-one',event_id='existing_0') for i in (self.item,other)]
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(self.service.change,requests))
        self.assertEqual(sum(r['ok'] for r in results),1)
        self.assertEqual(len(self.service.load(DAY)['links']),1)
        self.assertFalse(self.service.change({**requests[0],'request_id':uuid.uuid4().hex})['ok'])

    def test_network_wait_reserves_only_target_item_without_database_or_global_write_lock(self):
        entered, release = threading.Event(), threading.Event()
        original = self.calendar.create
        def wait(*args): entered.set(); self.assertTrue(release.wait(10)); return original(*args)
        self.calendar.create = wait
        other = self.new_item(); request = self.action()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.service.change,request)
            try:
                self.assertTrue(entered.wait(10))
                same=self.board.change(dict(type='move',request_id=uuid.uuid4().hex,id=self.item['id'],version=self.item['version'],state='doing'))
                self.assertFalse(same['ok'])
                self.assertTrue(self.board.change(dict(type='move',request_id=uuid.uuid4().hex,id=other['id'],version=other['version'],state='doing'))['ok'])
                self.assertTrue(self.service.load(DAY)['ok'])
                with WRITES.hold(blocking=False): pass
                with self.board.workspace_transaction() as (state,_): self.assertTrue(state['work_items'])
            finally: release.set()
            self.assertTrue(future.result(10)['ok'])

    def test_legacy_calendar_write_lease_conflicts_immediately_without_external_call(self):
        entered, release=threading.Event(),threading.Event()
        def hold():
            with calendar_operation(): entered.set(); release.wait(10)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(hold); self.assertTrue(entered.wait(5))
            try: result=self.service.change(self.action()); self.assertFalse(result['ok']); self.assertEqual(self.calendar.creates,[])
            finally: release.set(); future.result(5)
        self.assertTrue(self.service.change(self.action())['ok'])

    def test_lost_remote_ack_and_restart_never_replay_create_and_explicit_recovery_links_existing(self):
        original=self.calendar.create
        def lose(*args): original(*args); raise TimeoutError('lost acknowledgement')
        self.calendar.create=lose; request=self.action()
        result=self.service.change(request); self.assertEqual(result['change_state'],'unconfirmed')
        recovered=SchedulingService(self.board,self.calendar)
        self.assertFalse(recovered.change(request)['ok']); self.assertFalse(recovered.change(self.action())['ok'])
        item=self.item_now()
        self.assertTrue(self.board.change(dict(type='move',request_id=uuid.uuid4().hex,id=item['id'],version=item['version'],state='review'))['ok'])
        fixed=recovered.change(self.action('resolve',operation_id=request['request_id'],resolution='link',calendar_id='calendar-one',event_id='created-1_0'))
        self.assertTrue(fixed['ok'],fixed); self.assertEqual(len(self.calendar.creates),1)
        self.assertEqual(self.service.load(DAY,request_id=request['request_id'])['operation']['state'],'resolved')

    def test_remote_success_local_association_failure_retains_event_for_manual_recovery(self):
        request=self.action()
        with patch('desk_companion.schedule_domain.attach',side_effect=OSError('local disk')):
            failure=self.service.change(request)
        self.assertFalse(failure['ok']); self.assertEqual(failure['operation']['event']['event_id'],'created-1_0')
        self.assertEqual(self.service.load(DAY)['links'],[])
        resolved=self.service.change(self.action('resolve',operation_id=request['request_id'],resolution='link',calendar_id='calendar-one',event_id='created-1_0'))
        self.assertTrue(resolved['ok'],resolved); self.assertEqual(len(self.calendar.creates),1)

    def test_preparation_ack_failure_prevents_remote_write_and_absence_resolution_is_explicit(self):
        request=self.action(); original=self.service._save_operation; once=True
        def lose(op):
            nonlocal once
            original(op)
            if once: once=False; raise OSError('commit ack lost')
        with patch.object(self.service,'_save_operation',side_effect=lose): failure=self.service.change(request)
        self.assertEqual(failure['change_state'],'unconfirmed'); self.assertEqual(self.calendar.creates,[])
        action=self.action('resolve',operation_id=request['request_id'],resolution='absent',confirmed=True)
        self.assertTrue(self.service.change(action)['ok'])
        self.assertTrue(self.service.change(self.action())['ok'])

    def test_link_commit_ack_loss_and_post_commit_refresh_failure_keep_confirmed_relationship(self):
        original=self.service._finish
        def lose(*args,**kwargs): original(*args,**kwargs); raise OSError('commit acknowledgement lost')
        with patch.object(self.service,'_finish',side_effect=lose): result=self.service.change(self.action('link',calendar_id='calendar-one',event_id='existing_0'))
        self.assertTrue(result['ok'],result); self.assertEqual(len(self.service.load(DAY)['links']),1)
        with patch.object(self.service,'load',return_value=dict(ok=False,error='read failure')):
            failure=self.service.change(self.action())
        self.assertEqual(failure['change_state'],'committed'); self.assertEqual(len(self.calendar.creates),1)

    def test_query_failures_preserve_snapshots_and_distinguish_missing_and_permission(self):
        self.assertTrue(self.service.change(self.action('link',calendar_id='calendar-one',event_id='existing_0'))['ok'])
        loaded=self.service.load(DAY,self.item['id'],True); self.assertTrue(loaded['events_valid'])
        self.calendar.fail=CalendarError('denied','unavailable')
        failed=self.service.load(DAY,self.item['id'],True)
        self.assertFalse(failed['events_valid']); self.assertTrue(failed['events']); self.assertEqual(failed['links'][0]['event']['verification'],'unavailable')
        self.assertTrue(self.item_now()['schedules']['unchecked'])
        self.calendar.fail=None; self.calendar.events.pop('existing_0')
        missing=self.service.load(DAY,self.item['id'],True)
        self.assertEqual(missing['links'][0]['event']['verification'],'missing')
        self.assertIsNone(self.item_now()['schedules']['next'])

    def test_new_account_does_not_silently_rebind_existing_calendar_identity(self):
        self.assertTrue(self.service.change(self.action('link',calendar_id='calendar-one',event_id='existing_0'))['ok'])
        self.calendar.calendar_id='calendar-two'
        page=self.service.load(DAY,self.item['id'],True)
        self.assertEqual(page['links'][0]['event']['verification'],'unavailable')

    def test_confirmed_create_updates_cached_day_and_an_older_read_cannot_hide_it(self):
        self.service.load(DAY, refresh=True)
        entered, release = threading.Event(), threading.Event()
        original = self.calendar.day
        def delayed(*args):
            result = original(*args); entered.set(); self.assertTrue(release.wait(10)); return result
        with patch.object(self.calendar, 'day', side_effect=delayed), ThreadPoolExecutor(max_workers=1) as pool:
            read = pool.submit(self.service.load, DAY, '', True)
            self.assertTrue(entered.wait(5))
            try:
                result = self.service.change(self.action())
                self.assertTrue(result['ok'], result)
                self.assertIn('created-1_0', [e['event_id'] for e in result['events']])
            finally: release.set()
            self.assertIn('created-1_0', [e['event_id'] for e in read.result(5)['events']])

    def test_latest_item_read_wins_even_when_refreshing_different_dates(self):
        self.service.change(self.action('link', calendar_id='calendar-one', event_id='existing_0'))
        entered, release = threading.Event(), threading.Event()
        original = self.calendar.get
        # Each load owns a verification pool; identify its oldest verification by first call.
        once = True
        def first_delayed(*args):
            nonlocal once
            result = original(*args)
            if once:
                once = False; entered.set(); self.assertTrue(release.wait(10))
            return result
        with patch.object(self.calendar, 'get', side_effect=first_delayed), ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(self.service.load, DAY, self.item['id'], True)
            self.assertTrue(entered.wait(5))
            try:
                self.calendar.events['existing_0']['summary'] = 'new remote title'
                tomorrow = (datetime.fromisoformat(DAY)+timedelta(days=1)).date().isoformat()
                new = self.service.load(tomorrow, self.item['id'], True)
                self.assertEqual(new['links'][0]['event']['summary'], 'new remote title')
            finally: release.set()
            self.assertEqual(old.result(5)['links'][0]['event']['summary'], 'new remote title')

    def test_known_created_event_requires_its_own_identity_and_cannot_be_declared_absent(self):
        request = self.action()
        with patch('desk_companion.schedule_domain.attach', side_effect=OSError('disk')):
            self.service.change(request)
        wrong = self.service.change(self.action('resolve', operation_id=request['request_id'], resolution='link',
            calendar_id='calendar-one', event_id='existing_0'))
        self.assertFalse(wrong['ok'])
        absent = self.service.change(self.action('resolve', operation_id=request['request_id'], resolution='absent', confirmed=True))
        self.assertFalse(absent['ok'])
        self.calendar.events.pop('created-1_0')
        absent = self.service.change(self.action('resolve', operation_id=request['request_id'], resolution='absent', confirmed=True))
        self.assertTrue(absent['ok'], absent)

    def test_missing_request_reconciliation_prevents_a_late_original_create(self):
        late = self.action()
        self.assertIsNone(self.service.load(DAY, request_id=late['request_id'])['operation'])
        resolved = self.service.change(self.action('resolve', operation_id=late['request_id'], resolution='absent', confirmed=True))
        self.assertTrue(resolved['ok'], resolved)
        self.assertFalse(self.service.change(late)['ok'])
        self.assertEqual(self.calendar.creates, [])
        self.assertTrue(self.service.change(self.action())['ok'])

    def test_full_item_is_rejected_before_remote_create_and_internal_receipts_stay_out_of_collaboration(self):
        with self.board.workspace_transaction() as (state, _):
            for number in range(50): schedule_domain.attach(state, self.item['id'], event(f'limit-{number}_0'))
        failure = self.service.change(self.action())
        self.assertFalse(failure['ok']); self.assertEqual(self.calendar.creates, [])
        self.service.load(DAY, self.item['id'], refresh=True)
        from desk_companion.collaboration import Collaboration
        collab = Collaboration(managers=dict(work=Mock(settings={}), chat=Mock(settings={})), start=False)
        self.addCleanup(collab.shutdown)
        snapshot = collab.snapshot()
        self.assertNotIn('work_items', snapshot)
        self.assertFalse(any(key.startswith('schedule_') for key in snapshot))

    def test_next_schedule_prefers_ongoing_and_marks_unverified_information(self):
        now=datetime.now(TZ)
        ongoing=event('ongoing_0',(now-timedelta(minutes=10)).isoformat(),(now+timedelta(minutes=10)).isoformat())
        future=event('future_0',(now+timedelta(minutes=20)).isoformat(),(now+timedelta(minutes=40)).isoformat())
        state={}; schedule_domain.attach(state,'item',future); schedule_domain.attach(state,'item',ongoing)
        self.assertEqual(schedule_domain.item_schedule_view(state,'item')['next']['event_id'],'ongoing_0')
        schedule_domain.update_event(state,dict(ongoing,verification='read_failed',error='offline'))
        self.assertTrue(schedule_domain.item_schedule_view(state,'item')['unchecked'])

    def test_no_gui_service_does_not_construct_execution_queues(self):
        code='''
import sys
from unittest.mock import patch
from tests.gui_import_guard import NoGui,assert_no_gui
sys.meta_path.insert(0,NoGui())
from desk_companion.work_board import WorkBoardService
from desk_companion.scheduling import SchedulingService
from tests.schedule_fixture import FakeCalendar,DAY,START,END
with patch('desk_companion.tasks.TaskManager',side_effect=AssertionError('no queues')):
    board=WorkBoardService()
    item=board.change(dict(type='create',request_id='schedule-no-gui',fields=dict(title='offline')))['items'][0]
    service=SchedulingService(board,FakeCalendar())
    result=service.change(dict(type='create',request_id='schedule-create',item_id=item['id'],version=item['version'],day=DAY,summary='offline',start=START,end=END))
    assert result['ok'],result
assert_no_gui()
'''
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,encoding='utf-8',timeout=30,
            env={**os.environ,'PYTHONUTF8':'1'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

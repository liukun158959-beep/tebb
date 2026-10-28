"""Personal weekly scheduling with fake remote IO, durable receipts and actual process locks."""
import copy
from datetime import datetime, timedelta
import json
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from desk_companion.calendar_adapter import CalendarAdapter, CalendarError
from desk_companion.calendar_time import TZ
from desk_companion.resource_lock import WRITES
from desk_companion.schedule_domain import week_range
from desk_companion.scheduling import SchedulingService
from desk_companion.work_board import WorkBoardService
from tests.schedule_fixture import DAY, START, END, TimeCalendar


def native_event(**extra):
    raw = dict(event_id='existing_0', summary='个人安排', status='confirmed', recurrence='', is_exception=False,
        organizer_calendar_id='calendar-one', event_organizer=dict(user_id='fixture-self'),
        start_time=dict(timestamp=str(int(datetime.fromisoformat(START).timestamp()))),
        end_time=dict(timestamp=str(int(datetime.fromisoformat(END).timestamp()))))
    return dict(raw, **extra)


class AdapterTimeTests(unittest.TestCase):
    def adapter(self, pages=None, raw=None, calendar=None):
        calls = []
        pages = iter(pages or [dict(items=[], has_more=False)])
        def run(args, **kwargs):
            calls.append(args)
            if args[1:3] == ['calendars', 'primary']:
                data = dict(calendars=[dict(user_id='fixture-self', calendar=calendar or dict(
                    calendar_id='calendar-one', type='primary', role='owner', is_deleted=False, is_third_party=False))])
            elif args[1:3] == ['event.attendees', 'list']: data = next(pages)
            elif args[2] == 'instance_view': data = dict(items=[raw or native_event()])
            else: data = dict(event=raw or native_event())
            return json.dumps(dict(ok=True, data=data))
        return CalendarAdapter(run), calls

    def test_successful_empty_object_is_an_empty_day_and_week_but_malformed_data_still_fails(self):
        for data in ({},{'items':[]}):
            adapter=CalendarAdapter(lambda *args,**kwargs:json.dumps(dict(ok=True,data=data)))
            self.assertEqual(adapter.week('calendar-one',DAY),[])
            self.assertEqual(adapter.day('calendar-one',DAY),[])
        for data in ({'items':None},{'items':{}},{'unexpected':'payload'}):
            adapter=CalendarAdapter(lambda *args,**kwargs:json.dumps(dict(ok=True,data=data)))
            with self.assertRaises(CalendarError):adapter.week('calendar-one',DAY)
            with self.assertRaises(CalendarError):adapter.day('calendar-one',DAY)
        adapter=CalendarAdapter(lambda *args,**kwargs:json.dumps(dict(ok=False,data={})))
        with self.assertRaises(CalendarError):adapter.week('calendar-one',DAY)

    def test_week_is_seven_beijing_days_and_patch_changes_only_time_fields(self):
        adapter, calls = self.adapter()
        rows = adapter.week('calendar-one', DAY)
        self.assertEqual(len(rows), 1)
        args = calls[-1]
        a, b = week_range(DAY)
        self.assertEqual(a.weekday(), 0); self.assertEqual((b-a).days, 7)
        self.assertEqual(args[args.index('--start-time')+1], str(int(a.timestamp())))
        adapter.patch_time('calendar-one', 'existing_0', START, END)
        body = json.loads(calls[-1][calls[-1].index('--data')+1])
        self.assertEqual(set(body), {'start_time', 'end_time', 'need_notification'})
        self.assertFalse(body['need_notification'])
        self.assertEqual(body['start_time']['timezone'], 'Asia/Shanghai')
        self.assertNotIn('--idempotency-key', calls[-1])

    def test_ended_personal_event_checks_attendees_and_remains_editable(self):
        start='2026-01-01T05:00:00+08:00';end='2026-01-01T05:30:00+08:00'
        raw=native_event(start_time=dict(timestamp=str(int(datetime.fromisoformat(start).timestamp()))),
                         end_time=dict(timestamp=str(int(datetime.fromisoformat(end).timestamp()))))
        adapter,calls=self.adapter(raw=raw)
        result=adapter.inspect(adapter.primary(),'existing_0')
        self.assertTrue(result['editable'],result)
        self.assertTrue(any(args[1:3]==['event.attendees','list'] for args in calls))
        adapter,_=self.adapter(raw=raw,pages=[dict(items=[dict(type='user',user_id='another')],has_more=False)])
        self.assertFalse(adapter.inspect(adapter.primary(),'existing_0')['editable'])

    def test_empty_and_only_verified_self_organizer_are_editable(self):
        for items in ([], [dict(type='user', user_id='fixture-self', is_organizer=True)]):
            adapter, _ = self.adapter([dict(items=items, has_more=False)])
            self.assertTrue(adapter.inspect(adapter.primary(), 'existing_0')['editable'])

    def test_primary_optional_false_flags_may_be_omitted_by_feishu(self):
        adapter, _ = self.adapter(calendar=dict(calendar_id='calendar-one', type='primary', role='owner'))
        self.assertTrue(adapter.inspect(adapter.primary(), 'existing_0')['editable'])
        for flag in ('is_deleted', 'is_third_party'):
            adapter, _ = self.adapter(calendar=dict(calendar_id='calendar-one', type='primary', role='owner', **{flag: True}))
            self.assertFalse(adapter.inspect(adapter.primary(), 'existing_0')['editable'])

    def test_other_declined_removed_resources_and_unknown_attendees_are_readonly(self):
        for row in (dict(type='user', user_id='other', rsvp_status='decline'),
                    dict(type='resource', room_id='room', rsvp_status='removed'),
                    dict(type='user', user_id='fixture-self', is_organizer=False), {}, 'invalid'):
            adapter, _ = self.adapter([dict(items=[row], has_more=False)])
            self.assertFalse(adapter.inspect(adapter.primary(), 'existing_0')['editable'])

    def test_attendance_is_checked_to_an_explicit_complete_page(self):
        pages = [dict(items=[], has_more=True, page_token='next'),
                 dict(items=[dict(type='chat', chat_id='group')], has_more=False)]
        adapter, calls = self.adapter(pages)
        self.assertFalse(adapter.inspect(adapter.primary(), 'existing_0')['editable'])
        self.assertIn('--page-token', calls[-1]); self.assertIn('next', calls[-1])
        for pages in ([dict(items=[])], [dict(items=[], has_more=True)],
                      [dict(items=[], has_more=True, page_token='loop')]*2,
                      [dict(items=[], has_more=0)], [dict(items=None, has_more=False)]):
            adapter, _ = self.adapter(pages)
            self.assertFalse(adapter.inspect(adapter.primary(), 'existing_0')['editable'])

    def test_unknown_source_type_organizer_identity_and_readonly_calendar_never_enable_editing(self):
        for extra in (dict(source='unknown-special'), dict(is_exception=True), dict(recurrence='FREQ=DAILY'),
                      dict(event_organizer=dict(user_id='other')), dict(organizer_calendar_id='other'),
                      dict(end_time=dict(timestamp=str(int(datetime.fromisoformat(END).timestamp()+86400))))):
            adapter, _ = self.adapter(raw=native_event(**extra))
            self.assertFalse(adapter.inspect(adapter.primary(), 'existing_0')['editable'])
        adapter, _ = self.adapter(calendar=dict(calendar_id='calendar-one', type='primary', role='reader',
                                               is_deleted=False, is_third_party=False))
        self.assertFalse(adapter.inspect(adapter.primary(), 'existing_0')['editable'])


class WeekServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        scope = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=temp.name); scope.start(); self.addCleanup(scope.stop)
        self.board, self.calendar = WorkBoardService(), TimeCalendar()
        self.service = SchedulingService(self.board, self.calendar)
        self.item = self.new_item()

    def new_item(self):
        page = self.board.change(dict(type='create', request_id=uuid.uuid4().hex, fields=dict(title='安排工作')))
        return next(i for i in page['items'] if i['id'] == page['receipt']['item_id'])

    def action(self, **extra):
        return dict(type='retime', request_id=uuid.uuid4().hex, calendar_id='calendar-one', event_id='existing_0',
                    day=DAY, original_start=START, original_end=END,
                    start=DAY+'T11:00:00+08:00', end=DAY+'T12:00:00+08:00', **extra)

    def link(self):
        result = self.service.change(dict(type='link', request_id=uuid.uuid4().hex, item_id=self.item['id'],
            version=self.item['version'], calendar_id='calendar-one', event_id='existing_0', day=DAY))
        self.assertTrue(result['ok'], result)

    def test_unlinked_edit_keeps_identity_and_is_never_replayed(self):
        self.service.load_week(DAY, True)
        request = self.action(); result = self.service.change(request)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['operation']['state'], 'retimed')
        self.assertEqual(self.calendar.patches[0]['event_id'], 'existing_0')
        self.assertTrue(self.service.change(request)['ok'])
        self.assertFalse(self.service.change(dict(request, start=START))['ok'])
        self.assertEqual(len(self.calendar.patches), 1)
        self.assertEqual(self.board.load()['items'][0]['state'], 'todo')

    def test_linked_edit_updates_day_week_and_next_arrangement_without_changing_business_state(self):
        self.link(); self.service.load(DAY, refresh=True); self.service.load_week(DAY, True)
        result = self.service.change(self.action()); self.assertTrue(result['ok'], result)
        self.assertEqual(self.service.load(DAY)['events'][0]['start'], result['operation']['start'])
        self.assertEqual(self.service.load_week(DAY)['events'][0]['start'], result['operation']['start'])
        item = self.board.load()['items'][0]
        self.assertEqual(item['state'], 'todo'); self.assertEqual(item['links'], [])
        self.assertEqual(item['schedules']['next']['start'], result['operation']['start'])

    def test_stale_original_time_permission_and_changed_identity_prevent_patch(self):
        self.calendar.events['existing_0']['start'] = DAY+'T08:00:00+08:00'
        result = self.service.change(self.action())
        self.assertFalse(result['ok']); self.assertTrue(result['conflict']); self.assertEqual(self.calendar.patches, [])
        self.assertEqual(result['operation']['event']['start'], DAY+'T08:00:00+08:00')
        self.calendar.events['existing_0']['start'] = START
        with patch.object(self.calendar, 'inspect', return_value=dict(self.calendar.events['existing_0'], editable=False, edit_reason='others')):
            self.assertFalse(self.service.change(self.action())['ok'])
        self.assertEqual(self.calendar.patches, [])

    def test_validation_cross_day_past_short_resize_and_no_change(self):
        for fields in (dict(end=DAY+'T10:00:00+08:00'),
                       dict(end=(datetime.fromisoformat(END)+timedelta(days=1)).isoformat()),
                       dict(end=DAY+'T11:05:00+08:00')):
            self.assertFalse(self.service.change(dict(self.action(), **fields))['ok'])
        result = self.service.change(dict(self.action(), start=START, end=END))
        self.assertTrue(result['ok'], result); self.assertEqual(self.calendar.patches, [])
        short_end = DAY+'T09:05:00+08:00'; self.calendar.events['existing_0']['end'] = short_end
        result = self.service.change(dict(self.action(), original_end=short_end, end=DAY+'T11:05:00+08:00'))
        self.assertTrue(result['ok'], result)

    def test_lost_patch_ack_survives_restart_and_reconciliation_does_not_resend(self):
        self.link(); request = self.action(); original = self.calendar.patch_time
        def lost(*args): original(*args); raise TimeoutError('lost response')
        with patch.object(self.calendar, 'patch_time', side_effect=lost): result = self.service.change(request)
        self.assertEqual(result['change_state'], 'unconfirmed', result)
        service = SchedulingService(self.board, self.calendar)
        self.assertFalse(service.change(request)['ok']); self.assertFalse(service.change(self.action())['ok'])
        current_item = self.board.load()['items'][0]
        unlink = dict(type='unlink', request_id=uuid.uuid4().hex, item_id=self.item['id'], version=current_item['version'],
                      calendar_id='calendar-one', event_id='existing_0', day=DAY)
        self.assertFalse(service.change(unlink)['ok'])
        self.assertTrue(self.board.change(dict(type='move', id=current_item['id'], version=current_item['version'],
            state='doing', request_id=uuid.uuid4().hex))['ok'])
        inspected = service.inspect_event('calendar-one', 'existing_0')['event']
        self.assertFalse(inspected['editable'])
        resolved = service.change(dict(type='reconcile_time', request_id=uuid.uuid4().hex, operation_id=request['request_id'],
            calendar_id='calendar-one', event_id='existing_0', day=DAY, confirmed=True,
            original_start=inspected['start'], original_end=inspected['end']))
        self.assertTrue(resolved['ok'], resolved); self.assertEqual(len(self.calendar.patches), 1)
        self.assertEqual(service.load_week(DAY, request_id=request['request_id'])['operation']['state'], 'observed')

    def test_reconcile_missing_identity_seals_late_original_and_does_not_call_patch(self):
        request = self.action()
        result = self.service.change(dict(type='reconcile_time', request_id=uuid.uuid4().hex, operation_id=request['request_id'],
            calendar_id='calendar-one', event_id='existing_0', day=DAY, confirmed=True, original_start=START, original_end=END))
        self.assertTrue(result['ok'], result)
        self.assertFalse(self.service.change(request)['ok']); self.assertEqual(self.calendar.patches, [])

    def test_pending_event_and_item_are_reserved_reads_other_items_and_global_lock_continue(self):
        self.link(); other = self.new_item()
        entered, release = threading.Event(), threading.Event(); original = self.calendar.patch_time
        def waiting(*args): entered.set(); self.assertTrue(release.wait(10)); return original(*args)
        with patch.object(self.calendar, 'patch_time', side_effect=waiting), ThreadPoolExecutor(1) as pool:
            job = pool.submit(self.service.change, self.action())
            try:
                self.assertTrue(entered.wait(5))
                self.assertFalse(self.service.change(self.action())['ok'])
                item = self.board.load()['items'][0]
                move = lambda i: self.board.change(dict(type='move', id=i['id'], version=i['version'], state='doing', request_id=uuid.uuid4().hex))
                self.assertFalse(move(next(i for i in self.board.load()['items'] if i['id']==self.item['id']))['ok'])
                self.assertTrue(move(other)['ok']); self.assertTrue(self.service.load_week(DAY)['ok'])
                with WRITES.hold(blocking=False): pass
                with self.board.workspace_transaction(): pass
            finally: release.set()
            self.assertTrue(job.result(10)['ok'])

    def test_old_week_response_cannot_replace_a_confirmed_move(self):
        self.service.load_week(DAY, True)
        entered, release = threading.Event(), threading.Event(); snapshot = self.calendar.week('calendar-one', DAY)
        def old(*args): entered.set(); release.wait(10); return copy.deepcopy(snapshot)
        with patch.object(self.calendar, 'week', side_effect=old), ThreadPoolExecutor(1) as pool:
            read = pool.submit(self.service.load_week, DAY, True); self.assertTrue(entered.wait(5))
            try: self.assertTrue(self.service.change(self.action())['ok'])
            finally: release.set()
            page = read.result(10)
        self.assertEqual(page['events'][0]['start'], DAY+'T11:00:00+08:00')

    def test_failed_week_read_retains_snapshot_and_flags_unverified(self):
        first = self.service.load_week(DAY, True)
        self.calendar.fail = CalendarError('offline')
        second = self.service.load_week(DAY, True)
        self.assertFalse(second['events_valid']); self.assertEqual(second['events'], first['events'])
        self.assertIn('offline', second['events_error'])

    def test_older_detail_read_cannot_overwrite_a_newer_week_observation(self):
        self.service.load_week(DAY, True)
        old=self.calendar.inspect(self.calendar.primary(),'existing_0')
        entered,release=threading.Event(),threading.Event()
        def delayed(*args):entered.set();release.wait(5);return old
        with patch.object(self.calendar,'inspect',side_effect=delayed),ThreadPoolExecutor(1) as pool:
            waiting=pool.submit(self.service.inspect_event,'calendar-one','existing_0')
            self.assertTrue(entered.wait(5))
            try:
                self.calendar.events['existing_0']['start']=DAY+'T08:30:00+08:00'
                newer=self.service.load_week(DAY,True)['events'][0]
            finally:release.set()
            result=waiting.result(5)
        self.assertEqual(result['event']['start'],newer['start'])
        self.assertEqual(self.service.load_week(DAY)['events'][0]['start'],newer['start'])

    def test_post_patch_third_time_is_not_misreported_as_completed(self):
        original = self.calendar.patch_time
        def competing(*args):
            result = original(*args); self.calendar.events['existing_0']['start'] = DAY+'T10:30:00+08:00'
            return result
        with patch.object(self.calendar, 'patch_time', side_effect=competing): result = self.service.change(self.action())
        self.assertFalse(result['ok']); self.assertEqual(result['change_state'], 'unconfirmed')
        self.assertEqual(result['operation']['current_event']['start'], DAY+'T10:30:00+08:00')

    def test_journal_failure_before_remote_prevents_any_patch(self):
        with patch.object(self.service, '_save_operation', side_effect=OSError('disk')):
            result = self.service.change(self.action())
        self.assertFalse(result['ok']); self.assertEqual(self.calendar.patches, [])

    def test_calling_journal_ack_loss_does_not_start_patch_and_committed_ack_loss_reports_fact(self):
        original=self.service._save_operation
        def lost(operation):
            original(operation)
            if operation['state']=='calling':raise OSError('journal ack lost')
        with patch.object(self.service,'_save_operation',side_effect=lost):result=self.service.change(self.action())
        self.assertEqual(result['change_state'],'unconfirmed');self.assertEqual(self.calendar.patches,[])
        # A different event is not frozen by the first event's uncertain receipt.
        self.calendar.events['other_0']=dict(self.calendar.events['existing_0'],event_id='other_0')
        commit=self.service._commit_time
        def committed(*args):commit(*args);raise OSError('commit acknowledgement lost')
        with patch.object(self.service,'_commit_time',side_effect=committed):
            result=self.service.change(dict(self.action(),event_id='other_0'))
        self.assertTrue(result['ok'],result);self.assertEqual(result['operation']['state'],'retimed')

    def test_busy_calendar_archived_item_and_stale_inspection_cannot_change_time(self):
        from desk_companion.board_data import calendar_operation
        entered,release=threading.Event(),threading.Event()
        def held():
            with calendar_operation():entered.set();release.wait(10)
        with ThreadPoolExecutor(1) as pool:
            waiting=pool.submit(held);self.assertTrue(entered.wait(5))
            try:self.assertFalse(self.service.change(self.action())['ok'])
            finally:release.set();waiting.result(5)
        self.assertEqual(self.calendar.patches,[])
        self.link();item=self.board.load()['items'][0]
        self.assertTrue(self.board.change(dict(type='archive',id=item['id'],version=item['version'],archived=True,request_id=uuid.uuid4().hex))['ok'])
        self.assertFalse(self.service.inspect_event('calendar-one','existing_0')['event']['editable'])
        self.assertFalse(self.service.change(self.action())['ok'])

    def test_explicit_old_time_reconciliation_does_not_claim_the_original_was_not_executed(self):
        request=self.action()
        with patch.object(self.calendar,'patch_time',side_effect=TimeoutError('unknown')):
            self.service.change(request)
        self.service.inspect_event('calendar-one','existing_0')
        self.assertFalse(self.service.change(self.action())['ok'])
        result=self.service.change(dict(type='reconcile_time',request_id=uuid.uuid4().hex,operation_id=request['request_id'],
            calendar_id='calendar-one',event_id='existing_0',day=DAY,confirmed=True,original_start=START,original_end=END))
        self.assertTrue(result['ok'],result)
        receipt=self.service.load_week(DAY,request_id=request['request_id'])['operation']
        self.assertEqual(receipt['execution_result'],'unconfirmed')
        self.assertIn('不证明',result['operation']['notice'])

    def test_deleted_recovery_requires_new_verified_absence_and_never_replays(self):
        request=self.action()
        with patch.object(self.calendar,'patch_time',side_effect=TimeoutError('unknown')):
            self.service.change(request)
        del self.calendar.events['existing_0']
        observed=self.service.inspect_event('calendar-one','existing_0')
        self.assertEqual(observed['event']['verification'],'missing')
        reconcile=dict(type='reconcile_time',request_id=uuid.uuid4().hex,operation_id=request['request_id'],
            calendar_id='calendar-one',event_id='existing_0',day=DAY,confirmed=True,
            original_start=START,original_end=END)
        self.assertFalse(self.service.change(reconcile)['ok'])
        result=self.service.change(dict(reconcile,verification='missing'))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['operation']['event']['verification'],'missing')
        self.assertEqual(self.calendar.patches,[])
        self.assertEqual(self.service.change(request)['operation']['state'],'observed')
        self.assertEqual(self.calendar.patches,[])

    def test_changed_identity_on_second_read_prevents_remote_write(self):
        original=self.calendar.primary()
        with patch.object(self.calendar,'primary',side_effect=[original,dict(original,owner_id='other')]):
            result=self.service.change(self.action())
        self.assertFalse(result['ok']);self.assertEqual(self.calendar.patches,[])

    def test_explicit_reconciliation_ack_loss_reads_saved_fact(self):
        from contextlib import contextmanager
        request=self.action()
        with patch.object(self.calendar,'patch_time',side_effect=TimeoutError('unknown')):
            self.service.change(request)
        transaction=self.board.workspace_transaction
        # Fail only the transaction that persists the observed receipt; later reads succeed.
        fired=False
        @contextmanager
        def once():
            nonlocal fired
            with transaction() as value:
                yield value
                committed=any(op['type']=='reconcile_time' for op in value[0].get('schedule_operations',{}).values())
            if committed and not fired:fired=True;raise OSError('lost after persisted')
        with patch.object(self.board,'workspace_transaction',once):
            result=self.service.change(dict(type='reconcile_time',request_id=uuid.uuid4().hex,
                operation_id=request['request_id'],calendar_id='calendar-one',event_id='existing_0',
                day=DAY,confirmed=True,original_start=START,original_end=END))
        self.assertTrue(result['ok'],result);self.assertTrue(fired)

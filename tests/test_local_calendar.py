"""Local facts, no-CLI offline behavior and durable explicit publication."""
from datetime import timedelta, datetime
import os
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from desk_companion.calendar_time import TZ
from desk_companion.local_calendar import LOCAL
from desk_companion.scheduling import SchedulingService
from desk_companion.work_board import WorkBoardService
from tests.schedule_fixture import DAY, START, END, TimeCalendar


class LocalCalendarTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, DESK_COMPANION_DATA_DIR=temp.name); env.start(); self.addCleanup(env.stop)
        self.board, self.calendar = WorkBoardService(), TimeCalendar()
        self.service = SchedulingService(self.board, self.calendar)
        self.local = self.service.local

    def create(self, **fields):
        request = dict(type='create', request_id=uuid.uuid4().hex, summary='本地安排', start=START, end=END, item_id='')
        request.update(fields)
        result = self.local.change(request)
        self.assertTrue(result['ok'], result)
        return result['event'], request

    def sync(self, event, **extra):
        request = dict(type='sync', request_id=uuid.uuid4().hex, local_id=event['local_id'], version=event['local_version'])
        request.update(extra)
        return self.local.change(request), request

    def test_offline_creation_read_edit_move_never_call_feishu(self):
        with patch.object(self.calendar, 'primary', side_effect=RuntimeError('offline')):
            event, request = self.create()
            self.assertTrue(self.local.change(request)['ok'])
            week = self.service.load_week(DAY, True)
            self.assertFalse(week['events_valid']); self.assertIn('offline', week['events_error'])
            self.assertEqual([e['event_id'] for e in week['events']], [event['event_id']])
            self.assertTrue(week['events'][0]['editable'])
            result = self.service.change(dict(type='retime', request_id=uuid.uuid4().hex,
                calendar_id=LOCAL, event_id=event['event_id'], day=DAY, original_start=START, original_end=END,
                start=DAY+'T11:00:00+08:00', end=DAY+'T12:00:00+08:00'))
            self.assertTrue(result['ok'], result)
        self.assertEqual(self.calendar.creates, []); self.assertEqual(self.calendar.patches, [])
        self.assertEqual(len(self.local.load()['events']), 1)

    def test_history_edit_works_locally_without_allowing_past_creation_or_deletion(self):
        event,_=self.create();past='2026-01-01'
        result=self.local.change(dict(type='update',request_id=uuid.uuid4().hex,local_id=event['local_id'],
            version=event['local_version'],summary='更正历史标题',start=past+'T05:00:00+08:00',end=past+'T05:30:00+08:00',item_id=''))
        self.assertTrue(result['ok'],result);event=result['event'];self.assertTrue(event['editable'])
        moved=self.service.change(dict(type='retime',request_id=uuid.uuid4().hex,calendar_id=LOCAL,event_id=event['event_id'],day=past,
            original_start=event['start'],original_end=event['end'],original_summary=event['summary'],summary='第二次更正',
            start=past+'T06:00:00+08:00',end=past+'T06:30:00+08:00'))
        self.assertTrue(moved['ok'],moved);self.assertEqual(moved['operation']['event']['summary'],'第二次更正')
        refused=self.local.change(dict(type='create',request_id=uuid.uuid4().hex,summary='过去新建',start=event['start'],end=event['end'],item_id=''))
        self.assertFalse(refused['ok'])
        refused=self.local.change(dict(type='delete',request_id=uuid.uuid4().hex,calendar_id=LOCAL,event_id=event['event_id'],version=moved['operation']['event']['local_version']))
        self.assertFalse(refused['ok']);self.assertEqual(len(self.local.load()['events']),1)
        self.assertEqual(self.calendar.creates,[]);self.assertEqual(self.calendar.patches,[])

    def test_synced_history_edits_update_same_remote_and_preserve_local_identity(self):
        event,_=self.create();synced,_=self.sync(event);event=synced['event'];past='2026-01-02'
        remote=event['remote'];current=self.calendar.events[remote['event_id']]
        current.update(start=past+'T05:00:00+08:00',end=past+'T05:30:00+08:00')
        event=self.service.inspect_event(LOCAL,event['event_id'])['event'];self.assertTrue(event['editable'],event)
        request=dict(type='retime',request_id=uuid.uuid4().hex,calendar_id=LOCAL,event_id=event['event_id'],day=past,
            original_start=event['start'],original_end=event['end'],original_summary=event['summary'],summary='更正同步历史',
            start=past+'T05:15:00+08:00',end=past+'T05:45:00+08:00')
        result=self.service.change(request);self.assertTrue(result['ok'],result)
        self.assertEqual(result['operation']['event']['local_id'],event['local_id'])
        self.assertEqual(self.calendar.events[remote['event_id']]['summary'],'更正同步历史')
        self.assertEqual(len(self.calendar.creates),1);self.assertEqual(len(self.calendar.patches),1)

    def test_sync_keeps_local_identity_work_link_and_deduplicates_remote(self):
        item = self.board.change(dict(type='create', request_id=uuid.uuid4().hex, fields=dict(title='事项')))['items'][0]
        event, _ = self.create(item_id=item['id'])
        result, request = self.sync(event)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['event']['event_id'], event['event_id'])
        self.assertEqual(len(self.calendar.creates), 1)
        self.assertTrue(self.local.change(request)['ok']); self.assertEqual(len(self.calendar.creates), 1)
        page = self.service.load_week(DAY, True)
        matching = [e for e in page['events'] if e['summary'] == '本地安排']
        self.assertEqual(len(matching), 1); self.assertEqual(matching[0]['calendar_id'], LOCAL)
        link = self.board.load()['items'][0]['schedules']['events'][0]
        self.assertEqual(link['event_id'], event['event_id'])
        inspected = self.service.inspect_event(LOCAL, event['event_id'])
        self.assertTrue(inspected['event']['editable'], inspected)
        result = self.service.change(dict(type='retime', request_id=uuid.uuid4().hex, calendar_id=LOCAL,
            event_id=event['event_id'], day=DAY, original_start=START, original_end=END,
            start=DAY+'T11:00:00+08:00', end=DAY+'T12:00:00+08:00'))
        self.assertTrue(result['ok'], result); self.assertEqual(len(self.calendar.patches), 1)
        self.assertEqual(self.calendar.patches[0]['event_id'], matching[0]['remote']['event_id'])
        self.assertEqual(result['operation']['event']['event_id'], event['event_id'])
        page = self.service.load_week(DAY)
        self.assertEqual(len([e for e in page['events'] if e['event_id'] == event['event_id']]), 1)

    def test_batch_target_is_frozen_and_identity_changes_never_publish_elsewhere(self):
        event, _ = self.create()
        prepared = self.local.change(dict(type='prepare_sync', request_id=uuid.uuid4().hex,
            events=[dict(local_id=event['event_id'], version=event['local_version'])]))
        batch = prepared['batch']
        self.assertEqual(batch['target']['calendar_id'], 'calendar-one')
        self.assertNotIn('owner_id', batch['target'])
        self.calendar.calendar_id = 'another-calendar'
        result = self.local.batch_step(batch['id'], event['event_id'])
        self.assertEqual(result['batch']['entries'][0]['state'], 'failed')
        self.assertEqual(self.calendar.creates, [])

    def test_missing_sync_receipt_is_sealed_before_a_late_original_request(self):
        event, _ = self.create(); rid = uuid.uuid4().hex
        result = self.local.change(dict(type='resolve_sync', request_id=uuid.uuid4().hex,
            local_id=event['event_id'], operation_id=rid, resolution='absent', confirmed=True))
        self.assertTrue(result['ok'], result)
        late = self.local.change(dict(type='sync', request_id=rid, local_id=event['event_id'], version=1))
        self.assertFalse(late['ok']); self.assertEqual(self.calendar.creates, [])

    def test_missing_local_save_receipt_is_sealed_without_creating_anything(self):
        rid, uid = uuid.uuid4().hex, uuid.uuid4().hex
        result = self.local.change(dict(type='resolve_save', request_id=uuid.uuid4().hex,
            local_id=uid, operation_id=rid, confirmed=True))
        self.assertTrue(result['ok'], result)
        result = self.local.change(dict(type='create', request_id=rid, local_id=uid, item_id='', summary='迟到', start=START, end=END))
        self.assertFalse(result['ok']); self.assertEqual(self.local.load()['events'], [])

    def test_synced_retime_reserves_its_local_work_item(self):
        item = self.board.change(dict(type='create', request_id=uuid.uuid4().hex, fields=dict(title='事项')))['items'][0]
        event, _ = self.create(item_id=item['id']); self.assertTrue(self.sync(event)[0]['ok'])
        item = self.board.load()['items'][0]
        entered, release = threading.Event(), threading.Event(); original = self.calendar.patch_time
        def wait(*args):
            entered.set(); release.wait(5); return original(*args)
        results = []
        with patch.object(self.calendar, 'patch_time', side_effect=wait):
            worker = threading.Thread(target=lambda: results.append(self.service.change(dict(type='retime', request_id=uuid.uuid4().hex,
                calendar_id=LOCAL, event_id=event['event_id'], day=DAY, original_start=START, original_end=END,
                start=DAY+'T11:00:00+08:00', end=DAY+'T12:00:00+08:00')))); worker.start()
            try:
                self.assertTrue(entered.wait(3))
                moved = self.board.change(dict(type='move', request_id=uuid.uuid4().hex, id=item['id'], version=item['version'], state='doing'))
                self.assertFalse(moved['ok'], moved)
                self.assertTrue(self.local.load()['ok'])
            finally:
                release.set(); worker.join(5)
        self.assertTrue(results[0]['ok'], results)

    def test_lost_create_response_survives_restart_blocks_retries_and_reconciles(self):
        event, _ = self.create()
        original = self.calendar.create
        def lost(*args):
            original(*args)
            raise TimeoutError('remote accepted but ack lost')
        with patch.object(self.calendar, 'create', side_effect=lost):
            result, request = self.sync(event)
        self.assertEqual(result['change_state'], 'unconfirmed', result)
        local = SchedulingService(self.board, self.calendar).local
        self.assertFalse(local.change(request)['ok']); self.assertEqual(len(self.calendar.creates), 1)
        self.assertFalse(local.change(dict(request, request_id=uuid.uuid4().hex))['ok'])
        eid = next(k for k in self.calendar.events if k.startswith('created-'))
        result = local.change(dict(type='resolve_sync', request_id=uuid.uuid4().hex, local_id=event['event_id'],
            operation_id=request['request_id'], resolution='link', event_id=eid))
        self.assertTrue(result['ok'], result); self.assertEqual(len(self.calendar.creates), 1)
        self.assertEqual(local.load()['events'][0]['sync_state'], 'synced')

    def test_known_remote_creation_cannot_be_declared_absent(self):
        event, _ = self.create()
        with patch.object(self.local, '_bind', side_effect=OSError('registration failed')):
            result, request = self.sync(event)
        self.assertEqual(result['stage'], 'register'); self.assertEqual(result['change_state'], 'unconfirmed')
        absent = self.local.change(dict(type='resolve_sync', request_id=uuid.uuid4().hex, local_id=event['event_id'],
            operation_id=request['request_id'], resolution='absent', confirmed=True))
        self.assertFalse(absent['ok']); self.assertIn('仍存在', absent['error'])
        self.assertEqual(len(self.calendar.creates), 1)

    def test_prepared_storage_failure_never_sends_create(self):
        event, _ = self.create()
        with patch.object(self.local, '_save_operation', side_effect=OSError('disk')):
            result, _ = self.sync(event)
        self.assertFalse(result['ok']); self.assertEqual(self.calendar.creates, [])

    def test_batch_manifest_versions_partial_failure_and_no_replay(self):
        a, _ = self.create(); b, _ = self.create(summary='另一条')
        prepare = dict(type='prepare_sync', request_id=uuid.uuid4().hex,
                       events=[dict(local_id=e['event_id'], version=e['local_version']) for e in (a, b)])
        batch = self.local.change(prepare)['batch']
        changed = self.local.change(dict(type='update', request_id=uuid.uuid4().hex, local_id=a['event_id'],
            version=a['local_version'], item_id='', summary='已变动', start=START, end=END))
        self.assertTrue(changed['ok'], changed)
        one = self.local.batch_step(batch['id'], a['event_id'])
        self.assertEqual(one['batch']['entries'][0]['state'], 'failed')
        two = self.local.batch_step(batch['id'], b['event_id'])
        self.assertEqual(two['batch']['entries'][1]['state'], 'synced')
        self.local.batch_step(batch['id'], b['event_id']); self.assertEqual(len(self.calendar.creates), 1)

    def test_sync_reservation_rejects_edit_while_reads_and_other_events_work(self):
        a, _ = self.create(); b, _ = self.create(summary='可编辑')
        entered, release = threading.Event(), threading.Event()
        original = self.calendar.create
        def paused(*args):
            entered.set(); release.wait(5)
            return original(*args)
        result = []
        with patch.object(self.calendar, 'create', side_effect=paused):
            worker = threading.Thread(target=lambda: result.append(self.sync(a)[0])); worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertTrue(self.local.load()['ok'])
                for event, expected in ((a, False), (b, True)):
                    changed = self.local.change(dict(type='update', request_id=uuid.uuid4().hex,
                        local_id=event['event_id'], version=event['local_version'], summary='改名', item_id='', start=START, end=END))
                    self.assertEqual(changed['ok'], expected, changed)
            finally:
                release.set(); worker.join(5)
        self.assertTrue(result[0]['ok'], result)

    def test_remote_changes_project_back_and_local_old_versions_are_rejected(self):
        event, _ = self.create(); result, _ = self.sync(event)
        remote = result['event']['remote']
        current = self.calendar.events[remote['event_id']]
        current.update(start=DAY+'T12:00:00+08:00', end=DAY+'T13:00:00+08:00', checked_at=datetime.now(TZ).isoformat())
        page = self.service.load_week(DAY, True)
        local = next(e for e in page['events'] if e['event_id'] == event['event_id'])
        self.assertEqual(local['start'], current['start'])
        result = self.local.change(dict(type='update', request_id=uuid.uuid4().hex, local_id=event['event_id'],
            version=event['local_version'], summary='旧编辑', item_id='', start=START, end=END))
        self.assertFalse(result['ok']); self.assertEqual(len(self.calendar.creates), 1)

    def test_archived_link_blocks_direct_synced_retime_and_native_eligibility_is_preserved(self):
        item = self.board.change(dict(type='create', request_id=uuid.uuid4().hex, fields=dict(title='事项')))['items'][0]
        event, _ = self.create(item_id=item['id']); synced, _ = self.sync(event)
        item = self.board.load()['items'][0]
        archived = self.board.change(dict(type='archive', request_id=uuid.uuid4().hex,
            id=item['id'], version=item['version'], archived=True))
        self.assertTrue(archived['ok'], archived)
        result = self.service.change(dict(type='retime', request_id=uuid.uuid4().hex, calendar_id=LOCAL,
            event_id=event['event_id'], day=DAY, original_start=START, original_end=END,
            start=DAY+'T11:00:00+08:00', end=DAY+'T12:00:00+08:00'))
        self.assertFalse(result['ok']); self.assertIn('恢复', result['error']); self.assertEqual(self.calendar.patches, [])
        remote = synced['event']['remote']
        self.calendar.events[remote['event_id']].update(eligible=False, reason='重复日程', verification='confirmed')
        inspected = self.service.inspect_event(LOCAL, event['event_id'])
        self.assertFalse(inspected['event']['eligible']); self.assertEqual(inspected['event']['reason'], '重复日程')

    def test_local_reads_keep_a_stable_snapshot_until_actual_edit(self):
        event, _ = self.create()
        self.assertEqual(self.local.load()['events'][0]['checked_at'], event['checked_at'])
        changed = self.local.change(dict(type='update', request_id=uuid.uuid4().hex, local_id=event['event_id'],
            version=event['local_version'], summary='改名', item_id='', start=START, end=END))
        self.assertTrue(changed['ok'], changed)
        self.assertEqual(changed['event']['local_version'], 2)
        self.assertEqual(self.local.load()['events'][0]['checked_at'], changed['event']['checked_at'])

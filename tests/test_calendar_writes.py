"""Shared calendar writes retain history and never replay uncertain remote effects."""
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch
from desk_companion.scheduling import SchedulingService
from desk_companion.work_board import WorkBoardService
from desk_companion.local_calendar import LOCAL
from tests.schedule_fixture import TimeCalendar, DAY, START, END


class CalendarWritesTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=temp.name);env.start();self.addCleanup(env.stop)
        self.board,self.remote=WorkBoardService(),TimeCalendar()
        self.s=SchedulingService(self.board,self.remote)

    def create(self,**fields):
        result=self.s.local.change(dict(type='create',request_id=uuid.uuid4().hex,summary='个人安排',start=START,end=END,**fields))
        self.assertTrue(result['ok'],result);return result['event']

    def delete(self,event):
        action=dict(type='delete',request_id=uuid.uuid4().hex,calendar_id=event['calendar_id'],event_id=event['event_id'],
                    original_start=event['start'],original_end=event['end'],version=event.get('local_version'))
        return self.s.local.change(action),action

    def test_local_delete_has_no_remote_effect_and_preserves_item_and_link_snapshot(self):
        item=self.board.change(dict(type='create',request_id=uuid.uuid4().hex,fields=dict(title='保留事项')))['items'][0]
        event=self.create(item_id=item['id']);result,action=self.delete(event)
        self.assertTrue(result['ok'],result);self.assertEqual(self.remote.deletes,[])
        self.assertEqual(self.s.local.load()['events'],[])
        self.assertTrue(self.s.local.change(action)['ok'])
        item=self.board.load()['items'][0]
        self.assertEqual(item['title'],'保留事项');self.assertEqual(item['schedules']['events'][0]['verification'],'deleted')

    def test_synced_delete_uses_remote_identity_once_without_duplicate_local_copy(self):
        event=self.create();sync=self.s.local.change(dict(type='sync',request_id=uuid.uuid4().hex,local_id=event['local_id'],version=event['local_version']))
        self.assertTrue(sync['ok'],sync);event=sync['event']
        result,action=self.delete(event);self.assertTrue(result['ok'],result)
        self.assertEqual(self.remote.deletes,[(event['remote']['calendar_id'],event['remote']['event_id'])])
        self.assertTrue(self.s.local.change(action)['ok']);self.assertEqual(len(self.remote.deletes),1)
        self.assertFalse(any(e['summary']=='个人安排' and e['status']!='cancelled' for e in self.s.load_week(DAY,True)['events']))

    def test_native_delete_conflict_and_readonly_never_write(self):
        event=self.remote.get('calendar-one','existing_0')
        with patch.object(self.remote,'inspect',return_value=dict(event,editable=False,edit_reason='其他参会人')):
            self.assertFalse(self.delete(event)[0]['ok'])
        changed=dict(event,start=DAY+'T11:00:00+08:00')
        with patch.object(self.remote,'inspect',return_value=dict(changed,editable=True)):
            self.assertFalse(self.delete(event)[0]['ok'])
        self.assertEqual(self.remote.deletes,[])

    def test_remote_deleted_then_ack_lost_reconciles_without_resending(self):
        event=self.remote.get('calendar-one','existing_0');delete=self.remote.delete
        def lost(cid,eid):delete(cid,eid);raise TimeoutError('reply lost')
        with patch.object(self.remote,'delete',side_effect=lost):result,action=self.delete(event)
        self.assertEqual(result['change_state'],'unconfirmed')
        self.assertEqual(self.s.local.change(action)['change_state'],'unconfirmed');self.assertEqual(len(self.remote.deletes),1)
        fresh=SchedulingService(self.board,self.remote)
        result=fresh.local.change(dict(type='resolve_delete',request_id=uuid.uuid4().hex,operation_id=action['request_id']))
        self.assertTrue(result['ok'],result);self.assertEqual(len(self.remote.deletes),1)

    def test_existing_event_can_be_explicitly_kept_without_replaying_delete(self):
        event=self.remote.get('calendar-one','existing_0')
        with patch.object(self.remote,'delete',side_effect=TimeoutError('connection lost')):result,action=self.delete(event)
        self.assertEqual(result['change_state'],'unconfirmed')
        request=dict(type='resolve_delete',request_id=uuid.uuid4().hex,operation_id=action['request_id'])
        self.assertFalse(self.s.local.change(request)['ok'])
        result=self.s.local.change(dict(request,resolution='present',confirmed=True))
        self.assertTrue(result['ok'],result);self.assertEqual(result['operation']['state'],'observed')
        self.assertTrue(self.s.local.change(action)['ok']);self.assertEqual(self.remote.deletes,[])
        self.assertIn('existing_0',self.remote.events)

    def test_title_and_time_patch_share_checked_remote_service(self):
        event=self.create();sync=self.s.local.change(dict(type='sync',request_id=uuid.uuid4().hex,local_id=event['local_id'],version=event['local_version']))
        event=sync['event']
        action=dict(type='retime',request_id=uuid.uuid4().hex,calendar_id=LOCAL,event_id=event['event_id'],day=DAY,
            original_start=START,original_end=END,original_summary=event['summary'],summary='改后的标题',start=START,end=END)
        result=self.s.change(action);self.assertTrue(result['ok'],result)
        self.assertEqual(result['operation']['event']['summary'],'改后的标题')
        self.assertEqual(self.s.local.load()['events'][0]['summary'],'改后的标题')
        self.assertEqual(len(self.remote.patches),1)


if __name__=='__main__':unittest.main()

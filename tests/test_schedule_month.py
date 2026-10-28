"""Visible-month composition reuses durable week snapshots, without live Feishu."""
import os
import tempfile
import unittest
from unittest.mock import patch
from tests.schedule_fixture import TimeCalendar, event
from desk_companion.work_board import WorkBoardService
from desk_companion.scheduling import SchedulingService

class MonthScheduleTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=temp.name);env.start();self.addCleanup(env.stop)
        self.remote=TimeCalendar();self.service=SchedulingService(WorkBoardService(),self.remote)
    def test_six_week_month_includes_both_adjacent_months_and_deduplicates(self):
        self.remote.events={'edge':event('edge','2026-07-27T09:00:00+08:00','2026-07-27T10:00:00+08:00'),
                            'last':event('last','2026-09-06T09:00:00+08:00','2026-09-06T10:00:00+08:00')}
        page=self.service.load_month('2026-08-15',True)
        self.assertTrue(page['ok'],page);self.assertTrue(page['events_valid'])
        self.assertEqual((page['week_start'],page['week_end']),('2026-07-27','2026-09-07'))
        self.assertEqual({e['event_id'] for e in page['events']},{'edge','last'})
    def test_partial_remote_failure_is_explicit_and_keeps_successful_weeks(self):
        original=self.remote.week
        def read(calendar,day):
            if day=='2026-07-27':raise RuntimeError('fixture timeout')
            return original(calendar,day)
        with patch.object(self.remote,'week',side_effect=read):page=self.service.load_month('2026-08-15',True)
        self.assertTrue(page['ok']);self.assertFalse(page['events_valid']);self.assertIn('fixture timeout',page['events_error'])
    def test_invalid_date_never_reads_remote(self):
        with patch.object(self.remote,'week') as read:
            self.assertFalse(self.service.load_month('invalid',True)['ok']);read.assert_not_called()

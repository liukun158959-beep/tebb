import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from desk_companion import feishu_auth, feishu_access as feishu_connection, feishu_tools


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,DESK_COMPANION_DATA_DIR=self.temp.name)
        env.start();self.addCleanup(env.stop)

    def test_fresh_profile_never_uses_global_cli_login_for_data(self):
        with patch.object(feishu_tools,'_lark_cmd') as command:
            for args in (['calendar','+agenda'],['task','+get-my-tasks'],['--profile','p','docs','+list']):
                with self.assertRaisesRegex(RuntimeError,'尚未连接飞书'):
                    feishu_tools._run_lark(args)
            command.assert_not_called()

    def test_calendar_adapter_preserves_explicit_not_connected_reason(self):
        from desk_companion.calendar_adapter import CalendarAdapter, CalendarError
        def blocked(*args,**kwargs):feishu_connection.require_connection()
        with self.assertRaisesRegex(CalendarError,'尚未连接飞书'):
            CalendarAdapter(run=blocked).primary()

    def test_detect_connect_disconnect_preserve_system_login_and_profile_data(self):
        raw=json.dumps(dict(ok=True,data=dict(identities=dict(user=dict(available=True,userName='fixture',scope='calendar:read')))))
        cache=Path(self.temp.name)/'memory';cache.mkdir();(cache/'today.json').write_text('preserved')
        with patch.object(feishu_auth,'_lark_bin',return_value='fixture'),patch.object(feishu_auth,'_run_lark',return_value=raw) as cli:
            status=feishu_auth.feishu_status()
            self.assertTrue(status['system_logged_in']);self.assertFalse(status['logged_in']);self.assertFalse(status['connected'])
            self.assertTrue(feishu_auth.feishu_login_start()['ok'])
            self.assertTrue(feishu_auth.feishu_status()['logged_in'])
            calls=cli.call_count
            self.assertTrue(feishu_auth.feishu_logout()['ok'])
            self.assertEqual(cli.call_count,calls)
            self.assertFalse(feishu_connection.connected())
            self.assertEqual((cache/'today.json').read_text(),'preserved')
            self.assertTrue(all(call.args[0][:2]==['auth','status'] for call in cli.call_args_list))

    def test_connection_is_profile_local_and_malformed_state_is_not_consent(self):
        feishu_connection.save(True)
        with tempfile.TemporaryDirectory() as other,patch.dict(os.environ,DESK_COMPANION_DATA_DIR=other):
            self.assertFalse(feishu_connection.connected())
        self.assertTrue(feishu_connection.connected())
        (Path(self.temp.name)/'feishu-connection.json').write_text('[]')
        self.assertFalse(feishu_connection.connected())

    def test_business_transport_runs_only_after_explicit_connect(self):
        feishu_connection.save(True)
        with patch.object(feishu_tools,'_lark_cmd',return_value=['node','fixture.js']),patch.object(feishu_tools.subprocess,'run',return_value=Mock(returncode=0,stdout='{}')) as run:
            self.assertEqual(feishu_tools._run_lark(['calendar','+agenda']),'{}')
            self.assertEqual(run.call_count,1)

    def test_explicit_bot_binding_and_cli_metadata_do_not_use_personal_connection(self):
        with patch.object(feishu_tools,'_lark_cmd',return_value=['node','fixture.js']),patch.object(feishu_tools.subprocess,'run',return_value=Mock(returncode=0,stdout='{}')) as run:
            for args in (['profile','list'],['--profile','bot-fixture','whoami'],['--profile','bot-fixture','im','+reply','--as','bot']):
                self.assertEqual(feishu_tools._run_lark(args),'{}')
            self.assertEqual(run.call_count,3)
            self.assertFalse(feishu_connection.connected())

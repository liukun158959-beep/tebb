"""Real task worker with offline calendar IO; parent's file gate controls the remote wait."""
from pathlib import Path
import json
import runpy
import sys
import time
from unittest.mock import patch

from gui_import_guard import NoGui, assert_no_gui
sys.meta_path.insert(0, NoGui())
from desk_companion import board_data
from desk_companion.paths import data_root


def remote(args):
    if args[:2] == ['calendar', '+create']:
        (data_root() / 'calendar.ready').touch()
        deadline = time.monotonic()+20
        while not (data_root() / 'calendar.go').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('fixture not released')
            time.sleep(.01)
        return json.dumps(dict(ok=True, data=dict(event_id='worker-event')))
    return json.dumps(dict(ok=True, data=[]))


with patch.object(board_data, '_run_lark', side_effect=remote), \
        patch.object(board_data, 'today_date', return_value='2026-10-20'):
    runpy.run_path(str(Path(__file__).with_name('worker_fixture.py')), run_name='__main__')
assert_no_gui()

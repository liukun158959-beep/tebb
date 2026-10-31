"""Owned parent for the Windows abrupt-exit integration test; no cleanup on kill."""
import json
import os
from pathlib import Path
import sys
import time

from desk_companion.tasks import TaskManager
from desk_companion.task_budgets import Ledger

config=json.loads(sys.stdin.readline());folder=Path(config['folder'])
manager=TaskManager(path=folder/'tasks.sqlite3',command=[sys.executable,'-u',str(Path(__file__).with_name('readonly_worker_fixture.py'))])
manager.configure(1,30,60,False)
ledger=Ledger();budget=ledger.create('abrupt-parent','research',60000)
manager.submit('只输出JSON。','parent-exit','test',workflow='collaboration',kind='task',model_id='offline',
    collab_path=str(folder/'workspace.sqlite3'),collab_run='child',parent_run='parent',stage='collect',dependencies=[],
    execution_runtime='atlas_readonly',conversation='test',budget=8,role=dict(id='research',name='research',role='read',tools=['网页读取']),
    context=[],fixture_case='block_model',fixture_marker=config['marker'],_budget_root=budget,_budget_root_member=False)
time.sleep(60)
manager.shutdown()

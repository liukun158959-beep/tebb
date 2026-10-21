"""Real backend entry point with GUI imports denied and offline dependencies."""
import asyncio
import sys
from contextlib import ExitStack
from unittest.mock import patch

from tests.gui_import_guard import BACKEND_FORBIDDEN, NoGui, assert_no_gui

sys.meta_path.insert(0, NoGui(BACKEND_FORBIDDEN))

from desk_companion.local_api import server
from desk_companion.tasks import TaskManager
from tests.schedule_fixture import TimeCalendar


def manager():
    return TaskManager(command=[sys.executable, '-u', '-c',
        "exec(open('tests/worker_fixture.py', encoding='utf-8').read())"])


with ExitStack() as stack:
    stack.enter_context(patch('desk_companion.scheduling.CalendarAdapter',return_value=TimeCalendar()))
    stack.enter_context(patch('desk_companion.feishu_agent.FeishuAgent.autostart'))
    stack.enter_context(patch('desk_companion.tasks.TaskManager', side_effect=manager))
    stack.enter_context(patch('desk_companion.github.list_owned_repos', return_value=[dict(full_name='test/repo')]))
    stack.enter_context(patch('desk_companion.github.board_snapshot', return_value=dict(ok=True, repos=[])))
    stack.enter_context(patch('desk_companion.skill_catalog.list_skills', return_value=[dict(id='sample', description='offline')]))
    stack.enter_context(patch('desk_companion.mcp_client.list_mcp_menu', return_value=dict(ok=True, servers=[])))
    stack.enter_context(patch('desk_companion.feishu_docs.list_my_docx', return_value=[]))
    stack.enter_context(patch('desk_companion.wiki_connection.catalog', return_value=[]))
    stack.enter_context(patch('desk_companion.wiki_connection.load_settings', return_value={}))
    try:
        asyncio.run(server.main())
    finally:
        assert_no_gui(BACKEND_FORBIDDEN)
        print('backend GUI boundary verified', flush=True)

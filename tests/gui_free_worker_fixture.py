"""Run the real offline worker while forbidding desktop/UI imports."""
import importlib.abc
from pathlib import Path
import runpy
import sys

FORBIDDEN = ('desk_companion.app', 'desk_companion.local_api.host', 'desk_companion.layered',
             'desk_companion.pet_shell', 'desk_companion.tray', 'desk_companion.winforms_host',
             'webview', 'win32api', 'win32gui', 'win32con', 'pystray', 'clr')


class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in FORBIDDEN):
            raise RuntimeError('GUI import forbidden: ' + fullname)


sys.meta_path.insert(0, NoGui())
from desk_companion.conversation import ConversationService
from unittest.mock import patch
import traceback

run_chat = ConversationService.run_chat

def traced_chat(self, *args, **kwargs):
    try:
        return run_chat(self, *args, **kwargs)
    except Exception:
        traceback.print_exc()
        raise

def retrieve(question, _ids):
    hit = dict(doc='隔离测试文档', doc_id='fixture-doc', title='测试章节', text='只使用这一段样本资料。', score=1.0)
    return dict(question=question, candidates=[hit], kept=[hit])

with patch.object(ConversationService, 'run_chat', traced_chat), \
     patch('desk_companion.knowledge._retrieve', side_effect=retrieve):
    runpy.run_path(str(Path(__file__).with_name('worker_fixture.py')), run_name='__main__')
assert not any(name == prefix or name.startswith(prefix + '.')
               for name in sys.modules for prefix in FORBIDDEN)

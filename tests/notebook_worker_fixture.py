"""Isolated subprocess writers for the notebook storage regression."""
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

from desk_companion import knowledge, notebook


role, sid, nid = sys.argv[1:]
root = Path(os.environ['DESK_COMPANION_DATA_DIR'])
(root / (role + '.ready')).touch()
deadline = time.monotonic() + 20
while not (root / 'go').exists():
    if time.monotonic() > deadline:
        raise RuntimeError('parent did not release fixture')
    time.sleep(.01)

read = notebook._read_locked
def slow_read():
    book = read()
    time.sleep(.01)  # Force overlapping read/modify/write attempts between independent processes.
    return book

cites = [dict(n=1, doc='source', title='section', text='original', doc_id='doc-1')]
with patch.object(knowledge, 'public_view', return_value=dict(docs=[])), \
     patch.object(notebook, '_read_locked', side_effect=slow_read):
    if role == 'read':
        assert notebook.load_notebook()['sessions']
    else:
        for index in range(10):
            if role == 'save':
                notebook.save_note(sid, 'question ' + str(index), 'answer', cites)
            elif role == 'history':
                notebook.begin_notebook_turn(sid, 'question ' + str(index))
                notebook.finish_notebook_turn(sid, 'answer', cites)
            elif role == 'artifact':
                notebook.save_agent_artifact('fixture', str(index), 'title', 'question', 'answer', 'agent')
            elif role == 'export':
                notebook.write_note_markdown(sid, nid)
            else:
                raise RuntimeError('unknown fixture role')

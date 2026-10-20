"""Notebook snapshots and edits, independent of GUI and RPC."""
from __future__ import annotations

from contextlib import nullcontext

from . import notebook


class NotebookService:
    def __init__(self, chat_sessions, *, start_guard=nullcontext):
        self.sessions = chat_sessions
        self._start_guard = start_guard

    def load_notebook(self):
        try:
            return notebook.load_notebook()
        except Exception as exc:
            return dict(ok=False, error=str(exc))

    def _change(self, action):
        conversation = self.sessions.conversation
        reserved = False
        try:
            # Reserve the shared assistant briefly; independent task workers keep running.
            # Admission shares the existing selection lock but never changes selection.
            with self._start_guard(), self.sessions.selected_session():
                if conversation.running:
                    raise RuntimeError('助手正在处理其他操作，完成或停止后再修改笔记。')
                conversation.running = True
                reserved = True
            return action()
        except notebook.NotebookChangeError as exc:
            return dict(ok=False, error=str(exc), **exc.details)
        except Exception as exc:
            return dict(ok=False, change_state='not_changed', error=str(exc))
        finally:
            if reserved:
                with self.sessions.selected_session():
                    conversation.running = False

    def new_notebook(self):
        return self._change(notebook.new_notebook)

    def save_notebook_note(self, session_id, question, answer, cites):
        return self._change(lambda: notebook.save_note(session_id, question, answer, cites))

    def delete_notebook_note(self, session_id, note_id):
        return self._change(lambda: notebook.delete_note(session_id, note_id))

"""Notebook snapshots and edits, independent of GUI and RPC."""
from __future__ import annotations

from contextlib import nullcontext

from . import notebook
from .notebook_platform import NotebookPlatform


class NotebookService:
    def __init__(self, chat_sessions, *, start_guard=nullcontext, platform=None, create_document=None):
        self.sessions = chat_sessions
        self._start_guard = start_guard
        self.platform = platform if platform is not None else NotebookPlatform()
        self._create_document = create_document

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

    def _open_export(self, result, target):
        steps = result.setdefault('steps', {})
        try:
            self.platform.open(target)
        except Exception as exc:
            return dict(result, ok=False, change_state='committed', stage='open',
                        steps=dict(steps, open='failed'),
                        error=f'文档已生成：{target}，但请求系统打开失败：{exc}。请使用该路径或链接打开，不要重复导出。')
        steps['open'] = 'requested'
        return result

    def export_notebook_markdown(self, session_id, note_id):
        def run():
            try:
                saved = notebook.write_note_markdown(session_id, note_id)
            except notebook.NotebookChangeError as exc:
                exc.details['steps']['open'] = 'not_started'
                raise
            return self._open_export(saved, saved['path'])
        return self._change(run)

    def export_notebook_feishu(self, session_id, note_id):
        def run():
            doc = notebook.note_document(session_id, note_id)
            create = self._create_document
            if create is None:
                from .feishu_auth import create_markdown_doc
                create = create_markdown_doc
            try:
                created = create(doc['title'], doc['markdown'])
                if (not isinstance(created, dict) or created.get('ok') is not True
                        or not isinstance(created.get('url'), str) or not created['url'].strip()):
                    raise RuntimeError('创建返回结果缺少有效链接。')
            except Exception as exc:
                raise notebook.NotebookChangeError(
                    f'飞书文档创建结果未确认：{exc}。请到飞书核对，不要直接重复导出。',
                    change_state='unconfirmed', stage='creation',
                    steps=dict(creation='unconfirmed', open='not_started'),
                    session_id=session_id, note_id=note_id,
                ) from exc
            result = dict(created, ok=True, steps=dict(creation='confirmed'),
                          message='已生成飞书文档，并请求系统打开链接。')
            return self._open_export(result, created['url'])
        return self._change(run)

    def _file_request(self, session_id, note_id, name, *, reveal=False):
        def run():
            found = notebook.notebook_file(session_id, note_id, name)
            stage = 'reveal' if reveal else 'open'
            try:
                action = self.platform.reveal if reveal else self.platform.open
                action(found['path'])
            except Exception as exc:
                raise notebook.NotebookChangeError(
                    f'文件仍登记在笔记下：{found["path"]}，但系统请求失败：{exc}。请核对文件后重试。',
                    change_state='not_changed', stage=stage, path=found['path'],
                    steps={stage: 'failed'},
                ) from exc
            return dict(ok=True, path=found['path'], steps={stage: 'requested'},
                        message='已请求在文件夹中定位这个文件。' if reveal else '已请求系统打开 Markdown 文件。')
        return self._change(run)

    def open_notebook_file(self, session_id, note_id, name):
        return self._file_request(session_id, note_id, name)

    def reveal_notebook_file(self, session_id, note_id, name):
        return self._file_request(session_id, note_id, name, reveal=True)

    def delete_notebook_file(self, session_id, note_id, name):
        return self._change(lambda: notebook.delete_note_file(session_id, note_id, name))

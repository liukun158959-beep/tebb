"""Notebook snapshots and edits, independent of GUI and RPC."""
from __future__ import annotations

from contextlib import nullcontext
import re
import uuid

from . import notebook
from .notebook_platform import NotebookPlatform
from .model_text import require_text


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

    def _generate(self, session_id, action):
        details = dict(session_id=session_id, stage='admission', change_state='not_changed',
                       steps={}, answer_complete=False)

        def run():
            try:
                return action(details)
            except Exception as exc:
                if isinstance(exc, notebook.NotebookChangeError):
                    steps = dict(details['steps'], **exc.details.get('steps', {}))
                    details.update(exc.details)
                    details['steps'] = steps
                    if details['stage'] == 'note_save' and details['change_state'] == 'committed':
                        details['stage'] = 'note_refresh'
                        details['steps']['note_save'] = 'confirmed'
                partial = getattr(exc, 'partial', None)
                if isinstance(partial, dict):
                    message = partial.get('message') or {}
                    content = message.get('content') if isinstance(message, dict) else None
                    if isinstance(content, str):
                        details['answer'] = content
                details['steps'].setdefault(details['stage'],
                    'unconfirmed' if details['change_state'] == 'unconfirmed' else 'failed')
                target = ''.join(f' {key}={details[key]}' for key in ('note_id', 'path') if details.get(key))
                return dict(details, ok=False, error=f'笔记生成未完整完成（{details["stage"]}）：{exc}。'
                            f'{target} 请重新读取笔记核对结果，不要直接重复生成。')
        result = self._change(run)
        if not result.get('ok'):
            result = dict(details, **result)
        result.setdefault('session_id', session_id)
        return result

    def _complete(self, details, messages, sampling, on_delta=None):
        conversation = self.sessions.conversation
        llm = conversation.agent.llm
        try:
            llm.sampling = sampling
            llm.on_reasoning = None
            details['stage'] = 'model'
            kwargs = dict(tools=None)
            if on_delta is not None:
                kwargs['on_delta'] = on_delta
            # Atlas rejects incomplete transport finishes; the product also forbids tools.
            result = llm.chat(messages, **kwargs)
            message = result.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            if isinstance(content, str):
                details['answer'] = content
            details['steps']['model'] = 'returned'
            details['stage'] = 'usage'
            conversation.record_llm_usage(result.get('usage'), str(uuid.uuid4()))
            details['steps']['usage'] = 'confirmed'
            details['stage'] = 'validation'
            content = require_text(result)
            details['answer_complete'] = True
            details['steps']['validation'] = 'confirmed'
            return content.strip()
        finally:
            llm.sampling = None
            llm.on_reasoning = None

    def run_notebook(self, session_id, text, doc_ids, sampling, delta_sink, status_sink):
        def run(details):
            from .knowledge import ask_sources
            details['stage'] = 'model_setup'
            self.sessions.conversation.engage_model()
            details['stage'] = 'question_save'
            notebook.begin_notebook_turn(session_id, text)
            details['steps']['question_save'] = 'confirmed'
            details['change_state'] = 'partial'
            details['stage'] = 'retrieval'
            status_sink('正在检索来源')

            def complete(system, user):
                status_sink('正在写回答')
                return self._complete(details, [dict(role='system', content=system),
                    dict(role='user', content=user)], sampling, delta_sink)

            found = ask_sources(text, doc_ids, complete)
            details.update(answer=found['answer'], cites=found['cites'], stage='answer_save')
            notebook.finish_notebook_turn(session_id, found['answer'], found['cites'])
            return dict(ok=True, answer=found['answer'], cites=found['cites'])
        return self._generate(session_id, run)

    def summarize_notebook(self, session_id, note_ids, sampling):
        def run(details):
            details['stage'] = 'selection'
            found = notebook.selected_answers(session_id, note_ids)
            details['stage'] = 'model_setup'
            self.sessions.conversation.engage_model()
            question, body, cites = _summary_input(found)
            details['cites'] = cites
            answer = self._complete(details, [dict(role='system', content=(
                '你在把勾选的模型回答写成一份文档。只使用这些回答里的内容。'
                '不要改去引用原文，也不要补回答里没有的内容。'
                '用到哪一条回答里的编号，就保持那个编号，例如 [1]。不要调用工具。')),
                dict(role='user', content='请把下面勾选的回答总结成一份文档。\n\n' + body)], sampling)
            details['answer'] = answer
            details['stage'] = 'note_save'
            page = notebook.save_note(session_id, question, answer, cites)
            note_id = page['note_id']
            details.update(note_id=note_id, change_state='committed', stage='export')
            details['steps']['note_save'] = 'confirmed'
            saved = notebook.write_note_markdown(session_id, note_id)
            result = self._open_export(saved, saved['path'])
            if result['ok']:
                details['stage'] = 'complete'
            return dict(details, **{key: value for key, value in result.items() if key != 'steps'},
                        steps=dict(details['steps'], **result['steps']))
        return self._generate(session_id, run)


def _summary_input(notes):
    """Merge snapshots in selection order; duplicate local citation numbers are remapped."""
    cites, blocks = [], []
    for note in notes:
        mapping = {}
        for cite in note['cites']:
            number = len(cites) + 1
            mapping[cite['n']] = number
            cites.append(dict(cite, n=number))

        def shift(match):
            old = int(match.group(1))
            return f'[{mapping[old]}]' if old in mapping else match.group(0)

        answer = re.sub(r'\[(\d+)\]', shift, note['answer'])
        blocks.append(f'## {note["question"]}\n{answer}')
    return '总结：' + '、'.join(note['question'] for note in notes), '\n\n'.join(blocks), cites

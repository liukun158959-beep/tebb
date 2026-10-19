"""Ordinary chat selection, snapshots and clearing, independent of windows and RPC."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import threading

from . import context_pack, memory
from .assistant import build_system_prompt
from .facts import FACT_LIMIT, read_facts
from .resource_lock import WRITES
from .state import new_session_id


class ChatSessionService:
    def __init__(self, conversation, *, mutation_guard=None):
        self.conversation = conversation
        self._mutation_guard = mutation_guard or (lambda _sid: nullcontext())
        self._lock = threading.RLock()

    @property
    def state(self):
        return self.conversation.state

    @contextmanager
    def selected_session(self):
        """Pin selection briefly while a desktop adapter reserves its reply lifecycle."""
        with self._lock:
            yield self.state.session_id

    def _change(self, operation, action):
        # Shared writers also read snapshots: always take WRITES before our lock.
        with (WRITES if operation == 'clear' else nullcontext()), self._lock:
            try:
                with self._mutation_guard(self.state.session_id):
                    if self.conversation.running:
                        raise RuntimeError('助手正在回复，等待完成或停止后再修改会话。')
                    return action()
            except Exception as exc:
                return dict(ok=False, change_state='not_changed', error=str(exc))

    def _select(self, session_id):
        previous = self.state.session_id
        self.state.session_id = session_id
        try:
            self.state.save()
        except Exception as exc:
            self.state.session_id = previous
            return dict(ok=False, change_state='unconfirmed',
                        error=f'会话选择保存失败，已恢复内存选择，文件结果未确认：{exc}。请重新打开会话列表核对。')

    def _reset_agent(self):
        try:
            self.conversation.clear_agent_memory()
        except Exception as exc:
            return dict(ok=False, change_state='committed',
                        error=f'会话数据已更新，但助手临时上下文清理失败：{exc}。请重试或重启助手。')

    def new_chat_session(self):
        def change():
            if memory.list_chat(self.state.session_id):
                error = self._select(new_session_id())
                if error:
                    return error
            error = self._reset_agent()
            return error or dict(ok=True, session_id=self.state.session_id)
        return self._change('new', change)

    def switch_chat_session(self, session_id):
        def change():
            if type(session_id) is not str or not session_id.strip():
                raise RuntimeError('切换对话需要 session_id。')
            sid = session_id.strip()
            if sid != self.state.session_id and sid not in {row['id'] for row in memory.list_sessions()}:
                raise RuntimeError('没有这条对话线程。')
            rows = memory.list_chat(sid)
            error = self._select(sid)
            if error:
                return error
            error = self._reset_agent()
            return error or dict(ok=True, session_id=sid, items=rows)
        return self._change('switch', change)

    def clear_chat(self):
        def change():
            sid = self.state.session_id
            # Remove the derived summary first, so deleted history cannot survive in a summary.
            try:
                context_pack.drop_pack(sid)
            except Exception as exc:
                return dict(ok=False, change_state='unconfirmed',
                            error=f'会话摘要清理失败，尚未清理聊天历史：{exc}。请核对后重试清空。')
            try:
                memory.clear_session(sid)
            except Exception as exc:
                return dict(ok=False, change_state='partial',
                            error=f'会话摘要已清理，但聊天历史清理未完成：{exc}。请核对后重试清空。')
            error = self._reset_agent()
            return error or dict(ok=True)
        return self._change('clear', change)

    def _history_snapshot(self):
        sid = self.state.session_id
        return dict(session_id=sid, items=memory.list_chat(sid),
                    context=context_pack.context_view(sid, build_system_prompt(self.state.persona)))

    def load_chat_log(self):
        with self._lock:
            try:
                return dict(ok=True, **self._history_snapshot(), sessions=memory.list_sessions())
            except Exception as exc:
                return dict(ok=False, error=str(exc))

    def load_memory(self):
        with self._lock:
            try:
                snapshot = self._history_snapshot()
                return dict(ok=True, total=len(snapshot['items']), fact_limit=FACT_LIMIT, facts=read_facts(),
                            context=snapshot['context'], items=context_pack.raw_tail(snapshot['session_id']))
            except Exception as exc:
                return dict(ok=False, error=str(exc))

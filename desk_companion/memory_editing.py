"""Manual facts and ordinary-history edits, independent of GUI and RPC."""
from __future__ import annotations

from . import context_pack, facts, memory
from .resource_lock import WRITES


class MemoryEditingService:
    def __init__(self, chat_sessions):
        self.sessions = chat_sessions

    def _edit(self, action, *, history=False):
        try:
            # Match RPC/worker lock order; never hold selection while waiting for WRITES.
            guard = self.sessions.mutable_session if history else self.sessions.selected_session
            with WRITES, guard() as sid:
                if not history and self.sessions.conversation.running:
                    raise RuntimeError('助手正在回复，等这句说完再改事实。')
                return action(sid)
        except Exception as exc:
            return dict(ok=False, change_state='not_changed', error=str(exc))

    def _snapshot(self):
        try:
            result = self.sessions.load_memory()
        except Exception as exc:
            result = dict(ok=False, error=str(exc))
        if not result['ok']:
            return dict(ok=False, change_state='committed',
                        error=f"记忆数据已更新，但读取记忆页失败：{result['error']}。请重新读取核对。")
        return result

    def _fact(self, action):
        def change(_sid):
            try:
                action()
            except OSError as exc:
                return dict(ok=False, change_state='unconfirmed',
                            error=f'事实编辑写入失败，文件结果未确认：{exc}。请重新读取核对后再试。')
            return self._snapshot()
        return self._edit(change)

    def add_fact(self, text):
        return self._fact(lambda: facts.add_user_fact(text))

    def update_fact(self, fact_id, text):
        return self._fact(lambda: facts.update_user_fact(fact_id, text))

    def delete_fact(self, fact_id):
        return self._fact(lambda: facts.delete_user_fact(fact_id))

    def delete_memory_turn(self, role, text):
        def change(sid):
            # Validate and find the row before invalidating any derived summary.
            if role not in ('user', 'pet'):
                raise RuntimeError('只能删你或助手的一句。')
            if type(text) is not str or not text.strip():
                raise RuntimeError('要删的句子是空的。')
            if not any(row['role'] == role and row['text'].strip() == text.strip()
                       for row in memory.list_chat(sid)):
                raise RuntimeError('这一线程里没有这句。')
            try:
                dropped = context_pack.drop_pack_if_covered(sid, role, text)
            except Exception as exc:
                return dict(ok=False, change_state='unconfirmed',
                            error=f'摘要失效未完成，尚未删除历史原文：{exc}。请核对后重试。')
            try:
                memory.drop_session_turn(sid, role, text)
            except Exception as exc:
                return dict(ok=False, change_state='partial' if dropped else 'unconfirmed',
                            error=f"{'包含该句的摘要已失效；' if dropped else ''}历史删除未完成，文件结果未确认：{exc}。请核对后重试。")
            try:
                self.sessions.conversation.clear_agent_memory()
            except Exception as exc:
                return dict(ok=False, change_state='committed',
                            error=f'历史原文已删除，但助手临时上下文清理失败：{exc}。请重试或重启助手。')
            return self._snapshot()
        return self._edit(change, history=True)

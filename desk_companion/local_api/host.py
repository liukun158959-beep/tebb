"""P1：本地 API 的无头 host。

普通/知识问答委托 ConversationService；模型/人设 RPC 直接使用共享配置服务。
普通会话与只读记忆 RPC 使用 ChatSessionService，记忆编辑使用 MemoryEditingService。
手动压缩使用 ContextCompressionService；笔记读写、生成、导出及文件 RPC 使用 NotebookService。
知识库八入口使用 KnowledgeService；看板等方法仍保留宿主兼容。
这里仍有 GUI 类继承，任务 worker 直接使用共享服务，不导入此模块。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable

from ..app import App


class HeadlessApp(App):
    """本地 API 后端用的无头 App：数据方法照用，UI 回调改道。"""

    def __init__(self) -> None:
        super().__init__(skin_id="", headless=True)
        self.turn_lock = threading.RLock()
        from ..feishu_agent import FeishuAgent
        self.feishu_agent = FeishuAgent(self)
        self._tasks = None
        self._collaboration = None
        self._collaboration_lock = threading.Lock()

    @property
    def collaboration(self):
        with self._collaboration_lock:
            if self._collaboration is None:
                from ..collaboration import Collaboration
                self._collaboration = Collaboration()
            return self._collaboration

    @property
    def tasks(self):
        if self._tasks is not None:
            return self._tasks
        with self.turn_lock:
            if self._tasks is None:
                from ..tasks import TaskManager
                self._tasks = TaskManager(self)
            return self._tasks

    @contextmanager
    def _session_mutation_guard(self, session_id):
        with self.tasks.session_mutation_guard(session_id):
            yield

    def _session_reservation(self, session_id):
        return self.tasks.reserve_session(session_id)

    @contextmanager
    def _compression_start_guard(self):
        if not self.turn_lock.acquire(blocking=False):
            raise RuntimeError('助手正在处理其他操作，完成后再压缩上下文。')
        try:
            yield
        finally:
            self.turn_lock.release()

    @contextmanager
    def _notebook_start_guard(self):
        if not self.turn_lock.acquire(blocking=False):
            raise RuntimeError('助手正在处理其他操作，完成或停止后再修改笔记。')
        try:
            with super()._notebook_start_guard():
                yield
        finally:
            self.turn_lock.release()

    @contextmanager
    def _knowledge_start_guard(self):
        if not self.turn_lock.acquire(blocking=False):
            raise RuntimeError('助手正在处理其他操作，完成或停止后再操作知识库。')
        try:
            with super()._knowledge_start_guard():
                yield
        finally:
            self.turn_lock.release()

    def ui(self, fn) -> None:
        # 无头：没有窗口队列，直接同步执行
        fn()

    def on_llm_delta(self, piece: str) -> None:
        self.conversation.on_llm_delta(piece)

    def on_stream_status(self, text: str) -> None:
        self.conversation.on_stream_status(text)

    def run_channel_chat(self, text: str, session_id: str, chips: dict, knowledge: bool = False) -> str:
        """飞书有独立历史，桌面选中的线程在本轮结束后恢复。"""
        task_id = self.tasks.submit(text, session_id, "feishu", chips=chips, knowledge=knowledge)
        task = self.tasks.wait(task_id)
        if task["state"] != "succeeded":
            return task["error"] + ("\n\n" + task["answer"] if task["answer"] else "")
        return task["answer"]

    def run_chat(
        self,
        text: str,
        chips: dict | None,
        delta_sink: Callable[[str], None],
        status_sink: Callable[[str], None],
        sampling: dict,
        think_sink: Callable[[str], None],
        model_id: str = "",
        knowledge: bool = False,
        knowledge_sink: Callable[[dict], None] | None = None,
        resume_note: str = "",
    ) -> str:
        return self.conversation.run_chat(text, chips, delta_sink, status_sink, sampling,
            think_sink, model_id, knowledge, knowledge_sink, resume_note)

    def _last_usage_pair(self) -> tuple[int, int]:
        return self.conversation.last_usage_pair()

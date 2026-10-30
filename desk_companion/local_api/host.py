"""P1：本地 API 的无头 host。

普通/知识问答委托 ConversationService；模型/人设 RPC 直接使用共享配置服务。
普通会话与只读记忆 RPC 使用 ChatSessionService，记忆编辑使用 MemoryEditingService。
手动压缩使用 ContextCompressionService；笔记读写、生成、导出及文件 RPC 使用 NotebookService。
知识库八入口使用 KnowledgeService；今日看板四入口使用 BoardService。
周复盘三入口使用 WeeklyReviewService；自动化配置与调度由 AutomationService 拥有。
独立组装业务服务，不导入旧 App 或窗口模块。任务 worker 不导入此宿主。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable

from ..assistant_settings import AssistantSettingsService
from ..automation_service import AutomationService
from ..board_service import BoardService
from ..bridge import Bridge
from ..chat_sessions import ChatSessionService
from ..context_compression import ContextCompressionService
from ..conversation import ConversationService
from ..knowledge_service import KnowledgeService
from ..memory_editing import MemoryEditingService
from ..notebook_service import NotebookService
from ..state import load_state
from ..weekly_review import WeeklyReviewService
from ..work_board import WorkBoardService
from ..scheduling import SchedulingService


class HeadlessApp:
    """后台组合根；业务状态由服务拥有，任务与协作按需创建。"""

    def __init__(self) -> None:
        self.turn_lock = threading.RLock()
        self._tasks = None
        self._tasks_lock = threading.Lock()
        # 坐标仅供旧桌面使用；已有坐标照读，后台不读取显示器。
        self.state = load_state(0, 0)
        self.conversation = ConversationService(self.state)
        self.assistant_settings = AssistantSettingsService(self.conversation)
        self.chat_sessions = ChatSessionService(self.conversation,
            mutation_guard=self._session_mutation_guard, reservation_guard=self._session_reservation)
        self.memory_editing = MemoryEditingService(self.chat_sessions)
        self.context_compression = ContextCompressionService(self.chat_sessions,
            start_guard=self._compression_start_guard)
        self.notebooks = NotebookService(self.chat_sessions, start_guard=self._notebook_start_guard)
        self.knowledge_service = KnowledgeService(self.chat_sessions, start_guard=self._knowledge_start_guard)
        self.board_service = BoardService()
        self.weekly_review = WeeklyReviewService(self.chat_sessions, start_guard=self._review_start_guard)
        self.automation = AutomationService(review=self.weekly_review, tasks=lambda: self.tasks, execution=lambda: self.collaboration,
            busy=lambda: self.conversation.running)
        self.bridge = Bridge(self)
        self.work_board = WorkBoardService()
        self.scheduling = SchedulingService(self.work_board)
        from ..feishu_agent import FeishuAgent
        self.feishu_agent = FeishuAgent(self, task_backed=True)
        self._collaboration = None
        self._collaboration_lock = threading.Lock()
        self._project_environments = None
        self._project_environments_lock = threading.Lock()

    @property
    def project_environments(self):
        with self._project_environments_lock:
            if self._project_environments is None:
                from ..project_environments import ProjectEnvironmentService
                from ..paths import data_root
                self._project_environments=ProjectEnvironmentService(data_root()/'memory'/'project-environments',
                                                                    owner_active=self._project_task_active)
            return self._project_environments

    def _project_task_active(self,task_id):
        if self._collaboration is None:return False
        for manager in self._collaboration.managers.values():
            try:
                task=manager.get(task_id)
                if task:return task['state'] in {'queued','running'}
            except (ValueError,KeyError):continue
        return False

    @property
    def tasks(self):
        with self._tasks_lock:
            if self._tasks is None:
                from ..tasks import TaskManager
                self._tasks = TaskManager()
            return self._tasks

    @property
    def agent(self):
        return self.conversation.agent

    @agent.setter
    def agent(self, value):
        self.conversation.agent = value

    @property
    def _agent_running(self):
        return self.conversation.running

    def engage_model(self, model_id=""):
        return self.conversation.engage_model(model_id)

    @property
    def collaboration(self):
        with self._collaboration_lock:
            if self._collaboration is None:
                from ..collaboration import Collaboration
                self._collaboration = Collaboration()
            return self._collaboration

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
            yield
        finally:
            self.turn_lock.release()

    @contextmanager
    def _knowledge_start_guard(self):
        if not self.turn_lock.acquire(blocking=False):
            raise RuntimeError('助手正在处理其他操作，完成或停止后再操作知识库。')
        try:
            yield
        finally:
            self.turn_lock.release()

    @contextmanager
    def _review_start_guard(self):
        if not self.turn_lock.acquire(blocking=False):
            raise RuntimeError('助手正在处理其他操作，完成或停止后再操作周复盘。')
        try:
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

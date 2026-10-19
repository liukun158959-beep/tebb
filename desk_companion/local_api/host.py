"""P1：本地 API 的无头 host。

普通/知识问答委托 ConversationService；模型/人设 RPC 直接使用共享配置服务。
普通会话与只读记忆 RPC 使用 ChatSessionService；其余数据方法仍保留 App 的继承兼容。
这里仍有 GUI 类继承，任务 worker 直接使用共享服务，不导入此模块。
"""
from __future__ import annotations

import os
import threading
import uuid
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

    @contextmanager
    def _session_mutation_guard(self, session_id):
        with self.tasks.session_mutation_guard(session_id):
            yield
        with self.turn_lock:
            if self._tasks is None:
                from ..tasks import TaskManager
                self._tasks = TaskManager(self)
            return self._tasks

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

    def board_knowledge(self) -> dict:
        from ..knowledge import public_view

        try:
            return public_view()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def board_save_knowledge(self, payload: dict) -> dict:
        from ..knowledge import save_settings

        return self._knowledge_call(lambda: save_settings(payload))

    def board_download_knowledge(self, repo: str) -> dict:
        from ..knowledge import download_model

        return self._knowledge_call(lambda: download_model(repo))

    def board_delete_model(self, repo: str) -> dict:
        from ..knowledge import delete_model

        return self._knowledge_call(lambda: delete_model(repo))

    def board_add_knowledge(self, doc_id: str, label: str, source: dict | None = None) -> dict:
        from ..knowledge import add_doc

        return self._knowledge_call(lambda: add_doc(doc_id, label, source))

    def board_delete_knowledge(self, doc_id: str) -> dict:
        from ..knowledge import delete_doc

        return self._knowledge_call(lambda: delete_doc(doc_id))

    def board_rebuild_knowledge(self) -> dict:
        from ..knowledge import rebuild

        return self._knowledge_call(lambda: rebuild())

    def board_ask_knowledge(self, text: str) -> dict:
        def run() -> dict:
            self.engage_model()
            from ..knowledge import ask

            llm = self.agent.llm

            def complete(system: str, user: str) -> str:
                result = llm.chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    tools=None,
                )
                self._record_llm_usage(result.get("usage"), str(uuid.uuid4()))
                message = result.get("message") or {}
                return str(message.get("content") or "")

            return {"ok": True, "trace": ask(text, complete)}

        return self._knowledge_call(run)

    def board_load_notebook(self) -> dict:
        from ..notebook import load_notebook

        return self._knowledge_call(load_notebook)

    def board_new_notebook(self) -> dict:
        from ..notebook import new_notebook

        return self._knowledge_call(new_notebook)

    def run_notebook(self, session_id: str, text: str, doc_ids: list, sampling: dict, delta_sink, status_sink) -> dict:
        if self._agent_running:
            raise RuntimeError("助手正在回复，等这句说完再问。")
        self.engage_model()
        from ..knowledge import ask_sources
        from ..notebook import begin_notebook_turn, finish_notebook_turn

        self._agent_running = True
        llm = self.agent.llm
        llm.sampling = sampling
        llm.on_reasoning = None
        try:
            status_sink("正在检索来源")
            begin_notebook_turn(session_id, text)

            def complete(system: str, user: str) -> str:
                status_sink("正在写回答")
                result = llm.chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    tools=None,
                    on_delta=delta_sink,
                )
                self._record_llm_usage(result.get("usage"), str(uuid.uuid4()))
                message = result.get("message") or {}
                return str(message.get("content") or "")

            found = ask_sources(text, doc_ids, complete)
            finish_notebook_turn(session_id, found["answer"], found["cites"])
            return {"answer": found["answer"], "cites": found["cites"]}
        finally:
            llm.sampling = None
            llm.on_reasoning = None
            self._agent_running = False

    def board_save_notebook_note(self, session_id: str, question: str, answer: str, cites: list) -> dict:
        from ..notebook import save_note

        return self._knowledge_call(lambda: save_note(session_id, question, answer, cites))

    def board_delete_notebook_note(self, session_id: str, note_id: str) -> dict:
        from ..notebook import delete_note

        return self._knowledge_call(lambda: delete_note(session_id, note_id))

    def board_export_notebook_markdown(self, session_id: str, note_id: str) -> dict:
        def run() -> dict:
            from ..notebook import write_note_markdown

            saved = write_note_markdown(session_id, note_id)
            try:
                os.startfile(saved["path"])
            except OSError:
                saved["ok"] = False
                saved["error"] = f"文件已记在这条笔记下：{saved['path']}，但没有打开。恢复：点「打开」。"
                return saved
            return saved

        return self._knowledge_call(run)

    def board_export_notebook_feishu(self, session_id: str, note_id: str) -> dict:
        def run() -> dict:
            from ..feishu_auth import create_markdown_doc
            from ..notebook import note_document

            doc = note_document(session_id, note_id)
            created = create_markdown_doc(doc["title"], doc["markdown"])
            try:
                os.startfile(created["url"])
            except OSError as exc:
                raise RuntimeError(f"飞书文档已创建：{created['url']}，但没有打开。恢复：复制链接到浏览器。") from exc
            return {"ok": True, "url": created["url"], "message": "已生成飞书文档，并打开了链接。"}

        return self._knowledge_call(run)

    def board_summarize_notebook(self, session_id: str, note_ids: list, sampling: dict) -> dict:
        def run() -> dict:
            import re

            from ..notebook import save_note, selected_answers, write_note_markdown

            found = selected_answers(session_id, note_ids)
            self.engage_model()
            llm = self.agent.llm
            llm.sampling = sampling
            llm.on_reasoning = None
            try:
                cites = []
                blocks = []
                for note in found:
                    mapping = {}
                    for cite in note["cites"]:
                        number = len(cites) + 1
                        mapping[cite["n"]] = number
                        row = dict(cite)
                        row["n"] = number
                        cites.append(row)

                    def shift(match: re.Match, table=mapping) -> str:
                        old = int(match.group(1))
                        if old not in table:
                            return match.group(0)
                        return f"[{table[old]}]"

                    answer_text = re.sub(r"\[(\d+)\]", shift, note["answer"])
                    blocks.append(f"## {note['question']}\n{answer_text}")
                themes = "、".join(note["question"] for note in found)
                question = f"总结：{themes}"
                result = llm.chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你在把勾选的模型回答写成一份文档。只使用这些回答里的内容。"
                                "不要改去引用原文，也不要补回答里没有的内容。"
                                "用到哪一条回答里的编号，就保持那个编号，例如 [1]。不要调用工具。"
                            ),
                        },
                        {"role": "user", "content": "请把下面勾选的回答总结成一份文档。\n\n" + "\n\n".join(blocks)},
                    ],
                    tools=None,
                )
                self._record_llm_usage(result.get("usage"), str(uuid.uuid4()))
                message = result.get("message") or {}
                answer = str(message.get("content") or "").strip()
            finally:
                llm.sampling = None
                llm.on_reasoning = None
            if not answer:
                raise RuntimeError("总结没有写出正文。恢复：再总结一次。")
            page = save_note(session_id, question, answer, cites)
            note_id = page.get("note_id")
            if type(note_id) is not str or not note_id:
                raise RuntimeError("总结没有存进笔记。恢复：再总结一次。")
            try:
                saved = write_note_markdown(session_id, note_id)
            except Exception as exc:
                raise RuntimeError(f"总结已存成笔记，但文档没有写下来：{exc}。恢复：在这条笔记上点 Markdown。") from exc
            try:
                os.startfile(saved["path"])
            except OSError:
                saved["ok"] = False
                saved["error"] = f"总结已记在新笔记下：{saved['path']}，但没有打开。恢复：点「打开」。"
                return saved
            saved["message"] = "已把勾选的回答总结成一份文档，记在新笔记下面。"
            return saved

        return self._knowledge_call(run)

    def board_open_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        def run() -> dict:
            from ..notebook import notebook_file

            found = notebook_file(session_id, note_id, name)
            os.startfile(found["path"])
            return {"ok": True, "message": "已打开 Markdown 文件。"}

        return self._knowledge_call(run)

    def board_reveal_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        def run() -> dict:
            import subprocess

            from ..notebook import notebook_file

            found = notebook_file(session_id, note_id, name)
            subprocess.Popen(["explorer", f"/select,{found['path']}"])
            return {"ok": True, "message": "已在文件夹中定位这个文件。"}

        return self._knowledge_call(run)

    def board_delete_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        from ..notebook import delete_note_file

        return self._knowledge_call(lambda: delete_note_file(session_id, note_id, name))

    def _knowledge_call(self, fn):
        if self._agent_running:
            return {"ok": False, "error": "助手正在回复，等这句说完再操作知识库。"}
        self._agent_running = True
        try:
            return fn()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._agent_running = False

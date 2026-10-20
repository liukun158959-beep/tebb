"""Conversation execution and state; independent of windows, Bridge and RPC."""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Callable

from .assistant import inject_history, react_loop_count, spoken_answer, token_plugin, with_clock
from .conversation_input import compose_turn
from .facts import bind_turn_user
from .logutil import log
from .memory import TZ, append_chat
from .usage import append_usage, cost_cny


class ConversationService:
    """Own Agent state and turn resources; callers own UI and task scheduling."""

    def __init__(self, state, *, dispatch=None, on_delta=None, on_status=None):
        self.state = state
        self.agent = None
        self.agent_error = ''
        self.running = False
        self.knowledge_usage = None
        self._delta_sink = None
        self._status_sink = None
        self._dispatch = dispatch or (lambda fn: fn())
        self._on_delta = on_delta
        self._on_status = on_status

    @classmethod
    def for_session(cls, session_id):
        from .state import load_state
        state = load_state(0, 0, persist=False)
        state.session_id = session_id
        # A task must never persist its session over the desktop selection.
        state.save = lambda: None
        return cls(state)

    def ui(self, fn):
        self._dispatch(fn)

    def on_llm_delta(self, piece):
        callback = self._delta_sink if self._delta_sink is not None else self._on_delta
        if callback is not None:
            callback(piece)

    def on_stream_status(self, text):
        callback = self._status_sink if self._status_sink is not None else self._on_status
        if callback is not None:
            callback(text)

    def clear_agent_memory(self):
        """Discard temporary model history after a protected session mutation."""
        if self.running:
            raise RuntimeError('助手正在回复，等待完成或停止后再清理上下文。')
        if self.agent is not None:
            self.agent.memory.clear()

    def run_agent(self, model_message, *, run_id, user_message=None):
        """Return raw output; each entry point retains its accounting/history order."""
        with bind_turn_user(model_message if user_message is None else user_message):
            return str(self.agent.run(with_clock(model_message), run_id=run_id))

    def try_build_agent(self) -> None:
        from .assistant import build_agent
        try:
            self.agent = build_agent(self)
            self.agent_error = ""
            log("build_agent 完成")
        except Exception as exc:
            self.agent = None
            self.agent_error = str(exc)
            log(f"build_agent 失败: {exc}")

    def rebuild_agent(self) -> None:
        if self.running:
            raise RuntimeError("助手正在回复，等这句说完再保存。")
        self.try_build_agent()
        if self.agent is None:
            raise RuntimeError(self.agent_error)

    def record_usage(self, agent, run_id: str) -> dict:
        plugin = token_plugin(agent)
        summary = plugin.get_summary(run_id)
        model = getattr(agent.llm, "model", "") or ""
        inn = int(summary.get("input_tokens") or 0)
        out = int(summary.get("output_tokens") or 0)
        return self._append_usage(model, inn, out, run_id)

    def _append_usage(self, model: str, inn: int, out: int, run_id: str) -> dict:
        row = {
            "ts": datetime.now(TZ).isoformat(timespec="seconds"),
            "model": model,
            "input_tokens": inn,
            "output_tokens": out,
            "total_tokens": inn + out,
            "cost_cny": cost_cny(model, inn, out, self.state.model_prices),
            "run_id": run_id,
        }
        append_usage(row)
        return row

    def prepare_context(
        self,
        current_text: str,
        *,
        exclude_user: str | None = None,
        manual: bool = False,
        save_pack=None,
    ) -> list[dict]:
        from .assistant import build_system_prompt
        from .context_pack import prepare_injection

        if self.agent is None:
            raise RuntimeError(self.agent_error or "还没接上模型。打开设置添加一个。")

        def complete(prompt: str) -> str:
            result = self.agent.llm.chat(
                [
                    {
                        "role": "system",
                        "content": self.state.persona + "\n\n你现在只压缩对话，不要调用工具。",
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            self.record_llm_usage(result.get("usage"), str(uuid.uuid4()))
            message = result.get("message") or {}
            if message.get('tool_calls') or message.get('function_call'):
                raise RuntimeError('压缩返回了工具调用，未保存摘要，请检查模型后重试。')
            content = message.get('content')
            if type(content) is not str or not content.strip():
                raise RuntimeError('压缩结果为空或不是文本，未保存摘要，请检查模型后重试。')
            return content

        return prepare_injection(
            self.state.session_id,
            build_system_prompt(self.state.persona),
            current_text,
            exclude_user=exclude_user,
            complete=complete,
            manual=manual,
            save=save_pack,
        )

    def inject_prepared(self, current_text: str, *, exclude_user: str | None = None) -> None:
        rows = self.prepare_context(current_text, exclude_user=exclude_user)
        inject_history(self.agent, rows, persona=self.state.persona)

    def engage_model(self, model_id: str = "") -> None:
        from .model_catalog import activate, require_active

        if model_id:
            activate(model_id)
        else:
            require_active()
        if self.agent is None:
            self.try_build_agent()
        if self.agent is None:
            raise RuntimeError(self.agent_error or "还没接上模型。打开设置添加一个。")
        self.bind_active_llm()

    def bind_active_llm(self) -> None:
        from .model_catalog import bind_llm, require_active

        if self.agent is None:
            return
        bind_llm(self.agent.llm, require_active())

    def record_llm_usage(self, usage, run_id: str) -> None:
        model = ""
        if self.agent is not None:
            model = getattr(self.agent.llm, "model", "") or ""
        inn = 0
        out = 0
        if isinstance(usage, dict):
            inn = int(usage.get("prompt_tokens") or 0)
            out = int(usage.get("completion_tokens") or 0)
        self.knowledge_usage = (inn, out)
        self._append_usage(model, inn, out, run_id)

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
        """执行一轮普通或知识问答，管理本轮回调、历史和临时工具。

        现有 Atlas 插件把正文/工具进度交给调用方；思考只保存在本轮回复元数据，
        不进入下一轮模型历史。返回最终正文，失败抛给任务或 API 适配器处理。
        """
        if self.running:
            raise RuntimeError("助手正在回复，等这句说完再发。")
        self.engage_model(model_id)
        picked = chips or {}
        if type(picked) is not dict:
            raise RuntimeError("点选必须是对象。")
        if knowledge:
            return self._run_knowledge(text, picked, delta_sink, sampling, knowledge_sink)
        message = compose_turn(text, picked)
        model_message = message
        skill_names = picked.get("skills") or []
        if skill_names:
            from .skill_catalog import skill_turn_block

            model_message = message + "\n\n" + skill_turn_block(skill_names)
        doc_picks = picked.get("docs") or []
        if doc_picks:
            from .feishu_docs import doc_turn_block

            model_message = model_message + "\n\n" + doc_turn_block(doc_picks)
        if picked.get("attachments"):
            from .local_sources import turn_block
            model_message += "\n\n" + turn_block(picked["attachments"])
        if resume_note:
            model_message += resume_note
        thoughts: list[str] = []
        notes: list[str] = []

        def remember_think(piece: str) -> None:
            thoughts.append(piece)
            think_sink(piece)

        def remember_status(text: str) -> None:
            if text:
                notes.append(text)
            status_sink(text)

        self._delta_sink = delta_sink
        self._status_sink = remember_status
        self.running = True
        llm = self.agent.llm
        llm.sampling = sampling
        llm.on_reasoning = remember_think
        run_id = str(uuid.uuid4())
        started = time.perf_counter()
        bound_mcp: list[str] = []
        try:
            mcp_picks = picked.get("mcp") or []
            if mcp_picks:
                from .mcp_client import attach_mcp_tools

                bound_mcp = attach_mcp_tools(self.agent.tools, mcp_picks)
            self.inject_prepared(model_message)
            append_chat("user", message, self.state.session_id)
            raw = self.run_agent(model_message, run_id=run_id, user_message=message)
            loops = react_loop_count(self.agent, run_id)
            usage = self.record_usage(self.agent, run_id)
            answer = spoken_answer(raw)
            append_chat(
                "pet",
                answer,
                self.state.session_id,
                detail={
                    "elapsed_s": time.perf_counter() - started,
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "total_tokens": usage["total_tokens"],
                    "model": usage["model"] or llm.model,
                    "reasoning_effort": sampling["reasoning_effort"],
                    "temperature": sampling["temperature"],
                    "top_p": sampling["top_p"],
                    "thinking": "".join(thoughts),
                    "notes": notes,
                    "react_loops": loops,
                },
            )
            return answer
        finally:
            try:
                if bound_mcp:
                    from .mcp_client import detach_mcp_tools
                    detach_mcp_tools(self.agent.tools, bound_mcp)
            finally:
                llm.sampling = None
                llm.on_reasoning = None
                self.running = False
                self._delta_sink = None
                self._status_sink = None

    def _run_knowledge(self, text: str, picked: dict, delta_sink, sampling: dict, knowledge_sink) -> str:
        blocked = []
        if picked.get("skills"):
            blocked.append("技能")
        if picked.get("cli"):
            blocked.append("工具")
        if picked.get("github"):
            blocked.append("仓库")
        if picked.get("docs"):
            blocked.append("飞书文档")
        if picked.get("mcp"):
            blocked.append("MCP")
        if picked.get("attachments"):
            blocked.append("本地附件（请先入库，或关闭知识库直接阅读）")
        if blocked:
            raise RuntimeError(
                "知识库打开时不要同时点选" + "、".join(blocked) + "。关掉知识库，或先去掉这些点选。"
            )
        question = (text or "").strip()
        if not question:
            raise RuntimeError("问题是空的。")
        from .knowledge import ask

        self.running = True
        llm = self.agent.llm
        llm.sampling = sampling
        llm.on_reasoning = None
        run_id = str(uuid.uuid4())
        started = time.perf_counter()
        try:
            append_chat("user", question, self.state.session_id)

            def complete(system: str, user: str) -> str:
                result = llm.chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    tools=None,
                    on_delta=delta_sink,
                )
                self.record_llm_usage(result.get("usage"), run_id)
                from .model_text import require_text
                return require_text(result)

            trace = ask(question, complete, on_retrieved=knowledge_sink)
            answer = trace["answer"]
            usage = self.last_usage_pair()
            append_chat(
                "pet",
                answer,
                self.state.session_id,
                detail={
                    "elapsed_s": time.perf_counter() - started,
                    "input_tokens": usage[0],
                    "output_tokens": usage[1],
                    "total_tokens": usage[0] + usage[1],
                    "model": llm.model,
                    "reasoning_effort": sampling["reasoning_effort"],
                    "temperature": sampling["temperature"],
                    "top_p": sampling["top_p"],
                    "thinking": "",
                    "notes": [],
                    "react_loops": 1,
                    "knowledge": trace,
                },
            )
            return answer
        finally:
            llm.sampling = None
            llm.on_reasoning = None
            self.running = False

    def last_usage_pair(self) -> tuple[int, int]:
        pair = getattr(self, "knowledge_usage", None)
        if pair is None:
            raise RuntimeError("这次回答没有用量。恢复：再问一次。")
        return pair

"""组装：Electron Live2D 宠物 + 看板 + 托盘。"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import random
import sys
import threading
import time
import uuid
from datetime import datetime
from contextlib import contextmanager, nullcontext
from pathlib import Path

import webview
import win32api
import win32con
import win32gui

from .assistant import spoken_answer
from .assistant_settings import AssistantSettingsService
from .conversation import ConversationService
from .chat_sessions import ChatSessionService
from .memory_editing import MemoryEditingService
from .context_compression import ContextCompressionService
from .notebook_service import NotebookService
from .knowledge_service import KnowledgeService
from .automation import AutomationScheduler
from .board_workbench import BoardWorkbench
from .bridge import Bridge
from .layered import enable_dpi_aware, work_area
from .logutil import crash, install_crash_hooks, log, mark_ready, start_os_watchdog
from .memory import (
    TZ,
    append_chat,
    recent_chat,
    stamp_missing_session,
)
from .pet_shell import BASE_PET_H, BASE_PET_W, CMD_QUIT, start_pet_thread
from .skin import load_skin
from .state import load_state
from .tray import start_tray
from .usage import summarize_usage
from .winforms_host import (
    apply_app_window,
    apply_tool_window,
    as_hwnd,
    cache_hwnd,
    enable_webview_context_menu,
    ensure_ready,
    form_of,
    hide_form,
    invoke,
    move_form,
    root_hwnd,
    show_form,
)

BOARD_W = 1100
BOARD_H = 720
TODAY_ASK = "今天干什么？"
LOG_ASK = "看看日志。"
DAILY_ASK = """【今日纸条】请先调用 get_today_agenda 和 get_open_tasks。
用中文 Markdown 写今日重点：
1. 先用引用块标出最该盯的一件事，格式：
> **重点**
> 事项名（截止时间）
2. 再用有序列表列出今天其余值得盯的 2 到 4 项
3. 若有多条日程或待办，再给一张表格，列：事项 / 截止 / 状态
不要鸡汤，不要写成一段话。工具失败就原样说明怎么修。"""
SUMMARY_ASK = """【本周复盘】不要调用工具。只根据后面的本周材料写中文 Markdown，不要编造材料里没有的事。
结构、红线和能用的语法以【技能规程】为准，不要另起章节，不要把整篇包进代码块。
不要把会议、待办或对话写成产出。"""
PAPER = "#FFF6EC"
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWCP_DONOTROUND = 1
MUTEX_NAME = "Local\\DeskCompanion.Kaltsit"
ERROR_ALREADY_EXISTS = 183

_mutex_handle = None


class App(BoardWorkbench):
    def __init__(self, skin_id: str, *, headless: bool = False) -> None:
        # headless：本地 API 后端模式，不加载皮肤、不建窗口，只复用数据方法
        self.headless = headless
        self.skin = None if headless else load_skin(skin_id)
        left, top, right, bottom = work_area()
        self.state = load_state(right - BASE_PET_W - 16, bottom - BASE_PET_H - 8)
        self.conversation = ConversationService(self.state, dispatch=self.ui,
            on_delta=None if headless else self.on_llm_delta,
            on_status=None if headless else self.on_stream_status)
        self.assistant_settings = AssistantSettingsService(self.conversation)
        self.automation = AutomationScheduler(self)
        self.chat_sessions = ChatSessionService(self.conversation, mutation_guard=self._session_mutation_guard,
                                                reservation_guard=self._session_reservation)
        self.memory_editing = MemoryEditingService(self.chat_sessions)
        self.context_compression = ContextCompressionService(self.chat_sessions, start_guard=self._compression_start_guard)
        self.notebooks = NotebookService(self.chat_sessions, start_guard=self._notebook_start_guard)
        self.knowledge_service = KnowledgeService(self.chat_sessions, start_guard=self._knowledge_start_guard)
        self.bridge = Bridge(self, session_changed=None if headless else self._on_session_changed)
        self._queue: queue.Queue = queue.Queue()
        self.pet = None
        self.board = None
        self.icon = None
        self._quitting = False
        self._nudge_busy = False
        self._board_placed = False
        self._card_mode = "chat"
        self._history_loaded = False
        self._outside_btn_down = False
        self._startup_ok = threading.Event()
        self._next_nudge = time.monotonic() + random.uniform(12 * 60, 18 * 60)
        self._ui_dir = Path(__file__).resolve().parent / "ui"

    @property
    def agent(self):
        return self.conversation.agent

    @agent.setter
    def agent(self, value):
        self.conversation.agent = value

    @property
    def _agent_error(self):
        return self.conversation.agent_error

    @_agent_error.setter
    def _agent_error(self, value):
        self.conversation.agent_error = value

    @property
    def _agent_running(self):
        return self.conversation.running

    @_agent_running.setter
    def _agent_running(self, value):
        self.conversation.running = value

    @property
    def _knowledge_usage(self):
        return self.conversation.knowledge_usage

    @_knowledge_usage.setter
    def _knowledge_usage(self, value):
        self.conversation.knowledge_usage = value

    def ui(self, fn) -> None:
        self._queue.put(fn)

    def drain_queue(self) -> None:
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception as exc:
                log(f"drain_queue: {type(exc).__name__}: {exc}")

    def on_pet_moved(self, *, talk_js: bool = False) -> None:
        return

    def on_ready(self) -> None:
        log("on_ready 开始")
        try:
            log("ensure_ready 看板")
            ensure_ready(self.board, "看板")
            log("setup_forms")
            self._setup_webview_form(self.board, tool=False)
            enable_webview_context_menu(self.board)
            hide_form(self.board)
            log("stamp_session")
            stamp_missing_session(self.state.session_id)
            log("load_history")
            self._load_history()
            log("start_tray")
            self.icon = start_tray(self, self.skin)
            threading.Thread(target=self._run_daily, daemon=True).start()
            mark_ready()
            self._startup_ok.set()
            log("on_ready 完成")
        except Exception as exc:
            log(f"on_ready 失败: {exc}")
            _message_box(f"对话气泡没就绪，宠物仍可用:\n{exc}")
            mark_ready()
            self._startup_ok.set()

    def _setup_webview_form(
        self,
        window,
        *,
        tool: bool,
        round_corners: bool = True,
        transparent: bool = False,
    ) -> None:
        """shown 之后再碰句柄，避免和 WebView2 Focus 死锁。"""
        invoke(
            form_of(window),
            lambda: _prepare_form(
                window,
                tool=tool,
                round_corners=round_corners,
                transparent=transparent,
            ),
        )

    def post_pet(self, cmd: int) -> None:
        if self.pet is None:
            if cmd == CMD_QUIT:
                self.quit()
                return
            raise RuntimeError("宠物窗口不存在，无法响应托盘命令。")
        self.pet.post_cmd(cmd)

    def on_pet_click(self) -> None:
        try:
            if self._chat_visible():
                self.hide_bubble()
            else:
                self.show_bubble("today")
        except RuntimeError as exc:
            log(f"on_pet_click: {exc}")
        except Exception as exc:
            if _is_ui_fault(exc):
                log(f"on_pet_click: {exc}")
            else:
                crash("on_pet_click", exc)

    def on_pet_tick(self) -> None:
        self._poll_outside_click()
        self.automation.tick()
        if not self.state.nudge_enabled or self._nudge_busy:
            return
        now = time.monotonic()
        if now < self._next_nudge:
            return
        self._next_nudge = now + random.uniform(12 * 60, 18 * 60)
        self._nudge_busy = True
        threading.Thread(target=self._run_template_nudge, daemon=True).start()

    def on_open_board(self) -> None:
        self.show_board()

    def on_hide_pet(self) -> None:
        self.hide_pet()

    def on_quit(self) -> None:
        self.quit()

    def toggle_nudge(self) -> None:
        self.state.nudge_enabled = not self.state.nudge_enabled
        self.state.save()

    def toggle_click_through(self) -> None:
        self.state.click_through = not self.state.click_through
        self.state.save()

        def apply() -> None:
            if self.pet is None:
                return
            self.pet.emit("set_click_through", enabled=self.state.click_through)

        self.ui(apply)

    def show_pet(self) -> None:
        if self.pet:
            self.pet.show()

    def hide_pet(self) -> None:
        self.hide_bubble()
        if self.pet:
            self.pet.hide()

    def show_bubble(self, panel: str = "chat") -> None:
        if self.pet is None:
            raise RuntimeError("宠物窗口还在启动，请稍后再点。")
        if not self.pet.visible():
            self.pet.show()
        self._card_mode = "chat" if panel == "chat" else panel
        self.pet.bubble_open = True
        self._eval_bubble("show_panel", panel=panel)

    def hide_bubble(self) -> None:
        if self.pet is None:
            return
        self.pet.bubble_open = False
        self._eval_bubble("hide_bubble")

    def show_notice(self, text: str, panel: str = "chat") -> None:
        text = (text or "").strip()
        if not text:
            raise RuntimeError("纸条内容为空。")
        if self.pet is None:
            raise RuntimeError("宠物窗口还在启动，请稍后再看纸条。")
        if not self.pet.visible():
            self.pet.show()
        self._card_mode = panel
        self.pet.bubble_open = True
        if panel == "today":
            self._eval_bubble("today_notice", text=text)
            self._eval_bubble("show_panel", panel="today")
            return
        self._eval_bubble("append_message", role="pet", text=text)
        self._eval_bubble("show_panel", panel="chat")

    def fit_card(self, width: int, height: int) -> None:
        return

    def _load_history(self) -> None:
        if self._history_loaded or self.pet is None:
            return
        rows = recent_chat(40, self.state.session_id)
        self._eval_bubble("load_history", rows=rows)
        self._history_loaded = True

    @contextmanager
    def _session_mutation_guard(self, session_id):
        if self.pet is not None and self.pet.busy:
            raise RuntimeError('助手正在回复或仍在收尾，等待完成或停止后再修改会话。')
        yield

    def _session_reservation(self, session_id):
        return nullcontext()

    def _compression_start_guard(self):
        return nullcontext()

    @contextmanager
    def _notebook_start_guard(self):
        if self.pet is not None and self.pet.busy:
            raise RuntimeError('助手正在回复或仍在收尾，完成或停止后再修改笔记。')
        yield

    @contextmanager
    def _knowledge_start_guard(self):
        if self.pet is not None and self.pet.busy:
            raise RuntimeError('助手正在回复或仍在收尾，完成或停止后再操作知识库。')
        yield

    def board_knowledge(self):
        return self.knowledge_service.load_knowledge()

    def board_save_knowledge(self, payload):
        return self.knowledge_service.save_knowledge(payload)

    def board_download_knowledge(self, repo):
        return self.knowledge_service.download_knowledge(repo)

    def board_delete_model(self, repo):
        return self.knowledge_service.delete_model(repo)

    def board_add_knowledge(self, doc_id, label, source=None):
        return self.knowledge_service.add_knowledge(doc_id, label, source)

    def board_delete_knowledge(self, doc_id):
        return self.knowledge_service.delete_knowledge(doc_id)

    def board_rebuild_knowledge(self):
        return self.knowledge_service.rebuild_knowledge()

    def board_ask_knowledge(self, text):
        return self.knowledge_service.ask_knowledge(text)

    def _on_session_changed(self, operation):
        snapshot = self.chat_sessions.load_chat_log()
        if not snapshot['ok']:
            raise RuntimeError(snapshot['error'])
        self._eval_bubble('load_history', rows=snapshot['items'])
        if operation == 'clear_chat':
            self._eval_bubble('show_panel', panel='chat')
        self._eval_board('load_thread', items=snapshot['items'], session_id=snapshot['session_id'],
                         sessions=snapshot['sessions'])

    def new_chat_session(self) -> dict:
        return self.bridge.new_chat_session()

    def switch_chat_session(self, session_id: str) -> dict:
        return self.bridge.switch_chat_session(session_id)

    def clear_chat_history(self) -> dict:
        return self.bridge.clear_chat()

    def try_build_agent(self) -> None:
        return self.conversation.try_build_agent()

    def _record_usage(self, agent, run_id: str) -> dict:
        return self.conversation.record_usage(agent, run_id)

    def board_chat(self) -> dict:
        return self.chat_sessions.load_chat_log()

    def board_memory(self) -> dict:
        return self.chat_sessions.load_memory()

    def board_compress_context(self) -> dict:
        return self.context_compression.compress_context()

    def board_load_notebook(self) -> dict:
        return self.notebooks.load_notebook()

    def board_new_notebook(self) -> dict:
        return self.notebooks.new_notebook()

    def board_save_notebook_note(self, session_id: str, question: str, answer: str, cites: list) -> dict:
        return self.notebooks.save_notebook_note(session_id, question, answer, cites)

    def board_delete_notebook_note(self, session_id: str, note_id: str) -> dict:
        return self.notebooks.delete_notebook_note(session_id, note_id)

    def board_export_notebook_markdown(self, session_id: str, note_id: str) -> dict:
        return self.notebooks.export_notebook_markdown(session_id, note_id)

    def board_export_notebook_feishu(self, session_id: str, note_id: str) -> dict:
        return self.notebooks.export_notebook_feishu(session_id, note_id)

    def board_open_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        return self.notebooks.open_notebook_file(session_id, note_id, name)

    def board_reveal_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        return self.notebooks.reveal_notebook_file(session_id, note_id, name)

    def board_delete_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        return self.notebooks.delete_notebook_file(session_id, note_id, name)

    def run_notebook(self, session_id, text, doc_ids, sampling, delta_sink, status_sink):
        return self.notebooks.run_notebook(session_id, text, doc_ids, sampling, delta_sink, status_sink)

    def board_summarize_notebook(self, session_id: str, note_ids: list, sampling: dict) -> dict:
        return self.notebooks.summarize_notebook(session_id, note_ids, sampling)

    def prepare_context(
        self,
        current_text: str,
        *,
        exclude_user: str | None = None,
        manual: bool = False,
    ) -> list[dict]:
        return self.conversation.prepare_context(current_text, exclude_user=exclude_user, manual=manual)

    def inject_prepared(self, current_text: str, *, exclude_user: str | None = None) -> None:
        return self.conversation.inject_prepared(current_text, exclude_user=exclude_user)

    def board_add_fact(self, text: str) -> dict:
        return self.memory_editing.add_fact(text)

    def board_update_fact(self, fact_id: str, text: str) -> dict:
        return self.memory_editing.update_fact(fact_id, text)

    def board_drop_memory_turn(self, role: str, text: str) -> dict:
        return self.memory_editing.delete_memory_turn(role, text)

    def board_delete_fact(self, fact_id: str) -> dict:
        return self.memory_editing.delete_fact(fact_id)

    def load_today_board(self, refresh: bool = False) -> dict:
        from .board_data import load_today_snapshot

        try:
            return {"ok": True, **load_today_snapshot(refresh=bool(refresh))}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def board_log_errors(self) -> dict:
        from .log_inspect import inspect_today_errors

        try:
            return inspect_today_errors()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def board_skills(self) -> dict:
        from .skill_catalog import list_skills

        try:
            return {"ok": True, "items": list_skills()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def board_persona(self) -> dict:
        return self.assistant_settings.load_persona()

    def save_persona(self, payload: dict) -> dict:
        return self.assistant_settings.save_persona(payload)

    def board_model(self) -> dict:
        return self.assistant_settings.load_model()

    def board_models(self) -> dict:
        return self.assistant_settings.load_models()

    def save_model_entry(self, payload: dict) -> dict:
        return self.assistant_settings.save_model_entry(payload)

    def delete_model_entry(self, model_id: str) -> dict:
        return self.assistant_settings.delete_model_entry(model_id)

    def use_model(self, model_id: str) -> dict:
        return self.assistant_settings.use_model(model_id)

    def engage_model(self, model_id: str = "") -> None:
        return self.conversation.engage_model(model_id)

    def save_model(self, payload: dict) -> dict:
        return self.assistant_settings.save_model(payload)

    def test_model(self, payload: dict) -> dict:
        return self.assistant_settings.test_model(payload)

    def board_usage(self) -> dict:
        try:
            data = summarize_usage()
            data["ok"] = True
            return data
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def board_github(self) -> dict:
        from .github import board_snapshot

        try:
            return board_snapshot()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _chat_visible(self) -> bool:
        return bool(self.pet and self.pet.bubble_open)

    def _work_area(self) -> tuple[int, int, int, int]:
        if self.pet and self.pet.hwnd:
            return work_area(self.pet.hwnd)
        return work_area()

    def _poll_outside_click(self) -> None:
        down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
        was_down = self._outside_btn_down
        self._outside_btn_down = down
        if not down or was_down:
            return
        if not self.pet or not self.pet.bubble_open:
            return
        x, y = win32gui.GetCursorPos()
        hit = root_hwnd(int(win32gui.WindowFromPoint((x, y))))
        if self.pet.hwnd and hit == int(self.pet.hwnd):
            return
        self.hide_bubble()

    def show_board(self) -> None:
        if self.board is None:
            raise RuntimeError("看板窗口还没建好。")
        show_form(self.board, activate=True, tool=False, topmost=False)
        if not self._board_placed:
            left, top, right, bottom = self._work_area()
            x = left + max(24, (right - left - BOARD_W) // 2)
            y = top + max(24, (bottom - top - BOARD_H) // 2)
            move_form(self.board, x, y, topmost=False)
            self._board_placed = True
        self.board.evaluate_js("window.reloadBoard && window.reloadBoard()")

    def hide_board(self) -> None:
        hide_form(self.board)

    def ask_today(self) -> None:
        self.show_pet()
        self.send_chat(TODAY_ASK)

    def ask_logs(self) -> None:
        self.show_pet()
        self.send_chat(LOG_ASK)

    def generate_week_review(self) -> dict:
        if self._agent_running:
            return {"ok": False, "error": "助手正在回复，等这句说完再生成本周复盘。"}
        if self.agent is None:
            return {
                "ok": False,
                "error": self._agent_error or "还没接上模型。打开看板「模型」页填写。",
            }
        from .board_data import (
            fetch_week_feishu,
            load_today_snapshot,
            save_today_fields,
            week_range,
        )
        from .github import format_week_review_text
        from .skill_catalog import read_skill

        try:
            load_today_snapshot(refresh=False)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        monday, now = week_range()
        feishu = fetch_week_feishu(monday, now)
        if not feishu["agenda"].get("ok"):
            return {
                "ok": False,
                "error": feishu["agenda"].get("error")
                or "本周日程读取失败，去「飞书」页检查登录。",
            }
        if not feishu["tasks"].get("ok"):
            return {
                "ok": False,
                "error": feishu["tasks"].get("error")
                or "未完成待办读取失败，去「飞书」页检查登录。",
            }
        try:
            github_text = format_week_review_text(monday, now)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        try:
            skill = read_skill("weekly-retro")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self._agent_running = True
        run_id = str(uuid.uuid4())
        try:
            prompt = (
                SUMMARY_ASK
                + "\n\n【技能规程】\n"
                + skill["body"]
                + "\n\n【本周材料】\n"
                + self._week_review_materials(monday, now, feishu, github_text)
            )
            result = self.agent.llm.chat(
                [
                    {
                        "role": "system",
                        "content": self.state.persona
                        + "\n\n你现在只写本周复盘，不要调用工具。按用户消息里的技能规程写。",
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            self._record_llm_usage(result.get("usage"), run_id)
            message = result.get("message") or {}
            markdown = str(message.get("content") or "").strip()
            if not markdown:
                raise RuntimeError("复盘正文为空。")
            summary_at = datetime.now(TZ).isoformat(timespec="seconds")
            snap = save_today_fields(summary=markdown, summary_at=summary_at)
            return {
                "ok": True,
                **snap,
                "message": "本周复盘已写在自动化任务「周复盘」和今日页，并已落盘。",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._agent_running = False

    def write_today_summary_doc(self) -> dict:
        from .board_data import load_today_snapshot, save_today_fields, week_range
        from .feishu_auth import create_markdown_doc

        try:
            snap = load_today_snapshot(refresh=False)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        markdown = (snap.get("summary") or "").strip()
        if not markdown:
            return {"ok": False, "error": "还没有复盘。先在自动化任务「周复盘」里生成。"}
        try:
            monday, now = week_range()
            title = f"{monday.strftime('%Y-%m-%d')}～{now.strftime('%Y-%m-%d')} 周复盘"
            created = create_markdown_doc(title, markdown)
            snap = save_today_fields(doc_url=created["url"])
            os.startfile(created["url"])
            return {
                "ok": True,
                **snap,
                "url": created["url"],
                "message": "复盘已写到飞书文档，并打开了链接。",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _week_review_materials(
        self, monday: datetime, now: datetime, feishu: dict, github_text: str
    ) -> str:
        start = monday.strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d %H:%M")
        parts = [
            f"区间：{start}～{end} 东八区",
            "",
            "## GitHub 产出与待完成",
            github_text.strip() or "无",
            "",
            "## 飞书本周日程",
        ]
        agenda_items = feishu["agenda"].get("items") or []
        if not agenda_items:
            parts.append("本周无日程。")
        else:
            for item in agenda_items:
                when = item.get("start") or ""
                if item.get("end"):
                    when = f"{when} – {item['end']}"
                parts.append(f"- {when} {item.get('summary') or ''}".strip())
        parts.extend(["", "## 飞书未完成待办"])
        task_items = feishu["tasks"].get("items") or []
        if not task_items:
            parts.append("无未完成待办。")
        else:
            for item in task_items:
                due = item.get("due_at") or "无截止"
                parts.append(f"- {item.get('summary') or ''}（截止 {due}）")
        return "\n".join(parts)

    def _record_llm_usage(self, usage, run_id: str) -> None:
        return self.conversation.record_llm_usage(usage, run_id)

    def send_chat(self, text: str, *, from_board: bool = False) -> None:
        with self.chat_sessions.selected_session() as session_id:
            text = (text or "").strip()
            if not text or not self.pet:
                return
            if self.agent is None:
                msg = self._agent_error or "还没接上模型。打开看板侧边栏「模型」填写 API 地址、模型名和 Key。"
                if not from_board:
                    self.show_bubble("chat")
                self._eval_bubble("append_message", role="user", text=text)
                self._eval_bubble("append_message", role="err", text=msg)
                self._eval_bubble("set_busy", busy=False)
                self._eval_board("append_message", role="user", text=text)
                self._eval_board("append_message", role="err", text=msg)
                self._eval_board("set_busy", busy=False)
                return
            if self.pet.busy or self._agent_running:
                return
            if not from_board:
                self.show_bubble("chat")
            self._eval_bubble("append_message", role="user", text=text)
            self._eval_bubble("begin_stream")
            self._eval_board("append_message", role="user", text=text)
            self._eval_board("begin_stream")
            self.pet.set_busy(True)
            self._agent_running = True
            try:
                append_chat("user", text, session_id)
                threading.Thread(target=self._run_agent, args=(text,), daemon=True).start()
            except Exception:
                self._agent_running = False
                self.pet.set_busy(False)
                raise

    def _run_agent(self, text: str) -> None:
        try:
            self.engage_model()
        except Exception as exc:
            self.ui(lambda message=str(exc): self._on_agent_done(message, True))
            return
        self._agent_running = True
        run_id = str(uuid.uuid4())
        failed = False
        try:
            self.inject_prepared(text, exclude_user=text)
            answer = spoken_answer(self.conversation.run_agent(text, run_id=run_id))
            self._record_usage(self.agent, run_id)
        except Exception as exc:
            answer = str(exc)
            failed = True
            if self.agent is not None:
                try:
                    self._record_usage(self.agent, run_id)
                except Exception as usage_exc:
                    log(f"用量记录失败: {usage_exc}")
        self.ui(lambda: self._on_agent_done(answer, failed))

    def _on_agent_done(self, answer: str, failed: bool) -> None:
        try:
            if not self.pet:
                return
            role = "err" if failed else "pet"
            if not failed:
                self.pet.review()
                append_chat("pet", answer, self.state.session_id)
            else:
                self.pet.fail()
            self._eval_bubble("end_stream", text=answer, role=role)
            self._eval_bubble("set_busy", busy=False)
            self._eval_board("end_stream", text=answer, role=role)
            self._eval_board("set_busy", busy=False)
            if self._card_mode == "chat" or (self.pet and self.pet.bubble_open):
                self.show_bubble("chat")
        finally:
            self._agent_running = False

    def _run_daily(self) -> None:
        if not self.state.nudge_enabled:
            return
        today = datetime.now(TZ).date().isoformat()
        if self.state.last_daily_date == today:
            log("今日纸条已写过，跳过")
            return
        with self.chat_sessions.selected_session():
            if self._agent_running or (self.pet is not None and self.pet.busy):
                return
            self._agent_running = True
        try:
            self.engage_model()
        except Exception as exc:
            self._agent_running = False
            self.ui(lambda message=str(exc): self.show_notice(message))
            return
        ready = threading.Event()
        self.ui(lambda: self._begin_daily_stream(ready))
        if not ready.wait(8):
            log("今日纸条：气泡未及时开流")
        run_id = str(uuid.uuid4())
        failed = False
        try:
            self.inject_prepared(DAILY_ASK)
            answer = str(self.agent.run(DAILY_ASK, run_id=run_id)).strip()
            if not answer:
                raise RuntimeError("今日纸条返回空内容。")
            self._record_usage(self.agent, run_id)
        except Exception as exc:
            answer = str(exc)
            failed = True
            if self.agent is not None:
                try:
                    self._record_usage(self.agent, run_id)
                except Exception as usage_exc:
                    log(f"用量记录失败: {usage_exc}")
        self.ui(lambda: self._on_daily_done(answer, failed, today))

    def _begin_daily_stream(self, ready: threading.Event) -> None:
        try:
            if self.pet and not self.pet.visible():
                self.pet.show()
            self._card_mode = "chat"
            self.pet.bubble_open = True
            self._eval_bubble("show_panel", panel="chat")
            self._eval_bubble("begin_stream")
        except Exception as exc:
            log(f"今日纸条开流: {exc}")
        finally:
            ready.set()

    def _on_daily_done(self, answer: str, failed: bool, today: str) -> None:
        try:
            if not self.pet or not self.state.nudge_enabled:
                return
            self._card_mode = "chat"
            role = "err" if failed else "pet"
            self._eval_bubble("end_stream", text=answer, role=role)
            self._eval_bubble("show_panel", panel="chat")
            self._eval_board("end_stream", text=answer, role=role)
            if failed:
                return
            self.pet.wave()
            append_chat("pet", answer, self.state.session_id)
            self.state.last_daily_date = today
            self.state.save()
        finally:
            self._agent_running = False

    def _run_template_nudge(self) -> None:
        text = None
        err = None
        try:
            from .board_data import fetch_board
            from .nudge import pick_template

            text = pick_template(fetch_board())
        except Exception as exc:
            err = str(exc)
        self.ui(lambda: self._on_template(text, err))

    def _on_template(self, text: str | None, err: str | None) -> None:
        self._nudge_busy = False
        if not self.pet or not self.state.nudge_enabled:
            return
        if err:
            self.show_notice(err, panel="today")
            return
        if text:
            self.show_notice(text, panel="today")

    def on_llm_delta(self, piece: str) -> None:
        if not piece or self.pet is None:
            return
        self._eval_bubble("append_stream", piece=piece)
        self._eval_board("append_stream", piece=piece)

    def on_stream_status(self, text: str) -> None:
        if not text or self.pet is None:
            return
        self._eval_bubble("set_stream_status", text=text)
        self._eval_board("set_stream_status", text=text)

    def _eval_bubble(self, event: str, **params) -> None:
        if self.pet is None:
            log(f"跳过 {event}：宠物还不存在")
            return
        try:
            self.pet.emit(event, **params)
        except Exception as exc:
            log(f"emit {event}: {exc}")

    def _eval_board(self, event: str, **params) -> None:
        if self.board is None:
            return
        payload = {"event": event, **params}
        script = "window.onBoardEvent && window.onBoardEvent(" + json.dumps(
            payload, ensure_ascii=False
        ) + ")"
        try:
            self.board.evaluate_js(script)
        except Exception as exc:
            log(f"board emit {event}: {exc}")

    def quit(self) -> None:
        if self._quitting:
            os._exit(0)
        self._quitting = True
        if self.pet is not None:
            self.pet.persist()
            self.pet.close()
        if self.icon is not None:
            self.icon.visible = False
            icon = self.icon
            self.icon = None
            threading.Thread(target=icon.stop, daemon=True).start()
        self.pet = None
        os._exit(0)

    def on_board_shown(self, *_args) -> None:
        log("board shown")
        try:
            hide_form(self.board)
        except Exception as exc:
            log(f"board shown hide: {exc}")

    def on_board_closing(self, *_args):
        if self._quitting:
            return True
        self.hide_board()
        return False


def run_app(skin_id: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("desk-companion 只支持 Windows。")
    install_crash_hooks()
    log("启动 desk-companion")
    _acquire_singleton()
    enable_dpi_aware()
    ui_dir = Path(__file__).resolve().parent / "ui"
    board_path = ui_dir / "board.html"
    if not board_path.is_file():
        raise RuntimeError(f"缺少界面文件。请确认存在 {board_path}。")

    print("正在加载凯尔希…", flush=True)
    log("load_skin")
    app = App(skin_id)
    print("正在显示宠物…", flush=True)
    log("start_pet_thread")
    app.pet = start_pet_thread(app, app.skin)
    print("正在连接助手…", flush=True)
    log("build_agent")
    app.try_build_agent()
    log("webview.create_window 看板")
    app.board = webview.create_window(
        "看板",
        board_path.as_uri(),
        js_api=app.bridge,
        width=BOARD_W,
        height=BOARD_H,
        min_size=(720, 480),
        frameless=False,
        resizable=True,
        hidden=True,
        on_top=False,
        background_color=PAPER,
        text_select=True,
        shadow=True,
        easy_drag=False,
    )
    app.board.events.shown += app.on_board_shown
    app.board.events.closing += app.on_board_closing
    start_os_watchdog(25)
    try:
        log("webview.start")
        webview.start(app.on_ready, gui="edgechromium")
    except Exception as exc:
        raise RuntimeError(
            "无法启动 Edge WebView2。请安装 Microsoft Edge WebView2 Runtime 后重试。\n"
            f"{exc}"
        ) from exc


def _is_ui_fault(exc: BaseException) -> bool:
    name = type(exc).__name__
    msg = str(exc)
    if name in {"WebViewException", "JSException", "RuntimeError"}:
        return True
    if "WebView" in name:
        return True
    if "Main window failed" in msg or "evaluate_js" in msg:
        return True
    return False


def _acquire_singleton() -> None:
    global _mutex_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if not handle:
        raise RuntimeError("无法创建单实例锁，拒绝启动第二个桌宠。")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        raise RuntimeError(
            "桌宠已经在运行。若桌面上还留着窗口但退不出，"
            "打开任务管理器结束所有命令行含 desk_companion 的 python.exe 后再启动。"
        )
    _mutex_handle = handle


def _prepare_form(
    window,
    *,
    tool: bool,
    round_corners: bool = True,
    transparent: bool = False,
) -> None:
    form = form_of(window)
    if tool:
        apply_tool_window(form)
        if round_corners:
            _set_corner_preference(window, DWMWCP_ROUND)
        else:
            _set_corner_preference(window, DWMWCP_DONOTROUND)
    else:
        apply_app_window(form)
    form.Opacity = 0
    form.Hide()
    form.Visible = False
    cache_hwnd(window)


def _native_hwnd(window) -> int:
    native = window.native
    if native is None:
        raise RuntimeError("pywebview 窗口还没有 native 句柄。")
    handle = native.Handle
    if hasattr(handle, "ToInt64"):
        return as_hwnd(int(handle.ToInt64()))
    return as_hwnd(int(handle))


def _set_corner_preference(window, preference: int) -> None:
    hwnd = _native_hwnd(window)
    value = ctypes.c_int(preference)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd,
        DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(value),
        ctypes.sizeof(value),
    )


def _message_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, text, "desk-companion", 0x10)

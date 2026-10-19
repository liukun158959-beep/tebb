"""看板对话工作台：session、输入点选、复盘写入飞书。"""
from __future__ import annotations

import json
import os

from .memory import list_chat, list_sessions
from .state import new_session_id

from .conversation_input import CLI_CHIPS, compose_turn


class BoardWorkbench:
    def board_composer_options(self) -> dict:
        from .skill_catalog import list_skills

        try:
            skills = [
                {"id": item["id"], "label": item["id"], "description": item["description"]}
                for item in list_skills()
            ]
        except Exception as extra:
            return {"ok": False, "error": str(extra)}
        github = {"ok": True, "items": []}
        try:
            from .github import list_owned_repos

            github = {
                "ok": True,
                "items": [
                    {"id": row["full_name"], "label": row["full_name"]}
                    for row in list_owned_repos()
                ],
            }
        except Exception as extra:
            github = {"ok": False, "error": str(extra), "items": []}
        return {
            "ok": True,
            "skills": skills,
            "cli": [{"id": key, "label": label} for key, label in CLI_CHIPS],
            "github": github,
        }

    def new_chat_session(self) -> dict:
        try:
            if self._agent_running:
                raise RuntimeError("助手正在回复，等这句说完再开新对话。")
            current = list_chat(self.state.session_id)
            if current:
                self.state.session_id = new_session_id()
                self.state.save()
            self._reload_session_views()
            return {"ok": True, "session_id": self.state.session_id}
        except Exception as extra:
            return {"ok": False, "error": str(extra)}

    def switch_chat_session(self, session_id: str) -> dict:
        try:
            if type(session_id) is not str or not session_id.strip():
                raise RuntimeError("切换对话需要 session_id。")
            sid = session_id.strip()
            known = {row["id"] for row in list_sessions()}
            if sid != self.state.session_id and sid not in known:
                raise RuntimeError("没有这条对话线程。")
            if self._agent_running:
                raise RuntimeError("助手正在回复，等这句说完再切换对话。")
            self.state.session_id = sid
            self.state.save()
            self._reload_session_views()
            return {"ok": True, "session_id": sid, "items": list_chat(sid)}
        except Exception as extra:
            return {"ok": False, "error": str(extra)}

    def _reload_session_views(self) -> None:
        rows = list_chat(self.state.session_id)
        if self.agent is not None:
            self.agent.memory.clear()
        self._eval_bubble("load_history", rows=rows)
        self._eval_board(
            "load_thread",
            items=rows,
            session_id=self.state.session_id,
            sessions=list_sessions(),
        )

    def list_mcp_tools(self) -> dict:
        from .mcp_client import list_mcp_menu

        return list_mcp_menu()

    def list_feishu_docs(self) -> dict:
        from .feishu_docs import list_my_docx
        from .wiki_connection import catalog, load_settings, enrich_catalog
        items, errors = [], []
        for get in (list_my_docx, catalog):
            try:
                items.extend(get())
            except Exception as extra:
                items.extend(getattr(extra, 'items', []))
                errors.append(str(extra))
        unique = {item["token"]: item for item in items}
        grouped = enrich_catalog(list(unique.values())) if unique else []
        return {"ok": bool(items) or not errors, "items": grouped,
                "error": "\n".join(errors), "wiki_connected": bool(load_settings().get("wiki_url"))}

    def write_week_review_doc(self, payload: dict) -> dict:
        from .board_data import load_today_snapshot, save_today_fields
        from .feishu_docs import create_or_overwrite_markdown

        if not isinstance(payload, dict):
            return {"ok": False, "error": "写入参数必须是对象。"}
        try:
            snap = load_today_snapshot(refresh=False)
            markdown = (snap.get("summary") or "").strip()
            result = create_or_overwrite_markdown(
                markdown=markdown,
                doc=str(payload.get("doc") or ""),
                title=str(payload.get("title") or ""),
            )
            url = result.get("url") or ""
            if url:
                snap = save_today_fields(doc_url=url)
                if "新建" in str(result.get("message") or ""):
                    os.startfile(url)
            return {"ok": True, **snap, **result}
        except Exception as extra:
            return {"ok": False, "error": str(extra)}

    def list_automation_jobs(self, seen: bool = False) -> dict:
        try:
            if seen:
                return self.automation.seen()
            return self.automation.snapshot()
        except Exception as extra:
            return {"ok": False, "error": str(extra)}

    def save_automation_job(self, payload: dict) -> dict:
        from .automation import upsert_job

        try:
            upsert_job(payload)
            return self.automation.snapshot()
        except Exception as extra:
            return {"ok": False, "error": str(extra)}

    def delete_automation_job(self, job_id: str) -> dict:
        from .automation import delete_job

        try:
            self.automation.drop_queued(str(job_id or ""))
            delete_job(str(job_id or ""))
            return self.automation.snapshot()
        except Exception as extra:
            return {"ok": False, "error": str(extra)}

    def run_automation_job(self, job_id: str) -> dict:
        try:
            return self.automation.run_now(str(job_id or ""))
        except Exception as extra:
            return {"ok": False, "error": str(extra)}

    def send_board_chat(self, text: str, chips: dict | None = None) -> dict:
        if isinstance(chips, str):
            try:
                chips = json.loads(chips)
            except json.JSONDecodeError as extra:
                return {"ok": False, "error": f"点选参数不是合法 JSON。{extra}"}
        if chips is None:
            chips = {}
        if not isinstance(chips, dict):
            return {"ok": False, "error": "点选参数必须是对象。"}
        if self.pet is None:
            return {"ok": False, "error": "宠物还没就绪。"}
        if self.pet.busy or self._agent_running:
            return {"ok": False, "error": "助手正在回复。"}
        try:
            retro = chips.get("retro")
            if retro:
                raise RuntimeError(
                    "复盘请用自动化任务「周复盘」生成或写入，不要和聊天混在一条发送里。"
                )
            message = self._compose_turn(text, chips)
            self.ui(lambda: self.send_chat(message, from_board=True))
            return {"ok": True}
        except Exception as extra:
            return {"ok": False, "error": str(extra)}


    def _compose_turn(self, text: str, chips: dict) -> str:
        return compose_turn(text, chips)

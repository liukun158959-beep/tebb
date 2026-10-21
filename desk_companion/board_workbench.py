"""旧看板工作台：输入点选、自动化与复盘写入飞书。"""
from __future__ import annotations

import json

from . import workbench_queries
from .conversation_input import compose_turn


class BoardWorkbench:
    def board_composer_options(self) -> dict:
        return workbench_queries.load_composer_options()

    def list_mcp_tools(self) -> dict:
        return workbench_queries.list_mcp_tools()

    def list_feishu_docs(self) -> dict:
        return workbench_queries.list_feishu_docs()

    def write_week_review_doc(self, payload: dict) -> dict:
        return self.weekly_review.write_week_review_doc(payload)

    def list_automation_jobs(self, seen=False):
        return self.automation.list_automation_jobs(seen)

    def save_automation_job(self, payload):
        return self.automation.save_automation_job(payload)

    def delete_automation_job(self, job_id):
        return self.automation.delete_automation_job(job_id)

    def run_automation_job(self, job_id):
        return self.automation.run_automation_job(job_id)

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

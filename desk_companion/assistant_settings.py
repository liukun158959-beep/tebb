"""Model and persona settings shared by desktop and RPC; no window dependencies."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .assistant import TOOL_CONTRACT, list_providers
from .envconf import parse_env_file, public_llm_env, validate_llm_env, write_llm_env

if TYPE_CHECKING:
    from .conversation import ConversationService


class AssistantSettingsService:
    """Configuration rules; ConversationService owns shared state and Agent lifecycle."""

    def __init__(self, conversation: ConversationService):
        self.conversation = conversation

    @property
    def state(self):
        return self.conversation.state

    def load_persona(self) -> dict:
        try:
            return {
                "ok": True,
                "persona": self.state.persona,
                "max_steps": self.state.max_steps,
                "nudge_enabled": self.state.nudge_enabled,
                "tool_contract": TOOL_CONTRACT,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_persona(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            return {"ok": False, "error": "人设保存参数必须是对象。"}
        try:
            if self.conversation.running:
                raise RuntimeError("助手正在回复，等这句说完再保存。")
            persona = str(payload.get("persona") or "").strip()
            if not persona:
                raise RuntimeError("人设不能为空。")
            max_steps = int(payload.get("max_steps"))
            if max_steps < 1:
                raise RuntimeError("max_steps 至少为 1。")
            nudge = payload.get("nudge_enabled")
            if type(nudge) is not bool:
                raise RuntimeError("主动搭话必须是 true/false。")
            self.state.persona = persona
            self.state.max_steps = max_steps
            self.state.nudge_enabled = nudge
            self.state.save()
            self.conversation.rebuild_agent()
            return {"ok": True, "message": "人设已保存。"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def load_model(self) -> dict:
        try:
            pub = public_llm_env()
            model = pub["model"]
            price = self.state.model_prices.get(model) or {}
            return {
                "ok": True,
                "providers": list_providers(),
                "base_url": pub["base_url"],
                "model": model,
                "has_key": pub["has_key"],
                "agent_ok": self.conversation.agent is not None,
                "agent_error": self.conversation.agent_error,
                "input_cny_per_mtok": price.get("input_cny_per_mtok"),
                "output_cny_per_mtok": price.get("output_cny_per_mtok"),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def load_models(self) -> dict:
        from .model_catalog import public_catalog

        try:
            return {"ok": True, **public_catalog()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_model_entry(self, payload: dict) -> dict:
        from .model_catalog import public_catalog, upsert_entry

        if not isinstance(payload, dict):
            return {"ok": False, "error": "模型保存参数必须是对象。"}
        try:
            if self.conversation.running:
                raise RuntimeError("助手正在回复，等这句说完再保存。")
            cat = upsert_entry(payload)
            self.conversation.bind_active_llm()
            return {"ok": True, "message": "模型已保存。", **public_catalog(cat)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def delete_model_entry(self, model_id: str) -> dict:
        from .model_catalog import delete_entry, public_catalog

        try:
            if self.conversation.running:
                raise RuntimeError("助手正在回复，等这句说完再删除。")
            cat = delete_entry(model_id)
            self.conversation.bind_active_llm()
            return {"ok": True, "message": "模型已删除。", **public_catalog(cat)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def use_model(self, model_id: str) -> dict:
        from .model_catalog import activate, public_catalog

        try:
            if self.conversation.running:
                raise RuntimeError("助手正在回复，等这句说完再换模型。")
            activate(model_id)
            self.conversation.bind_active_llm()
            return {"ok": True, "message": "已切换模型。", **public_catalog()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_model(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            return {"ok": False, "save_state": "not_saved", "error": "模型保存参数必须是对象。"}
        try:
            if self.conversation.running:
                raise RuntimeError("助手正在回复，等这句说完再保存。")
            base_url = str(payload.get("base_url") or "").strip()
            model = str(payload.get("model") or "").strip()
            api_key = str(payload.get("api_key") or "").strip()
            if not api_key:
                api_key = parse_env_file().get("ATLAS_API_KEY") or ""
            connection = validate_llm_env(api_key=api_key, base_url=base_url, model=model)
            prices = dict(self.state.model_prices)
            inn_raw = payload.get("input_cny_per_mtok")
            out_raw = payload.get("output_cny_per_mtok")
            blank = inn_raw in (None, "") and out_raw in (None, "")
            if blank:
                prices.pop(model, None)
            else:
                if inn_raw in (None, "") or out_raw in (None, ""):
                    raise RuntimeError("输入、输出单价要一起填，或都留空。")
                try:
                    if isinstance(inn_raw, bool) or isinstance(out_raw, bool):
                        raise ValueError
                    inn, outv = float(inn_raw), float(out_raw)
                except (TypeError, ValueError, OverflowError):
                    raise RuntimeError("单价必须是有限的非负数字。") from None
                if not all(math.isfinite(value) and value >= 0 for value in (inn, outv)):
                    raise RuntimeError("单价必须是有限的非负数字。")
                prices[model] = {
                    "input_cny_per_mtok": inn,
                    "output_cny_per_mtok": outv,
                }
        except Exception as exc:
            return {"ok": False, "save_state": "not_saved", "error": str(exc)}

        try:
            write_llm_env(**connection)
        except Exception as exc:
            return {"ok": False, "save_state": "unconfirmed",
                    "error": f"模型连接配置写入失败，保存结果未确认：{exc}。请重新打开设置核对后重试。"}

        previous_prices = self.state.model_prices
        self.state.model_prices = prices
        try:
            self.state.save()
        except Exception as exc:
            self.state.model_prices = previous_prices
            return {"ok": False, "save_state": "partial",
                    "error": f"模型连接配置已写入，但价格配置保存未完成：{exc}。请重新打开设置核对后重试。"}

        try:
            self.conversation.rebuild_agent()
        except Exception as exc:
            return {"ok": False, "save_state": "saved",
                    "error": f"模型配置已保存，但助手初始化失败：{exc}。请检查配置后重试。"}
        return {"ok": True, "save_state": "saved", "message": "模型配置已保存。"}

    def test_model(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            return {"ok": False, "error": "测试参数必须是对象。"}
        try:
            base_url = str(payload.get("base_url") or "").strip()
            model = str(payload.get("model") or "").strip()
            api_key = str(payload.get("api_key") or "").strip()
            if not api_key:
                entry_id = str(payload.get("id") or "").strip()
                if entry_id:
                    from .model_catalog import require_item

                    api_key = require_item(entry_id)["api_key"]
                else:
                    api_key = parse_env_file().get("ATLAS_API_KEY") or ""
            if not api_key or not base_url or not model:
                raise RuntimeError("测试前先填 API 地址、模型名和 Key。")
            from .model_validation import validate_base_url
            from .model_network import openai_client
            with openai_client(api_key=api_key, base_url=validate_base_url(base_url), timeout=20) as client:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=8,
                )
            text = (response.choices[0].message.content or "").strip()
            return {"ok": True, "message": "连通正常。" + (f" 回复：{text}" if text else "")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

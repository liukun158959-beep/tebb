"""可切换的模型清单。Key 记在 models.json，接口不把原文交回页面。"""
from __future__ import annotations

from .paths import data_root

import json
import uuid
from pathlib import Path

from .envconf import LLM_KEYS, parse_env_file, write_llm_env
from .model_validation import validate_base_url

NAME = "models.json"


def catalog_path() -> Path:
    return data_root() / NAME


def load_catalog() -> dict:
    path = catalog_path()
    if not path.is_file():
        seeded = _seed_from_env()
        if seeded["items"]:
            _write(seeded)
        return seeded
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} 不是合法 JSON。删掉该文件后重新打开设置。") from exc
    return _validate(raw, path)


def public_catalog(cat: dict | None = None) -> dict:
    data = cat if cat is not None else load_catalog()
    return {
        "active": data["active"],
        "items": [
            {
                "id": item["id"],
                "base_url": item["base_url"],
                "model": item["model"],
                "has_key": bool(item["api_key"]),
            }
            for item in data["items"]
        ],
    }


def require_item(model_id: str) -> dict:
    cat = load_catalog()
    item = _find(cat, model_id)
    if item is None:
        raise RuntimeError("没有这个模型。打开设置重新选一条。")
    return item


def require_active() -> dict:
    cat = load_catalog()
    if not cat["items"]:
        raise RuntimeError("还没有可选模型。打开设置添加一个。")
    item = _find(cat, cat["active"])
    if item is None:
        raise RuntimeError(f"{catalog_path()} 的 active 没有对应模型。删掉该文件后重新打开设置。")
    return item


def activate(model_id: str) -> dict:
    if type(model_id) is not str or not model_id.strip():
        raise RuntimeError("要切换的模型不能为空。")
    cat = load_catalog()
    item = _find(cat, model_id.strip())
    if item is None:
        raise RuntimeError("没有这个模型。打开设置重新选一条。")
    cat["active"] = item["id"]
    _write(cat)
    write_llm_env(api_key=item["api_key"], base_url=item["base_url"], model=item["model"])
    return item


def upsert_entry(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeError("模型保存参数必须是对象。")
    base_url = str(payload.get("base_url") or "").strip()
    model = str(payload.get("model") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    entry_id = str(payload.get("id") or "").strip()
    _reject_newline(base_url, model, api_key)
    if not base_url:
        raise RuntimeError("API 地址不能为空。")
    if not model:
        raise RuntimeError("模型名不能为空。")
    base_url = validate_base_url(base_url)
    cat = load_catalog()
    if entry_id:
        current = _find(cat, entry_id)
        if current is None:
            raise RuntimeError("没有这个模型。打开设置重新选一条。")
        if not api_key:
            api_key = current["api_key"]
        if not api_key:
            raise RuntimeError("API Key 不能为空。")
        current["base_url"] = base_url
        current["model"] = model
        current["api_key"] = api_key
        item = current
    else:
        if not api_key:
            source_id = str(payload.get("copy_key_from") or "").strip()
            source = _find(cat, source_id) if source_id else None
            if source is None or not source["api_key"]:
                raise RuntimeError("API Key 不能为空。要沿用已有 Key，先在列表里点一条。")
            api_key = source["api_key"]
            _reject_newline(api_key)
        item = {
            "id": str(uuid.uuid4()),
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
        }
        cat["items"].append(item)
        if not cat["active"]:
            cat["active"] = item["id"]
    _write(cat)
    if cat["active"] == item["id"]:
        write_llm_env(api_key=item["api_key"], base_url=item["base_url"], model=item["model"])
    return cat


def delete_entry(model_id: str) -> dict:
    if type(model_id) is not str or not model_id.strip():
        raise RuntimeError("要删除的模型不能为空。")
    cat = load_catalog()
    if len(cat["items"]) <= 1:
        raise RuntimeError("至少留一个模型。要换的话直接改这一条。")
    kept = [item for item in cat["items"] if item["id"] != model_id.strip()]
    if len(kept) == len(cat["items"]):
        raise RuntimeError("没有这个模型。打开设置重新选一条。")
    cat["items"] = kept
    if cat["active"] == model_id.strip():
        cat["active"] = kept[0]["id"]
        write_llm_env(
            api_key=kept[0]["api_key"],
            base_url=kept[0]["base_url"],
            model=kept[0]["model"],
        )
    _write(cat)
    return cat


def bind_llm(llm, item: dict, timeout=90) -> None:
    from .model_network import openai_client

    client = openai_client(api_key=item["api_key"], base_url=item["base_url"], timeout=timeout)
    llm.bind_client(client, model=item["model"], owned=True)


def _seed_from_env() -> dict:
    data = parse_env_file()
    if not all(data.get(key) for key in LLM_KEYS):
        return {"active": "", "items": []}
    item = {
        "id": str(uuid.uuid4()),
        "base_url": data["ATLAS_BASE_URL"],
        "model": data["ATLAS_MODEL"],
        "api_key": data["ATLAS_API_KEY"],
    }
    return {"active": item["id"], "items": [item]}


def _find(cat: dict, model_id: str) -> dict | None:
    for item in cat["items"]:
        if item["id"] == model_id:
            return item
    return None


def _reject_newline(*values: str) -> None:
    if any(ch in value for value in values for ch in "\n\r"):
        raise RuntimeError("配置不能包含换行。")


def _write(cat: dict) -> None:
    catalog_path().write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate(raw, path: Path) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} 根节点必须是对象。删掉该文件后重新打开设置。")
    items = raw.get("items")
    active = raw.get("active")
    if type(active) is not str:
        raise RuntimeError(f"{path} 的 active 必须是字符串。删掉该文件后重新打开设置。")
    if type(items) is not list:
        raise RuntimeError(f"{path} 的 items 必须是列表。删掉该文件后重新打开设置。")
    seen = set()
    clean = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError(f"{path} 的模型条目必须是对象。删掉该文件后重新打开设置。")
        entry_id = item.get("id")
        base_url = item.get("base_url")
        model = item.get("model")
        api_key = item.get("api_key")
        if any(type(value) is not str or not value.strip() for value in (entry_id, base_url, model, api_key)):
            raise RuntimeError(f"{path} 的模型条目缺 id、地址、模型名或 Key。删掉该文件后重新打开设置。")
        if entry_id in seen:
            raise RuntimeError(f"{path} 的模型 id 重复。删掉该文件后重新打开设置。")
        seen.add(entry_id)
        clean.append(
            {
                "id": entry_id.strip(),
                "base_url": base_url.strip(),
                "model": model.strip(),
                "api_key": api_key.strip(),
            }
        )
    if active and active not in seen:
        raise RuntimeError(f"{path} 的 active 没有对应模型。删掉该文件后重新打开设置。")
    if clean and not active:
        raise RuntimeError(f"{path} 有模型但没有 active。删掉该文件后重新打开设置。")
    return {"active": active, "items": clean}

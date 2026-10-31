"""保留原历史与显式手动摘要；最终消息/工具容量由执行适配器核对。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .memory import memory_path, session_for_model

TZ = timezone(timedelta(hours=8))
PACK_MARK = "【上下文压缩】"

_ASK = (
    "把下面更早的对话压成一段中文。"
    "保留授权与禁止事项、当前目标、决定、人名、时间、未完成事项、原操作编号、失败和未知回执；未知不能改成成功。"
    "不要编。不要客套。不要调用工具。压完直接给正文。"
)


def estimate_tokens(text: str) -> int:
    """偏大的估算：汉字和标点各 1，连续英文或数字每 4 个字符 1，空白各 1。"""
    if type(text) is not str:
        raise RuntimeError("token 估算只能对字符串。")
    tokens = 0
    ascii_run = 0
    for ch in text:
        if ch.isascii() and not ch.isspace():
            ascii_run += 1
            continue
        if ascii_run:
            tokens += (ascii_run + 3) // 4
            ascii_run = 0
        tokens += 1
    if ascii_run:
        tokens += (ascii_run + 3) // 4
    return tokens


def estimate_messages(rows: list[dict]) -> int:
    total = 0
    for row in rows:
        total += 4 + estimate_tokens(str(row.get("text") or ""))
    return total


def summary_text(body: str) -> str:
    text = (body or "").strip()
    if not text:
        raise RuntimeError("压缩摘要是空的。")
    return (
        f"{PACK_MARK}以下是更早对话的压缩。只根据这段回忆，不要编原文里没有的决定。\n"
        + text
    )


def pack_path() -> Path:
    return memory_path().parent / "context_pack.json"


def context_view(session_id: str, system_prompt: str) -> dict:
    """给页面看的估算和摘要。不调用模型。"""
    system = estimate_tokens(system_prompt)
    pack = _read_session(session_id)
    rows = session_for_model(session_id)
    before = system + estimate_messages(rows)
    estimate = system + estimate_messages(_inject_rows_from(rows, pack))
    from .model_capacity import current
    capacity=current();window=capacity.get('window');reserve=capacity.get('output_reserve',0)
    return {
        "budget": window,
        "tool_reserve": reserve if window is not None else None,
        "capacity_source": capacity.get('source','unknown'),
        "estimate_source": "character_heuristic_history_only",
        "before": before,
        "estimate": estimate,
        "saved": before - estimate,
        "summary": pack["summary"] if pack else "",
        "covered": pack["covered"] if pack else [],
        "over": None,
    }


def prepare_injection(
    session_id: str,
    system_prompt: str,
    current_text: str,
    *,
    exclude_user: str | None,
    complete,
    manual: bool,
    save=None,
) -> list[dict]:
    """返回要灌进模型的历史。manual 为真时不管预算，收掉最近一轮以外的原文。"""
    if type(current_text) is not str:
        raise RuntimeError("本轮正文必须是字符串。")
    rows = _without_current(session_for_model(session_id), exclude_user)
    pack = _read_session(session_id)
    if manual:
        return _compress_manual(session_id, rows, pack, complete, save or save_pack)
    return _inject_rows_from(rows, pack)


def raw_tail(session_id: str) -> list[dict]:
    """还没收进摘要、会按原文注入的句子。"""
    return _uncovered(session_for_model(session_id), _read_session(session_id))


def drop_pack(session_id: str) -> None:
    _write_session(session_id, None)


def drop_pack_if_covered(session_id: str, role: str, text: str) -> bool:
    """删掉的句子已经在摘要里时，整段摘要作废，避免模型还记得被删的话。"""
    pack = _read_session(session_id)
    if not pack:
        return False
    body = text.strip()
    if any(item["role"] == role and item["text"] == body for item in pack["covered"]):
        _write_session(session_id, None)
        return True
    return False


def _compress_manual(session_id: str, rows: list[dict], pack: dict | None, complete, save) -> list[dict]:
    uncovered = _uncovered(rows, pack)
    prefix, tail = _last_exchange(uncovered)
    if not prefix:
        raise RuntimeError(
            "还没有更早的对话可以整理。请保留最近一轮，继续对话后再手动整理。"
        )
    summary = _summarize(pack, prefix, complete)
    covered = list(pack["covered"]) if pack else []
    covered.extend(prefix)
    saved = save(session_id, summary, covered)
    return _inject_rows_from(rows, saved)


def _summarize(pack: dict | None, folded: list[dict], complete) -> str:
    parts = [_ASK, ""]
    if pack and pack.get("summary"):
        parts.append("已有压缩：")
        parts.append(pack["summary"])
        parts.append("")
    if folded:
        parts.append("还要收进去的原文：")
        for row in folded:
            who = "用户" if row["role"] == "user" else "助手"
            parts.append(f"{who}：{row['text']}")
    if not pack and not folded:
        raise RuntimeError("没有可以压缩的对话。")
    text = complete("\n".join(parts))
    if type(text) is not str or not text.strip():
        raise RuntimeError(
            "压缩结果是空的。恢复：再压一次；仍空就删掉 memory/context_pack.json 后重试。"
        )
    return text.strip()


def _last_exchange(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    if len(rows) >= 2 and rows[-2]["role"] == "user" and rows[-1]["role"] == "pet":
        return rows[:-2], rows[-2:]
    if rows:
        return rows[:-1], rows[-1:]
    return [], []


def _inject_rows(session_id: str, pack: dict | None) -> list[dict]:
    return _inject_rows_from(session_for_model(session_id), pack)


def _inject_rows_from(rows: list[dict], pack: dict | None) -> list[dict]:
    out = []
    if pack:
        out.append({"role": "user", "text": summary_text(pack["summary"]), "kind": "pack"})
    out.extend(_uncovered(rows, pack))
    return out


def _uncovered(rows: list[dict], pack: dict | None) -> list[dict]:
    if not pack:
        return list(rows)
    covered = {(item["role"], item["text"]) for item in pack["covered"]}
    return [row for row in rows if (row["role"], row["text"]) not in covered]


def _without_current(rows: list[dict], exclude_user: str | None) -> list[dict]:
    if not exclude_user or not rows:
        return rows
    body = exclude_user.strip()
    last = rows[-1]
    if last["role"] == "user" and last["text"] == body:
        return rows[:-1]
    return rows


def save_pack(session_id: str, summary: str, covered: list[dict]) -> dict:
    """Validate and persist one summary while preserving other sessions."""
    clean = []
    seen = set()
    for item in covered:
        role = item["role"]
        text = item["text"].strip()
        if role not in ("user", "pet") or not text:
            raise RuntimeError("压缩范围里有空句子。")
        key = (role, text)
        if key in seen:
            continue
        seen.add(key)
        clean.append({"role": role, "text": text})
    pack = {
        "summary": summary.strip(),
        "covered": clean,
        "updated": datetime.now(TZ).isoformat(timespec="seconds"),
    }
    if not pack["summary"] or not pack["covered"]:
        raise RuntimeError("压缩没有摘要或没有收进任何原文。")
    _write_session(session_id, pack)
    return pack


def _read_session(session_id: str) -> dict | None:
    with _PACK_LOCK:
        return _read_session_locked(session_id)


def _read_session_locked(session_id: str) -> dict | None:
    if type(session_id) is not str or not session_id.strip():
        raise RuntimeError("压缩记录需要非空 session_id。")
    path = pack_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{path} 不是合法 JSON。删掉该文件后重启，压缩会按原文重新计算。"
        ) from exc
    if not isinstance(raw, dict) or set(raw) - {"sessions"}:
        raise RuntimeError(f"{path} 只能有 sessions。删掉该文件后重启。")
    sessions = raw.get("sessions")
    if not isinstance(sessions, dict):
        raise RuntimeError(f"{path} 的 sessions 必须是对象。删掉该文件后重启。")
    item = sessions.get(session_id.strip())
    if item is None:
        return None
    return _check_pack(item, path)


def _check_pack(item, path: Path) -> dict:
    if not isinstance(item, dict) or set(item) - {"summary", "covered", "updated"}:
        raise RuntimeError(f"{path} 里某一线程的压缩字段不对。删掉该文件后重启。")
    summary = item.get("summary")
    covered = item.get("covered")
    updated = item.get("updated")
    if type(summary) is not str or not summary.strip():
        raise RuntimeError(f"{path} 的压缩摘要是空的。删掉该文件后重启。")
    if type(updated) is not str or not updated.strip():
        raise RuntimeError(f"{path} 的压缩时间是空的。删掉该文件后重启。")
    if type(covered) is not list or not covered:
        raise RuntimeError(f"{path} 的压缩范围是空的。删掉该文件后重启。")
    clean = []
    for row in covered:
        if not isinstance(row, dict) or set(row) - {"role", "text"}:
            raise RuntimeError(f"{path} 的压缩原文必须带 role 和 text。删掉该文件后重启。")
        role = row.get("role")
        text = row.get("text")
        if role not in ("user", "pet") or type(text) is not str or not text.strip():
            raise RuntimeError(f"{path} 的压缩原文角色或正文不对。删掉该文件后重启。")
        clean.append({"role": role, "text": text.strip()})
    return {"summary": summary.strip(), "covered": clean, "updated": updated.strip()}


def _write_session(session_id: str, pack: dict | None) -> None:
    with _PACK_LOCK:
        _write_session_locked(session_id, pack)


from .resource_lock import ResourceLock
_PACK_LOCK = ResourceLock("context-pack")


def _write_session_locked(session_id: str, pack: dict | None) -> None:
    path = pack_path()
    sessions: dict = {}
    if path.is_file():
        current = _read_all(path)
        sessions = dict(current)
    sid = session_id.strip()
    if pack is None:
        sessions.pop(sid, None)
    else:
        sessions[sid] = pack
    path.parent.mkdir(parents=True, exist_ok=True)
    if not sessions:
        if path.is_file():
            path.unlink()
        return
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"sessions": sessions}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_all(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{path} 不是合法 JSON。删掉该文件后重启，压缩会按原文重新计算。"
        ) from exc
    if not isinstance(raw, dict) or set(raw) - {"sessions"}:
        raise RuntimeError(f"{path} 只能有 sessions。删掉该文件后重启。")
    sessions = raw.get("sessions")
    if not isinstance(sessions, dict):
        raise RuntimeError(f"{path} 的 sessions 必须是对象。删掉该文件后重启。")
    return sessions

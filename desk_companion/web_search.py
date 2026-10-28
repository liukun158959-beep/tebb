"""Shared search tool formatting for Atlas, partners, video and news."""
from __future__ import annotations

from .search_service import search_endpoint, search as search_sources

QUERY_MAX = 200
SOURCE_LIMIT = 5

def format_search(payload: dict) -> str:
    """收成标题、摘要和链接。没有可用链接就失败。"""
    if not isinstance(payload, dict):
        raise RuntimeError("联网搜索返回不是对象。不要编。")
    if payload.get("error") and "results" not in payload:
        raise RuntimeError(f"联网搜索失败：{payload.get('error')} 不要编。")
    rows = payload.get("results")
    if type(rows) is not list or not rows:
        message = str(payload.get("message") or "").strip()
        if message:
            raise RuntimeError(f"联网搜索失败：{message} 不要编。")
        raise RuntimeError("没有搜到结果。不要编。")
    lines = ["来源："]
    if payload.get("provider"):
        lines.append("搜索渠道："+str(payload["provider"]))
    if payload.get("fallback_reason"):
        lines.append("主搜索未完成，使用备用来源："+str(payload["fallback_reason"]))
        lines.append("备用 RSS 只返回候选链接，可能跑题；必须核对实体、主题与日期，无关结果应说明证据不足，不能视为问题已解决。")
    kept = 0
    skipped = 0
    for row in rows:
        if kept >= SOURCE_LIMIT:
            break
        if not isinstance(row, dict):
            skipped += 1
            continue
        title = str(row.get("title") or "").strip()
        link = str(row.get("link") or "").strip()
        if not title or not (link.startswith("https://") or link.startswith("http://")):
            skipped += 1
            continue
        kept += 1
        lines.append(f"{kept}. {title}")
        lines.append(link)
        snippet = str(row.get("snippet") or "").strip()
        if len(snippet) > 300:
            snippet = snippet[:300] + "（摘要后面没带上）"
        if snippet:
            lines.append(snippet)
        published = str(row.get("published_date") or "").strip()
        if published:
            lines.append(f"日期：{published}")
    if kept == 0:
        raise RuntimeError("搜索结果没有可用的标题和链接。不要编。")
    extra = len(rows) - kept - skipped
    if extra > 0:
        lines.append(f"还有 {extra} 条来源没列出。")
    if skipped:
        lines.append(f"有 {skipped} 条缺少标题或链接，没有列入。")
    lines.append("本次搜索只提供标题、摘要和链接，未读取来源全文。引用本次搜索时只用这些内容支持的结论，对不上就不要采用；与视频字幕等其他来源分开标明。")
    return "\n".join(lines)


def web_search(args: dict, *, on_status=None) -> str:
    try:
        if not isinstance(args, dict):
            raise RuntimeError("搜索参数不是对象，没有搜索。不要编。")
        query = args.get("query")
        if type(query) is not str or not query.strip():
            raise RuntimeError("搜索词是空的，没有搜索。不要编。")
        text = query.strip()
        if len(text) > QUERY_MAX:
            raise RuntimeError(f"搜索词超过 {QUERY_MAX} 字，没有搜索。不要编。")
        payload = search_sources(text, on_status=on_status)
        return format_search(payload)
    except Exception as exc:
        message = str(exc).strip() or "联网搜索失败。"
        if "不要编" not in message:
            message += " 不要编。"
        # Atlas 根据异常记录真实失败，仍会把错误交给模型解释。
        raise RuntimeError(message) from None


WEB_SEARCH_SPEC = {
    "func": web_search,
    "name": "web_search",
    "description": (
        "搜索互联网上的实时信息。用户问天气、新闻、现在、最新、网上才有的事，"
        "或这一轮指定必须调用 web_search 时使用。"
        "也用于读取视频字幕后，核实关键争议或时效性事实，优先搜索官方资料；不能代替读取视频。"
        "query 是一句搜索词，不超过 200 字。"
        "只根据返回的来源标题、摘要和链接回答。失败或没有结果就原样说明，不要编。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索词，不超过 200 字"},
        },
        "required": ["query"],
    },
    "isReadOnly": True,
    "retry_max": 0,
}

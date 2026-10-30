"""飞书发布阶段：确定目标、图片、每日文档、逐条记录及幂等群卡片。"""
from __future__ import annotations
import json
import shutil
import time
from html import escape
from pathlib import Path

from .feishu_tools import _run_lark
from .news import FIELDS, digest, display_title, phase, runs_root
from .paths import data_root


class PublishError(RuntimeError):
    def __init__(self, message, *, confirmed=False, retryable=None):
        super().__init__(message)
        self.confirmed = confirmed
        self.retryable = retryable


def platform_error(value):
    if not isinstance(value, dict) or value.get("ok") is not False:
        return None
    error = value.get("error") or {}
    if not isinstance(error, dict):
        return PublishError("飞书操作未确认成功，请检查平台诊断。")
    code = error.get("code")
    if code == 230099:
        return PublishError("飞书卡片格式校验未通过，未发送群消息。", confirmed=True, retryable=False)
    confirmed = error.get("type") in {"validation", "auth", "authentication", "authorization", "config"} or (
        error.get("type") == "api" and isinstance(code, int) and code > 0 and code not in {500, 502, 503, 504})
    subtype = str(error.get("subtype", ""))[:60]
    if subtype in {"transport", "timeout", "network"} and not confirmed:
        return PublishError("飞书网络请求未完成（" + subtype + "），已保留发布回执，可稍后恢复。", retryable=True)
    return PublishError("飞书操作未成功（" + subtype + "），请检查权限和推送位置。",
                        confirmed=confirmed, retryable=not confirmed)


def cli(cfg, args, *, identity="user", stdin=None, timeout=60):
    try:
        raw = _run_lark(["--profile", cfg["profile"], *args, "--as", identity], timeout=timeout, stdin=stdin)
        value = json.loads(raw)
        error = platform_error(value)
        if error:
            raise error
        data = value.get("data", value)
        if isinstance(data, dict) and data.get("code", 0) != 0:
            raise PublishError("飞书操作被拒绝，请检查权限。", confirmed=True)
        return data
    except PublishError:
        raise
    except Exception as exc:
        raw = str(exc)
        start = raw.find("{")
        if start >= 0:
            try:
                value, _ = json.JSONDecoder().raw_decode(raw[start:])
                error = platform_error(value)
            except (ValueError, TypeError):
                error = None
            if error:
                raise error from None
        text = str(exc).lower()
        auth = any(x in text for x in ("scope", "permission", "unauthorized", "invalid_client", "forbidden"))
        raise PublishError("飞书授权或权限未通过，请在飞书页检查。" if auth else
                           "飞书请求结果未确认，请检查网络；已保存的回执会用于恢复。",
                           confirmed=auth, retryable=not auth) from None


def validate_targets(cfg):
    if not cfg["profile"] or not cfg["wiki_url"] or not cfg["base_url"] or not cfg["table_id"]:
        raise ValueError("请先填写飞书应用、知识库、多维表格和数据表。")
    wiki = cli(cfg, ["wiki", "+node-get", "--node-token", cfg["wiki_url"]])
    resolved = cli(cfg, ["base", "+url-resolve", "--url", cfg["base_url"]])
    token = resolved.get("base_token")
    if not token:
        raise ValueError("多维表格链接未解析为有效 Base。")
    # 仅复用已确认的数据表，URL 的 table 参数可能表示其他类型 block。
    fields = cli(cfg, ["base", "+field-list", "--base-token", token, "--table-id", cfg["table_id"]]).get("fields", [])
    mapping = {f.get("name"): f for f in fields}
    if any(name not in mapping or mapping[name].get("type") != "text" or
           mapping[name].get("style", {}).get("type", "plain") != "plain" for name in FIELDS):
        raise ValueError("数据表缺少资讯所需的普通文本字段，请使用桌宠创建的资讯表。")
    group = {}
    if cfg["chat_id"]:
        group = cli(cfg, ["im", "chats", "get", "--params", json.dumps({"chat_id": cfg["chat_id"]})], identity="bot")
        if group.get("chat_mode") not in {"group", "topic"}:
            raise ValueError("推送目标必须是机器人已加入的群聊。")
    return {"base_token": token, "space_id": wiki["space_id"], "node_token": wiki["node_token"],
            "wiki_title": wiki.get("title", ""), "chat_name": group.get("name", "未设置群")}


def cover_path(run):
    return runs_root() / (run["id"] + ".png")


def render_cover(run):
    from .news_poster import render_cover as poster
    return poster(run)


def document_xml(run, image_relative):
    e = lambda text: escape(str(text), quote=True)
    report = run["report"]
    blocks = [f"<title>{e(doc_title(run))}</title>", f"<p><b>{e(report['lead'])}</b></p>",
              f'<img path="@./{e(image_relative)}" caption="本期重点海报；背景为原创科技视觉，文字来自本期资料"/>',
              f"<p>核实 {report['candidate_count']} 个官方来源。摘要依据网页正文，实践建议需在项目中验证；未标注日期的来源不代表今日发布。</p>"]
    for item in report["items"]:
        blocks += [f'<h1 seq="auto">{e(display_title(item))}</h1>',
                   f"<p><b>{e(item.get('company', ''))} · {e(item['category'])}</b></p>",
                   f"<p>{e(item.get('detail') or item['summary'])}</p>"]
        if item.get("key_points"):
            blocks += ["<p><b>技术要点</b></p>", "<ul>" + "".join(f"<li>{e(p)}</li>" for p in item["key_points"]) + "</ul>"]
        blocks += [
                   f"<p><b>实践建议：</b>{e(item['value'])}</p>",
                   f"<p><b>局限：</b>{e(item['caution'] or '尚未在桌宠项目中验证。')}</p>",
                   f"<p>发布时间：{e(item['published'] if item['date_verified'] else '来源未标注可核实日期')}</p>",
                   f'<p><a href="{e(item["url"])}">官方原始来源</a></p>']
    if run.get("warnings"):
        blocks += ['<h1 seq="auto">采集缺口</h1>', "<ul>" + "".join(f"<li>{e(w)}</li>" for w in run["warnings"]) + "</ul>"]
    return "".join(blocks)


def doc_title(run):
    return f"AI/Agent 工程日报 · {run['day']} · {run['id'][:8]}"


def find_document(cfg, target, title):
    page = ""
    found = []
    while True:
        args = ["wiki", "+node-list", "--space-id", target["space_id"], "--parent-node-token", target["node_token"]]
        if page:
            args += ["--page-token", page]
        data = cli(cfg, args)
        found.extend(r for r in data.get("nodes", data.get("items", [])) if r.get("title") == title and r.get("obj_type") == "docx")
        if not data.get("has_more"):
            break
        newpage = data.get("page_token") or data.get("next_page_token")
        if not newpage or newpage == page:
            raise PublishError("知识库分页未完成，无法安全查重。")
        page = newpage
    if len(found) > 1:
        raise PublishError("知识库存在同名日报，请先核实，未重复写入。")
    return found[0] if found else None


def create_document(run, target, save, emit):
    cfg = run["settings"]
    existing = find_document(cfg, target, doc_title(run))
    if existing:
        base = cfg["wiki_url"].split("/wiki/")[0]
        save(doc_id=existing["obj_token"], doc_url=base + "/wiki/" + existing["node_token"])
        return
    if run["receipts"].get("doc_pending"):
        raise PublishError("上次文档创建结果尚未确认，当前查重无匹配；未重复创建，请稍后核实。")
    decision = {"audience": "桌面 Agent 开发者", "reader_task": "判断本期前沿技术是否值得在桌宠中试用，并查看官方证据与局限",
                "genre_contract": "none", "adapter": "none", "presentation_mode": "rich",
                "visual_plan": {"reason": "以海报呈现本期五项重点，正文展开技术机制、实践价值和来源",
                                "blocks": [{"type": "img", "min_count": 1, "purpose": "突出本期主线及五项技术动向"}]}}
    draft = cli(cfg, ["docs", "+script", "--command", "init-draft", "--presentation-decision", json.dumps(decision, ensure_ascii=False)])
    root = data_root().resolve()
    workspace = (root / draft["workspace"]).resolve()
    path = (root / draft["draft_path"]).resolve()
    if not workspace.is_relative_to(root) or workspace == root or not path.is_relative_to(workspace):
        raise ValueError("CLI 草稿路径不在任务工作区，未写入或清理。")
    try:
        relative_image = cover_path(run).resolve().relative_to(root).as_posix()
        path.write_text(document_xml(run, relative_image), "utf-8")
        content = "@./" + path.relative_to(root).as_posix()
        assessment = cli(cfg, ["docs", "+script", "--command", "parse", "--content", content])
        if assessment.get("assessment", {}).get("status") != "passed":
            raise PublishError("文档草稿结构或图片预检未通过，未创建文档。", confirmed=True)
        run["receipts"]["doc_pending"] = True
        save(status="publishing")
        try:
            result = phase(emit, "write", "news_create_document", lambda: cli(cfg, ["docs", "+create", "--doc-format", "xml",
                "--content", content, "--parent-token", target["node_token"]], timeout=90))
        except PublishError as exc:
            if exc.confirmed:
                run["receipts"].pop("doc_pending", None)
                save()
            raise
        document = result.get("document", {})
        if not document.get("document_id") or not document.get("url"):
            raise PublishError("文档创建回执不完整，未再次创建。")
        save(doc_id=document["document_id"], doc_url=document["url"])
        if result.get("warnings"):
            save(warnings=run["warnings"] + ["文档创建存在资源警告，请打开检查。"])
    finally:
        # 仅清理由本次 init-draft 返回、已经确认属于数据根目录的独占工作区。
        if workspace.exists():
            shutil.rmtree(workspace)


def single_record_id(value):
    """Decode one Base v3 receipt, retaining legacy single-record responses."""
    from .lark_receipts import single_record_id as decode
    try:
        return decode(value)
    except ValueError as exc:
        raise PublishError('资讯表' + str(exc) + '未重复写入。') from None


def find_record(cfg, token, key):
    # Business-key equality must not depend on keyword search index freshness.
    args = ["base", "+record-list", "--base-token", token, "--table-id", cfg["table_id"],
            "--field-id", "资讯ID", "--filter-json", json.dumps({"logic": "and", "conditions": [["资讯ID", "==", key]]}), "--format", "json"]
    result = cli(cfg, args)
    if not isinstance(result, dict):
        raise PublishError("资讯表查重回执格式无效，未重复写入。")
    if result.get("has_more"):
        raise PublishError("资讯表查重结果未完整返回，未重复写入。")
    if "record_id_list" in result:
        return single_record_id(result)
    rows = result.get("records", result.get("items"))
    if not isinstance(rows, list):
        raise PublishError("资讯表查重回执缺少记录列表，未重复写入。")
    if len(rows) > 1:
        raise PublishError("资讯表存在重复资讯 ID，请先核实。")
    if rows:
        rid = single_record_id(rows[0])
        if not rid:
            raise PublishError("查重记录缺少记录 ID，未重复写入。")
        return rid
    return ""


def ensure_record(run, item, token, save, emit):
    cfg = run["settings"]
    key = digest([run["day"], item["url"]])
    receipts = run["receipts"].setdefault("records", {})
    if receipts.get(key):
        return
    existing = find_record(cfg, token, key)
    if existing:
        receipts[key] = existing
        pending = run["receipts"].get("records_pending", [])
        if key in pending:
            pending.remove(key)
        save()
        return
    pending = run["receipts"].setdefault("records_pending", [])
    if key in pending:
        raise PublishError("上次记录写入结果尚未确认，当前查重无匹配；未重复写入，请稍后核实。")
    fields = {"资讯ID": key, "日期": run["day"], "标题": display_title(item), "类别": item["category"], "摘要": item["summary"],
              "实践价值": item["value"] + "\n局限：" + item["caution"], "来源链接": item["url"],
              "发布时间": item["published"] if item["date_verified"] else "未标注可核实日期", "每日文档": run["doc_url"], "状态": "已归档"}
    pending.append(key)
    save()
    try:
        result = phase(emit, "write", "news_archive_record", lambda: cli(cfg, ["base", "+record-upsert", "--base-token", token,
                "--table-id", cfg["table_id"], "--json", json.dumps(fields, ensure_ascii=False)]))
    except PublishError as exc:
        if exc.confirmed:
            pending.remove(key)
            save()
        raise
    rid = single_record_id(result.get("record", {}))
    if not rid:
        # typed Base v3 创建回执可能只有写入字段；通过业务键回读确定记录 ID。
        rid = find_record(cfg, token, key)
    if not rid:
        raise PublishError("资讯表写入未返回记录 ID，未再次创建。")
    receipts[key] = rid
    pending.remove(key)
    save()


def news_card(run, image_key):
    def text(content):
        return {"tag": "markdown", "content": content}
    def safe(value):
        value = escape(str(value), quote=False)
        for ch in "*_[]":
            value = value.replace(ch, "&#" + str(ord(ch)) + ";")
        return value
    def column(elements, background="default"):
        return {"tag": "column", "width": "weighted", "weight": 1, "background_style": background, "padding": "12px", "elements": elements}
    def row(elements, background="default"):
        return {"tag": "column_set", "flex_mode": "none", "columns": [column(elements, background)]}
    report = run["report"]
    first = [{"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": "本期大厂开源与技术动向海报"}, "scale_type": "fit_horizontal"},
             {**text("**" + safe(report["lead"]) + "**"), "text_size": "heading-3"}]
    others = []
    for index, item in enumerate(report["items"], 1):
        others.append(text(f"**{index}. {safe(display_title(item))}**\n{safe(item['summary'][:80])}"))
    links = [{"tag": "button", "text": {"tag": "plain_text", "content": title}, "type": kind,
              "behaviors": [{"type": "open_url", "default_url": url}]} for title, kind, url in
             (("阅读每日文档", "primary_filled", run["doc_url"]), ("打开资讯表", "default", run["settings"]["base_url"]))]
    return {"schema": "2.0", "config": {"update_multi": True, "width_mode": "default", "summary": {"content": f"AI/Agent 工程日报 · {run['day']} · {run['id'][:8]}"}},
            "header": {"title": {"tag": "plain_text", "content": "AI/Agent 工程日报"}, "subtitle": {"tag": "plain_text", "content": run["day"] + " · " + str(len(report["items"])) + " 条重点"}, "template": "blue"},
            "body": {"direction": "vertical", "vertical_spacing": "12px", "padding": "12px",
                     "elements": [row(first, "blue-50"), row(others), row([
                         {**text("<font color='grey'>已归档官方来源；实践建议需验证，发布时间和局限详见文档。</font>"), "text_size": "notation"},
                         {"tag": "column_set", "flex_mode": "none", "columns": [column([b]) for b in links]}])]}}


def publish(run, save, emit, *, send_group=True):
    cfg = run["settings"]
    if run.get("status") == "published":
        emit("status", "本期资讯已经发布，复用回执，不重复推送。")
        return
    emit("status", "正在检查知识库、资讯表字段和机器人群权限。")
    target = phase(emit, "tool", "news_validate_targets", lambda: validate_targets(cfg))
    save(status="publishing", error="")
    image = render_cover(run)
    if not run.get("doc_id"):
        emit("status", "正在创建本期知识库文档，写入技术详解与海报。")
        create_document(run, target, save, emit)
    if not run["receipts"].get("doc_verified"):
        result = phase(emit, "tool", "news_verify_document", lambda: cli(cfg, ["docs", "+fetch", "--doc", run["doc_id"], "--doc-format", "xml", "--detail", "simple"]))
        raw = json.dumps(result, ensure_ascii=False)
        if any(item["url"] not in raw for item in run["report"]["items"]) or "<img" not in raw:
            raise PublishError("文档正文或图片未核实完整，未推送群消息。")
        run["receipts"]["doc_verified"] = True
        save()
    for i, item in enumerate(run["report"]["items"], 1):
        emit("status", f"正在归档资讯 {i}/{len(run['report']['items'])}：{item['title'][:60]}。")
        ensure_record(run, item, target["base_token"], save, emit)
    if send_group and cfg["chat_id"] and not run.get("message_id"):
        if not run["receipts"].get("image_key"):
            emit("status", "正在上传本期资讯海报。")
            key = phase(emit, "write", "news_upload_image", lambda: cli(cfg, ["im", "images", "create", "--data", '{"image_type":"message"}',
                "--file", "image=./" + image.resolve().relative_to(data_root().resolve()).as_posix()], identity="bot"))["image_key"]
            run["receipts"]["image_key"] = key
            save()
        pending = run["receipts"].get("message_pending_at", 0)
        if pending and time.time() - pending > 3500:
            raise PublishError("上次消息发送结果未确认且已超过幂等窗口，请核实群消息，未重复推送。")
        run["receipts"]["message_pending_at"] = pending or time.time()
        save()
        emit("status", "正在向目标群推送本期图文资讯卡片。")
        result = phase(emit, "write", "news_send_card", lambda: cli(cfg, ["im", "+messages-send", "--chat-id", cfg["chat_id"],
            "--msg-type", "interactive", "--content", json.dumps(news_card(run, run["receipts"]["image_key"]), ensure_ascii=False),
            "--idempotency-key", "dc-news-" + run["id"]], identity="bot"))
        mid = result.get("message_id") or result.get("data", {}).get("message_id")
        if not mid:
            raise PublishError("群卡片发送没有返回消息 ID，未确认成功。")
        save(message_id=mid)
    save(status="published" if send_group else "archived", error="", retryable=False)
    emit("status", "本期资讯已完成：每日文档和资讯表已归档" + ("，图文卡片已推送。" if send_group and cfg["chat_id"] else "。本次未发送群消息。"))

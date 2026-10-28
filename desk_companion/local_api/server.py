"""P1：本地 API 服务。HTTP /health + WebSocket RPC。

- HTTP GET /health：Rust 壳轮询就绪用。
- WS /ws?token=：收 {"type":"rpc","id","method","args"}，按白名单派发到 Bridge，
  回 {"type":"rpc_result","id","result"}。窗口/UI 类方法不在白名单，消除误用。

用法：python -m desk_companion.local_api.server --port 8770 --token <token>
本轮先做 RPC 数据方法；流式对话在下一刀接入。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import hmac
import os
import threading

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from ..runtime_instance import InstanceInUse, INSTANCE_EXIT_CODE
from ..sampling import parse_sampling
from .host import HeadlessApp

# 白名单：bridge 上可无头调用的数据方法。窗口/UI 类（close_bubble/fit_card/
# open_url/ask_today/ask_logs/close_board/send_chat）与流式（send_board_chat）不在此。
RPC_METHODS = frozenset({
    "load_work_board", "change_work_item", "tebb_operation", "check_automation_execution",
    "load_schedule", "change_schedule", "load_schedule_week", "load_schedule_month", "inspect_schedule_event",
    "load_local_calendar", "change_local_calendar", "calendar_sync_step",
    "load_collaboration", "collaboration_action", "project_workspace", "save_collaboration_note", "prepare_notebook_source",
    "terminal_status", "terminal_start", "terminal_list", "terminal_read", "terminal_input", "terminal_cancel",
    "terminal_files", "terminal_import_files", "terminal_preview_file",
    "load_video_login", "save_video_login", "clear_video_login",
    "list_video_tasks", "start_video_task", "continue_video_task",
    "load_video_settings", "save_video_settings", "list_task_videos", "export_task_video",
    "load_wiki_connection", "save_wiki_connection",
    "load_onboarding", "complete_onboarding", "report_pet_status", "check_updates",
    "load_pet_preferences", "save_pet_preferences",
    "list_agent_tasks", "get_agent_task", "cancel_agent_task", "continue_agent_task", "save_agent_task_settings", "load_task_progress",
    "list_debug_calls", "get_debug_call", "set_debug_recording", "explain_debug_call",
    "load_board", "delete_agenda", "delete_task", "create_agenda", "load_log_errors", "load_skills",
    "load_persona", "save_persona",
    "load_model", "save_model", "test_model",
    "load_model_network", "save_model_network",
    "load_models", "save_model_entry", "delete_model_entry", "use_model",
    "load_usage", "load_chat_log", "load_memory", "compress_context",
    "add_fact", "update_fact", "delete_fact", "delete_memory_turn",
    "load_feishu", "feishu_login", "feishu_logout",
    "load_feishu_agent", "start_feishu_agent", "stop_feishu_agent",
    "list_feishu_agent_profiles", "save_feishu_agent_settings",
    "update_feishu_agent_credentials", "check_feishu_agent_connection",
    "load_github",
    "generate_week_review",
    "write_today_summary_doc", "write_week_review_doc",
    "list_feishu_docs", "list_mcp_tools", "load_composer_options",
    "load_knowledge", "save_knowledge", "download_knowledge", "delete_model",
    "add_knowledge", "delete_knowledge", "rebuild_knowledge", "ask_knowledge",
    "pick_local_sources", "stage_local_sources", "read_local_source", "cancel_local_source_request",
    "load_notebook", "new_notebook", "save_notebook_note", "delete_notebook_note",
    "export_notebook_markdown", "export_notebook_feishu", "summarize_notebook",
    "open_notebook_file", "reveal_notebook_file", "delete_notebook_file",
    "new_chat_session", "switch_chat_session", "clear_chat",
    "list_automation_jobs", "save_automation_job",
    "delete_automation_job", "run_automation_job",
    "load_news", "save_news_settings", "check_news_targets", "run_news",
})

TOKEN = ""
HOST: HeadlessApp | None = None
SHUTDOWN: asyncio.Event | None = None


def _health(connection, request):
    if request.path == "/shutdown":
        authorization = request.headers.get("Authorization", "")
        if not TOKEN or not hmac.compare_digest(authorization.encode(), f"Bearer {TOKEN}".encode()):
            return connection.respond(401, "bad token\n")
        if SHUTDOWN is None:
            return connection.respond(503, "shutdown unavailable\n")
        SHUTDOWN.set()
        response = connection.respond(200, "shutting down\n")
        response.headers["Cache-Control"] = "no-store"
        return response
    # 非 WS 的普通 HTTP：健康检查与已认证的静态资源
    if request.path == "/health":
        return connection.respond(200, "ok\n")
    if request.path.startswith("/pet-assets/"):
        return _pet_asset(connection, request.path)
    if request.path.startswith("/news-cover/"):
        return _news_cover(connection, request.path)
    if request.path.startswith("/ws") or request.path.startswith("/collaboration?"):
        return None
    return connection.respond(404, "not found\n")


def _news_cover(connection, path):
    from urllib.parse import parse_qs, urlsplit
    from websockets.asyncio.server import Response
    from websockets.datastructures import Headers
    from ..news import run_path
    parts = urlsplit(path)
    token = parse_qs(parts.query).get("token", [""])[0]
    if not TOKEN or not hmac.compare_digest(token.encode(), TOKEN.encode()):
        return connection.respond(401, "bad token\n")
    try:
        file = run_path(parts.path.removeprefix("/news-cover/")).with_suffix(".png")
        body = file.read_bytes()
    except (OSError, ValueError):
        return connection.respond(404, "cover not found\n")
    headers = Headers()
    headers["Content-Type"] = "image/png"
    headers["Content-Length"] = str(len(body))
    headers["Cache-Control"] = "no-store"
    headers["Access-Control-Allow-Origin"] = "*"
    return Response(200, "OK", headers, body)


def read_pet_asset(request_path: str):
    from urllib.parse import unquote, urlsplit
    from ..paths import asset_root
    path = urlsplit(request_path).path.removeprefix("/pet-assets/")
    token, _, relative = path.partition("/")
    if not TOKEN or token != TOKEN:
        raise PermissionError("素材请求的 token 不对。")
    root = asset_root().resolve()
    target = (root / unquote(relative)).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix.lower() not in {
        ".js", ".json", ".png", ".jpg", ".jpeg", ".webp", ".moc3", ".wasm", ".wav", ".mp3", ".ogg"
    }:
        raise FileNotFoundError("找不到形象素材。")
    return target


def _pet_asset(connection, request_path: str):
    from websockets.asyncio.server import Response
    from websockets.datastructures import Headers
    try:
        path = read_pet_asset(request_path)
    except PermissionError as exc:
        return connection.respond(401, str(exc))
    except (FileNotFoundError, ValueError, OSError):
        return connection.respond(404, "asset not found\n")
    body = path.read_bytes()
    headers = Headers()
    # Windows 注册表可能将 .js 标记成 text/plain；跨源脚本会被浏览器拒绝。
    # 素材类型固定，不能使用受注册表影响的 mimetypes.guess_type。
    headers["Content-Type"] = {
        ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".wasm": "application/wasm", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".webp": "image/webp", ".wav": "audio/wav",
        ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    }.get(path.suffix.lower(), "application/octet-stream")
    headers["Content-Length"] = str(len(body))
    headers["Access-Control-Allow-Origin"] = "*"
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Cache-Control"] = "no-store"
    return Response(200, "OK", headers, body)




def _dispatch(method: str, args: dict, preview=False) -> dict:
    # 按白名单派发到 Bridge。不在白名单直接失败，不静默兜底。
    if method not in RPC_METHODS:
        return {"ok": False, "error": f"方法不允许或不存在：{method}"}
    if method == 'collaboration_action' and preview:
        try: return {'ok': True, 'result': HOST.collaboration.action(args.get('action'), readonly=True)}
        except Exception as exc: return {'ok': False, 'error': str(exc)}
    fn = getattr(HOST.bridge, method, None)
    if fn is None:
        return {"ok": False, "error": f"Bridge 无此方法：{method}"}
    try:
        target = fn
        def fn(**kwargs):
            from ..agent_debug import context
            with context(session=HOST.state.session_id, channel="backend"):
                return target(**kwargs)
        if method.startswith("terminal_") or method in {"tebb_operation", "check_automation_execution","load_local_calendar", "change_local_calendar", "calendar_sync_step", "load_schedule", "change_schedule", "load_schedule_week", "load_schedule_month", "inspect_schedule_event", "load_work_board", "change_work_item", "compress_context", "load_chat_log", "load_memory",
                      "load_notebook", "new_notebook", "save_notebook_note", "delete_notebook_note",
                      "export_notebook_markdown", "export_notebook_feishu", "open_notebook_file",
                      "reveal_notebook_file", "delete_notebook_file",
                      "summarize_notebook",
                      "load_knowledge", "save_knowledge", "download_knowledge", "delete_model",
                      "add_knowledge", "delete_knowledge", "rebuild_knowledge", "ask_knowledge",
                      "load_board", "create_agenda", "delete_agenda", "delete_task",
                      "generate_week_review", "write_today_summary_doc", "write_week_review_doc",
                      "list_automation_jobs", "save_automation_job", "delete_automation_job", "run_automation_job",
                      "load_collaboration", "collaboration_action", "project_workspace", "check_updates", "list_agent_tasks", "get_agent_task", "cancel_agent_task", "continue_agent_task", "save_agent_task_settings", "load_task_progress",
                      "list_debug_calls", "get_debug_call", "set_debug_recording", "explain_debug_call",
                      "load_model_network", "save_model_network",
                      "load_video_settings", "save_video_settings", "list_task_videos", "export_task_video",
                      "load_wiki_connection", "save_wiki_connection",
                      "load_news", "save_news_settings", "check_news_targets", "run_news",
                      "load_feishu_agent", "start_feishu_agent", "stop_feishu_agent",
                      "list_feishu_agent_profiles", "save_feishu_agent_settings",
                      "update_feishu_agent_credentials", "check_feishu_agent_connection",
                      "pick_local_sources", "stage_local_sources", "read_local_source", "cancel_local_source_request", "list_feishu_docs"}:
            result = fn(**args) if args else fn()
        else:
            # 桌面数据操作仍串行；Agent 的共享写入也与界面修改互斥。
            with HOST.turn_lock:
                mutating = {"add_fact", "update_fact", "delete_fact", "delete_memory_turn", "clear_chat"}
                if method in mutating:
                    from ..resource_lock import WRITES
                    with WRITES:
                        result = fn(**args) if args else fn()
                else:
                    result = fn(**args) if args else fn()
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _handler(ws):
    from urllib.parse import parse_qs, urlsplit
    req_path = ws.request.path if ws.request else ""
    parts = urlsplit(req_path)
    from ..collaboration_access import preview_allowed
    from ..collaboration import METHODS as COLLABORATION_METHODS
    preview = parts.path == "/collaboration"
    token = parse_qs(parts.query).get("token", [""])[0]
    origin = ws.request.headers.get("Origin", "") if ws.request else ""
    authorized = preview_allowed(token, origin) if preview else parts.path == "/ws" and (not TOKEN or hmac.compare_digest(token.encode(), TOKEN.encode()))
    if not authorized:
        await ws.close(code=4401, reason="bad token")
        return
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send(json.dumps({"type": "error", "data": "非法 JSON"}))
            continue
        mtype = msg.get("type")
        if preview and (not preview_allowed(token, origin) or mtype != "rpc" or msg.get("method") not in COLLABORATION_METHODS):
            await ws.send(json.dumps({"type": "error", "data": "此浏览器连接仅允许有效期内的协作聊天操作。"}))
            continue
        if mtype == "rpc":
            method = str(msg.get("method", ""))
            args = msg.get("args") or {}
            # 数据方法可能有阻塞 IO（飞书/GitHub），丢线程池不堵事件循环
            res = await asyncio.to_thread(_dispatch, method, args, preview)
            try:
                await ws.send(json.dumps({"type": "rpc_result", "id": msg.get("id"), "result": res}))
            except ConnectionClosed:
                return  # The operation finished; a disconnected caller must read its result again.
        elif mtype == "chat":
            await _handle_chat(ws, msg)
        elif mtype == "notebook":
            await _handle_notebook(ws, msg)
        else:
            await ws.send(json.dumps({"type": "error", "data": f"未知消息类型：{mtype}"}))


async def _handle_chat(ws, msg: dict) -> None:
    # 流式对话：agent.run 阻塞跑在线程池，deltas 经线程安全队列回推 WS。
    try:
        sampling = parse_sampling(msg.get("sampling"))
    except RuntimeError as exc:
        await ws.send(json.dumps({"type": "error", "data": str(exc)}))
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    session_id = HOST.state.session_id

    def delta_sink(piece: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("token", piece))

    def think_sink(piece: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("think", piece))

    def status_sink(text: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("status", text))

    def run() -> None:
        try:
            def knowledge_sink(payload: dict) -> None:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("knowledge", json.dumps(payload, ensure_ascii=False)),
                )

            def callback(kind, data):
                if kind in {"token", "status", "think", "knowledge", "done", "error"}:
                    if kind == "knowledge":
                        data = json.dumps(data, ensure_ascii=False)
                    loop.call_soon_threadsafe(queue.put_nowait, (kind, data))
            task_id = HOST.tasks.submit(str(msg.get("text", "")), session_id,
                                        chips=msg.get("chips"), sampling=sampling,
                                        model_id=str(msg.get("model_id") or ""), knowledge=bool(msg.get("knowledge")),
                                        callback=callback)
            loop.call_soon_threadsafe(queue.put_nowait, ("task", task_id))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", f"{type(exc).__name__}: {exc}"))

    asyncio.get_running_loop().run_in_executor(None, run)
    while True:
        kind, data = await queue.get()
        await ws.send(json.dumps({"type": kind, "data": data}))
        if kind in ("done", "error"):
            break


async def _handle_notebook(ws, msg: dict) -> None:
    try:
        sampling = parse_sampling(msg.get("sampling"))
    except RuntimeError as exc:
        await ws.send(json.dumps({"type": "error", "data": str(exc)}))
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    connected = threading.Event()
    connected.set()

    def publish(kind, data):
        if connected.is_set():
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (kind, data))
            except RuntimeError:
                pass  # A closed event loop cannot receive results; model cleanup still runs.

    def delta_sink(piece: str) -> None:
        publish('token', piece)

    def status_sink(text: str) -> None:
        publish('status', text)

    def run() -> None:
        try:
            from ..agent_debug import context
            with context(session=str(msg.get("session_id") or ""), channel="notebook"):
                found = HOST.notebooks.run_notebook(
                    str(msg.get("session_id") or ""),
                    str(msg.get("text") or ""),
                    msg.get("doc_ids") if isinstance(msg.get("doc_ids"), list) else [],
                    sampling,
                    delta_sink,
                    status_sink,
                )
            if found['ok']:
                publish('done', json.dumps(dict(answer=found['answer'], cites=found['cites']), ensure_ascii=False))
            else:
                publish('error', found)
        except Exception as exc:
            publish('error', f'{type(exc).__name__}: {exc}')

    asyncio.get_running_loop().run_in_executor(None, run)
    try:
        while True:
            kind, data = await queue.get()
            frame = dict(type=kind, data=data)
            if kind == 'error' and isinstance(data, dict):
                frame.update(data=data['error'], result=data)
            await ws.send(json.dumps(frame, ensure_ascii=False))
            if kind in ('done', 'error'):
                break
    except ConnectionClosed:
        return
    finally:
        connected.clear()


async def scheduled_jobs(host, shutdown):
    """无头客户端也驱动本地定时器，不依赖旧宠物窗口 tick。"""
    while not shutdown.is_set():
        await asyncio.to_thread(host.automation.tick)
        try:
            await asyncio.wait_for(shutdown.wait(), 5)
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    global TOKEN, HOST, SHUTDOWN
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", type=str, default="")
    args = parser.parse_args()
    from ..agent_debug import install
    install({"channel": "backend"})
    TOKEN = args.token
    os.environ["DESK_TERMINAL_PORT"] = str(args.port)
    os.environ["DESK_TERMINAL_TOKEN"] = TOKEN
    from ..runtime_instance import acquire_runtime
    acquire_runtime()
    HOST = HeadlessApp()
    SHUTDOWN = asyncio.Event()
    asyncio.create_task(asyncio.to_thread(HOST.feishu_agent.autostart))
    scheduler = asyncio.create_task(scheduled_jobs(HOST, SHUTDOWN))
    try:
        async with serve(_handler, "127.0.0.1", args.port, process_request=_health):
            print(f"local_api ready on 127.0.0.1:{args.port}", flush=True)
            await SHUTDOWN.wait()
            HOST.automation.stop()
            from ..local_sources import cancel_all
            cancel_all()
    finally:
        SHUTDOWN.set()
        HOST.automation.stop()
        await scheduler
        if HOST._tasks is not None:
            HOST._tasks.shutdown()
        if HOST._collaboration is not None:
            await asyncio.to_thread(HOST._collaboration.shutdown)
        from ..terminal import manager
        await asyncio.to_thread(manager().shutdown)
        await asyncio.to_thread(HOST.feishu_agent.stop, False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except InstanceInUse as exc:
        print(str(exc), flush=True)
        raise SystemExit(INSTANCE_EXIT_CODE)

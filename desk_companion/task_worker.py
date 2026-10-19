"""独立执行单个任务；只通过本地 stdin/stdout 和管理进程交换事件。"""
import contextlib
import json
import hashlib
import sys


def main():
    if '--standby' in sys.argv:
        # Imports/transport initialization only. No model call or user task runs
        # until stdin supplies one request; this process exits after that request.
        from . import collaboration_worker, project_workspace, local_sources, web_search, web_sources
        from .model_network import http_client
        with http_client(5):pass
        sys.stdout.write(json.dumps(dict(kind='standby_ready'))+'\n');sys.stdout.flush()
    request = json.loads(sys.stdin.readline())
    from .agent_debug import install
    install({"task_id": request.get("task_id", ""), "session": request["session_id"], "channel": request.get("channel", "desktop")})
    transport = sys.stdout

    def emit(kind, data):
        transport.write(json.dumps({"kind": kind, "data": data}, ensure_ascii=False) + "\n")
        transport.flush()

    # Atlas renderer、第三方库的调试输出不能混入事件协议。
    with contextlib.redirect_stdout(sys.stderr):
        if request.get("workflow") == "collaboration":
            from .collaboration_worker import execute
            try:
                execute(request, emit)
            except Exception as exc:
                emit("failure", {"type": type(exc).__name__, "message": getattr(exc, "public_message", "协作执行失败（" + type(exc).__name__ + "），请查看调用记录、模型配置或网络。请核查任务的文件改动与命令回执。")})
            return
        if request.get("workflow") == "debug_explain":
            from .agent_debug import execute_explanation
            try:
                execute_explanation(request, emit)
            except Exception as exc:
                emit("failure", {"type": type(exc).__name__, "message": "调用解读失败（" + type(exc).__name__ + "），请检查模型上下文容量或网络。"})
            return
        if request.get("workflow") == "ai_news":
            from .news import execute
            execute(request, emit)
            return
        if request.get("workflow") == "video_note":
            from .video_analysis import execute, failure_details
            try:
                execute(request, emit)
            except Exception as exc:
                emit("failure", failure_details(exc))
            return
        from atlas.core.plugin import BasePlugin
        from atlas.core.terminator import MaxSteps, Timeout
        from .local_api.host import HeadlessApp
        from .assistant import build_agent
        from .model_catalog import bind_llm, require_active, require_item
        from .resource_lock import WRITES
        from .sampling import parse_sampling
        from .video_archive import VideoArchive
        archive = VideoArchive(request, emit)

        class Progress(BasePlugin):
            name = "task_progress"
            def on_tool_before_call(self, **kw):
                emit("tool_start", {"tool": kw["tool_name"], "step": kw["step_id"],
                                    "input": kw["input"],
                                    "fingerprint": fingerprint(kw["tool_name"], kw["input"]),
                                    "read_only": host.agent.tools.get(kw["tool_name"]).isReadOnly})
            def on_tool_after_call(self, **kw):
                emit("tool_end", {"tool": kw["tool_name"], "step": kw["step_id"],
                                  "input": kw["input"], "result": kw["result"],
                                  "status": kw["status"], "duration_ms": kw["duration_ms"],
                                  "fingerprint": fingerprint(kw["tool_name"], kw["input"]),
                                  "read_only": host.agent.tools.get(kw["tool_name"]).isReadOnly})

        def fingerprint(name, args):
            return hashlib.sha256((name + json.dumps(args, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()

        host = HeadlessApp()
        host.state.session_id = request["session_id"]
        host.state.save = lambda: None

        def engage(model_id=""):
            if host.agent is not None:
                return
            host.agent = build_agent(host)
            bind_llm(host.agent.llm, require_item(model_id) if model_id else require_active(),
                     timeout=request["limits"]["call_timeout"])
            original_chat = host.agent.llm.chat
            count = 0
            def chat(*args, **kwargs):
                nonlocal count
                import time
                started = time.monotonic()
                count += 1
                emit("llm_start", {"turn": count, "model": host.agent.llm.model})
                emit("status", "正在分析问题。" if count == 1 else "正在核对已有资料并整理回答。")
                try:
                    return original_chat(*args, **kwargs)
                finally:
                    emit("llm_end", {"turn": count, "duration_ms": int((time.monotonic()-started)*1000)})
            host.agent.llm.chat = chat
            host.agent.terminator = MaxSteps(host.state.max_steps) | Timeout(request["limits"]["task_timeout"] * 1000)
            host.agent.runtime.terminator = host.agent.terminator
            host.agent.plugin_manager.register(Progress())
            original = host.agent.tools.execute
            def execute(name, arguments):
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
                if fingerprint(name, args) in request.get("completed_writes", []):
                    return "该写入在前次任务中已经尝试过，本次不再重复执行；请查询确认原结果，再下达新的操作。"
                tool = host.agent.tools.get(name)
                if tool.isReadOnly:
                    output = original(name, arguments)
                    archive.record(name, output)
                    return output
                with WRITES:
                    if name == "save_video_summary":
                        archive.manual_save = True
                    return original(name, arguments)
            host.agent.tools.execute = execute

        host.engage_model = engage
        try:
            answer = host.run_chat(request["text"], request.get("chips"), lambda x: emit("token", x),
                                   lambda x: emit("status", x), parse_sampling(request.get("sampling")),
                                   lambda x: emit("think", x), request.get("model_id", ""), bool(request.get("knowledge")),
                                   lambda x: emit("knowledge", x), resume_note=request.get("resume_note", ""))
            result = getattr(host.agent, "last_result", None)
            if getattr(result, "status", "") == "aborted" or answer.startswith("Agent 终止："):
                emit("failure", "Agent 达到步骤或时间上限。已保留进度，请查看任务时间线。")
            else:
                emit("result", answer)
                archive.finish(answer)
        except Exception as exc:
            # 不通过网络泄露地址、凭证或堆栈，监控台给出可操作的类型。
            name = type(exc).__name__
            emit("failure", {"type": name, "message": "任务处理失败（" + name + "），请检查模型、工具权限或网络。"})


if __name__ == "__main__":
    main()

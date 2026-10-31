"""独立执行单个任务；只通过本地 stdin/stdout 和管理进程交换事件。"""
import contextlib
import json
import hashlib
import sys
import time


def main():
    if '--standby' in sys.argv:
        # Imports/transport initialization only. No model call or user task runs
        # until stdin supplies one request; this process exits after that request.
        from . import collaboration_worker, project_workspace, local_sources, web_search, web_sources
        # Atlas now defers SDK loading. Warm the adapter too; otherwise the
        # first user request still pays the OpenAI/Pydantic import cost.
        from .collaboration_llm import CollaborationLLM
        from .model_network import http_client
        with http_client(5):pass
        sys.stdout.write(json.dumps(dict(kind='standby_ready'))+'\n');sys.stdout.flush()
    request = json.loads(sys.stdin.readline())
    transport = sys.stdout

    def emit(kind, data):
        transport.write(json.dumps({"kind": kind, "data": data}, ensure_ascii=False) + "\n")
        transport.flush()

    # Atlas renderer、第三方库的调试输出不能混入事件协议。
    from . import task_budgets
    with contextlib.redirect_stdout(sys.stderr),task_budgets.installed(request['task_id'],emit):
        emit('worker_phase', {'stage': 'request_received'})
        from .agent_debug import install
        install({"task_id": request.get("task_id", ""), "session": request["session_id"], "channel": request.get("channel", "desktop"),
                 'model_id':request.get('model_id',''),'collab_run':request.get('collab_run',''),
                 'conversation':request.get('conversation',''),'phase':request.get('kind','')})
        if request.get('workflow')=='budget_probe':
            from .model_generation import verify
            try:verify(request,emit)
            except Exception as exc:emit('failure',dict(type=type(exc).__name__,message=getattr(exc,'public_message','输出上限验证未通过（'+type(exc).__name__+'），未保存新绑定；同路由同参数的旧验证将停用，请查看调用记录。')))
            return
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
        if request.get("workflow") == "automation_graph":
            from .workflow_worker import execute
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
        from .conversation import ConversationService
        from .assistant import build_agent
        from .model_catalog import bind_llm, require_active, require_item
        from .resource_lock import WRITES
        from .sampling import parse_sampling
        from .video_archive import VideoArchive
        archive = VideoArchive(request, emit)
        actual_receipts=[]
        actual_sources=[]

        class Progress(BasePlugin):
            name = "task_progress"
            def on_tool_before_call(self, **kw):
                emit("tool_start", {"tool": kw["tool_name"], "step": kw["step_id"],
                                    "input": kw["input"],
                                    "fingerprint": fingerprint(kw["tool_name"], kw["input"]),
                                    "read_only": host.agent.tools.get(kw["tool_name"]).isReadOnly})
            def on_tool_after_call(self, **kw):
                raw=kw['result'].get('output',kw['result'])if isinstance(kw['result'],dict)else kw['result']
                if isinstance(raw,str):
                    try:raw=json.loads(raw)
                    except (ValueError,TypeError):pass
                actual_receipts.append(dict(tool=kw['tool_name'],call_id=str(kw['step_id']),arguments=kw['input'],result=raw,read_only=host.agent.tools.get(kw['tool_name']).isReadOnly,status=kw['status']))
                if kw['tool_name']in{'web_fetch','web_search','github_repository'}and isinstance(raw,dict):
                    from .web_sources import evidence_sources
                    actual_sources.extend(evidence_sources([raw]))
                emit("tool_end", {"tool": kw["tool_name"], "step": kw["step_id"],
                                  "input": kw["input"], "result": kw["result"],
                                  "status": kw["status"], "duration_ms": kw["duration_ms"],
                                  "fingerprint": fingerprint(kw["tool_name"], kw["input"]),
                                  "read_only": host.agent.tools.get(kw["tool_name"]).isReadOnly})

        def fingerprint(name, args):
            return hashlib.sha256((name + json.dumps(args, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()

        host = ConversationService.for_session(request["session_id"])

        def engage(model_id=""):
            if host.agent is not None:
                return
            started = time.monotonic()
            emit('worker_phase', {'stage': 'building_agent'})
            host.agent = build_agent(host)
            bind_llm(host.agent.llm, require_item(model_id) if model_id else require_active(),
                     timeout=request["limits"]["call_timeout"])
            host.agent.llm.on_context=lambda report:emit('context_admission',report)
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
                from .feedback import pending_operations
                pending=[op for op in pending_operations(actual_receipts)if op['write_attempted']]
                if not tool.isReadOnly and pending:
                    emit('unknown_operation_blocked',dict(tool=name,operation_ids=[op['operation_id']for op in pending]))
                    return '原写入/执行结果仍未知，本次操作未执行。按原编号只读核对，不重复创建或发送。'
                try:task_budgets.current().tool(tool.isReadOnly,name)
                except task_budgets.BudgetExceeded as exc:
                    return dict(ok=False,error=exc.public_message,budget_denied=True,operation=dict(state='not_started'))
                # Calendar writes own a cross-process board lease and short cache lock.
                # Do not retain the global write lock across their remote IO.
                if tool.isReadOnly or name == 'create_calendar_event':
                    output = original(name, arguments)
                    task_budgets.record_progress(name,args,output)
                    archive.record(name, output)
                    return output
                with WRITES:
                    if name == "save_video_summary":
                        archive.manual_save = True
                    output=original(name, arguments)
                    task_budgets.record_progress(name,args,output)
                    return output
            host.agent.tools.execute = execute
            emit('worker_phase', {'stage': 'agent_ready',
                                 'duration_ms': int((time.monotonic() - started) * 1000)})

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
                from .delivery_feedback import assess
                from .feedback import report
                checked_request={**request,'collab_run':request.get('task_id',request['session_id'])}
                answer,checks,audit=assess(checked_request,answer,actual_sources,None,actual_receipts)
                if audit is not None:emit('citation_check',audit)
                outcome=report(checks,receipts=actual_receipts);emit('feedback_check',outcome)
                if not outcome['ok']:
                    emit('answer_reset',True);emit('token',answer)
                    emit('failure',dict(type='DeliveryCheckFailed',message='交付核验未完成，已保留回答和实际回执，请查看反馈检查。'))
                    return
                emit("result", answer)
                archive.finish(answer)
        except Exception as exc:
            # 不通过网络泄露地址、凭证或堆栈，监控台给出可操作的类型。
            name = type(exc).__name__
            emit("failure", {"type": name, "message": getattr(exc,'public_message',"任务处理失败（" + name + "），请检查模型、工具权限或网络。")})


if __name__ == "__main__":
    main()

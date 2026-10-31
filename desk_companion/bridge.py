"""pywebview JS 桥：气泡和看板共用。"""
from __future__ import annotations

import os

from . import workbench_queries


class Bridge:
    def __init__(self, host, *, session_changed=None) -> None:
        self._host = host
        self._session_changed = session_changed

    def _change_session(self, method, **args):
        result = getattr(self._host.chat_sessions, method)(**args)
        if result.get('ok') and self._session_changed is not None:
            try:
                self._session_changed(method)
            except Exception as exc:
                return {**result, 'ok': False, 'change_state': 'committed',
                        'error': f'会话操作已完成，但界面刷新失败：{exc}。请重新打开会话。'}
        return result

    def check_automation_execution(self, job_id):
        return self._host.automation.check_agent_execution(job_id)

    def automation_workflow(self, operation, payload=None):
        return self._host.automation.workflows.operation(operation, payload)

    def tebb_operation(self, run_id, tool, args):
        from .tebb_operations import execute
        return execute(self._host, run_id, tool, args)

    def project_operation(self, run_id, task_id, operation, args):
        from .project_operations import execute
        return execute(self._host,run_id,task_id,operation,args)

    def project_environments_ui(self, operation, payload=None):
        """Desktop-token API. Model tools use the separate run-authorized dispatcher."""
        service=self._host.project_environments;payload=payload or {}
        if operation=='probe':return service.client.probe()
        if operation=='list':return dict(ok=True,environments=service.store.list())
        ident=payload.get('environment','');environment=service.store.get(ident)
        if operation=='status':return service.status(ident,environment['scope'])
        if operation=='stop':return service.stop(ident,environment['scope'])
        if operation=='reconcile':return service.reconcile(ident,environment['scope'],verify=payload.get('verify',False))
        if operation=='integrity':return service.integrity(ident,environment['scope'],payload.get('request_id',''))
        if operation=='archive_create':return service.archive_unknown_create(ident,environment['scope'],
            payload.get('expected_operation',''),payload.get('request_id',''),confirm=payload.get('confirm',False))
        if operation=='log':return service.log(ident,environment['scope'],payload.get('operation',''),payload.get('offset',0))
        raise ValueError('不支持的项目环境界面操作。')

    def load_onboarding(self) -> dict:
        from .onboarding import status
        return status()

    def load_pet_preferences(self) -> dict:
        from .pet_preferences import load
        return load()

    def save_pet_preferences(self, skin: str, size=None) -> dict:
        from .pet_preferences import save
        return save(skin, size)

    def load_collaboration(self, since="") -> dict:
        return self._host.collaboration.snapshot(since=since)

    def load_context_debug(self,conversation,call_id='',before=None,draft=''):
        from .context_inspector import inspect
        return inspect(self._host.collaboration,conversation,call_id,before,draft)

    def save_context_capacity(self,model_id,window=None,output_reserve=0,tokenizer_dir=None):
        from .model_capacity import save
        return save(model_id,window,output_reserve,tokenizer_dir)

    def get_report_chart(self,chart_id):
        from .report_charts import public
        return public(chart_id)

    def load_work_board(self, request_id="") -> dict:
        return self._host.work_board.load(request_id)

    def change_work_item(self, action: dict) -> dict:
        if isinstance(action,dict) and action.get('type')=='execute':
            from .work_execution import WorkExecutionService
            return WorkExecutionService(self._host.work_board,self._host.collaboration).execute(action)
        return self._host.work_board.change(action)

    def load_schedule(self, day, item_id='', refresh=False, request_id='') -> dict:
        return self._host.scheduling.load(day, item_id, refresh, request_id)

    def change_schedule(self, action: dict) -> dict:
        return self._host.scheduling.change(action)

    def load_schedule_week(self, day, refresh=False, request_id='') -> dict:
        return self._host.scheduling.load_week(day, refresh, request_id)

    def load_schedule_month(self, day, refresh=False, request_id='') -> dict:
        return self._host.scheduling.load_month(day, refresh, request_id)

    def inspect_schedule_event(self, calendar_id, event_id) -> dict:
        return self._host.scheduling.inspect_event(calendar_id, event_id)

    def load_local_calendar(self, request_id='') -> dict:
        return self._host.scheduling.local.load(request_id)

    def change_local_calendar(self, action: dict) -> dict:
        return self._host.scheduling.local.change(action)

    def calendar_sync_step(self, batch_id, local_id) -> dict:
        return self._host.scheduling.local.batch_step(batch_id, local_id)

    def collaboration_action(self, action: dict) -> dict:
        return self._host.collaboration.action(action)

    def save_collaboration_note(self, run_id: str) -> dict:
        from .collaboration import transaction
        from .notebook import save_agent_artifact
        collab = self._host.collaboration
        with collab.lock, transaction(collab.path) as (state, _):
            run = next((r for r in state['runs'] if r['id'] == run_id), None)
            if not run or run['state'] != 'succeeded' or not run.get('answer') or run['kind'] not in {'task','chat','route'}:
                raise ValueError('只能收藏已成功完成的回答')
            if run['kind']=='route' and any(r.get('parent')==run_id for r in state['runs']):
                raise ValueError('这是任务登记消息，请收藏实际成果')
            conversation = next(c for c in state['conversations'] if c['id']==run['conversation'])
            agent = next(a for a in state['agents'] if a['id']==run['agent'])
            payload = (conversation['id'],run_id,conversation['title'],run['input'],run['answer'],agent['name'])
            project = next((p for p in state['projects'] if p['id']==conversation.get('project_id')),None)
            if project and len(project.get('sources',[]))>=30 and not any(s['id']==run_id for s in project['sources']): raise ValueError('项目最多保存30份资料，请先移除不再使用的资料')
            result = save_agent_artifact(*payload)
            if project:
                sources=project.setdefault('sources',[])
                if not any(s['id']==run_id for s in sources):
                    sources.append(dict(id=run_id,title=run['title'],text=run['answer'][:12000],truncated=len(run['answer'])>12000))
        return result

    def prepare_notebook_source(self, session_id: str, note_id: str) -> dict:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from .notebook import note_document
        from .local_sources import stage
        document = note_document(session_id, note_id)
        with TemporaryDirectory(prefix='tebb-note-') as folder:
            path = Path(folder) / '笔记资料.md'
            path.write_text(document['markdown'], encoding='utf-8')
            return stage([str(path)], 'file')

    def project_workspace(self, operation="load", **args):
        from . import project_workspace as project
        if operation == "pick":
            from .local_sources import pick
            return pick("folder", args.get("request_id", ""), stage_selection=False)
        if operation == 'open':
            state=self._host.collaboration.action(dict(type='open_folder_project',primary=args.get('primary'),bind_id=args.get('bind_id',''),request_id=args['request_id']))['state']
            return dict(ok=True,workspace=state['workspace'],state=state)
        if operation == 'migration_preview':
            from .chat_projects import preview
            from .collaboration import transaction
            with self._host.collaboration.lock, transaction(self._host.collaboration.path) as (state,_): return preview(state)
        if operation == "load": return {"ok": True, "workspace": project.load(args.get('project_id'))}
        if operation == "save":
            from .chat_projects import matching
            from .collaboration import transaction
            collab=self._host.collaboration
            with collab.lock, transaction(collab.path) as (state,_):
                pid=args.get('project_id') or next((c.get('project_id','') for c in state['conversations'] if c['id']==state['selected']),'')
                if not pid or not any(p['id']==pid for p in state['projects']): raise ValueError('请先打开文件夹创建项目')
                main=str(project.clean_root(args.get('primary')))
                existing=matching(state,main)
                if existing and existing['id']!=pid: raise ValueError('该目录已属于另一个项目，请从侧栏打开已有项目')
                args['project_id']=pid
                return project.save(**args)
        if operation in {'tree','read','write'}:
            cfg=project.load(args.pop('project_id',None))
            return getattr(project,operation)(config=cfg,**args)
        raise ValueError("未知工作区操作")

    def terminal_status(self, refresh=False, distro=None):
        from .terminal import manager
        return manager().status(refresh, distro)

    def terminal_start(self, command, session="", cwd="/workspace", timeout=60, owner="user", task_id="", request_id="", workspace_revision=""):
        from .terminal import manager
        if session.startswith("project:"):
            from . import project_workspace as project
            cfg = project.load()
            if session != "project:"+cfg["primary"] or workspace_revision != cfg["revision"]: raise ValueError("工作目录已切换，请重新打开终端")
            with project.lease(cfg["primary"]):
                return manager().start(command, session, cwd, timeout, owner, task_id, request_id, project=cfg)
        return manager().start(command, session, cwd, timeout, owner, task_id, request_id)

    def terminal_list(self, session=None):
        from .terminal import manager
        return manager().list(session)

    def terminal_read(self, job_id, offset=0):
        from .terminal import manager
        return manager().read(job_id, offset)

    def terminal_input(self, job_id, text=None, cols=None, rows=None):
        from .terminal import manager
        return manager().input(job_id, text, cols, rows)

    def terminal_cancel(self, job_id):
        from .terminal import manager
        return manager().cancel(job_id)

    def terminal_files(self, session=""):
        from .terminal import manager
        return manager().files(session)

    def terminal_import_files(self, source_ids, session=""):
        from .terminal import manager
        return manager().import_files(session, source_ids)

    def terminal_preview_file(self, name, session=""):
        from .terminal import manager
        return manager().preview_file(session, name)

    def check_updates(self) -> dict:
        from .release_info import check_updates
        return check_updates()

    def complete_onboarding(self) -> dict:
        from .onboarding import complete
        return complete()

    def save_onboarding_progress(self, step) -> dict:
        from .onboarding import save_progress
        return save_progress(step)

    def report_pet_status(self, ready: bool, message: str = "") -> dict:
        from .logutil import log
        if type(ready) is not bool or type(message) is not str:
            return {"ok": False, "error": "形象状态参数不正确。"}
        log(f"pet_load {'ready' if ready else 'error'}: {message[:2000]}")
        return {"ok": True}

    def load_feishu_agent(self) -> dict:
        return self._host.feishu_agent.status()

    def list_agent_tasks(self, channel: str = "", session: str = "") -> dict:
        return self._host.tasks.list(channel, session)

    def list_debug_calls(self, channel: str = "", task_id: str = "", before=None) -> dict:
        from .agent_debug import listing
        return listing(channel, task_id, before)

    def get_debug_call(self, call_id: str) -> dict:
        from .agent_debug import detail
        return detail(call_id)

    def set_debug_recording(self, enabled: bool) -> dict:
        from .agent_debug import configure
        return configure(enabled)

    def explain_debug_call(self, call_id: str, question: str = "") -> dict:
        from .agent_debug import detail
        detail(call_id)
        if not isinstance(question, str) or len(question) > 4000:
            raise ValueError("请填写不超过4000字的问题。")
        task = self._host.tasks.submit(question.strip() or "请解读这次 Agent 调用，说明输入组成、行动与返回，以及需要注意的异常。",
            "debug-" + call_id, "debug", workflow="debug_explain", debug_call_id=call_id, auto_video_archive=False)
        return {"ok": True, "task_id": task}

    def get_agent_task(self, task_id: str, focus: bool = True) -> dict:
        from .memory import list_chat
        if type(focus) is not bool:
            raise ValueError("任务焦点须为开关。")
        task = self._host.tasks.get(task_id)
        if focus:
            self._host._focused_task = task_id
        return {"ok": True, "task": task, "history": list_chat(task["session"])}

    def verify_model_output_limit(self, model_id: str, parameter: str='max_completion_tokens',request_id: str='') -> dict:
        from .model_catalog import require_item
        from .model_generation import PARAMETERS
        require_item(model_id)
        if parameter not in PARAMETERS:raise ValueError('输出参数无效。')
        if not isinstance(request_id,str)or not 1<=len(request_id)<=80:raise ValueError('请提供稳定的验证请求编号，断连后核对同一编号。')
        task=self._host.tasks.submit('验证模型输出上限（两次有限探针）：'+parameter,'model-budget-'+model_id,'debug',
            submission_key='model-output:'+model_id+':'+request_id,
            workflow='budget_probe',model_id=model_id,parameter=parameter,task_limits=dict(task_timeout=90,call_timeout=30))
        return dict(ok=True,task_id=task)

    def load_model_output_limit(self,model_id: str) -> dict:
        from .model_generation import public
        return public(model_id)

    def cancel_agent_task(self, task_id: str) -> dict:
        return self._host.tasks.cancel(task_id)

    def load_video_settings(self) -> dict:
        from .video import load_settings
        return {"ok": True, "settings": load_settings()}

    def load_wiki_connection(self) -> dict:
        from .wiki_connection import load_settings
        return {"ok": True, "settings": load_settings()}

    def save_wiki_connection(self, payload: dict) -> dict:
        from .wiki_connection import save_settings
        return save_settings(payload)

    def load_video_login(self) -> dict:
        from .video_login import status
        return status()

    def save_video_login(self, platform: str, cookies: list) -> dict:
        from .video_login import save
        return save(platform, cookies)

    def clear_video_login(self, platform: str) -> dict:
        from .video_login import clear
        return clear(platform)

    def list_video_tasks(self) -> dict:
        from .video_controller import snapshot
        return snapshot(self._host)

    def start_video_task(self, url: str) -> dict:
        from .video_controller import start
        return start(self._host, url)

    def continue_video_task(self, task_id: str, text: str) -> dict:
        from .video_controller import continue_task
        return continue_task(self._host, task_id, text)

    def save_video_settings(self, proxy: str = "", cookie_file: str = "") -> dict:
        from .video import save_settings
        return save_settings(proxy, cookie_file)

    def list_task_videos(self, task_id: str) -> dict:
        import hashlib
        from .video import sources, get_source
        task = self._host.tasks.get(task_id, False)
        items = sources(task["session"])
        key = hashlib.sha256(task["answer"].strip().encode()).hexdigest()
        for item in items:
            saved = get_source(task["session"], item["source_id"]).get("exports", {}).get(key, {})
            item.update(export_state=saved.get("state", ""), document_url=saved.get("url", ""),
                        presentation_warnings=saved.get("presentation_warnings", []))
        return {"ok": True, "items": items}

    def export_task_video(self, task_id: str, source_id: str, confirmed_absent: bool = False) -> dict:
        from .video import export_summary
        task = self._host.tasks.get(task_id, False)
        if task["state"] != "succeeded" or not task["answer"].strip():
            raise ValueError("请等待总结完成再保存到飞书。")
        return export_summary(task["session"], source_id, task["answer"], confirmed_absent=confirmed_absent)

    def continue_agent_task(self, task_id: str, text: str, send_back: bool = False) -> dict:
        if type(send_back) is not bool:
            raise ValueError("发送回飞书须为开关。")
        if send_back:
            found = self._host.feishu_agent.continue_task(task_id, text)
        else:
            found = self._host.tasks.resume(task_id, text, send_back=False)
        self._host._focused_task = found
        return {"ok": True, "task_id": found}

    def save_agent_task_settings(self, parallel: int, call_timeout: int, task_timeout: int, pet_progress: bool) -> dict:
        return self._host.tasks.configure(parallel, call_timeout, task_timeout, pet_progress)

    def load_task_progress(self) -> dict:
        tasks = self._host.tasks
        if not tasks.settings["pet_progress"]:
            return {"ok": True, "enabled": False}
        focus = getattr(self._host, "_focused_task", "")
        candidates = tasks.list()["items"]
        selected = next((t for t in candidates if t["id"] == focus and t["state"] in {"running", "queued", "cancelling"}), None)
        selected = selected or next((t for t in candidates if t["state"] in {"running","queued","cancelling"}), None)
        if selected: self._host._pet_last_task = selected["id"]
        if not selected:
            retained = getattr(self._host, "_pet_last_task", "") or focus
            selected = next((t for t in candidates if t["id"] == retained), None)
        if not selected:
            return {"ok": True, "enabled": True, "task": None}
        task = tasks.get(selected["id"])
        events = task["events"]
        status = next((e for e in reversed(events) if e["kind"] == "status"), None)
        visible = []
        for e in events:
            if e["kind"] in {"status","tool_start","tool_end","error"}:
                data = e["data"]
                visible.append(str(data) if isinstance(data,str) else f"{data.get('tool','')} · {data.get('status','')}".strip(' ·'))
        return {"ok": True, "enabled": True, "task": {"id": task["id"], "status": status["data"] if status else "",
                "state": task["state"], "text": task["text"][:200], "answer": task["answer"], "events": visible[-120:],
                "created": task["created"], "error": task["error"],
                "seq": events[-1]["seq"] if events else 0}}

    def start_feishu_agent(self) -> dict:
        return self._host.feishu_agent.enable()

    def stop_feishu_agent(self) -> dict:
        return self._host.feishu_agent.stop()

    def list_feishu_agent_profiles(self) -> dict:
        return self._host.feishu_agent.profiles()

    def save_feishu_agent_settings(self, **settings) -> dict:
        return self._host.feishu_agent.save_settings(**settings)

    def update_feishu_agent_credentials(self, profile: str, app_secret: str) -> dict:
        return self._host.feishu_agent.update_credentials(profile, app_secret)

    def check_feishu_agent_connection(self, profile: str = "") -> dict:
        return self._host.feishu_agent.check_connection(profile)

    def send_chat(self, text: str) -> None:
        self._host.ui(lambda: self._host.send_chat(text))

    def clear_chat(self) -> dict:
        return self._change_session('clear_chat')

    def send_board_chat(self, text: str, chips: dict | None = None) -> dict:
        return self._host.send_board_chat(text, chips)

    def new_chat_session(self) -> dict:
        return self._change_session('new_chat_session')

    def switch_chat_session(self, session_id: str) -> dict:
        return self._change_session('switch_chat_session', session_id=session_id)

    def load_composer_options(self) -> dict:
        return workbench_queries.load_composer_options()

    def list_feishu_docs(self) -> dict:
        return workbench_queries.list_feishu_docs()

    def load_knowledge(self) -> dict:
        return self._host.knowledge_service.load_knowledge()

    def save_knowledge(self, payload: dict) -> dict:
        return self._host.knowledge_service.save_knowledge(payload)

    def download_knowledge(self, repo: str) -> dict:
        return self._host.knowledge_service.download_knowledge(repo)

    def delete_model(self, repo: str) -> dict:
        return self._host.knowledge_service.delete_model(repo)

    def add_knowledge(self, doc_id: str, label: str, source: dict | None = None) -> dict:
        return self._host.knowledge_service.add_knowledge(doc_id, label, source)

    def material_operation(self, payload: dict) -> dict:
        return self._host.knowledge_service.material_operation(payload)

    def pick_local_sources(self, kind: str = "file", request_id: str = "") -> dict:
        from .local_sources import pick
        return pick(kind, request_id)

    def stage_local_sources(self, paths: list, kind: str = "file", request_id: str = "") -> dict:
        from .local_sources import stage
        return stage(paths, kind, request_id)

    def cancel_local_source_request(self, request_id: str) -> dict:
        from .local_sources import cancel
        return cancel(request_id)

    def read_local_source(self, source_id: str, offset: int = 0, limit: int = 12000) -> dict:
        from .local_sources import read_page
        return {"ok": True, "source": read_page(source_id, offset, limit)}

    def delete_knowledge(self, doc_id: str) -> dict:
        return self._host.knowledge_service.delete_knowledge(doc_id)

    def rebuild_knowledge(self) -> dict:
        return self._host.knowledge_service.rebuild_knowledge()

    def ask_knowledge(self, text: str) -> dict:
        return self._host.knowledge_service.ask_knowledge(text)

    def load_notebook(self) -> dict:
        return self._host.notebooks.load_notebook()

    def new_notebook(self) -> dict:
        return self._host.notebooks.new_notebook()

    def save_notebook_note(self, session_id: str, question: str, answer: str, cites: list) -> dict:
        return self._host.notebooks.save_notebook_note(session_id, question, answer, cites)

    def delete_notebook_note(self, session_id: str, note_id: str) -> dict:
        return self._host.notebooks.delete_notebook_note(session_id, note_id)

    def export_notebook_markdown(self, session_id: str, note_id: str) -> dict:
        return self._host.notebooks.export_notebook_markdown(session_id, note_id)

    def export_notebook_feishu(self, session_id: str, note_id: str) -> dict:
        return self._host.notebooks.export_notebook_feishu(session_id, note_id)

    def summarize_notebook(self, session_id: str, note_ids: list, sampling: dict) -> dict:
        return self._host.notebooks.summarize_notebook(session_id, note_ids, sampling)

    def open_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        return self._host.notebooks.open_notebook_file(session_id, note_id, name)

    def reveal_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        return self._host.notebooks.reveal_notebook_file(session_id, note_id, name)

    def delete_notebook_file(self, session_id: str, note_id: str, name: str) -> dict:
        return self._host.notebooks.delete_notebook_file(session_id, note_id, name)

    def list_mcp_tools(self) -> dict:
        return workbench_queries.list_mcp_tools()

    def write_week_review_doc(self, payload: dict) -> dict:
        return self._host.weekly_review.write_week_review_doc(payload)

    def list_automation_jobs(self, seen: bool = False) -> dict:
        return self._host.automation.list_automation_jobs(bool(seen))

    def save_automation_job(self, payload: dict) -> dict:
        return self._host.automation.save_automation_job(payload)

    def delete_automation_job(self, job_id: str) -> dict:
        return self._host.automation.delete_automation_job(job_id)

    def run_automation_job(self, job_id: str) -> dict:
        return self._host.automation.run_automation_job(job_id)

    def load_news(self) -> dict:
        from .news_controller import snapshot
        return snapshot()

    def save_news_settings(self, payload: dict) -> dict:
        from .news import save_settings
        return save_settings(payload)

    def check_news_targets(self) -> dict:
        from .news_controller import check
        return check()

    def run_news(self, publish: bool = False, run_id: str = "", send_group: bool = True) -> dict:
        from .news_controller import start
        return start(self._host.tasks, publish=publish, run_id=run_id, send_group=send_group)

    def close_bubble(self) -> None:
        self._host.ui(self._host.hide_bubble)

    def ack_notice(self) -> None:
        self._host.ui(self._host.hide_bubble)

    def fit_card(self, width: int, height: int) -> None:
        self._host.ui(lambda: self._host.fit_card(int(width), int(height)))

    def load_board(self, refresh: bool = False) -> dict:
        return self._host.board_service.load_board(bool(refresh))

    def delete_agenda(self, event_id: str) -> dict:
        return self._host.board_service.delete_agenda(event_id)

    def delete_task(self, guid: str) -> dict:
        return self._host.board_service.delete_task(guid)

    def create_agenda(self, summary: str, start: str, end: str) -> dict:
        return self._host.board_service.create_agenda(summary, start, end)

    def load_log_errors(self) -> dict:
        return workbench_queries.load_log_errors()

    def load_skills(self) -> dict:
        return workbench_queries.load_skills()

    def ask_today(self) -> None:
        self._host.ui(self._host.ask_today)

    def ask_logs(self) -> None:
        self._host.ui(self._host.ask_logs)

    def generate_week_review(self) -> dict:
        return self._host.weekly_review.generate_week_review()

    def write_today_summary_doc(self) -> dict:
        return self._host.weekly_review.write_today_summary_doc()

    def close_board(self) -> None:
        self._host.ui(self._host.hide_board)

    def open_url(self, url: str) -> None:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise RuntimeError("只打开 https 链接。")
        os.startfile(url)

    def load_chat_log(self) -> dict:
        return self._host.chat_sessions.load_chat_log()

    def load_memory(self) -> dict:
        return self._host.chat_sessions.load_memory()

    def compress_context(self) -> dict:
        return self._host.context_compression.compress_context()

    def add_fact(self, text: str) -> dict:
        return self._host.memory_editing.add_fact(text)

    def update_fact(self, fact_id: str, text: str) -> dict:
        return self._host.memory_editing.update_fact(fact_id, text)

    def delete_fact(self, fact_id: str) -> dict:
        return self._host.memory_editing.delete_fact(fact_id)

    def delete_memory_turn(self, role: str, text: str) -> dict:
        return self._host.memory_editing.delete_memory_turn(role, text)

    def load_persona(self) -> dict:
        return self._host.assistant_settings.load_persona()

    def save_persona(self, payload: dict) -> dict:
        return self._host.assistant_settings.save_persona(payload)

    def load_model(self) -> dict:
        return self._host.assistant_settings.load_model()

    def load_search_settings(self) -> dict:
        from .search_service import settings
        return dict(ok=True, **settings())

    def save_search_settings(self, payload: dict) -> dict:
        from .search_service import save_settings
        return save_settings(payload)

    def test_search(self, query: str = 'Python asyncio TaskGroup official documentation') -> dict:
        from .search_service import probe
        return probe(query)

    def load_model_network(self) -> dict:
        from .model_network import public
        return dict(ok=True, **public())

    def save_model_network(self, payload: dict) -> dict:
        from .model_network import save
        if self._host.conversation.running:
            return dict(ok=False, error='助手正在执行，完成后再修改模型网络配置。')
        return save(payload)

    def load_models(self) -> dict:
        return self._host.assistant_settings.load_models()

    def save_model_entry(self, payload: dict) -> dict:
        return self._host.assistant_settings.save_model_entry(payload)

    def delete_model_entry(self, model_id: str) -> dict:
        return self._host.assistant_settings.delete_model_entry(model_id)

    def use_model(self, model_id: str) -> dict:
        return self._host.assistant_settings.use_model(model_id)

    def save_model(self, payload: dict) -> dict:
        return self._host.assistant_settings.save_model(payload)

    def test_model(self, payload: dict) -> dict:
        return self._host.assistant_settings.test_model(payload)

    def load_usage(self) -> dict:
        return workbench_queries.load_usage()

    def load_feishu(self) -> dict:
        from .feishu_auth import feishu_status

        try:
            return feishu_status()
        except Exception as exc:
            return {
                "ok": False,
                "installed": True,
                "logged_in": False,
                "error": str(exc),
            }

    def feishu_login(self) -> dict:
        from .feishu_auth import feishu_login_start

        return feishu_login_start()

    def feishu_logout(self) -> dict:
        from .feishu_auth import feishu_logout

        return feishu_logout()


    def load_github(self) -> dict:
        return workbench_queries.load_github()

"""Local, durable collaboration. Only user messages create bounded execution graphs."""
from __future__ import annotations

from contextlib import closing, contextmanager
import copy
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .paths import data_root
from .tasks import TaskManager, TERMINAL

ACTIVE = {"pending", "queued", "running", "waiting", "cancelling"}
METHODS = {"load_collaboration", "collaboration_action"}
COMPANIONS = {
    "tebb": ("tebb", "小缇", "spark", "温暖爽快，善于把模糊的想法变成可执行的计划。"),
    "research": ("研究员", "阿探", "fox", "好奇、敏锐，喜欢追问为什么；认真核实来源，坦率承认未知。"),
    "writer": ("写作者", "墨墨", "cat", "细腻、有条理，擅长把复杂内容讲清楚，有一点冷幽默。"),
}
TOOLS = {"联网搜索", "网页读取", "工作区读取", "文件编辑", "终端", "扩展只读工具"}
AVATARS = {"spark", "fox", "cat", "owl"}


def parse_route(text):
    decision = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip()))
    if not isinstance(decision, dict) or decision.get('mode') not in {'answer', 'task'}:
        raise ValueError('无效路由模式')
    content = decision.get('text') if decision['mode'] == 'answer' else decision.get('instructions')
    if not isinstance(content, str) or not content.strip() or len(content) > 24000:
        raise ValueError('无效路由内容')
    return decision


def execution_limits(agent_id):
    defaults = dict(call_timeout=90, task_timeout=600 if agent_id == 'coder' else 300)
    path = data_root() / 'memory' / 'task_settings.json'
    if not path.exists(): return defaults
    try:
        saved = json.loads(path.read_text('utf-8'))
        call, total = saved['call_timeout'], saved['task_timeout']
        if type(call) is not int or type(total) is not int or not 10 <= call <= 300 or not call <= total <= 1800:
            return defaults
        return dict(call_timeout=call, task_timeout=total)
    except (ValueError, KeyError, TypeError):
        return defaults


def migrate_companions(state):
    if state.get("companion_version", 0) >= 1:
        return
    for agent in state["agents"]:
        template = COMPANIONS.get(agent["id"])
        agent.setdefault("avatar", template[2] if template else "owl")
        agent.setdefault("personality", template[3] if template else "")
        if template and agent["name"] == template[0] and not any(a["id"] != agent["id"] and a["name"] == template[1] for a in state["agents"]):
            agent["name"] = template[1]
            for conversation in state["conversations"]:
                if conversation["kind"] == "dm" and conversation["coordinator"] == agent["id"]:
                    conversation["title"] = agent["name"]
    state["companion_version"] = 1


def workspace_path():
    return data_root() / "memory" / "collaboration" / "workspace.sqlite3"


def seed():
    agents = [
        dict(id="tebb", name="tebb", role="协调目标、拆分有限任务并明确验收条件", mark="t", tone="gold", model="", tools=[], memory=""),
        dict(id="research", name="研究员", role="核实一手资料，区分来源事实与推断，给出出处和限制", mark="R", tone="sage", model="", tools=["联网搜索","网页读取"], memory=""),
        dict(id="writer", name="写作者", role="根据收到的资料写成清晰报告，不编造来源或补齐未经核实的事实", mark="W", tone="lavender", model="", tools=[], memory=""),
    ]
    state = dict(version=1, next=20, selected="group-research", projects=[], agents=agents, messages=[], runs=[], requests=[], notice="", conversations=[
        dict(id="group-research", title="AI 研究室", kind="group", members=[a["id"] for a in agents], coordinator="tebb", goal="研究与写作协作，以实际来源和执行回执交付成果。"),
        *[dict(id="dm-"+a["id"], title=a["name"], kind="dm", members=[a["id"]], coordinator=a["id"], goal="独立会话与 Agent 配置") for a in agents],
    ])
    migrate_companions(state)
    return state


def import_legacy(state):
    if state.get("legacy_imported"): return
    from .memory import list_chat, list_sessions
    try:
        sessions, rows = list_sessions(), list_chat()
    except (ValueError, RuntimeError):
        state["notice"] = "旧历史格式异常，原文件已保留，请在开发者模式检查。"
        return
    for session in sessions:
        cid = "legacy-" + hashlib.sha256(session["id"].encode()).hexdigest()[:24]
        if any(c["id"] == cid for c in state["conversations"]): continue
        state["conversations"].append(dict(id=cid, title=session["title"], kind="dm", members=["tebb"], coordinator="tebb", goal="", legacy=True))
        for index, row in enumerate(r for r in rows if r.get("session_id") == session["id"]):
            state["messages"].append(dict(id=cid+"-"+str(index), conversation=cid, sender="user" if row["role"] == "user" else "tebb", text=row["text"], ts=row.get("ts", "")))
    state["legacy_imported"] = True


@contextmanager
def transaction(path):
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("BEGIN IMMEDIATE")
        raw = db.execute("SELECT data FROM workspace WHERE id=1").fetchone()[0]
        state = json.loads(raw)
        state.setdefault("projects", [])
        yield state, db
        value = json.dumps(state, ensure_ascii=False)
        if value != raw:
            state["revision"] = int(state.get("revision", 0)) + 1
            value = json.dumps(state, ensure_ascii=False)
            db.execute("UPDATE workspace SET data=? WHERE id=1", (value,))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def updates(path, run_id):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        state = json.loads(db.execute("SELECT data FROM workspace WHERE id=1").fetchone()[0])
    return next((r["updates"] for r in state["runs"] if r["id"] == run_id), [])


def selected_materials(path, run_id):
    with closing(sqlite3.connect(path, timeout=10)) as db:
        state = json.loads(db.execute('SELECT data FROM workspace WHERE id=1').fetchone()[0])
    return next((r.get('selection', {}) for r in state['runs'] if r['id']==run_id), {})


def dependencies(run):
    """Read both legacy single edges and bounded fan-in edges."""
    return run.get('dependencies') or ([run['dependency']] if run.get('dependency') else [])


def call_budget(agent_id, kind='task'):
    return 2 if kind in {'plan','route','chat','compress'} else 12 if agent_id == 'coder' else 8


def parse_plan(text, members):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    value = json.loads(text)
    rows = value.get("tasks") if isinstance(value, dict) else None
    if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
        raise ValueError("委派计划必须包含1至3项任务")
    used = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("agent_id") not in members or row["agent_id"] in used:
            raise ValueError("委派对象无效或重复")
        used.add(row["agent_id"])
        for field in ("title", "instructions"):
            if not isinstance(row.get(field), str) or not row[field].strip() or len(row[field]) > (160 if field == "title" else 3000):
                raise ValueError("委派说明无效")
        dep = row.get("depends_on")
        edges = dep if isinstance(dep,list) else [] if dep is None else [dep]
        if len(edges)>2 or len(set(map(str,edges)))!=len(edges) or any(type(d) is not int or not 0 <= d < index for d in edges):
            raise ValueError("依赖必须指向前序任务")
        if row.get('stage','collect') not in {'collect','deliver'}: raise ValueError('任务阶段无效')
        required=row.get('required_sources',[])
        if not isinstance(required,list) or any(s not in {'repository','web'} for s in required): raise ValueError('来源要求无效')
        topics=row.get('source_topics',[])
        if not isinstance(topics,list) or len(topics)>4 or any(not isinstance(t,str) or not t.strip() or len(t)>80 for t in topics):raise ValueError('资料对象最多4项，每项1至80字')
    return rows


def initialize_workspace(path=None):
    """Initialize durable workspace data without constructing task managers."""
    path = Path(path or workspace_path())
    from .resource_lock import ResourceLock
    with ResourceLock("collaboration-bootstrap"):
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS workspace(id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO workspace VALUES(1,?)", (json.dumps(seed(), ensure_ascii=False),))
            db.execute("CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
            db.commit()
            if json.loads(db.execute("SELECT data FROM workspace WHERE id=1").fetchone()[0]).get("companion_version", 0) < 1:
                backup = path.with_name("workspace.before-companions.sqlite3")
                if not backup.exists():
                    with closing(sqlite3.connect(backup)) as target:
                        db.backup(target)
        with closing(sqlite3.connect(path)) as source:
            if not json.loads(source.execute('SELECT data FROM workspace WHERE id=1').fetchone()[0]).get('legacy_imported'):
                backup = path.with_name('workspace.before-unified.sqlite3')
                if not backup.exists():
                    with closing(sqlite3.connect(backup)) as target: source.backup(target)
        with transaction(path) as (state, _):
            migrate_companions(state)
            import_legacy(state)


class Collaboration:
    def __init__(self, path=None, managers=None, start=True):
        self.path = Path(path or workspace_path())
        self.lock = threading.RLock()
        initialize_workspace(self.path)
        self.stop = threading.Event()
        self.managers = managers or {
            "work": TaskManager(path=self.path.parent / "work" / "tasks.sqlite3"),
            "chat": TaskManager(path=self.path.parent / "chat" / "tasks.sqlite3",prewarm=1),
        }
        # A separate single-slot chat queue remains available during two long jobs.
        self.managers["work"].settings["parallel"] = 2
        self.managers["chat"].settings["parallel"] = 1
        self.thread = threading.Thread(target=self._loop, daemon=True, name="collaboration")
        if start:
            self.thread.start()

    def snapshot(self, since=""):
        from .model_catalog import public_catalog
        with self.lock, transaction(self.path) as (state, _):
            result = copy.deepcopy(state)
        # Business items and calendar receipts have their own authenticated services.
        for key in ("work_items", "schedule_links", "schedule_operations", "schedule_days", "schedule_reads", "schedule_read_version"):
            result.pop(key, None)
        # Internal snapshots/keys/worker requests never cross the browser boundary.
        for run in result["runs"]:
            for key in ("input", "agent_config", "members", "context", "cursor", "task", "attempt", "parent_draft", "source_evidence", "selection", "compress_ids", "public_answer"):
                run.pop(key, None)
        catalog = public_catalog()
        result["models"] = [{"id": item["id"], "model": item["model"]} for item in catalog["items"]]
        result["active_model"] = catalog.get("active", "")
        from .skill_catalog import list_skills
        try: result["skills"] = [dict(id=s["id"], description=s["description"]) for s in list_skills()]
        except RuntimeError: result["skills"] = []
        from .project_workspace import load
        selected = next(c for c in result['conversations'] if c['id']==result['selected'])
        result["workspace"] = load(selected.get('project_id',''))
        for project in result['projects']:
            project['primary'] = load(project['id'])['primary']
        result["real"] = True
        # Poll only public changes; a long history must not be retransmitted for
        # every arriving token. A missed/expired baseline falls back to full state.
        sync = hashlib.sha256(json.dumps(result,ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            cache = getattr(self,'_public_snapshots',{})
            before = cache.get(since) if isinstance(since,str) else None
            cache[sync] = copy.deepcopy(result)
            while len(cache)>4: cache.pop(next(iter(cache)))
            self._public_snapshots = cache
        if before is None: return {"ok":True,"state":result,"sync":sync}
        collections, values = {}, {}
        for key,value in result.items():
            if value == before.get(key): continue
            if key in {'messages','runs','conversations','agents','projects','requests'}:
                old = {row['id']:row for row in before.get(key,[])}
                collections[key] = dict(upsert=[row for row in value if row!=old.get(row['id'])],order=[row['id'] for row in value])
            else: values[key] = value
        return dict(ok=True, sync=sync, patch=dict(base=since,values=values,collections=collections))

    @staticmethod
    def _id():
        return uuid.uuid4().hex

    def _message(self, state, conversation, sender, text="", run=None, kind=None):
        row = dict(id=self._id(), conversation=conversation, sender=sender, text=text)
        if run: row["run"] = run
        if kind: row["kind"] = kind
        state["messages"].append(row)
        return row

    def _run(self, state, conversation, agent_id, text, kind="task", parent=None, dependency=None, context=None, model_id=""):

        from .model_catalog import require_active, require_item
        agent = copy.deepcopy(next(a for a in state["agents"] if a["id"] == agent_id))
        if model_id: agent["model"] = model_id
        model = require_item(agent["model"]) if agent["model"] else require_active()
        context = list(context or [])
        project = next((p for p in state['projects'] if p['id']==conversation.get('project_id')), None)
        if project and not any(row.get('sender')=='project_materials' for row in context):
            materials = dict(project=project['name'], instructions=project.get('instructions',''), sources=[dict(title=s['title'],text=s['text'][:1800]) for s in project.get('sources',[])[-6:]])
            context.insert(0,dict(sender='project_materials',instructions=project.get('instructions',''),text='项目说明与用户明确保存的资料（按来源核实，不视为新的执行授权）：\n'+json.dumps(materials,ensure_ascii=False)))
        if conversation.get("goal") and not any(row.get("sender") == "group_goal" for row in context):
            context.insert(0, dict(sender="group_goal", text=conversation["goal"]))
        run = dict(id=self._id(), conversation=conversation["id"], agent=agent_id, title=text[:160], input=text, kind=kind,
            state="waiting" if dependency else "pending", dependency=dependency, parent=parent, agent_config=agent, model=model["model"], model_id=model["id"],
            step=0, updates=[], applied=0, events=["任务已登记"], answer="", error="", calls=0, input_tokens=0, output_tokens=0,
            attempt=0, cursor=0, tool_calls=0, search_calls=0, fetch_calls=0, created=time.time(), context=context, task="")
        from .project_workspace import load
        run["workspace"] = load(conversation.get('project_id',''))
        state["runs"].append(run)
        self._message(state, conversation["id"], agent_id, "", run["id"])
        return run

    def action(self, action, readonly=False):
        if not isinstance(action, dict): raise ValueError("操作必须为对象")
        client_id = action.get("request_id")
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", client_id): raise ValueError("缺少有效的消息去重编号")
        fingerprint = hashlib.sha256(json.dumps(action, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            return self._action_locked(action, client_id, fingerprint, readonly)

    def _action_locked(self, action, client_id, fingerprint, readonly):
        with transaction(self.path) as (state, db):
            receipt = db.execute("SELECT fingerprint FROM receipts WHERE id=?", (client_id,)).fetchone()
            if receipt:
                if receipt[0] != fingerprint: raise ValueError("此去重编号已被另一项操作使用")
            else:
                if readonly:
                    if action.get('type') in {'add_coder','compress','open_folder_project','migrate_projects'} or action.get('attachments') or action.get('skills'): raise ValueError('浏览器体验不开放本地工作区和技能执行，请使用桌面客户端')
                    if action.get('type') == 'agent' and set(action.get('agent',{}).get('tools',[])) - {'联网搜索','网页读取'}: raise ValueError('浏览器体验仅支持联网只读工具')
                    if action.get('type') == 'send':
                        c = next((c for c in state['conversations'] if c['id']==action.get('conversation')),None)
                        if c and any(set(a['tools'])-{'联网搜索','网页读取'} for a in state['agents'] if a['id'] in c['members']): raise ValueError('此会话包含本地工具权限，请在桌面客户端执行')
                state["notice"] = ""
                self._apply(state, action)
                db.execute("INSERT INTO receipts VALUES(?,?)", (client_id, fingerprint))
        return self.snapshot()

    def _apply(self, state, action):
        kind = action.get("type")
        conversation = next((c for c in state["conversations"] if c["id"] == action.get("conversation")), None)
        def project_id(default=""):
            value = action.get("project_id", default)
            if not isinstance(value, str) or (value and not any(p["id"] == value for p in state["projects"])):
                raise ValueError("项目不存在，请刷新后重新选择")
            return value

        if kind == 'open_folder_project':
            from .chat_projects import attach
            project = attach(state,action.get('primary'),self._id,action.get('bind_id',''))
            row = next((c for c in reversed(state['conversations']) if c.get('project_id')==project['id']),None)
            if not row:
                agent = state['agents'][0]
                row=dict(id=self._id(),title='新对话',kind='dm',members=[agent['id']],coordinator=agent['id'],goal='',project_id=project['id'])
                state['conversations'].append(row)
            state['selected']=row['id']
        elif kind == 'migrate_projects':
            from .chat_projects import migrate
            count=migrate(state,action.get('ids'),action.get('token'),self._id)
            state['notice']=f'已按工作目录归类 {count} 段对话'
        elif kind == 'project_details':
            project = next((p for p in state['projects'] if p['id']==action.get('id')),None)
            if not project: raise ValueError('项目不存在')
            instructions=action.get('instructions','')
            if not isinstance(instructions,str) or len(instructions)>4000: raise ValueError('项目说明最多4000字')
            project['instructions']=instructions
            if action.get('remove_source'):
                project['sources']=[s for s in project.get('sources',[]) if s['id']!=action['remove_source']]
        elif kind in {"create_project", "rename_project", "delete_project"}:
            project = None if kind == "create_project" else next((p for p in state["projects"] if p["id"] == action.get("id")), None)
            if kind != "create_project" and not project: raise ValueError("项目不存在")
            if kind == "delete_project":
                from .work_items import detach_project
                detach_project(state, project)
                state["projects"].remove(project)
                for row in state["conversations"]:
                    if row.get("project_id") == project["id"]: row.pop("project_id", None)
            else:
                name = action.get("name")
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40: raise ValueError("项目名称须为1至40字")
                name = name.strip()
                if any(p["id"] != (project or {}).get("id") and p["name"].casefold() == name.casefold() for p in state["projects"]):
                    raise ValueError("项目名称已存在")
                if 'instructions' in action and (not isinstance(action['instructions'],str) or len(action['instructions'])>4000): raise ValueError('项目说明最多4000字')
                if project:
                    project["name"] = name
                    if 'instructions' in action: project['instructions']=action['instructions']
                else: state["projects"].append(dict(id=self._id(), name=name))
        elif kind == "move_conversation":
            if "project_id" not in action: raise ValueError("请选择目标项目或未归类")
            row = next((c for c in state["conversations"] if c["id"] == action.get("id")), None)
            if not row: raise ValueError("会话不存在")
            pid=project_id()
            if pid != row.get('project_id','') and any(r['conversation']==row['id'] and r['state'] in ACTIVE for r in state['runs']):
                raise ValueError('请等待或停止当前会话任务后再切换项目')
            row["project_id"] = pid
        elif kind == "select":
            if not conversation: raise ValueError("会话不存在")
            state["selected"] = conversation["id"]
        elif kind == "open_dm":
            agent = next((a for a in state["agents"] if a["id"] == action.get("agent_id")), None)
            if not agent: raise ValueError("伙伴不存在")
            pid = project_id((conversation or {}).get("project_id", ""))
            dm = next((c for c in state["conversations"] if c["kind"] == "dm" and c["coordinator"] == agent["id"] and c.get("project_id", "") == pid), None)
            if not dm:
                dm = dict(id=self._id(), title=agent["name"], kind="dm", members=[agent["id"]], coordinator=agent["id"], goal="", project_id=pid)
                state["conversations"].append(dm)
            state["selected"] = dm["id"]
        elif kind in {"delete_conversation", "rename_conversation"}:
            conversation = next((c for c in state["conversations"] if c["id"] == action.get("id")), None)
            if not conversation: raise ValueError("会话不存在")
            if kind == "rename_conversation":
                title = action.get("title")
                if conversation["kind"] != "group" or not isinstance(title, str) or not title.strip() or len(title) > 40:
                    raise ValueError("请为协作群填写1至40字的名称；私聊名称跟随伙伴昵称")
                conversation["title"] = title.strip()
                return
            if any(r["conversation"] == conversation["id"] and r["state"] in ACTIVE for r in state["runs"]):
                raise ValueError("此会话仍有任务执行中，请先停止任务并等待结束后删除")
            for key in ("messages", "runs", "requests"):
                state[key] = [row for row in state[key] if row["conversation"] != conversation["id"]]
            state["conversations"].remove(conversation)
            if not state["conversations"]:
                agent = state["agents"][0]
                state["conversations"].append(dict(id=self._id(), title=agent["name"], kind="dm", members=[agent["id"]], coordinator=agent["id"], goal=""))
            if state["selected"] == conversation["id"]:
                state["selected"] = state["conversations"][0]["id"]
        elif kind == "send":
            if not conversation: raise ValueError("会话不存在")
            text = action.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 4000: raise ValueError("消息须为1至4000字")
            text = text.strip()
            if conversation["title"] == "新对话": conversation["title"] = text.replace("\n", " ")[:40]
            if action.get("run"):
                run = next((r for r in state["runs"] if r["id"] == action["run"] and r["conversation"] == conversation["id"]), None)
                if not run or run["state"] not in ACTIVE or run["kind"] == "chat": raise ValueError("此任务已结束或不支持补充")
                if len(run["updates"]) >= 3: raise ValueError("本轮最多补充3次，请等待结束后创建新任务")
                selection = dict(skills=action.get('skills', []), attachments=action.get('attachments', []))
                if not all(isinstance(values,list) and len(values)<=10 and all(isinstance(v,str) for v in values) for values in selection.values()): raise ValueError('补充资料参数无效')
                from .skill_catalog import skill_turn_block
                from .local_sources import turn_block
                if selection['skills']: text += '\n\n'+skill_turn_block(selection['skills'])
                if selection['attachments']: text += '\n\n'+turn_block(selection['attachments'])
                previous = run.setdefault('selection', {})
                for key, values in selection.items(): previous[key] = list(dict.fromkeys(previous.get(key, [])+values))
                if any(len(v)>10 for v in previous.values()): raise ValueError('单项任务最多10个技能或附件')
                run["updates"].append(text); run["events"].append("补充要求已接收，等待调用边界应用")
                self._message(state, conversation["id"], "user", text, run["id"])
                return
            if action.get("mode", "auto") not in {"auto", "task", "message"}: raise ValueError("消息类型无效")
            target = action.get("target") or conversation["coordinator"]
            mention_text = re.sub(r"```[\s\S]*?```|`[^`]*`", "", text)
            mentions = re.findall(r"(?:^|\s)@([^\s@]+)", mention_text)
            selected = []
            for name in mentions:
                ident = next((a["id"] for a in state["agents"] if a["name"] == name and a["id"] in conversation["members"]), "")
                if not ident:
                    if mention_text.startswith("@"+name): raise ValueError("@ 的伙伴不在当前会话中")
                    continue
                if ident not in selected: selected.append(ident)
            if len(selected) == 1: target = selected[0]
            elif len(selected) > 1: target = conversation["coordinator"]
            if target not in conversation["members"]: raise ValueError("发送对象不在当前会话中")
            history = self._history(state, conversation)
            selection = dict(skills=action.get("skills", []), attachments=action.get("attachments", []))
            if not all(isinstance(selection[k], list) and len(selection[k]) <= 10 and all(isinstance(v,str) for v in selection[k]) for k in selection):
                raise ValueError("每轮最多选择10个技能或附件")
            from .skill_catalog import skill_turn_block
            from .local_sources import turn_block
            additions = []
            if selection['skills']: additions.append(skill_turn_block(selection['skills']))
            if selection['attachments']: additions.append(turn_block(selection['attachments']))
            if additions: history.append(dict(sender="user_selected_materials", text="\n\n".join(additions)))
            mode = action.get("mode", "auto")
            # Literal greetings need no tool-routing envelope. Retain the full
            # conversation context; selected material/skills/mentions still route.
            if (mode == 'auto' and not selected and not additions
                    and re.fullmatch(r'(?:hello|hi|hey|你好|您好|嗨|哈喽)[!！.。?？\s]*', text, re.IGNORECASE)):
                mode = 'message'
            is_plan = mode == "task" and conversation["kind"] == "group" and target == conversation["coordinator"] and len(conversation["members"]) > 1
            reserved = sum((2 if r["kind"] in {"plan", "route"} else 1) for r in state["runs"] if r["state"] in ACTIVE)
            if reserved + (2 if is_plan or mode == "auto" else 1) > 12: raise ValueError("已有较多待处理工作，请先完成或停止部分任务")
            run = self._run(state, conversation, target, text, "route" if mode == "auto" else "plan" if is_plan else "chat" if mode == "message" else "task", context=history, model_id=action.get("model_id", ""))
            run["selection"] = selection
            run["multi_mention"] = len(selected)>1
            if action.get('sampling'):
                from .sampling import parse_sampling
                run['sampling'] = parse_sampling(action['sampling'])
            run['participants'] = selected or conversation['members']
            if is_plan or mode == "auto":
                run["members"] = [dict(id=a["id"], name=a["name"], role=a["role"], tools=a['tools']) for a in state["agents"] if a["id"] in run['participants'] and (a["id"] != target or len(selected)>1)]
            # Keep the user message before the streaming reply.
            reply = state["messages"].pop()
            self._message(state, conversation["id"], "user", text, run["id"])
            state["messages"].append(reply)
        elif kind == "cancel":
            run = next((r for r in state["runs"] if r["id"] == action.get("id")), None)
            if not run: raise ValueError("任务不存在")
            stop_ids = {run["id"]}
            while True:
                expanded = stop_ids | {r["id"] for r in state["runs"] if set(dependencies(r)) & stop_ids or r.get("parent") in stop_ids}
                if expanded == stop_ids: break
                stop_ids = expanded
            for item in state["runs"]:
                if item["id"] in stop_ids and item["state"] in ACTIVE:
                    item["state"] = "cancelling" if item.get("task") else "cancelled"
                    item["events"].append("已请求停止，不再派发后续任务")
                    if item.get("task"): self._manager(item).cancel(item["task"])
        elif kind == "new_chat":
            agent = next((a for a in state['agents'] if a['id'] == action.get('agent_id', 'tebb')), None)
            if not agent: raise ValueError("伙伴不存在")
            row = dict(id=self._id(), title="新对话", kind="dm", members=[agent['id']], coordinator=agent['id'], goal="", project_id=project_id((conversation or {}).get("project_id", "")))
            state['conversations'].append(row); state['selected'] = row['id']
        elif kind == "add_coder":
            if not any(a['id'] == 'coder' for a in state['agents']):
                if len(state['agents']) >= 8: raise ValueError('当前最多8位伙伴')
                state['agents'].append(dict(id='coder', name='码可', role='在授权工作目录实现代码，真实运行构建和测试，核对退出结果，修复失败并交付差异和验证范围。', personality='务实、细心，说清楚做了什么以及尚未验证的部分。', avatar='owl', mark='C', tone='sage', model='', tools=['工作区读取','文件编辑','终端'], memory=''))
                state['conversations'].append(dict(id='dm-coder',title='码可',kind='dm',members=['coder'],coordinator='coder',goal=''))
            state['selected'] = next(c['id'] for c in state['conversations'] if c['kind']=='dm' and c['coordinator']=='coder')
        elif kind == "compress":
            if not conversation: raise ValueError('会话不存在')
            if any(r['conversation']==conversation['id'] and r['state'] in ACTIVE for r in state['runs']): raise ValueError('请等待当前会话任务完成后整理上下文')
            rows = [m for m in state['messages'] if m['conversation']==conversation['id'] and m['text']]
            if sum(len(m['text']) for m in rows)>60000: raise ValueError('当前会话超过60000字，请先选择较小范围整理')
            run = self._run(state, conversation, conversation['coordinator'], '压缩为后续对话的上下文摘要：保留用户目标、约束、决定、来源与未完成事项，不补造事实。', 'compress', context=[dict(sender=m['sender'],text=m['text']) for m in rows])
            run['compress_ids'] = [m['id'] for m in rows]
        elif kind == "group":
            title, goal, members = action.get("title"), action.get("goal", ""), action.get("members")
            if not isinstance(title, str) or not title.strip() or len(title) > 40 or not isinstance(goal, str) or len(goal) > 500: raise ValueError("群名或目标无效")
            if not isinstance(members, list) or not all(isinstance(m, str) for m in members): raise ValueError("成员无效")
            members = list(dict.fromkeys(members))
            if not 2 <= len(members) <= 6 or not set(members) <= {a["id"] for a in state["agents"]}: raise ValueError("请选择2至6位现有 Agent")
            existing = next((c for c in state['conversations'] if c['id']==action.get('id') and c['kind']=='group'),None)
            if action.get('id') and not existing: raise ValueError('协作群不存在')
            if existing and any(r['conversation']==existing['id'] and r['state'] in ACTIVE for r in state['runs']): raise ValueError('请等待或停止本群任务后调整成员')
            row = dict(id=existing['id'] if existing else self._id(), title=title.strip(), kind="group", members=members, coordinator="tebb" if "tebb" in members else members[0], goal=goal,
                project_id=existing.get("project_id", "") if existing else project_id((conversation or {}).get("project_id", "")))
            if existing: state["conversations"][state["conversations"].index(existing)] = row
            else: state["conversations"].append(row)
            state["selected"] = row["id"]
        elif kind == "agent":
            from .model_catalog import require_item
            agent = action.get("agent")
            if not isinstance(agent, dict): raise ValueError("Agent 参数无效")
            for field, limit in {"name": 24, "role": 160, "memory": 2000, "model": 100}.items():
                if not isinstance(agent.get(field), str) or len(agent[field]) > limit: raise ValueError("Agent 字段无效")
            name = agent["name"].strip()
            if not name or re.search(r"[\s@]", name) or any(a["name"] == name and a["id"] != agent.get("id") for a in state["agents"]): raise ValueError("Agent 名称为空、重复或包含空格/@")
            if not isinstance(agent.get("tools"), list) or any(t not in TOOLS for t in agent["tools"]): raise ValueError("真实验证仅支持联网只读工具")
            if not isinstance(agent.get("avatar", "owl"), str) or agent.get("avatar", "owl") not in AVATARS: raise ValueError("头像无效")
            if not isinstance(agent.get("personality", ""), str) or len(agent.get("personality", "")) > 160: raise ValueError("性格描述最多160字")
            if agent["model"]: require_item(agent["model"])
            current = next((a for a in state["agents"] if a["id"] == agent.get("id")), None)
            if not current and agent.get("id"): raise ValueError("Agent 不存在")
            if not current and len(state["agents"]) >= 8: raise ValueError("当前最多8位 Agent")
            row = dict(id=current["id"] if current else self._id(), name=name, role=agent["role"], memory=agent["memory"], model=agent["model"], tools=agent["tools"], mark=current["mark"] if current else "A", tone=current["tone"] if current else "sage")
            row.update(avatar=agent.get("avatar", (current or {}).get("avatar", "owl")), personality=agent.get("personality", (current or {}).get("personality", "")))
            if current: state["agents"][state["agents"].index(current)] = row
            else: state["agents"].append(row)
            dm = next((c for c in state["conversations"] if c["kind"] == "dm" and c["coordinator"] == row["id"]), None)
            if dm: dm["title"] = name
            else: state["conversations"].append(dict(id="dm-"+row["id"], title=name, kind="dm", members=[row["id"]], coordinator=row["id"], goal="独立会话与 Agent 配置"))
        else:
            raise ValueError("此操作不属于真实协作功能")

    @staticmethod
    def _history(state, conversation):
        rows = [m for m in state['messages'] if m['conversation']==conversation['id'] and m.get('text')]
        covered = set(conversation.get('summary_ids', []))
        history = [dict(sender=m['sender'],text=m['text'][:8000]) for m in rows if m['id'] not in covered][-20:]
        if conversation.get('summary'): history.insert(0, dict(sender='conversation_summary',text=conversation['summary']))
        return history

    def _manager(self, run):
        return self.managers["chat" if run["kind"] in {"chat", "route", "compress"} else "work"]

    def _finish(self, state, run, status, error=""):
        run["state"] = status; run["error"] = error
        for message in state["messages"]:
            if message.get("run") == run["id"] and message["sender"] == run["agent"]:
                message["text"] = run["answer"] + ("\n\n[未完成："+error+"]" if error and run["answer"] else "") if run["answer"] else error
                if status == "succeeded" and run["kind"] == "task": message["kind"] = "result"
        if status == "succeeded" and run["kind"] == "task": run["artifact"] = run["answer"]

    def tick(self):
        with self.lock, transaction(self.path) as (state, _):
            # Polling and submitting occur under one state transaction. Deterministic
            # task IDs bridge a crash between queue insertion and state commit.
            for run in list(state["runs"]):
                if run["state"] not in ACTIVE: continue
                if run["state"] == "waiting":
                    preceding = [next(r for r in state['runs'] if r['id']==ident) for ident in dependencies(run)]
                    if any(dep['state'] not in ACTIVE | {'succeeded'} for dep in preceding):
                        self._finish(state, run, "cancelled", "前序任务未成功，未继续执行")
                    elif all(dep['state']=='succeeded' for dep in preceding):
                        for dep in preceding:
                            run["context"].append(dict(sender=dep["agent"], text=dep["answer"][:24000]))
                        run['source_evidence']=[e for dep in preceding for e in dep.get('source_evidence',[])+dep.get('evidence',[])]
                        run["state"] = "pending"; run["events"].append("已收到前序成果，准备接续")
                    continue
                manager = self._manager(run)
                if run["state"] == "pending":
                    if run.get('workspace', {}).get('primary') and set(run['agent_config']['tools']) & {'文件编辑', '终端'} and any(other['id'] != run['id'] and other['state'] in {'queued','running','cancelling'} and other.get('workspace',{}).get('primary') == run['workspace'].get('primary') and set(other['agent_config']['tools']) & {'文件编辑','终端'} for other in state['runs']):
                        run['status'] = '等待工作目录的编码任务完成'; continue
                    request = dict(workflow="collaboration", collab_path=str(self.path), collab_run=run["id"],
                        role=run["agent_config"], kind=run["kind"], model_id=run["model_id"], context=run["context"], workspace=run.get("workspace"), selection=run.get("selection", {}),
                        members=run.get("members", []), applied=run["applied"], draft=run.get("parent_draft", ""), budget=max(0, call_budget(run['agent'],run['kind'])-run["calls"]),
                        objective=run.get('objective',run['input']),stage=run.get('stage','collect'),required_sources=run.get('required_sources',[]),
                        search_budget=max(0, run.get('search_limit',2)-run.get("search_calls",0)),fetch_budget=max(0,run.get('fetch_limit',4)-run.get("fetch_calls",0)),
                        source_topics=run.get('source_topics',[]),
                        source_evidence=run.get("source_evidence",[]),evidence=run.get("evidence",[]))
                    run["task"] = manager.submit(run["input"], "collab-"+run["id"], "collaboration", submission_key=f"collab:{run['id']}:{run['attempt']}",
                        task_limits=execution_limits(run['agent']), **({**request, **({"sampling":run["sampling"]} if run.get("sampling") else {})}))
                    run["state"] = "queued"
                task = manager.get(run["task"])
                for event in task["events"]:
                    if event["seq"] <= run["cursor"]: continue
                    run["cursor"] = event["seq"]
                    kind, data = event["kind"], event["data"]
                    if kind == "status": run["events"].append(str(data)); run["status"] = str(data)
                    elif kind == 'token' and run['kind'] == 'route': run['public_answer'] = run.get('public_answer','') + str(data)
                    elif kind == 'answer_reset': run.pop('public_answer',None)
                    elif kind == "llm_start": run["calls"] += 1; run["events"].append("调用模型：" + run["model"])
                    elif kind == "collab_applied": run["applied"] = int(data)
                    elif kind == "usage":
                        run["input_tokens"] += int(data.get("input_tokens", 0)); run["output_tokens"] += int(data.get("output_tokens", 0))
                    elif kind == "evidence": run.setdefault("evidence", []).append(data)
                    elif kind == "file_change":
                        run.setdefault("changes", []).append(data)
                        for check in run.get("checks", []): check["stale"] = True
                    elif kind == "verification": run.setdefault("checks", []).append(data)
                    elif kind == "citation_check": run["citation_check"]=data
                    elif kind == "delivery_check": run['delivery_check']=data
                    elif kind in {"tool_start", "tool_end"}:
                        if kind == "tool_start":
                            counter={'web_fetch':'fetch_calls','web_search':'search_calls'}.get(data.get('tool'))
                            if counter: run[counter]=run.get(counter,0)+1
                            run["tool_calls"] = run.get("tool_calls", 0)+1
                        run["events"].append(f"{kind}: {data.get('tool')} {data.get('url') or data.get('query') or ''} {data.get('status', '')} {data.get('provider') or ''} {data.get('error', '')}")
                        run['status'] = ('正在调用：' if kind=='tool_start' else '调用结束：') + str(data.get('tool',''))
                run["answer"] = run.get('public_answer','') if run['kind'] in {'route','plan'} and task['state'] not in TERMINAL else task["answer"]
                if run["state"] != "cancelling": run["state"] = task["state"]
                for message in state["messages"]:
                    if message.get("run") == run["id"] and message["sender"] == run["agent"]: message["text"] = run["answer"]
                if task["state"] not in TERMINAL: continue
                if run["state"] == "cancelling" or task["state"] == "cancelled":
                    self._finish(state, run, "cancelled", "任务已停止，部分结果保留")
                    continue
                if task["state"] != "succeeded":
                    self._finish(state, run, task["state"], task["error"])
                    continue
                if run["applied"] < len(run["updates"]):
                    if run["calls"] >= call_budget(run['agent'],run['kind']):
                        self._finish(state, run, "failed", "调用预算已用完，最新补充尚未应用；请参考部分结果新建任务")
                    else:
                        run.update(state="pending", attempt=run["attempt"]+1, cursor=0, parent_draft=run["answer"])
                        run["events"].append("收到迟到补充，在剩余预算内修订；未交接旧成果")
                    continue
                if run['kind'] == 'route':
                    try:
                        decision = parse_route(run['answer'])
                        content = decision.get('text') if decision['mode']=='answer' else decision.get('instructions')
                    except (ValueError, TypeError):
                        self._finish(state,run,'failed','未取得有效的回答或任务路由，请重试'); continue
                    if run.get('multi_mention'):
                        decision = dict(mode='task',instructions=run['input'])
                        content = run['input']
                    if decision['mode']=='answer':
                        run['answer'] = content
                    else:
                        conversation = next(c for c in state['conversations'] if c['id']==run['conversation'])
                        is_group = conversation['kind']=='group' and (run['agent']==conversation['coordinator']) and bool(run.get('members'))
                        # Routing classifies; it cannot replace or narrow the human request.
                        child = self._run(state, conversation, run['agent'], run['input'], 'plan' if is_group else 'task', parent=run['id'], context=run['context']+[dict(sender='user',text=t) for t in run['updates']], model_id=run['model_id'])
                        child['workspace'] = run['workspace']
                        child['members'] = run.get('members', [])
                        child['selection'] = run.get('selection', {})
                        if run.get('sampling'): child['sampling'] = run['sampling']
                        run['answer'] = ''
                        run['handoff'] = child['id']
                    self._finish(state,run,'succeeded'); continue
                if run['kind'] == 'compress':
                    conversation = next(c for c in state['conversations'] if c['id']==run['conversation'])
                    conversation['summary'] = run['answer']; conversation['summary_ids'] = run.get('compress_ids', [])
                if run["kind"] == "plan":
                    try:
                        plans = parse_plan(run["answer"], {m["id"] for m in run["members"]})
                    except (ValueError, TypeError):
                        self._finish(state, run, "failed", "协调者未返回有效委派计划，未执行任何子任务")
                        continue
                    conversation = next(c for c in state["conversations"] if c["id"] == run["conversation"])
                    children = []
                    for plan in plans:
                        dep=plan.get('depends_on')
                        edges=dep if isinstance(dep,list) else [] if dep is None else [dep]
                        agent=next(a for a in run['members'] if a['id']==plan['agent_id'])
                        stage='deliver' if children and not agent.get('tools') else plan.get('stage','collect')
                        # A synthesis task consumes every preceding collection, even
                        # if a model omitted a dependency in its proposed plan.
                        if stage=='deliver': edges=list(range(len(children)))
                        deps=[children[index]['id'] for index in edges]
                        dependency=deps[-1] if deps else None
                        assigned=next(a for a in state['agents'] if a['id']==plan['agent_id'])
                        child = self._run(state, conversation, plan["agent_id"], plan["instructions"], parent=run["id"], dependency=dependency,
                            context=run["context"] + [dict(sender="user", text=run["input"]), *[dict(sender="user", text=t) for t in run["updates"]]],model_id='' if assigned.get('model') else run['model_id'])
                        child.update(dependencies=deps,objective=run['input'],stage=stage,required_sources=plan.get('required_sources',[]))
                        topics=list(dict.fromkeys(plan.get('source_topics',[])))
                        child.update(source_topics=topics,search_limit=max(2,len(topics)),fetch_limit=max(4,len(topics)*2))
                        if stage=='deliver': child['required_sources']=list(dict.fromkeys(child['required_sources']+[s for previous in children for s in previous.get('required_sources',[])]))
                        child["workspace"] = run["workspace"]; child["title"] = plan["title"]; child["selection"] = run.get("selection", {}); children.append(child)
                    run["answer"] = "已委派：\n" + "\n".join(next(a["name"] for a in state["agents"] if a["id"] == p["agent_id"]) + "：" + p["title"] for p in plans)
                    run["events"].append("委派计划已校验；子任务不能继续委派")
                self._finish(state, run, "succeeded")
            by_parent={}
            for child in state['runs']:
                if child.get('parent'): by_parent.setdefault(child['parent'],[]).append(child)
            for plan in (r for r in state['runs'] if r['kind']=='plan' and r['state']=='succeeded'):
                children=by_parent.get(plan['id'],[])
                if children:
                    plan['delivery_state']='running' if any(c['state'] in ACTIVE for c in children) else 'succeeded' if all(c['state']=='succeeded' for c in children) else 'failed'

    def _loop(self):
        while not self.stop.wait(.3):
            try:
                self.tick()
            except Exception as exc:
                from .logutil import log
                log("collaboration scheduler: " + type(exc).__name__)
                with self.lock, transaction(self.path) as (state, _):
                    state["notice"] = "协作调度异常（"+type(exc).__name__+"），请检查本地日志；不会自动重做外部操作。"

    def shutdown(self):
        self.stop.set()
        if self.thread.is_alive(): self.thread.join(3)
        for manager in self.managers.values(): manager.shutdown()

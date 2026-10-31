"""Workflow definitions and execution receipts, owned by one transactional store."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .paths import data_root
from .workflow_graph import KINDS, WRITES, ready_node, validate, version


class WorkflowStore:
    def __init__(self, path=None):
        self.path = Path(path or data_root() / 'memory' / 'workflows.sqlite3')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS definitions(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL, version TEXT NOT NULL, graph TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(workflow TEXT, version TEXT, graph TEXT, PRIMARY KEY(workflow,version));
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, workflow TEXT, version TEXT, name TEXT,
                    state TEXT, mode TEXT, attempt INTEGER, task_id TEXT, created REAL, updated REAL,
                    error TEXT, approved TEXT, input TEXT, request_key TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS receipts(run TEXT, node TEXT, kind TEXT, state TEXT, input TEXT,
                    output TEXT, error TEXT, started REAL, ended REAL, receipt_key TEXT, PRIMARY KEY(run,node));
            ''')

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def definitions(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT id,name,revision,version,updated FROM definitions ORDER BY updated DESC')]

    def definition(self, ident, requested_version=''):
        with self.db() as db:
            row = db.execute('SELECT * FROM definitions WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise ValueError('工作流不存在。')
            result = dict(row)
            if requested_version:
                old = db.execute('SELECT graph FROM versions WHERE workflow=? AND version=?', (ident, requested_version)).fetchone()
                if old is None:
                    raise ValueError('工作流版本不存在。')
                result.update(version=requested_version, graph=old['graph'])
            result['graph'] = json.loads(result['graph'])
            return result

    def save(self, payload):
        name = payload.get('name', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('工作流名称需要 1–100 字。')
        graph, _ = validate(payload.get('graph'))
        ident = payload.get('id') or uuid.uuid4().hex
        if not isinstance(ident, str) or len(ident) > 100:
            raise ValueError('工作流编号无效。')
        raw, digest = json.dumps(graph, ensure_ascii=False), version(graph)
        with self.db(True) as db:
            row = db.execute('SELECT revision FROM definitions WHERE id=?', (ident,)).fetchone()
            if row and payload.get('revision') != row['revision']:
                raise ValueError('工作流已被其他窗口修改，请读取最新版本；当前草稿保留。')
            revision = row['revision'] + 1 if row else 1
            try:
                db.execute('INSERT INTO definitions VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                           'name=excluded.name,revision=excluded.revision,version=excluded.version,graph=excluded.graph,updated=excluded.updated',
                           (ident, name.strip(), revision, digest, raw, time.time()))
            except sqlite3.IntegrityError:
                raise ValueError('已有同名工作流，请修改名称。') from None
            db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?)', (ident, digest, raw))
        return self.definition(ident)

    def runs(self, ident=''):
        with self.db() as db:
            rows = db.execute('SELECT id,workflow,version,name,state,mode,attempt,task_id,created,updated,error FROM runs '
                              + ('WHERE workflow=? ' if ident else '') + 'ORDER BY created DESC LIMIT 100', (ident,) if ident else ())
            return [dict(row) for row in rows]

    def run(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise ValueError('工作流执行不存在。')
            result = dict(row)
            for key in ('approved', 'input'):
                result[key] = json.loads(result[key])
            result['receipts'] = {}
            for item in db.execute('SELECT * FROM receipts WHERE run=? ORDER BY started', (ident,)):
                receipt = dict(item)
                for key in ('input', 'output'):
                    receipt[key] = json.loads(receipt[key])
                result['receipts'][receipt['node']] = receipt
            result['graph'] = self.definition(result['workflow'], result['version'])['graph']
            return result

    def create_run(self, ident, selected_version, request_key, mode, inputs, approve):
        definition = self.definition(ident, selected_version)
        if mode not in {'step', 'full'} or not isinstance(inputs, dict) or set(inputs) - {'text'} or not isinstance(inputs.get('text', ''), str) or len(inputs.get('text', '')) > 50000:
            raise ValueError('执行模式或触发输入无效。')
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 160 or type(approve) is not bool:
            raise ValueError('需要稳定的执行请求编号和明确的写入确认。')
        run_id = uuid.uuid5(uuid.NAMESPACE_URL, 'tebb-workflow:' + request_key).hex
        approved = [n['id'] for n in definition['graph']['nodes'] if n['kind'] in WRITES] if approve else []
        with self.db(True) as db:
            found = db.execute('SELECT * FROM runs WHERE request_key=?', (request_key,)).fetchone()
            if found:
                if found['workflow'] != ident or found['version'] != definition['version'] or json.loads(found['input']) != inputs or found['mode'] != mode:
                    raise ValueError('执行请求编号已用于不同配置。')
            else:
                if db.execute("SELECT 1 FROM runs WHERE workflow=? AND state IN ('queued','running','waiting_approval','unknown','paused','failed','interrupted')", (ident,)).fetchone():
                    raise ValueError('该工作流已有未完成或待核对执行，请继续原执行，避免重复写入。')
                now = time.time()
                db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (run_id, ident, definition['version'], definition['name'], 'queued', mode, 0, '', now, now, '', json.dumps(approved), json.dumps(inputs), request_key))
        return self.run(run_id)

    def set_run(self, ident, *, attempt=None, require_running=False, **changes):
        if not changes or set(changes) - {'state', 'error', 'task_id', 'approved', 'attempt'}:
            raise ValueError('无效执行状态字段。')
        if 'approved' in changes:
            changes['approved'] = json.dumps(changes['approved'])
        changes['updated'] = time.time()
        with self.db(True) as db:
            where = 'id=?' + (' AND attempt=?' if attempt is not None else '') + (" AND state='running'" if require_running else '')
            args = [*changes.values(), ident] + ([attempt] if attempt is not None else [])
            db.execute('UPDATE runs SET ' + ','.join(k + '=?' for k in changes) + ' WHERE ' + where, args)

    def claim(self, ident, attempt):
        with self.db(True) as db:
            return db.execute("UPDATE runs SET state='running',updated=? WHERE id=? AND state='queued' AND attempt=?",
                              (time.time(), ident, attempt)).rowcount == 1

    def receipt(self, run_id, node, state, *, inputs=None, output=None, error='', attempt=None):
        with self.db(True) as db:
            if attempt is not None:
                owner = db.execute('SELECT state,attempt FROM runs WHERE id=?', (run_id,)).fetchone()
                if owner is None or owner['attempt'] != attempt or owner['state'] != 'running':
                    raise RuntimeError('原工作进程已不拥有当前执行，拒绝迟到回执。')
            old = db.execute('SELECT * FROM receipts WHERE run=? AND node=?', (run_id, node['id'])).fetchone()
            now = time.time()
            db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?,?,?,?,?,?)',
                (run_id, node['id'], node['kind'], state, json.dumps(inputs if inputs is not None else json.loads(old['input']) if old else {}, ensure_ascii=False),
                 json.dumps(output if output is not None else json.loads(old['output']) if old else {}, ensure_ascii=False), error,
                 old['started'] if old else now, 0 if state == 'pending' else now,
                 old['receipt_key'] if old else uuid.uuid5(uuid.NAMESPACE_URL, run_id + ':' + node['id']).hex))

    def reconcile_commit(self, run, receipt, output):
        with self.db(True) as db:
            current = db.execute('SELECT state,attempt FROM runs WHERE id=?', (run['id'],)).fetchone()
            row = db.execute('SELECT state,receipt_key,input FROM receipts WHERE run=? AND node=?', (run['id'], receipt['node'])).fetchone()
            if current is None or current['state'] != 'unknown' or current['attempt'] != run['attempt'] or row is None or row['state'] != 'unknown' or row['receipt_key'] != receipt['receipt_key'] or json.loads(row['input']) != receipt['input']:
                raise ValueError('核对期间执行状态已变化，未覆盖新回执；请重新读取。')
            db.execute("UPDATE receipts SET state='confirmed',output=?,error='',ended=? WHERE run=? AND node=?", (json.dumps(output, ensure_ascii=False),time.time(),run['id'],receipt['node']))
            db.execute("UPDATE runs SET state='paused',error='已核对原写入，可以继续原执行。',updated=? WHERE id=?", (time.time(),run['id']))

    def observe_terminal(self, ident, attempt, task_state):
        with self.db(True) as db:
            current = db.execute('SELECT state,attempt FROM runs WHERE id=?', (ident,)).fetchone()
            if current is None or current['attempt'] != attempt or current['state'] not in {'queued','running'}:
                return
            for row in db.execute("SELECT node,kind FROM receipts WHERE run=? AND state='pending'", (ident,)).fetchall():
                db.execute("UPDATE receipts SET state=?,error='工作进程结束，原节点未取得完整回执。',ended=? WHERE run=? AND node=? AND state='pending'",
                           ('unknown' if row['kind'] in WRITES else 'failed', time.time(),ident,row['node']))
            unknown = db.execute("SELECT 1 FROM receipts WHERE run=? AND state='unknown'", (ident,)).fetchone()
            state = 'unknown' if unknown else 'cancelled' if task_state == 'cancelled' else 'interrupted'
            db.execute('UPDATE runs SET state=?,error=?,updated=? WHERE id=?', (state,'执行已停止；已确认节点保留，未知写入先核对。',time.time(),ident))


class WorkflowService:
    def __init__(self, tasks, path=None):
        self.tasks = tasks
        self.store = WorkflowStore(path)

    def operation(self, operation, payload=None):
        try:
            return dict(ok=True, **self._operation(operation, payload or {}))
        except Exception as exc:
            return dict(ok=False, error=str(exc))

    def _operation(self, operation, payload):
        if not isinstance(payload, dict):
            raise ValueError('工作流参数须为对象。')
        if operation == 'catalog':
            return dict(nodes=[dict(kind=k, label=v[0], defaults=v[1], outputs=v[2], writes=k in WRITES) for k, v in KINDS.items()])
        if operation == 'list':
            return dict(definitions=self.store.definitions(), runs=self.store.runs(payload.get('id', '')))
        if operation == 'load':
            return dict(definition=self.store.definition(payload['id']))
        if operation == 'validate':
            graph, order = validate(payload.get('graph'))
            return dict(graph=graph, order=order, version=version(graph))
        if operation == 'check_targets':
            from .workflow_nodes import preflight
            graph, _ = validate(payload.get('graph'))
            return dict(targets=[dict(node=n['id'], kind=n['kind'], **preflight(n['kind'], n['config'])) for n in graph['nodes'] if n['kind'] in WRITES])
        if operation == 'save':
            return dict(definition=self.store.save(payload))
        if operation == 'start':
            if not payload.get('version'):
                raise ValueError('先保存并明确工作流版本，再执行。')
            run = self.store.create_run(payload['id'], payload['version'], payload.get('request_id', ''), payload.get('mode', 'step'),
                                        payload.get('input', {}), payload.get('confirm_writes', False))
            return dict(run=self._submit(run) if run['state'] == 'queued' else self.observe(run['id']))
        if operation == 'get':
            return dict(run=self.observe(payload['run_id']))
        if operation == 'preview_next':
            from .workflow_graph import resolve
            run = self.observe(payload['run_id'])
            graph, order = validate(run['graph'])
            node, active = ready_node(graph, order, run['receipts'])
            outputs = {key: r['output'] for key, r in run['receipts'].items() if r['state'] == 'confirmed'}
            return dict(node=None if node is None else {**node, 'active': active, 'resolved': resolve(node['config'], outputs) if active else {}})
        if operation == 'cancel':
            run = self.observe(payload['run_id'])
            if run['task_id'] and run['state'] in {'queued', 'running'}:
                self.tasks().cancel(run['task_id'])
            elif run['state'] not in {'completed', 'unknown'}:
                self.store.set_run(run['id'], state='cancelled', error='用户停止。')
            return dict(run=self.observe(run['id']))
        if operation == 'advance':
            run = self.observe(payload['run_id'])
            if run['state'] not in {'paused', 'failed', 'interrupted', 'cancelled', 'waiting_approval'}:
                raise ValueError('执行正在运行、已完成或结果未知；未知写入须先核对，不会重放。')
            graph, order = validate(run['graph'])
            node, _ = ready_node(graph, order, run['receipts'])
            approved = list(run['approved'])
            if payload.get('confirm_node'):
                if node is None or payload['confirm_node'] != node['id'] or node['kind'] not in WRITES:
                    raise ValueError('写入确认须对应当前待执行节点。')
                if node['id'] not in approved:
                    approved.append(node['id'])
            with self.store.db(True) as db:
                changed = db.execute("UPDATE runs SET state='queued',attempt=attempt+1,approved=?,error='',task_id='',updated=? "
                    "WHERE id=? AND state=? AND attempt=?", (json.dumps(approved), time.time(), run['id'], run['state'], run['attempt'])).rowcount
                if changed != 1:
                    raise ValueError('执行状态已变化，请重新读取。')
            return dict(run=self._submit(self.store.run(run['id'])))
        if operation == 'reconcile':
            from .workflow_nodes import reconcile
            run = self.observe(payload['run_id'])
            receipt = run['receipts'].get(payload.get('node_id'))
            if not receipt or receipt['state'] != 'unknown' or run['state'] != 'unknown':
                raise ValueError('只有结果未知的写入节点需要核对。')
            node = next(n for n in run['graph']['nodes'] if n['id'] == payload['node_id'])
            output = reconcile(node['kind'], receipt['input'], receipt['receipt_key'], payload.get('remote_id', ''))
            self.store.reconcile_commit(run, receipt, output)
            return dict(run=self.store.run(run['id']))
        raise ValueError('未知工作流操作。')

    def _submit(self, run):
        # Save the stable task identity first; a lost submit response is observable.
        from .tasks import submission_task_id
        key = f'workflow:{run["id"]}:{run["attempt"]}'
        task_id = submission_task_id(key)
        self.store.set_run(run['id'], attempt=run['attempt'], task_id=task_id)
        from .task_budgets import Ledger
        ledger=Ledger();budget_root=ledger.create('workflow:'+run['id'],'research',900000,prior_usage_unknown=run['attempt']>0)
        ledger.resume(budget_root)
        try:
            self.tasks().submit('工作流：' + run['name'], 'workflow:' + run['workflow'], channel='automation',
                _budget_root=budget_root,_budget_root_member=True,
                source=dict(workflow_run=run['id'], workflow_id=run['workflow']), submission_key=key,
                workflow='automation_graph', workflow_run=run['id'], workflow_attempt=run['attempt'],
                task_limits=dict(task_timeout=900, call_timeout=120))
        except Exception:
            # Admission may have committed; keep the same key for reconciliation.
            self.store.set_run(run['id'], attempt=run['attempt'], error='任务提交回执未确认，请读取原执行核对，不要重新创建。')
        return self.store.run(run['id'])

    def observe(self, ident):
        run = self.store.run(ident)
        if run['state'] in {'queued', 'running'} and run['task_id']:
            try:
                task = self.tasks().get(run['task_id'], False)
            except ValueError:
                # Deterministic resubmission only reconciles queue admission, never node writes.
                if run['state'] == 'queued':
                    return self._submit(run)
                task = dict(state='interrupted')
            if task['state'] in {'failed', 'cancelled', 'timed_out', 'interrupted', 'succeeded'}:
                self.store.observe_terminal(ident, run['attempt'], task['state'])
                run=self.store.run(ident)
        from .task_budgets import Ledger
        ledger=Ledger();budget_root=ledger.for_owner('workflow:'+run['id'])
        if budget_root:
            if run['state']in{'paused','waiting_approval','unknown'}:ledger.waiting(budget_root,True)
            elif run['state']in{'completed','failed','interrupted','cancelled'}:ledger.finish(budget_root)
        run['task_budget']=ledger.view(budget_root)if budget_root else None
        return run

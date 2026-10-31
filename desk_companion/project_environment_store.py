"""Short transactions for project environments and idempotent operation receipts."""
from contextlib import contextmanager,closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from .resource_lock import ResourceLock

ACTIVE={'running','unknown','stopping'}
READ_KINDS={'read','integrity'}


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class EnvironmentStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS environments (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY,environment TEXT NOT NULL,fingerprint TEXT NOT NULL,data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY,environment TEXT NOT NULL,fingerprint TEXT NOT NULL,operation TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO requests SELECT id,environment,fingerprint,id FROM operations')

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            db.execute('BEGIN IMMEDIATE')
            try:yield db
            except BaseException:db.rollback();raise
            else:db.commit()

    @staticmethod
    def _environment(db,ident):
        row=db.execute('SELECT data FROM environments WHERE id=?',(ident,)).fetchone()
        if row is None:raise ValueError('项目环境不存在。')
        return json.loads(row[0])

    @staticmethod
    def _operation(db,ident):
        row=db.execute('SELECT data FROM operations WHERE id=?',(ident,)).fetchone()
        if row is None:raise ValueError('项目操作不存在。')
        return json.loads(row[0])

    @staticmethod
    def _save(db,environment):
        environment['updated']=time.time()
        db.execute('UPDATE environments SET data=? WHERE id=?',(json.dumps(environment,ensure_ascii=False),environment['id']))

    @staticmethod
    def _save_operation(db,operation):
        operation['updated']=time.time()
        db.execute('UPDATE operations SET data=? WHERE id=?',(json.dumps(operation,ensure_ascii=False),operation['id']))

    def get(self,ident):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:return self._environment(db,ident)

    def operation(self,ident):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:return self._operation(db,ident)

    def list(self):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM environments ORDER BY rowid DESC')]

    def creation_guard(self,operation):
        """OS-owned lifetime, not a task/PID/TTL inference; never restore the lock."""
        database=self.path.resolve()
        return ResourceLock('project-create:'+os.path.normcase(str(database))+':'+operation,directory=database.parent/'operation-locks')

    def creation_active(self,operation):
        lock=self.creation_guard(operation)
        if not lock.acquire(blocking=False):return True
        lock.__exit__();return False

    def archive_status(self,ident):
        environment=self.get(ident);original=environment.get('prepare_operation')
        operation=self.operation(original) if original else {}
        active=self.creation_active(original) if original else False
        eligible=bool(original and operation.get('kind')=='prepare' and operation.get('state')=='unknown'
                      and environment.get('active_operation')==original and not environment.get('sandbox_id')
                      and not environment.get('service') and not environment.get('creation_archive') and not active)
        return dict(eligible=eligible,operation=original,local_active=active,
                    archived=bool(environment.get('creation_archive')),creation_outcome_unknown=operation.get('state')=='unknown')

    def archive_create(self,ident,expected,request):
        """Release admission once; a late acknowledgement still belongs to the old row."""
        arguments=dict(expected_operation=expected)
        digest=fingerprint(dict(environment=ident,kind='archive_prepare',arguments=arguments))
        def replay(db):
            previous=db.execute('SELECT environment,fingerprint,operation FROM requests WHERE id=?',(request,)).fetchone()
            if previous:
                if previous[0]!=ident or previous[1]!=digest:raise ValueError('请求编号已用于不同项目操作。')
                return self._environment(db,ident),self._operation(db,previous[2]),True
            environment=self._environment(db,ident);archived=environment.get('creation_archive')
            if archived:
                if archived['operation']!=expected:raise ValueError('原创建操作已变化，请刷新后重试。')
                db.execute('INSERT INTO requests VALUES (?,?,?,?)',(request,ident,digest,archived['request']))
                return environment,self._operation(db,archived['request']),True
        # An acknowledged historical archive can be replayed while a later explicit
        # observation is active. It does not release admission a second time.
        with self.transaction() as db:
            previous=replay(db)
            if previous:return previous
        lock=self.creation_guard(expected)
        if not lock.acquire(blocking=False):raise ValueError('原创建或归属验证仍在父后台执行，暂不能归档。')
        try:
            with self.transaction() as db:
                previous=replay(db)
                if previous:return previous
                environment=self._environment(db,ident);operation=self._operation(db,expected)
                if environment.get('prepare_operation')!=expected or environment.get('active_operation')!=expected or operation['environment']!=ident:
                    raise ValueError('原创建操作已变化，请刷新后重试。')
                if operation['kind']!='prepare' or operation['state']!='unknown':raise ValueError('仅可归档结果未知的原创建请求。')
                if environment.get('sandbox_id') or environment.get('service'):
                    raise ValueError('已绑定实例或存在服务，需先按原身份核对与停止，不能归档释放。')
                now=time.time();fact=dict(operation=expected,request=request,at=now,actor='desktop',creation_outcome='unknown')
                environment.update(active_operation='',creation_archive=fact)
                operation['creation_archive']=fact
                receipt=dict(id=request,environment=ident,kind='archive_prepare',state='succeeded',stage='archived',owner_task='',
                             created=now,updated=now,cancel_requested=False,error='',result=dict(archived_operation=expected,
                             admission_released=True,creation_outcome='unknown',stop_confirmed=False))
                db.execute('INSERT INTO operations VALUES (?,?,?,?)',(request,ident,digest,json.dumps(receipt,ensure_ascii=False)))
                db.execute('INSERT INTO requests VALUES (?,?,?,?)',(request,ident,digest,request))
                self._save_operation(db,operation);self._save(db,environment)
                return environment,receipt,False
        finally:lock.__exit__()

    def begin(self,ident,kind,request_id,arguments,owner_task='',*,initial=None):
        digest=fingerprint(dict(environment=ident,kind=kind,arguments=arguments))
        with self.transaction() as db:
            previous=db.execute('SELECT environment,fingerprint,operation FROM requests WHERE id=?',(request_id,)).fetchone()
            if previous:
                # A replay of create uses a newly generated candidate ID; its inputs decide identity.
                compared=fingerprint(dict(environment=previous[0] if initial else ident,kind=kind,arguments=arguments))
                if previous[1]!=compared:raise ValueError('请求编号已用于不同项目操作。')
                return self._environment(db,previous[0]),self._operation(db,previous[2]),False
            if initial is not None:
                environment=dict(initial)
                if environment['id']!=ident:raise ValueError('项目环境身份不符。')
                if initial.get('scope') and initial.get('repository'):
                    for row in db.execute('SELECT data FROM environments'):
                        saved=json.loads(row[0])
                        if any(saved.get(key)!=initial.get(key) for key in ('scope','repository','ref')):continue
                        if saved.get('creation_archive'):continue
                        previous_id=saved.get('prepare_operation')
                        if not previous_id:continue
                        original=self._operation(db,previous_id)
                        if saved.get('sandbox_id') or saved.get('active_operation') or original['state'] in ACTIVE or original['state']=='succeeded':
                            alias=fingerprint(dict(environment=saved['id'],kind=kind,arguments=arguments))
                            db.execute('INSERT INTO requests VALUES (?,?,?,?)',(request_id,saved['id'],alias,previous_id))
                            return saved,original,False
                db.execute('INSERT INTO environments VALUES (?,?)',(ident,json.dumps(environment,ensure_ascii=False)))
            else:environment=self._environment(db,ident)
            if environment.get('creation_archive') and kind!='stop':
                raise ValueError('原创建已归档；仅可核对原身份与停止，新的工作请使用新请求。')
            active=environment.get('active_operation')
            if active and self._operation(db,active)['state'] in ACTIVE:raise ValueError('此项目操作仍在执行或结果未知，请先查询或停止原操作。')
            if kind not in READ_KINDS|{'stop'} and (environment.get('service') or {}).get('state') in {'ready','stopping','unknown'}:raise ValueError('请先停止此项目服务，再修改或执行配置命令。')
            if kind!='stop':
                others=[json.loads(row[0]) for row in db.execute('SELECT data FROM environments WHERE id<>?',(ident,))]
                occupied=sum(bool(value.get('active_operation') or (value.get('service') or {}).get('state') in {'starting','ready','stopping','unknown'}) for value in others)
                if occupied>=2:raise ValueError('已有两个项目正在操作或运行，请先停止其中一个。')
            now=time.time()
            operation=dict(id=request_id,environment=ident,kind=kind,state='running',stage='admitted',owner_task=owner_task,
                           created=now,updated=now,cancel_requested=False,error='',result=None)
            environment['active_operation']=request_id
            db.execute('INSERT INTO operations VALUES (?,?,?,?)',(request_id,ident,digest,json.dumps(operation,ensure_ascii=False)))
            db.execute('INSERT INTO requests VALUES (?,?,?,?)',(request_id,ident,digest,request_id))
            self._save(db,environment)
            return environment,operation,True

    def checkpoint(self,operation_id,stage,**changes):
        with self.transaction() as db:
            operation=self._operation(db,operation_id);environment=self._environment(db,operation['environment'])
            if operation['state']!='running' or operation['cancel_requested'] or environment.get('active_operation')!=operation_id:
                raise RuntimeError('项目操作已停止或失去执行权。')
            operation['stage']=stage;environment.update(changes)
            self._save_operation(db,operation);self._save(db,environment)

    def bind_runtime(self,operation_id,sandbox_id):
        """A late create acknowledgement remains observable even after a cancel request."""
        with self.transaction() as db:
            operation=self._operation(db,operation_id);environment=self._environment(db,operation['environment'])
            if environment.get('sandbox_id') and environment['sandbox_id']!=sandbox_id:raise ValueError('原项目沙箱身份发生变化。')
            environment['sandbox_id']=sandbox_id
            if environment.get('creation_archive'):
                environment['late_runtime_observed_at']=time.time()
            self._save(db,environment)
            return environment

    def finish(self,operation_id,state,*,result=None,error='',changes=None,error_details=None):
        with self.transaction() as db:
            operation=self._operation(db,operation_id);environment=self._environment(db,operation['environment'])
            if operation.get('creation_archive'):
                observation=dict(state=state,result=result,error=error,changes=changes,at=time.time())
                if error_details is not None:observation['error_details']=error_details
                operation['late_observations']=(operation.get('late_observations',[])+[observation])[-20:]
                self._save_operation(db,operation)
                return environment,operation
            if operation['state'] not in ACTIVE:return environment,operation
            if operation['cancel_requested'] and state=='succeeded':raise RuntimeError('取消后不能登记项目操作成功。')
            operation.update(state=state,result=result,error=error)
            if error_details is not None:operation['error_details']=error_details
            if environment.get('active_operation')==operation_id:
                environment.update(changes or {})
                environment['latest_operation']=operation_id
                environment['error']=error
                if state!='unknown':environment['active_operation']=''
                self._save(db,environment)
            self._save_operation(db,operation)
            return environment,operation

    def cancel(self,ident):
        with self.transaction() as db:
            environment=self._environment(db,ident)
            if environment.get('active_operation'):
                operation=self._operation(db,environment['active_operation'])
                if operation['state'] in ACTIVE:
                    operation['cancel_requested']=True;self._save_operation(db,operation)
            return environment

    def observe(self,ident,**changes):
        """Runtime observations cannot overwrite the result of an active operation."""
        with self.transaction() as db:
            environment=self._environment(db,ident)
            if not environment.get('active_operation'):
                environment.update(changes);self._save(db,environment)
            return environment

    def observe_service(self,ident,service_id,service,error):
        """Update this exact service even while a separate read operation is active."""
        with self.transaction() as db:
            environment=self._environment(db,ident)
            if (environment.get('service') or {}).get('id')==service_id:
                environment.update(service=service,error=error);self._save(db,environment)
            return environment

    def recover_pending(self):
        """A new parent observes old infrastructure; it never replays or adopts a PID."""
        with self.transaction() as db:
            for row in db.execute('SELECT id,data FROM operations').fetchall():
                operation=json.loads(row[1])
                if operation['state']=='running':
                    operation.update(state='unknown',error='上次后台退出，需核对原操作；未自动重放。')
                    self._save_operation(db,operation)
            for row in db.execute('SELECT data FROM environments').fetchall():
                environment=json.loads(row[0]);service=environment.get('service') or {}
                if service.get('state') in {'ready','starting'}:
                    service.update(state='unknown',url='');environment['service']=service
                    environment['error']='上次服务会话需核对，旧PID仅诊断，未自动接管或重启。'
                    self._save(db,environment)

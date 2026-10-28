"""Explicit calendar deletion, shared by the UI and tebb, with durable uncertainty."""
import copy
from contextlib import ExitStack
from datetime import datetime
from . import schedule_domain as domain
from .calendar_adapter import CalendarError, identifier
from .calendar_time import TZ
from .board_data import calendar_operation
from .local_calendar import LOCAL, ACTIVE, fingerprint

class CalendarWrites:
    def __init__(self,scheduling):
        self.service=scheduling;self.local=scheduling.local;self.board=scheduling.board

    def _finish(self,op):
        with self.board.workspace_transaction() as (state,_):
            uid=op.get('local_id')
            if uid:
                self.local.records(state).pop(uid,None)
                event=dict(op['local_event'],verification='deleted',status='cancelled',editable=False,eligible=False,
                           checked_at=datetime.now(TZ).isoformat(),edit_reason='日程已删除')
                domain.cache_event(state,event)
            if op.get('remote'):
                event=dict(op['event'],verification='deleted',status='cancelled',editable=False,eligible=False,
                           checked_at=datetime.now(TZ).isoformat(),edit_reason='日程已删除')
                domain.cache_event(state,event)
            op.update(state='deleted',stage='committed',error='')
            self.local.operations(state)[op['id']]=copy.deepcopy(op)
        return self.local.result(op)

    def delete(self,action):
        op=None;sent=False;writing=False
        try:
            rid=self.board.validate_request_id(action.get('request_id'))
            cid,eid=identifier(action.get('calendar_id')),identifier(action.get('event_id'))
            with ExitStack() as stack:
                stack.enter_context(self.service.hold_event(cid,eid))
                with self.board.workspace_transaction() as (state,_):
                    old=self.local.operations(state).get(rid)
                    if old:
                        if old.get('fingerprint')!=fingerprint(action):raise ValueError('操作编号已用于其他请求。')
                        return self.local.result(old)
                    row=copy.deepcopy(self.local.records(state).get(eid)) if cid==LOCAL else next((copy.deepcopy(r) for r in self.local.records(state).values() if r.get('remote')==dict(calendar_id=cid,event_id=eid)),None)
                    if cid==LOCAL and not row:raise ValueError('本地日程不存在，请先读取。')
                    if row:
                        self.local.idle(state,row)
                        if action.get('version')!=row['version']:raise ValueError('日程版本已变化，请重新读取。')
                        local_event=self.local.view(state,row)
                        if not row.get('remote') and not local_event['editable']:raise ValueError(local_event['edit_reason'])
                    else:local_event=None
                    remote=row.get('remote') if row else dict(calendar_id=cid,event_id=eid)
                    link=next((l for l in state.get('schedule_links',[]) if domain.event_key(l['event'])==((LOCAL+'/'+row['id']) if row else cid+'/'+eid)),None)
                    item_id=link['item_id'] if link else ''
                if item_id:stack.enter_context(self.board.hold_item(item_id))
                if remote and (cid,eid)!=(remote['calendar_id'],remote['event_id']):stack.enter_context(self.service.hold_event(**remote))
                if remote:
                    stack.enter_context(calendar_operation())
                    with self.board.workspace_transaction() as (state,_):domain.require_time_idle(state,**remote)
                    calendar=self.service.calendar.primary()
                    if calendar['id']!=remote['calendar_id']:raise ValueError('当前主日历与原日程不同。')
                    event=self.service.calendar.inspect(calendar,remote['event_id'])
                    if not event.get('editable'):raise ValueError(event.get('edit_reason') or '编辑资格未核实。')
                    start,end=(row['start'],row['end']) if row else (action.get('original_start'),action.get('original_end'))
                    if not domain.same_time(event,start,end):raise ValueError('远端时间已变化，请重新读取再删除。')
                else:event=local_event
                if datetime.fromisoformat(event['end'])<=datetime.now(TZ):raise ValueError('已结束日程支持编辑，删除范围本轮未扩大。')
                op=dict(id=rid,type='delete',fingerprint=fingerprint(action),local_id=row['id'] if row else '',
                    local_event=local_event,remote=remote,event=event,calendar_id=remote['calendar_id'] if remote else LOCAL,
                    event_id=remote['event_id'] if remote else eid,state='prepared',stage='prepare',error='')
                writing=True
                with self.board.workspace_transaction() as (state,_):self.local.operations(state)[rid]=copy.deepcopy(op)
                writing=False
                if remote:
                    op.update(state='calling',stage='remote_delete');writing=True
                    with self.board.workspace_transaction() as (state,_):self.local.operations(state)[rid]=copy.deepcopy(op)
                    writing=False
                    current_identity=self.service.calendar.primary()
                    if current_identity['id']!=calendar['id'] or current_identity.get('owner_id')!=calendar.get('owner_id'):raise ValueError('飞书身份已变化，未删除。')
                    sent=True
                    self.service.calendar.delete(remote['calendar_id'],remote['event_id'])
                op['stage']='local_commit';writing=True
                return self._finish(op)
        except Exception as exc:
            if op:
                op.update(state='unconfirmed' if sent or writing else 'not_started',error=str(exc))
                try:
                    with self.board.workspace_transaction() as (state,_):
                        saved=self.local.operations(state).get(op['id'])
                        if saved and saved['state']=='deleted':return self.local.result(saved)
                        self.local.operations(state)[op['id']]=op
                except Exception:pass
                return self.local.result(op)
            return dict(ok=False,stage='delete_validate',change_state='not_changed',error=str(exc))

    def resolve(self,action):
        try:
            self.board.validate_request_id(action.get('request_id'))
            original=self.board.validate_request_id(action.get('operation_id'))
            with self.board.workspace_transaction() as (state,_):op=copy.deepcopy(self.local.operations(state).get(original))
            if not op or op['type']!='delete':raise ValueError('删除回执不存在，请核对编号。')
            if op['state'] not in ACTIVE:return self.local.result(op)
            with self.service.hold_event(op['calendar_id'],op['event_id']):
                present=False
                if op.get('remote'):
                    calendar=self.service.calendar.primary()
                    if calendar['id']!=op['calendar_id']:raise ValueError('请切回原飞书身份核对。')
                    try:
                        event=self.service.calendar.get(**op['remote'])
                        if event.get('verification') not in {'deleted','missing'}:
                            present=True
                    except CalendarError as exc:
                        if exc.status not in {'deleted','missing'}:raise
                with self.board.workspace_transaction() as (state,_):
                    if not op.get('remote'):present=op.get('local_id') in self.local.records(state)
                    if present:
                        if action.get('resolution')!='present' or action.get('confirmed') is not True:
                            raise ValueError('当前日程仍存在；可明确保留当前记录并结束等待，原删除不会重发。')
                        op.update(state='observed',stage='reconciled',error='已保留当前日程并结束原等待；没有重发删除，不推断原请求是否执行。')
                        self.local.operations(state)[original]=copy.deepcopy(op)
                        return self.local.result(op)
                return self._finish(op)
        except Exception as exc:return dict(ok=False,stage='delete_reconcile',change_state='unconfirmed',error=str(exc))
